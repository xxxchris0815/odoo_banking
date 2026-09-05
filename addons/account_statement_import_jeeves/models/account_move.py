# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

import logging

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

    def action_jeeves_export_bulk_csv(self):
        bills = self.filtered(lambda move: move.move_type == "in_invoice")
        if not bills:
            raise UserError(self.env._("Select posted vendor bills first."))
        rows = []
        skipped = 0
        for move in bills:
            if move.state != "posted":
                skipped += 1
                continue
            residual = move.amount_residual
            if residual <= 0 and not self.env.context.get("jeeves_bulk_include_paid"):
                skipped += 1
                continue
            partner = move.partner_id.commercial_partner_id
            bank = partner.bank_ids[:1]
            currency = move.currency_id or move.company_currency_id
            rows.append(
                {
                    "vendor_name": partner.name,
                    "account_number": bank.acc_number if bank else "",
                    "currency": currency.name,
                    "amount": residual or move.amount_total,
                    "memo": move.payment_reference or move.ref or move.name or "Bulk payment",
                    "invoice_id": move.name,
                    "invoice_date": format_bulk_date(move.invoice_date),
                    "invoice_due_date": format_bulk_date(move.invoice_date_due),
                }
            )
        if not rows:
            raise UserError(
                self.env._(
                    "No open vendor bills to export. Posted bills with a residual "
                    "amount are required."
                )
            )
        content = build_bulk_payments_csv(rows)
        attachment = self.env["ir.attachment"].create(
            {
                "name": "jeeves_bulk_payments.csv",
                "type": "binary",
                "raw": content.encode("utf-8"),
                "mimetype": "text/csv",
                "res_model": "account.move",
                "res_id": bills[0].id,
            }
        )
        return {
            "type": "ir.actions.act_url",
            "url": f"/web/content/{attachment.id}?download=true",
            "target": "self",
        }
