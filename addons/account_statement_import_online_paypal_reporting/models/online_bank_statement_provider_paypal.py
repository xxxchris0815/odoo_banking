# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

import logging
from datetime import datetime, timedelta, timezone

import requests

from odoo import api, fields, models
from odoo.exceptions import UserError

from ..lib.paypal_transactions import (
    PAYPAL_API_BASE,
    WEBHOOK_LOOKBACK_DAYS,
    PayPalClient,
    PayPalConfigError,
    PayPalHTTPError,
    new_webhook_token,
    public_https_base,
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


class OnlineBankStatementProviderPayPal(models.Model):
    _inherit = "online.bank.statement.provider"

    paypal_webhook_token = fields.Char(
        string="Webhook token",
        copy=False,
        help="Identifies this PayPal account in the webhook URL. "
        "Each provider gets its own token.",
    )
    paypal_webhook_id = fields.Char(
        string="Webhook ID",
        copy=False,
        help="ID PayPal shows after you add the webhook URL "
        "(or after Register webhook).",
    )
    paypal_webhook_url = fields.Char(
        string="Webhook URL",
        compute="_compute_paypal_webhook_url",
    )
    paypal_module_version = fields.Char(
        string="Module version",
        compute="_compute_paypal_module_version",
    )

    @api.model
    def _get_available_services(self):
        services = [
            service
            for service in super()._get_available_services()
            if service[0] != "paypal"
        ]
        return services + [("paypal", "PayPal")]

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get("service") == "paypal" and not vals.get("paypal_webhook_token"):
                vals["paypal_webhook_token"] = new_webhook_token()
        return super().create(vals_list)

    def write(self, vals):
        res = super().write(vals)
        if "paypal_webhook_token" not in vals:
            for rec in self.filtered(
                lambda item: item.service == "paypal" and not item.paypal_webhook_token
            ):
                rec.paypal_webhook_token = new_webhook_token()
        return res

    @api.model
    def _paypal_assign_missing_tokens(self):
        records = self.search(
            [
                ("service", "=", "paypal"),
                "|",
                ("paypal_webhook_token", "=", False),
                ("paypal_webhook_token", "=", ""),
            ]
        )
        for rec in records:
            rec.paypal_webhook_token = new_webhook_token()

    def _register_hook(self):
        super()._register_hook()
        self._paypal_hide_oca_credential_view()

    @api.model
    def _paypal_hide_oca_credential_view(self):
        """OCA paypal adds a second Client ID/Secret group on the same form."""
        view = self.env.ref(
            "account_statement_import_online_paypal.online_bank_statement_provider_form",
            raise_if_not_found=False,
        )
        if view and view.active:
            view.sudo().write({"active": False})
            _logger.info("Disabled OCA PayPal credential view (duplicate Client ID fields)")

    @api.depends("service", "paypal_webhook_token")
    def _compute_paypal_webhook_url(self):
        base = self._paypal_public_base_url()
        for rec in self:
            if rec.service == "paypal" and rec.paypal_webhook_token:
                rec.paypal_webhook_url = webhook_url(base, rec.paypal_webhook_token)
            else:
                rec.paypal_webhook_url = False

    def _compute_paypal_module_version(self):
        module = self.env["ir.module.module"].sudo().search(
            [("name", "=", "account_statement_import_online_paypal_reporting")],
            limit=1,
        )
        version = module.latest_version or module.installed_version or "19.0.1.4.0"
        for rec in self:
            rec.paypal_module_version = version

    def _paypal_public_base_url(self):
        raw = self.env["ir.config_parameter"].sudo().get_param("web.base.url") or ""
        return public_https_base(raw) or raw.rstrip("/")

    def _obtain_statement_data(self, date_since, date_until):
        self.ensure_one()
        if self.service != "paypal":
            return super()._obtain_statement_data(date_since, date_until)
        return self._paypal_obtain_statement_data(date_since, date_until)

    def _paypal_client(self):
        self.ensure_one()
        if not self.username or not self.password:
            raise UserError(
                self.env._(
                    "Please set the PayPal Client ID and Secret on the provider."
                )
            )
        return PayPalClient(
            self.username,
            self.password,
            api_base=self.api_base or PAYPAL_API_BASE,
            http_request=_requests_http,
        )

    def _paypal_obtain_statement_data(self, date_since, date_until):
        self.ensure_one()
        journal = self.journal_id
        currency = (journal.currency_id or journal.company_id.currency_id).name
        try:
            lines, extras = self._paypal_client().obtain_statement_lines(
                date_since, date_until, currency=currency
            )
        except (PayPalConfigError, PayPalHTTPError) as error:
            raise UserError(str(error)) from error
        filtered = []
        for line in lines:
            currency_code = line.pop("currency_code", None)
            line.pop("partner_email", None)
            if (
                currency_code
                and currency
                and currency_code != currency
            ):
                _logger.info(
                    "Skipping PayPal transaction %s with currency %s (journal is %s)",
                    line.get("unique_import_id"),
                    currency_code,
                    currency,
                )
                continue
            filtered.append(line)
        return filtered, extras

    def action_paypal_register_webhook(self):
        self.ensure_one()
        if self.service != "paypal":
            raise UserError(self.env._("This action is only for the PayPal provider."))
        if not self.paypal_webhook_url:
            raise UserError(self.env._("Save the provider first so a webhook URL exists."))
        try:
            webhook_id = self._paypal_client().ensure_webhook(self.paypal_webhook_url)
        except (PayPalConfigError, PayPalHTTPError) as error:
            raise UserError(str(error)) from error
        self.paypal_webhook_id = webhook_id
        return True

    def _paypal_handle_webhook(self, headers, event):
        """Verify (when a webhook ID is set) and pull the last few days."""
        self.ensure_one()
        if self.paypal_webhook_id:
            try:
                if not self._paypal_client().verify_webhook(
                    self.paypal_webhook_id, headers, event
                ):
                    return False
            except (PayPalConfigError, PayPalHTTPError) as error:
                _logger.warning("PayPal webhook verification failed: %s", error)
                return False
        else:
            _logger.info(
                "PayPal webhook %s accepted by URL token only; set Webhook ID to verify",
                self.id,
            )
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        until = (now + timedelta(days=1)).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        since = until - timedelta(days=WEBHOOK_LOOKBACK_DAYS)
        lines, extras = self._paypal_obtain_statement_data(since, until)
        if not lines:
            return True
        self._create_or_update_statement((lines, extras), since, until)
        return True
