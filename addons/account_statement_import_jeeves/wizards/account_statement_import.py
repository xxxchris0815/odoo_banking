# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

import logging

from odoo import models
from odoo.exceptions import UserError

from ..lib.jeeves_csv import (
    JeevesCSVError,
    detect_jeeves_csv,
    parse_jeeves_csv,
    statement_from_rows,
)
from ..lib.jeeves_invoices import detect_jeeves_bulk_payments_csv

_logger = logging.getLogger(__name__)


class AccountStatementImport(models.TransientModel):
    _inherit = "account.statement.import"

    def _parse_file(self, data_file):
        if detect_jeeves_bulk_payments_csv(data_file):
            raise UserError(
                self.env._(
                    "This file is a Jeeves Bulk Payments template, not a bank "
                    "statement. Pay it in the Jeeves web app, or create it from "
                    "vendor bills via Action → Export Jeeves bulk payments. "
                    "Import Activity and Exports (or use the daily MCP pull) "
                    "for cash lines."
                )
            )
        if detect_jeeves_csv(data_file):
            try:
                lines = parse_jeeves_csv(data_file)
            except JeevesCSVError as error:
                raise UserError(str(error)) from error
            lines = self._jeeves_filter_currency(lines)
            self._jeeves_assign_partners(lines)
            for line in lines:
                self.env["account.move"]._jeeves_apply_statement_line(line)
            currency, account_number, statements = statement_from_rows(lines)
            if not statements or not statements[0].get("transactions"):
                raise UserError(
                    self.env._(
                        "The Jeeves CSV contained no posted transactions for "
                        "this journal. Pending rows are skipped; check that "
                        "the file currency matches the journal."
                    )
                )
            return currency, account_number, statements
        return super()._parse_file(data_file)

    def _jeeves_journal(self):
        journal = getattr(self, "journal_id", False)
        if journal:
            return journal
        journal_id = self.env.context.get("journal_id")
        if journal_id:
            return self.env["account.journal"].browse(journal_id)
        return self.env["account.journal"]

    def _jeeves_text(self, value):
        if value in (True, False, None):
            return ""
        return str(value).strip()

    def _jeeves_filter_currency(self, lines):
        journal = self._jeeves_journal()
        if not journal:
            return lines
        currency = (journal.currency_id or journal.company_id.currency_id).name
        kept = []
        for line in lines:
            code = line.get("currency_code") or ""
            if code in (True, False, None) or not str(code).strip():
                kept.append(line)
                continue
            if str(code).strip().upper() == currency:
                kept.append(line)
            else:
                _logger.info(
                    "Skipping Jeeves row %s with currency %s (journal is %s)",
                    line.get("unique_import_id"),
                    code,
                    currency,
                )
        return kept

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

    def _jeeves_assign_partners(self, lines):
        """Stored vendor id first, then unique vendor e-mail, then unique name."""
        Partner = self.env["res.partner"].sudo()
        for line in lines:
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
            if not found:
                continue
            line["partner_id"] = found.id
            if not line.get("partner_name"):
                line["partner_name"] = found.name
