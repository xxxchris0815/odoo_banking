# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo import api, fields, models
from odoo.exceptions import UserError

from ..lib.jeeves_mcp import (
    JeevesMCPClient,
    JeevesMCPConfigError,
    JeevesMCPError,
    default_http_request,
)
from ..lib.jeeves_vendors import (
    JeevesVendorDraft,
    JeevesVendorError,
    PAYMENT_METHODS,
    default_payment_method,
    format_jeeves_phone,
    iso3_from_country_code,
    partner_phone,
    match_vendor,
    sanitize_iban,
    split_personal_name,
)


class JeevesVendorWizard(models.TransientModel):
    _name = "jeeves.vendor.wizard"
    _description = "Create or update a Jeeves vendor"

    partner_id = fields.Many2one("res.partner", required=True, ondelete="cascade")
    vendor_id = fields.Char(string="Jeeves vendor id")
    match_note = fields.Char(readonly=True)
    entity_type = fields.Selection(
        [("COMPANY", "Company"), ("PERSONAL", "Person")],
        required=True,
        default="COMPANY",
    )
    company_name = fields.Char()
    first_name = fields.Char()
    last_name = fields.Char()
    email = fields.Char(required=True)
    phone = fields.Char(required=True)
    street = fields.Char(required=True)
    city = fields.Char(required=True)
    state = fields.Char()
    zip = fields.Char(required=True)
    country_id = fields.Many2one("res.country", required=True)
    bank_country_id = fields.Many2one("res.country", required=True)
    currency_id = fields.Many2one("res.currency", required=True)
    payment_method = fields.Selection(PAYMENT_METHODS, required=True, default="SEPA")
    iban = fields.Char(string="IBAN")
    account_number = fields.Char()
    account_name = fields.Char()
    swift = fields.Char(string="SWIFT / BIC")
    bank_name = fields.Char()

    def _jeeves_partner(self):
        self.ensure_one()
        return self.partner_id.commercial_partner_id or self.partner_id

    def _country(self, code):
        if not code:
            return self.env["res.country"]
        return self.env["res.country"].search([("code", "=", code)], limit=1)

    def _currency(self, name):
        if not name:
            return self.env["res.currency"]
        return self.env["res.currency"].search([("name", "=", name)], limit=1)

    def _values_from_partner(self, partner):
        partner = partner.commercial_partner_id or partner
        company = partner.is_company
        first, last = split_personal_name(partner.name)
        bank = partner.bank_ids[:1]
        acc = sanitize_iban(bank.acc_number) if bank else ""
        country = partner.country_id or self._country("DE")
        bank_country = bank.bank_id.country if bank and bank.bank_id else country
        currency = (
            bank.currency_id
            if bank and bank.currency_id
            else partner.company_id.currency_id or self.env.company.currency_id
        )
        iso2 = country.code or "DE"
        iso3 = iso3_from_country_code(iso2)
        bank_iso3 = iso3_from_country_code(
            (bank_country.code if bank_country else iso2) or iso2
        )
        phone = partner_phone(partner)
        try:
            phone = format_jeeves_phone(phone, iso2) if phone else ""
        except JeevesVendorError:
            pass
        return {
            "partner_id": partner.id,
            "vendor_id": (partner.jeeves_vendor_id or "").strip() or False,
            "entity_type": "COMPANY" if company else "PERSONAL",
            "company_name": partner.name if company else False,
            "first_name": False if company else first,
            "last_name": False if company else last,
            "email": partner.email or False,
            "phone": phone or False,
            "street": " ".join(
                part for part in (partner.street, partner.street2) if part
            )
            or False,
            "city": partner.city or False,
            "state": partner.state_id.name if partner.state_id else False,
            "zip": partner.zip or False,
            "country_id": country.id if country else False,
            "bank_country_id": bank_country.id if bank_country else False,
            "currency_id": currency.id if currency else False,
            "payment_method": default_payment_method(currency.name if currency else "EUR", bank_iso3),
            "iban": acc or False,
            "account_name": (bank.acc_holder_name if bank else False) or partner.name,
            "swift": (bank.bank_id.bic if bank and bank.bank_id else False),
            "bank_name": (bank.bank_id.name if bank and bank.bank_id else False),
        }

    @api.model
    def default_get(self, fields_list):
        values = super().default_get(fields_list)
        partner = self.env["res.partner"]
        partner_id = values.get("partner_id") or self.env.context.get(
            "default_partner_id"
        )
        if partner_id:
            partner = self.env["res.partner"].browse(int(partner_id))
        if partner:
            values.update(self._values_from_partner(partner))
            note, vendor_id = self._lookup_existing(partner, values)
            if vendor_id and not values.get("vendor_id"):
                values["vendor_id"] = vendor_id
            if note:
                values["match_note"] = note
        return values

    def _lookup_existing(self, partner, values):
        try:
            client = self._jeeves_client()
        except (UserError, JeevesMCPConfigError, JeevesMCPError):
            return False, False
        email = values.get("email") or partner.email or ""
        name = partner.name or ""
        found = None
        try:
            if email and "@" in email:
                found = match_vendor(client.list_vendors(email), email=email)
            if not found and name:
                found = match_vendor(client.list_vendors(name), name=name)
        except (JeevesMCPConfigError, JeevesMCPError):
            return False, False
        if not found:
            return False, False
        vendor_id = str(found.get("id") or "").strip()
        stored = (partner.jeeves_vendor_id or "").strip()
        if stored and stored == vendor_id:
            return False, vendor_id
        if stored and stored != vendor_id:
            return (
                self.env._(
                    "Jeeves already has %(name)s (%(email)s) as %(vendor)s.",
                    name=found.get("vendorName") or name,
                    email=found.get("emailAddress") or email,
                    vendor=vendor_id,
                ),
                stored,
            )
        return (
            self.env._(
                "Already in Jeeves as %(name)s. Write updates this vendor "
                "instead of creating a second one.",
                name=found.get("vendorName") or name,
            ),
            vendor_id,
        )

    def _jeeves_client(self):
        provider = self.env["online.bank.statement.provider"]._jeeves_find_provider()
        return JeevesMCPClient(
            provider.password,
            account_id=provider.username or "",
            mcp_url=provider.api_base or None,
            http_request=default_http_request,
        )

    def _draft(self):
        self.ensure_one()
        partner = self._jeeves_partner()
        country = self.country_id or partner.country_id
        bank_country = self.bank_country_id or country
        try:
            return JeevesVendorDraft(
                entity_type=self.entity_type,
                company_name=self.company_name or partner.name or "",
                first_name=self.first_name or "",
                last_name=self.last_name or "",
                email=self.email or "",
                phone=self.phone or "",
                street=self.street or "",
                city=self.city or "",
                state=self.state or "n/a",
                postcode=self.zip or "",
                country_iso3=iso3_from_country_code(country.code if country else ""),
                bank_country_iso3=iso3_from_country_code(
                    bank_country.code if bank_country else ""
                ),
                currency=(self.currency_id.name or "EUR"),
                payment_method=self.payment_method or "SEPA",
                iban=self.iban or "",
                account_number=self.account_number or "",
                account_name=self.account_name or partner.name or "",
                swift=self.swift or "",
                bank_name=self.bank_name or "",
                vendor_id=(self.vendor_id or "").strip(),
            )
        except JeevesVendorError as error:
            raise UserError(str(error)) from error

    def _remember_vendor_id(self, vendor_id):
        partner = self._jeeves_partner()
        if not vendor_id or "jeeves_vendor_id" not in partner._fields:
            return
        other = self.env["res.partner"].sudo().search(
            [("jeeves_vendor_id", "=", vendor_id), ("id", "!=", partner.id)],
            limit=1,
        )
        if other:
            raise UserError(
                self.env._(
                    "Jeeves vendor %(vendor)s is already on %(partner)s.",
                    vendor=vendor_id,
                    partner=other.display_name,
                )
            )
        partner.sudo().write({"jeeves_vendor_id": vendor_id})
        self.vendor_id = vendor_id

    def _notify(self, message):
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": "Jeeves",
                "message": message,
                "type": "success",
                "sticky": False,
                "next": {"type": "ir.actions.act_window_close"},
            },
        }

    def action_submit(self):
        self.ensure_one()
        draft = self._draft()
        try:
            client = self._jeeves_client()
            if draft.vendor_id:
                client.update_vendor(draft)
                vendor_id = draft.vendor_id
                message = self.env._("Updated Jeeves vendor %s", vendor_id)
            else:
                created = client.create_vendor(draft)
                vendor_id = created["id"]
                message = self.env._("Created Jeeves vendor %s", vendor_id)
        except (JeevesVendorError, JeevesMCPConfigError, JeevesMCPError) as error:
            raise UserError(str(error)) from error
        self._remember_vendor_id(vendor_id)
        return self._notify(message)

    def action_link(self):
        self.ensure_one()
        vendor_id = (self.vendor_id or "").strip()
        if not vendor_id:
            raise UserError(self.env._("Set the Jeeves vendor id first."))
        self._remember_vendor_id(vendor_id)
        return self._notify(self.env._("Linked Jeeves vendor %s", vendor_id))
