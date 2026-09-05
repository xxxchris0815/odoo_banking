# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

import logging
from datetime import datetime, timedelta, timezone

import requests

from odoo import api, fields, models
from odoo.exceptions import UserError

from ..lib.stripe_transactions import (
    STRIPE_API_BASE,
    WEBHOOK_LOOKBACK_DAYS,
    StripeClient,
    StripeConfigError,
    StripeHTTPError,
    new_webhook_token,
    public_https_base,
    verify_webhook_signature,
    webhook_url,
)

_logger = logging.getLogger(__name__)


def _requests_http(method, url, headers, data=None):
    response = requests.request(
        method, url, headers=headers, data=data, timeout=60
    )
    try:
        payload = response.json()
    except ValueError:
        payload = response.text
    return response.status_code, payload


class OnlineBankStatementProviderStripe(models.Model):
    _inherit = "online.bank.statement.provider"

    stripe_webhook_token = fields.Char(string="Webhook token", copy=False)
    stripe_webhook_secret = fields.Char(
        string="Webhook signing secret",
        copy=False,
        help="whsec_… from the Stripe Dashboard after you add this URL.",
    )
    stripe_webhook_url = fields.Char(
        string="Webhook URL",
        compute="_compute_stripe_webhook_url",
    )
    stripe_module_version = fields.Char(
        string="Module version",
        compute="_compute_stripe_module_version",
    )

    @api.model
    def _get_available_services(self):
        services = [
            service
            for service in super()._get_available_services()
            if service[0] != "stripe"
        ]
        return services + [("stripe", "Stripe")]

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get("service") == "stripe" and not vals.get("stripe_webhook_token"):
                vals["stripe_webhook_token"] = new_webhook_token()
        return super().create(vals_list)

    def write(self, vals):
        res = super().write(vals)
        if "stripe_webhook_token" not in vals:
            for rec in self.filtered(
                lambda item: item.service == "stripe" and not item.stripe_webhook_token
            ):
                rec.stripe_webhook_token = new_webhook_token()
        return res

    @api.model
    def _stripe_assign_missing_tokens(self):
        records = self.search(
            [
                ("service", "=", "stripe"),
                "|",
                ("stripe_webhook_token", "=", False),
                ("stripe_webhook_token", "=", ""),
            ]
        )
        for rec in records:
            rec.stripe_webhook_token = new_webhook_token()

    def _register_hook(self):
        super()._register_hook()
        self._stripe_hide_oca_credential_view()

    @api.model
    def _stripe_hide_oca_credential_view(self):
        """OCA stripe uses the same service key and a second credentials group."""
        view = self.env.ref(
            "account_statement_import_online_stripe.online_bank_statement_provider_form",
            raise_if_not_found=False,
        )
        if view and view.active:
            view.sudo().write({"active": False})
            _logger.info("Disabled OCA Stripe credential view (duplicate API key fields)")

    @api.depends("service", "stripe_webhook_token")
    def _compute_stripe_webhook_url(self):
        base = self._stripe_public_base_url()
        for rec in self:
            if rec.service == "stripe" and rec.stripe_webhook_token:
                rec.stripe_webhook_url = webhook_url(base, rec.stripe_webhook_token)
            else:
                rec.stripe_webhook_url = False

    def _compute_stripe_module_version(self):
        module = self.env["ir.module.module"].sudo().search(
            [("name", "=", "account_statement_import_online_stripe_reporting")],
            limit=1,
        )
        version = module.latest_version or module.installed_version or "19.0.1.3.0"
        for rec in self:
            rec.stripe_module_version = version

    def _stripe_public_base_url(self):
        raw = self.env["ir.config_parameter"].sudo().get_param("web.base.url") or ""
        return public_https_base(raw) or raw.rstrip("/")

    def _obtain_statement_data(self, date_since, date_until):
        self.ensure_one()
        if self.service != "stripe":
            return super()._obtain_statement_data(date_since, date_until)
        return self._stripe_obtain_statement_data(date_since, date_until)

    def _stripe_client(self):
        self.ensure_one()
        if not self.password:
            raise UserError(
                self.env._("Please set the Stripe API key on the provider.")
            )
        return StripeClient(
            self.password,
            api_base=self.api_base or STRIPE_API_BASE,
            http_request=_requests_http,
        )

    def _stripe_obtain_statement_data(self, date_since, date_until):
        self.ensure_one()
        journal = self.journal_id
        currency = (journal.currency_id or journal.company_id.currency_id).name
        try:
            lines, extras = self._stripe_client().obtain_statement_lines(
                date_since, date_until, currency=currency
            )
        except (StripeConfigError, StripeHTTPError) as error:
            raise UserError(str(error)) from error
        filtered = []
        for line in lines:
            currency_code = line.pop("currency_code", None)
            line.pop("partner_email", None)
            if currency_code and currency and currency_code != currency:
                _logger.info(
                    "Skipping Stripe transaction %s with currency %s (journal is %s)",
                    line.get("unique_import_id"),
                    currency_code,
                    currency,
                )
                continue
            filtered.append(line)
        return filtered, extras

    def _update_statement_balances(self, statement_values):
        """Keep extras when Stripe reconstructed the wallet; else start + lines."""
        if self.service != "stripe":
            return super()._update_statement_balances(statement_values)
        super()._update_statement_balances(statement_values)
        if "balance_end_real" in statement_values:
            return
        total = 0.0
        for command in statement_values.get("line_ids") or []:
            if (
                isinstance(command, (list, tuple))
                and len(command) >= 3
                and isinstance(command[2], dict)
            ):
                total += float(command[2].get("amount") or 0)
        start = float(statement_values.get("balance_start") or 0)
        statement_values["balance_end_real"] = start + total

    def _stripe_handle_webhook(self, headers, raw_body, event):
        self.ensure_one()
        if self.stripe_webhook_secret:
            signature = None
            for key, value in (headers or {}).items():
                if str(key).lower() == "stripe-signature":
                    signature = value
                    break
            if not verify_webhook_signature(
                self.stripe_webhook_secret, raw_body, signature
            ):
                return False
        else:
            _logger.info(
                "Stripe webhook %s accepted by URL token only; set the signing secret",
                self.id,
            )
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        until = (now + timedelta(days=1)).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        since = until - timedelta(days=WEBHOOK_LOOKBACK_DAYS)
        lines, extras = self._stripe_obtain_statement_data(since, until)
        if not lines:
            return True
        self._create_or_update_statement((lines, extras), since, until)
        return True
