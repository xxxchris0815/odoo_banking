# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

import base64
import logging
from datetime import timedelta

from odoo import api, fields, models
from odoo.exceptions import UserError

from ..lib.zen_transactions import (
    ZEN_DEFAULT_API_BASE,
    ZenClient,
    ZenConfigError,
    ZenHTTPError,
    ZenTLS,
    new_webhook_token,
    public_https_base,
    requests_get_mtls,
    webhook_url,
)

_logger = logging.getLogger(__name__)


def _decode_binary_pem(value):
    """Accept PEM text or a Binary field that stores a .crt/.key as base64."""
    if not value:
        return ""
    candidates = []
    if isinstance(value, bytes):
        candidates.append(value)
        try:
            candidates.append(base64.b64decode(value))
        except (ValueError, TypeError):
            pass
    else:
        text = str(value).strip()
        if text.startswith("-----BEGIN"):
            return text
        candidates.append(text.encode())
        try:
            candidates.append(base64.b64decode(text))
        except (ValueError, TypeError):
            pass
    for raw in candidates:
        try:
            text = raw.decode()
        except UnicodeDecodeError:
            continue
        if text.strip().startswith("-----BEGIN"):
            return text.strip()
    return ""


def _requests_get(url, headers, tls=None):
    return requests_get_mtls(url, headers, tls)


class OnlineBankStatementProviderZen(models.Model):
    _inherit = "online.bank.statement.provider"

    zen_webhook_token = fields.Char(string="Webhook token", copy=False)
    zen_webhook_url = fields.Char(
        string="Webhook URL",
        compute="_compute_zen_webhook_url",
    )

    @api.model
    def _get_available_services(self):
        return super()._get_available_services() + [("zen", "ZEN.COM")]

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get("service") == "zen" and not vals.get("zen_webhook_token"):
                vals["zen_webhook_token"] = new_webhook_token()
        return super().create(vals_list)

    def write(self, vals):
        res = super().write(vals)
        if "zen_webhook_token" not in vals:
            for rec in self.filtered(
                lambda item: item.service == "zen" and not item.zen_webhook_token
            ):
                rec.zen_webhook_token = new_webhook_token()
        return res

    @api.model
    def _zen_assign_missing_tokens(self):
        records = self.search(
            [
                ("service", "=", "zen"),
                "|",
                ("zen_webhook_token", "=", False),
                ("zen_webhook_token", "=", ""),
            ]
        )
        for rec in records:
            rec.zen_webhook_token = new_webhook_token()

    @api.depends("service", "zen_webhook_token")
    def _compute_zen_webhook_url(self):
        base = self._zen_public_base_url()
        for rec in self:
            if rec.service == "zen" and rec.zen_webhook_token:
                rec.zen_webhook_url = webhook_url(base, rec.zen_webhook_token)
            else:
                rec.zen_webhook_url = False

    def _zen_public_base_url(self):
        raw = self.env["ir.config_parameter"].sudo().get_param("web.base.url") or ""
        return public_https_base(raw) or raw.rstrip("/")

    def _obtain_statement_data(self, date_since, date_until):
        self.ensure_one()
        if self.service != "zen":
            return super()._obtain_statement_data(date_since, date_until)
        return self._zen_obtain_statement_data(date_since, date_until)

    def _zen_tls(self):
        self.ensure_one()
        cert = (self.certificate_public_key or "").strip() or _decode_binary_pem(
            self.certificate
        )
        key = (self.certificate_private_key or "").strip() or _decode_binary_pem(
            self.key
        )
        ca = (self.certificate_chain or "").strip() or None
        if not cert or not key:
            raise UserError(
                self.env._(
                    "ZEN.COM Transfers API uses mTLS. "
                    "Paste the client certificate and private key on the provider."
                )
            )
        try:
            tls = ZenTLS(
                client_cert=cert,
                client_key=key,
                ca_cert=ca,
                key_password=self.passphrase or None,
            )
            tls.validate()
        except ZenConfigError as error:
            raise UserError(str(error)) from error
        return tls

    def _zen_client(self):
        self.ensure_one()
        api_key = self.password
        if not api_key:
            raise UserError(self.env._("Please set the ZEN.COM API key on the provider."))
        return ZenClient(
            api_key,
            api_base=self.api_base or ZEN_DEFAULT_API_BASE,
            account_id=self.username or None,
            iban=self.account_number or None,
            tls=self._zen_tls(),
            http_get=_requests_get,
        )

    def _zen_obtain_statement_data(self, date_since, date_until):
        self.ensure_one()
        try:
            lines, extras = self._zen_client().obtain_statement_lines(
                date_since, date_until
            )
        except (ZenConfigError, ZenHTTPError) as error:
            raise UserError(str(error)) from error
        filtered = self._zen_filter_lines(lines)
        if not filtered:
            return [], extras
        return filtered, extras

    def _zen_filter_lines(self, lines):
        self.ensure_one()
        journal = self.journal_id
        journal_currency = journal.currency_id or journal.company_id.currency_id
        filtered = []
        for line in lines:
            currency_code = line.pop("currency_code", None)
            if (
                currency_code
                and journal_currency
                and currency_code != journal_currency.name
            ):
                _logger.info(
                    "Skipping ZEN transaction %s with currency %s (journal is %s)",
                    line.get("unique_import_id"),
                    currency_code,
                    journal_currency.name,
                )
                continue
            filtered.append(line)
        return filtered

    def _zen_provider_for_account(self, account_id):
        """Route a notification to the wallet that owns this account UUID."""
        account_id = (account_id or "").strip()
        if account_id:
            match = self.filtered(lambda rec: rec.username == account_id)
            if match:
                return match[:1]
            others = self.env["online.bank.statement.provider"].sudo().search(
                [
                    ("service", "=", "zen"),
                    ("active", "=", True),
                    ("username", "=", account_id),
                ],
                limit=1,
            )
            if others:
                return others
        return self[:1]

    def _zen_handle_webhook_event(self, event):
        self.ensure_one()
        status = (event.get("status") or "").upper()
        if status and status != "SETTLED":
            return True
        payment_id = event.get("payment_id")
        if not payment_id:
            return True
        try:
            lines, extras = self._zen_client().obtain_statement_lines_for_payment(
                payment_id
            )
        except (ZenConfigError, ZenHTTPError) as error:
            raise UserError(str(error)) from error
        filtered = self._zen_filter_lines(lines)
        if not filtered:
            _logger.info(
                "ZEN webhook payment %s produced no new statement lines",
                payment_id,
            )
            return True
        dates = [line["date"] for line in filtered]
        statement = self._create_or_update_statement(
            (filtered, extras), min(dates), max(dates) + timedelta(days=1)
        )
        _logger.info(
            "ZEN webhook booked payment %s (%s lines) on %s",
            payment_id,
            len(filtered),
            statement.name if statement else "existing-or-empty",
        )
        return True
