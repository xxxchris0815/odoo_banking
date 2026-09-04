# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

import logging
from datetime import date, datetime, timedelta

import requests

from odoo import api, models
from odoo.exceptions import UserError

from ..lib.gocardless_payments import (
    GC_API_BASE,
    GoCardlessConfigError,
    GoCardlessHTTPError,
    GoCardlessPaymentsClient,
)

_logger = logging.getLogger(__name__)

_BSL_CREATE_KEYS = {
    "date",
    "payment_ref",
    "ref",
    "unique_import_id",
    "amount",
    "partner_name",
    "partner_id",
    "narration",
    "account_number",
    "transaction_type",
    "journal_id",
}


def _requests_get(url, headers):
    response = requests.get(url, headers=headers, timeout=30)
    try:
        payload = response.json()
    except ValueError:
        payload = response.text
    return response.status_code, payload


class OnlineBankStatementProviderGoCardlessPayments(models.Model):
    _inherit = "online.bank.statement.provider"

    @api.model
    def _get_available_services(self):
        return super()._get_available_services() + [
            ("gocardless_payments", "GoCardless Payments"),
        ]

    def _obtain_statement_data(self, date_since, date_until):
        self.ensure_one()
        if self.service != "gocardless_payments":
            return super()._obtain_statement_data(date_since, date_until)
        return self._gc_obtain_statement_data(date_since, date_until)

    def _gc_client(self):
        self.ensure_one()
        if not self.password:
            raise UserError(
                self.env._("Please set the GoCardless access token on the provider.")
            )
        return GoCardlessPaymentsClient(
            self.password,
            api_base=self.api_base or GC_API_BASE,
            http_get=_requests_get,
        )

    def _gc_obtain_statement_data(self, date_since, date_until):
        self.ensure_one()
        try:
            wanted = self._gc_client().obtain_statement_lines(date_since, date_until)
        except (GoCardlessConfigError, GoCardlessHTTPError) as error:
            raise UserError(str(error)) from error
        return self._gc_upsert_lines(wanted, date_since, date_until)

    def _gc_as_naive_datetime(self, value):
        if value is None:
            return None
        if isinstance(value, datetime):
            return value.replace(tzinfo=None) if value.tzinfo else value
        if isinstance(value, date):
            return datetime.combine(value, datetime.min.time())
        return datetime.fromisoformat(str(value)[:19])

    def _gc_date_in_window(self, line_date, date_since, date_until):
        when = self._gc_as_naive_datetime(line_date)
        since = self._gc_as_naive_datetime(date_since)
        until = self._gc_as_naive_datetime(date_until)
        if when is None or since is None or until is None:
            return True
        return since <= when < until

    def _gc_date_after_window(self, line_date, date_until):
        when = self._gc_as_naive_datetime(line_date)
        until = self._gc_as_naive_datetime(date_until)
        if when is None or until is None:
            return False
        return when >= until

    def _gc_create_new_line(self, values):
        """Create a collection even if OCA would drop it for being out of day."""
        create_vals = {
            key: value
            for key, value in values.items()
            if key in _BSL_CREATE_KEYS and value is not False
        }
        create_vals["journal_id"] = self.journal_id.id
        create_vals.setdefault("amount", values.get("amount") or 0.0)
        create_vals.setdefault(
            "payment_ref", values.get("payment_ref") or values.get("ref") or "/"
        )
        self.env["account.bank.statement.line"].create(create_vals)
        _logger.info(
            "Created GoCardless line %s %s amount %s",
            create_vals.get("unique_import_id"),
            create_vals.get("payment_ref"),
            create_vals.get("amount"),
        )

    def _gc_upsert_lines(self, wanted_lines, date_since=None, date_until=None):
        """Update existing collection lines when GoCardless changes status.

        Lines whose charge date sits in the current OCA day-window go through
        the normal statement creator. Older collections belonging to a payout
        in this window are created here — OCA would otherwise discard them.
        """
        self.ensure_one()
        journal = self.journal_id
        journal_currency = journal.currency_id or journal.company_id.currency_id
        new_lines = []
        created = 0
        for values in wanted_lines:
            values = dict(values)
            currency_code = values.pop("currency_code", None)
            if (
                currency_code
                and journal_currency
                and currency_code != journal_currency.name
            ):
                continue
            values = self._gc_prepare_line_values(values)
            existing = self._gc_find_statement_line(values.get("unique_import_id"))
            if not existing:
                if date_since is not None and date_until is not None:
                    if self._gc_date_after_window(values.get("date"), date_until):
                        continue
                    if not self._gc_date_in_window(
                        values.get("date"), date_since, date_until
                    ):
                        self._gc_create_new_line(values)
                        created += 1
                        continue
                new_lines.append(values)
                continue
            self._gc_write_existing_line(existing, values)
        _logger.info(
            "GoCardless upsert: %s new in-window, %s created out-of-window, %s wanted",
            len(new_lines),
            created,
            len(wanted_lines),
        )
        return new_lines, {}

    def _gc_prepare_line_values(self, values):
        """Map GoCardless customer fields onto an Odoo partner, drop extras."""
        partner = self._gc_match_partner(values)
        if partner:
            values["partner_id"] = partner.id
            values["partner_name"] = values.get("partner_name") or partner.name
        return values

    def _gc_match_partner(self, values):
        """Prefer e-mail, then IBAN, then unique name. Skip ambiguous matches."""
        Partner = self.env["res.partner"]
        email = (values.pop("partner_email", None) or "")
        if email is True or email is False:
            email = ""
        email = str(email).strip()
        company = values.pop("partner_company", None) or ""
        if company is True or company is False:
            company = ""
        company = str(company).strip()
        values.pop("gc_customer_id", None)
        account_number = (values.get("account_number") or "")
        if account_number is True or account_number is False:
            account_number = ""
        account_number = str(account_number).replace(" ", "")
        name = values.get("partner_name") or ""
        if name is True or name is False:
            name = ""
        name = str(name).strip()

        if email:
            found = Partner.search([("email", "=ilike", email)], limit=2)
            if len(found) == 1:
                return found
        if account_number:
            Bank = self.env["res.partner.bank"]
            domain = [("acc_number", "ilike", account_number)]
            if "sanitized_acc_number" in Bank._fields:
                domain = [
                    "|",
                    ("acc_number", "=", account_number),
                    ("sanitized_acc_number", "=", account_number),
                ]
            bank = Bank.search(domain, limit=1)
            if bank:
                return bank.partner_id
        for candidate in (company, name):
            if not candidate:
                continue
            found = Partner.search([("name", "=ilike", candidate)], limit=2)
            if len(found) == 1:
                return found
        return Partner.browse()

    def _gc_find_statement_line(self, unique_import_id):
        if not unique_import_id:
            return self.env["account.bank.statement.line"]
        Line = self.env["account.bank.statement.line"]
        domain = [
            ("journal_id", "=", self.journal_id.id),
            ("unique_import_id", "ilike", unique_import_id),
        ]
        return Line.search(domain, limit=1)

    def _gc_write_existing_line(self, line, values):
        updates = {}
        if values.get("payment_ref") and line.payment_ref != values["payment_ref"]:
            updates["payment_ref"] = values["payment_ref"]
        if "narration" in values and line.narration != values["narration"]:
            updates["narration"] = values["narration"]
        if values.get("partner_name") and line.partner_name != values["partner_name"]:
            updates["partner_name"] = values["partner_name"]
        if values.get("partner_id") and not line.partner_id:
            updates["partner_id"] = values["partner_id"]
        if values.get("account_number") and not line.account_number:
            updates["account_number"] = values["account_number"]
        new_amount = float(values.get("amount") or 0.0)
        amount_changed = abs(float(line.amount) - new_amount) > 0.0001
        if amount_changed:
            if getattr(line, "is_reconciled", False):
                _logger.info(
                    "GoCardless line %s is reconciled, posting adjustment instead of rewrite",
                    line.unique_import_id,
                )
                self._gc_create_adjustment(line, values, new_amount - line.amount)
            else:
                updates["amount"] = new_amount
        if updates:
            line.write(updates)
            _logger.info(
                "Updated GoCardless line %s to %s amount %s",
                line.unique_import_id,
                values.get("payment_ref"),
                values.get("amount"),
            )

    def _gc_create_adjustment(self, line, values, delta):
        if not delta:
            return
        suffix = (values.get("transaction_type") or "adj").replace(" ", "_")
        adjustment = {
            "date": values.get("date") or line.date,
            "payment_ref": values.get("payment_ref") or line.payment_ref,
            "ref": f"{line.ref or ''}-adj-{suffix}",
            "unique_import_id": f"{values.get('unique_import_id')}:adj:{suffix}",
            "amount": delta,
            "partner_name": line.partner_id.name if line.partner_id else False,
            "narration": self.env._(
                "Adjustment after GoCardless status change (original line reconciled)."
            ),
            "journal_id": self.journal_id.id,
        }
        already = self._gc_find_statement_line(adjustment["unique_import_id"])
        if already:
            return
        self.env["account.bank.statement.line"].create(adjustment)

    def _gc_handle_webhook_events(self, events):
        """Apply payment/payout/refund events so failures hit the journal immediately."""
        self.ensure_one()
        client = self._gc_client()
        wanted = []
        for event in events:
            wanted.extend(client.lines_for_event(event))
        if not wanted:
            return
        new_lines, extras = self._gc_upsert_lines(wanted)
        if not new_lines:
            return
        dates = [line["date"] for line in new_lines]
        self._create_or_update_statement(
            (new_lines, extras), min(dates), max(dates) + timedelta(days=1)
        )
