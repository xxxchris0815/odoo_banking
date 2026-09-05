# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

import base64
import logging
from datetime import date

from odoo import api, fields, models
from odoo.exceptions import UserError

from ..lib.jeeves_invoices import (
    build_bulk_payments_csv,
    format_bulk_date,
    invoice_number,
    invoice_vendor_id,
)
from ..lib.jeeves_mcp import JeevesMCPConfigError, JeevesMCPError

_logger = logging.getLogger(__name__)


class AccountMove(models.Model):
    _inherit = "account.move"

    jeeves_invoice_id = fields.Char(
        string="Jeeves invoice",
        index=True,
        copy=False,
        help="Jeeves bill-pay invoiceId. Filled when a cash line or "
        "list_billpay_invoices matches this vendor bill.",
    )
    jeeves_payment_reference = fields.Char(
        string="Jeeves payment ref",
        copy=False,
        help="Jeeves paymentReferenceNumber (JPP…).",
    )
    jeeves_invoice_status = fields.Char(
        string="Jeeves status",
        copy=False,
    )

    def _jeeves_text(self, value):
        if value in (True, False, None):
            return ""
        return str(value).strip()

    @api.model
    def _jeeves_find_vendor_bill(self, line_or_invoice):
        """Find a posted vendor bill by Odoo number or stored Jeeves id."""
        number = ""
        jeeves_id = ""
        if isinstance(line_or_invoice, dict):
            number = self._jeeves_text(
                line_or_invoice.get("invoice_number")
                or invoice_number(line_or_invoice)
            )
            jeeves_id = self._jeeves_text(
                line_or_invoice.get("jeeves_invoice_id")
                or line_or_invoice.get("invoiceId")
            )
        else:
            number = self._jeeves_text(line_or_invoice)
        if not number and not jeeves_id:
            return self.browse()
        domain = [("move_type", "in", ("in_invoice", "in_refund"))]
        clauses = []
        if jeeves_id and "jeeves_invoice_id" in self._fields:
            clauses.append(("jeeves_invoice_id", "=", jeeves_id))
        if number:
            clauses.extend(
                [
                    ("name", "=", number),
                    ("ref", "=", number),
                    ("payment_reference", "=", number),
                ]
            )
        if not clauses:
            return self.browse()
        if len(clauses) == 1:
            domain.append(clauses[0])
        else:
            domain.extend(["|"] * (len(clauses) - 1) + clauses)
        found = self.sudo().search(domain, limit=2)
        if len(found) != 1:
            return self.browse()
        return found

    def _jeeves_write_from_invoice(self, invoice, extra=None):
        extra = extra or {}
        values = {}
        jeeves_id = self._jeeves_text(
            extra.get("jeeves_invoice_id") or invoice.get("invoiceId")
        )
        status = self._jeeves_text(
            extra.get("jeeves_invoice_status") or invoice.get("status")
        )
        payment_ref = self._jeeves_text(
            extra.get("jeeves_payment_reference")
            or invoice.get("paymentReferenceNumber")
        )
        vendor_id = invoice_vendor_id(invoice) if invoice else ""
        for move in self:
            write = {}
            if jeeves_id and not (move.jeeves_invoice_id or "").strip():
                write["jeeves_invoice_id"] = jeeves_id
            if status:
                write["jeeves_invoice_status"] = status
            if payment_ref and not (move.jeeves_payment_reference or "").strip():
                write["jeeves_payment_reference"] = payment_ref
            if write:
                move.sudo().write(write)
            partner = move.partner_id.commercial_partner_id
            if (
                vendor_id
                and partner
                and "jeeves_vendor_id" in partner._fields
                and not (partner.jeeves_vendor_id or "").strip()
            ):
                other = self.env["res.partner"].sudo().search(
                    [
                        ("jeeves_vendor_id", "=", vendor_id),
                        ("id", "!=", partner.id),
                    ],
                    limit=1,
                )
                if not other:
                    partner.sudo().write({"jeeves_vendor_id": vendor_id})
        return values

    @api.model
    def _jeeves_apply_statement_line(self, line):
        """Set partner from the vendor bill and remember Jeeves ids."""
        move = self._jeeves_find_vendor_bill(line)
        if not move:
            return
        if not line.get("partner_id"):
            line["partner_id"] = move.partner_id.id
        if not line.get("partner_name"):
            line["partner_name"] = move.partner_id.display_name
        number = self._jeeves_text(line.get("invoice_number")) or move.name
        if number:
            partner_name = line.get("partner_name") or move.partner_id.name
            line["payment_ref"] = f"{partner_name} — {number}"
        move._jeeves_write_from_invoice(
            {
                "invoiceId": line.get("jeeves_invoice_id"),
                "status": line.get("jeeves_invoice_status"),
                "paymentReferenceNumber": line.get("jeeves_payment_reference"),
                "vendor": {"id": line.get("jeeves_vendor_id")},
            },
            extra=line,
        )

    def action_jeeves_sync_invoices(self):
        provider = self.env["online.bank.statement.provider"]._jeeves_find_provider()
        try:
            invoices = provider._jeeves_client().list_billpay_invoices()
        except (JeevesMCPConfigError, JeevesMCPError) as error:
            raise UserError(str(error)) from error
        matched = self.browse()
        for invoice in invoices:
            move = self._jeeves_find_vendor_bill(invoice)
            if self:
                move = move.filtered(lambda rec: rec in self)
            if not move:
                continue
            move._jeeves_write_from_invoice(invoice)
            matched |= move
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": "Jeeves",
                "message": self.env._(
                    "Matched %s vendor bill(s) to Jeeves bill-pay invoices.",
                    len(matched),
                ),
                "type": "success",
                "sticky": False,
            },
        }

    def _jeeves_partner_account_number(self, partner, currency):
        banks = partner.bank_ids.filtered(lambda bank: bank.acc_number)
        if not banks:
            return ""
        currency_name = (currency.name or "").upper()
        if currency_name:
            for bank in banks:
                bank_ccy = bank.currency_id.name if bank.currency_id else ""
                if bank_ccy and bank_ccy.upper() == currency_name:
                    return bank.acc_number
        return banks[0].acc_number

    def _jeeves_bulk_export_rows(self):
        rows = []
        for move in self:
            if move.move_type != "in_invoice":
                continue
            partner = move.partner_id.commercial_partner_id
            currency = move.currency_id or move.company_currency_id
            amount = move.amount_residual
            if amount <= 0:
                amount = move.amount_total
            invoice_id = (
                move.name
                if move.state == "posted" and move.name != "/"
                else (move.ref or "")
            )
            rows.append(
                {
                    "vendor_name": partner.name or "",
                    "account_number": self._jeeves_partner_account_number(
                        partner, currency
                    ),
                    "currency": currency.name if currency else "",
                    "amount": amount,
                    "memo": move.payment_reference or move.ref or "Bulk payment",
                    "invoice_id": invoice_id,
                    "invoice_date": format_bulk_date(move.invoice_date),
                    "invoice_due_date": format_bulk_date(move.invoice_date_due),
                }
            )
        return rows

    def action_jeeves_export_bulk_csv(self):
        bills = self.filtered(lambda move: move.move_type == "in_invoice")
        if not bills:
            raise UserError(
                self.env._(
                    "Select one or more vendor bills (Lieferantenrechnungen), "
                    "then export again."
                )
            )
        rows = bills._jeeves_bulk_export_rows()
        if not rows:
            raise UserError(self.env._("No vendor bills in the selection."))
        missing_bank = sum(1 for row in rows if not row.get("account_number"))
        content = build_bulk_payments_csv(rows)
        filename = f"Bulk-Payments-{date.today().isoformat()}.csv"
        note = self.env._("%s bill(s) in the Jeeves Bulk Payments file.", len(rows))
        if missing_bank:
            note = self.env._(
                "%s bill(s) in the file. %s without IBAN on the contact — "
                "fill Account number before importing in Jeeves.",
                len(rows),
                missing_bank,
            )
        wizard = self.env["jeeves.bulk.export.wizard"].create(
            {
                "filename": filename,
                "data": base64.b64encode(content.encode("utf-8")),
                "line_count": len(rows),
                "note": note,
            }
        )
        return {
            "type": "ir.actions.act_window",
            "name": self.env._("Jeeves Bulk Payments"),
            "res_model": "jeeves.bulk.export.wizard",
            "res_id": wizard.id,
            "view_mode": "form",
            "target": "new",
        }
