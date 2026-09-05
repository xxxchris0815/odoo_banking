# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

import logging

from odoo import api, models
from odoo.exceptions import UserError

from ..lib.jeeves_mcp import (
    JEEVES_MCP_URL,
    JeevesMCPClient,
    JeevesMCPConfigError,
    JeevesMCPError,
    default_http_request,
)

_logger = logging.getLogger(__name__)


class OnlineBankStatementProviderJeeves(models.Model):
    _inherit = "online.bank.statement.provider"

    @api.model
    def _get_available_services(self):
        return super()._get_available_services() + [("jeeves", "Jeeves")]

    def _obtain_statement_data(self, date_since, date_until):
        self.ensure_one()
        if self.service != "jeeves":
            return super()._obtain_statement_data(date_since, date_until)
        return self._jeeves_obtain_statement_data(date_since, date_until)

    def _jeeves_journal_currency(self):
        self.ensure_one()
        journal = self.journal_id
        return (journal.currency_id or journal.company_id.currency_id).name

    def _jeeves_client(self):
        self.ensure_one()
        if not self.password:
            raise UserError(
                self.env._("Set the Jeeves MCP API key on the provider.")
            )
        return JeevesMCPClient(
            self.password,
            account_id=self.username or "",
            currency=self._jeeves_journal_currency(),
            mcp_url=self.api_base or JEEVES_MCP_URL,
            http_request=default_http_request,
        )

    @api.model
    def _jeeves_find_provider(self):
        """Any Jeeves provider on this company that has an MCP key."""
        company = self.env.company
        found = self.env["online.bank.statement.provider"]
        for provider in self.search([("service", "=", "jeeves")]):
            if not provider.password:
                continue
            journal = provider.journal_id
            if journal and journal.company_id and journal.company_id != company:
                continue
            found = provider
            break
        if not found:
            raise UserError(
                self.env._(
                    "Set a Jeeves online bank statement provider with an "
                    "MCP API key first."
                )
            )
        return found

    def _jeeves_obtain_statement_data(self, date_since, date_until):
        self.ensure_one()
        try:
            lines, extras = self._jeeves_client().obtain_statement_lines(
                date_since, date_until
            )
        except (JeevesMCPConfigError, JeevesMCPError) as error:
            raise UserError(str(error)) from error
        currency = self._jeeves_journal_currency()
        filtered = []
        for line in lines:
            currency_code = line.pop("currency_code", None)
            if currency_code and currency and currency_code != currency:
                _logger.info(
                    "Skipping Jeeves MCP row %s with currency %s (journal is %s)",
                    line.get("unique_import_id"),
                    currency_code,
                    currency,
                )
                continue
            self._jeeves_assign_partner_line(line)
            line.pop("partner_email", None)
            line.pop("jeeves_vendor_id", None)
            filtered.append(line)
        return filtered, extras

    def _jeeves_text(self, value):
        if value in (True, False, None):
            return ""
        return str(value).strip()

    def _jeeves_remember_vendor_id(self, partner, vendor_id):
        if not partner or not vendor_id or "jeeves_vendor_id" not in partner._fields:
            return
        if (partner.jeeves_vendor_id or "").strip():
            return
        other = self.env["res.partner"].sudo().search(
            [("jeeves_vendor_id", "=", vendor_id), ("id", "!=", partner.id)],
            limit=1,
        )
        if other:
            return
        partner.sudo().write({"jeeves_vendor_id": vendor_id})

    def _jeeves_assign_partner_line(self, line):
        """Stored vendor id first, then unique vendor e-mail, then unique name."""
        Partner = self.env["res.partner"].sudo()
        vendor_id = self._jeeves_text(line.get("jeeves_vendor_id"))
        email = self._jeeves_text(line.get("partner_email"))
        name = self._jeeves_text(line.get("partner_name"))
        found = Partner.browse()
        if vendor_id and "jeeves_vendor_id" in Partner._fields:
            found = Partner.search([("jeeves_vendor_id", "=", vendor_id)], limit=2)
            if len(found) != 1:
                found = Partner.browse()
        if not found and email and "@" in email:
            by_email = Partner.search([("email", "=ilike", email)], limit=2)
            if len(by_email) == 1:
                found = by_email
                self._jeeves_remember_vendor_id(found, vendor_id)
        if not found and name:
            by_name = Partner.search([("name", "=ilike", name)], limit=2)
            if len(by_name) == 1:
                found = by_name
                self._jeeves_remember_vendor_id(found, vendor_id)
        if not found:
            return
        line["partner_id"] = found.id
        if not line.get("partner_name"):
            line["partner_name"] = found.name
