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
    contact_from_vendor,
    default_payment_method,
    format_jeeves_phone,
    iban_country_iso2,
    iso2_from_country_code,
    iso3_from_country_code,
    missing_jeeves_requirements,
    parse_partner_email,
    partner_phone,
    sanitize_iban,
    split_personal_name,
)


class JeevesVendorWizard(models.TransientModel):
    _name = "jeeves.vendor.wizard"
    _description = "Create or update a Jeeves vendor"

    partner_id = fields.Many2one("res.partner", required=True, ondelete="cascade")
    vendor_id = fields.Char(string="Jeeves vendor id")
    match_note = fields.Char(readonly=True)
    missing_note = fields.Char(readonly=True)
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
    account_last4 = fields.Char()
    account_number = fields.Char()
    account_name = fields.Char()
    swift = fields.Char(string="SWIFT / BIC")
    bank_name = fields.Char()

    def _jeeves_partner(self):
        self.ensure_one()
        return self.partner_id.commercial_partner_id or self.partner_id

    def _country(self, code):
        iso2 = iso2_from_country_code(code) if code else ""
        if not iso2:
            iso2 = (code or "").strip().upper()
        if not iso2 or len(iso2) != 2:
            return self.env["res.country"]
        return self.env["res.country"].search([("code", "=", iso2)], limit=1)

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
        bank_country = self._country(iban_country_iso2(acc))
        if not bank_country and bank and bank.bank_id and bank.bank_id.country:
            bank_country = bank.bank_id.country
        if not bank_country:
            bank_country = country
        currency = (
            bank.currency_id
            if bank and bank.currency_id
            else partner.company_id.currency_id or self.env.company.currency_id
        )
        iso2 = country.code or "DE"
        bank_iso2 = (bank_country.code if bank_country else iso2) or iso2
        try:
            bank_iso3 = iso3_from_country_code(bank_iso2)
        except JeevesVendorError:
            bank_iso3 = ""
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
            "email": parse_partner_email(partner.email) or False,
            "phone": phone or False,
            "street": " ".join(
                part for part in (partner.street, partner.street2) if part
            )
            or False,
            "city": partner.city or False,
            "state": (partner.state_id.name if partner.state_id else False) or "n/a",
            "zip": partner.zip or False,
            "country_id": country.id if country else False,
            "bank_country_id": bank_country.id if bank_country else False,
            "currency_id": currency.id if currency else False,
            "payment_method": default_payment_method(
                currency.name if currency else "EUR", bank_iso3
            ),
            "iban": acc or False,
            "account_name": (bank.acc_holder_name if bank else False) or partner.name,
            "swift": (bank.bank_id.bic if bank and bank.bank_id else False),
            "bank_name": (bank.bank_id.name if bank and bank.bank_id else False),
        }

    def _values_from_jeeves_vendor(self, vendor):
        """Jeeves wins for every field it actually returns."""
        contact = contact_from_vendor(vendor)
        values = {}
        if contact.get("vendor_id"):
            values["vendor_id"] = contact["vendor_id"]
        if contact.get("entity_type"):
            values["entity_type"] = contact["entity_type"]
        if contact.get("company_name"):
            values["company_name"] = contact["company_name"]
        if contact.get("first_name"):
            values["first_name"] = contact["first_name"]
        if contact.get("last_name"):
            values["last_name"] = contact["last_name"]
        if contact.get("email"):
            values["email"] = contact["email"]
        if contact.get("phone"):
            values["phone"] = contact["phone"]
        if contact.get("street"):
            values["street"] = contact["street"]
        if contact.get("city"):
            values["city"] = contact["city"]
        if contact.get("zip"):
            values["zip"] = contact["zip"]
        if contact.get("state"):
            values["state"] = contact["state"]
        country = self._country(contact.get("country_iso3"))
        if country:
            values["country_id"] = country.id
        bank_country = self._country(contact.get("bank_iso3"))
        if bank_country:
            values["bank_country_id"] = bank_country.id
        currency = self._currency(contact.get("currency"))
        if currency:
            values["currency_id"] = currency.id
        if contact.get("payment_method"):
            values["payment_method"] = contact["payment_method"]
        if contact.get("iban"):
            values["iban"] = contact["iban"]
        if contact.get("account_last4"):
            values["account_last4"] = contact["account_last4"]
        if contact.get("account_name"):
            values["account_name"] = contact["account_name"]
        if contact.get("swift"):
            values["swift"] = contact["swift"]
        if contact.get("bank_name"):
            values["bank_name"] = contact["bank_name"]
        return values

    def _missing_note(self, values):
        country = self.env["res.country"].browse(values.get("country_id") or 0)
        bank_country = self.env["res.country"].browse(values.get("bank_country_id") or 0)
        currency = self.env["res.currency"].browse(values.get("currency_id") or 0)
        try:
            country_iso3 = iso3_from_country_code(country.code if country else "")
        except JeevesVendorError:
            country_iso3 = ""
        try:
            bank_iso3 = iso3_from_country_code(
                bank_country.code if bank_country else ""
            )
        except JeevesVendorError:
            bank_iso3 = ""
        draft = JeevesVendorDraft(
            entity_type=values.get("entity_type") or "COMPANY",
            company_name=values.get("company_name") or "",
            first_name=values.get("first_name") or "",
            last_name=values.get("last_name") or "",
            email=values.get("email") or "",
            phone=values.get("phone") or "",
            street=values.get("street") or "",
            city=values.get("city") or "",
            postcode=values.get("zip") or "",
            country_iso3=country_iso3,
            bank_country_iso3=bank_iso3,
            currency=currency.name if currency else "",
            iban=values.get("iban") or "",
            account_number=values.get("account_number") or "",
        )
        missing = missing_jeeves_requirements(draft)
        if not missing:
            return False
        return self.env._(
            "Jeeves cannot create or update this vendor without: %s.",
            ", ".join(missing),
        )

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
            note, vendor_id, vendor = self._lookup_existing(partner, values)
            if vendor_id and not values.get("vendor_id"):
                values["vendor_id"] = vendor_id
            if note:
                values["match_note"] = note
            elif vendor_id:
                values["match_note"] = self.env._(
                    "Found in Jeeves as %s. Use “Von Jeeves laden”, then "
                    "“Nach Odoo schreiben”.",
                    vendor_id,
                )
            values["missing_note"] = self._missing_note(values)
        return values

    def _lookup_existing(self, partner, values):
        try:
            client = self._jeeves_client()
        except (UserError, JeevesMCPConfigError, JeevesMCPError):
            return False, False, None
        email = values.get("email") or parse_partner_email(partner.email) or ""
        name = partner.name or ""
        stored = (partner.jeeves_vendor_id or values.get("vendor_id") or "").strip()
        try:
            found = client.find_vendor(vendor_id=stored, email=email, name=name)
        except (JeevesMCPConfigError, JeevesMCPError):
            return False, False, None
        if not found:
            return False, False, None
        vendor_id = str(found.get("id") or found.get("vendorId") or "").strip()
        if stored and stored == vendor_id:
            return False, vendor_id, found
        if stored and stored != vendor_id:
            return (
                self.env._(
                    "Jeeves already has %(name)s (%(email)s) as %(vendor)s.",
                    name=found.get("vendorName") or name,
                    email=found.get("emailAddress") or email,
                    vendor=vendor_id,
                ),
                stored,
                found,
            )
        return (
            self.env._(
                "Already in Jeeves as %(name)s. Load from Jeeves, then "
                "write to Odoo.",
                name=found.get("vendorName") or name,
            ),
            vendor_id,
            found,
        )

    def _jeeves_client(self):
        provider = self.env["online.bank.statement.provider"]._jeeves_find_provider()
        return JeevesMCPClient(
            provider.password,
            account_id=provider.username or "",
            mcp_url=provider.api_base or None,
            http_request=default_http_request,
        )

    @api.onchange("iban")
    def _onchange_iban(self):
        iso2 = iban_country_iso2(self.iban)
        country = self._country(iso2)
        if country:
            self.bank_country_id = country
            currency = self.currency_id.name if self.currency_id else "EUR"
            try:
                self.payment_method = default_payment_method(
                    currency, iso3_from_country_code(iso2)
                )
            except JeevesVendorError:
                pass

    def _draft(self):
        self.ensure_one()
        partner = self._jeeves_partner()
        country = self.country_id or partner.country_id
        bank_country = self.bank_country_id or self._country(
            iban_country_iso2(self.iban)
        ) or country
        try:
            return JeevesVendorDraft(
                entity_type=self.entity_type,
                company_name=self.company_name or partner.name or "",
                first_name=self.first_name or "",
                last_name=self.last_name or "",
                email=parse_partner_email(self.email) or (self.email or ""),
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

    def _write_back_partner(self):
        partner = self._jeeves_partner()
        values = {}
        if not (partner.street or "").strip() and self.street:
            values["street"] = self.street
        if not (partner.zip or "").strip() and self.zip:
            values["zip"] = self.zip
        if not (partner.city or "").strip() and self.city:
            values["city"] = self.city
        if not partner.country_id and self.country_id:
            values["country_id"] = self.country_id.id
        cleaned = parse_partner_email(self.email)
        if cleaned and parse_partner_email(partner.email) != cleaned:
            values["email"] = cleaned
        if "phone" in partner._fields and not partner_phone(partner) and self.phone:
            values["phone"] = self.phone
        if values:
            partner.sudo().write(values)

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
        self._write_back_partner()
        return self._notify(message)

    def action_link(self):
        self.ensure_one()
        vendor_id = (self.vendor_id or "").strip()
        if not vendor_id:
            raise UserError(self.env._("Set the Jeeves vendor id first."))
        self._remember_vendor_id(vendor_id)
        return self._notify(self.env._("Linked Jeeves vendor %s", vendor_id))

    def _fetch_jeeves_vendor(self):
        self.ensure_one()
        partner = self._jeeves_partner()
        try:
            client = self._jeeves_client()
            return client.find_vendor(
                vendor_id=self.vendor_id or partner.jeeves_vendor_id,
                email=parse_partner_email(self.email) or parse_partner_email(partner.email),
                name=partner.name,
            )
        except (JeevesMCPConfigError, JeevesMCPError) as error:
            raise UserError(str(error)) from error

    def _import_bank(self, partner):
        iban = sanitize_iban(self.iban)
        if "*" in iban:
            iban = ""
        last4 = (self.account_last4 or "").strip()
        bank = partner.bank_ids[:1]
        if last4:
            same_tail = partner.bank_ids.filtered(
                lambda rec: sanitize_iban(rec.acc_number).endswith(last4)
            )
            if same_tail:
                bank = same_tail[:1]
        if iban:
            same = partner.bank_ids.filtered(
                lambda rec: sanitize_iban(rec.acc_number) == iban
            )
            if same:
                bank = same[:1]
            elif bank and not (bank.acc_number or "").strip():
                bank.sudo().write({"acc_number": iban})
            elif not bank:
                bank = self.env["res.partner.bank"].sudo().create(
                    {
                        "partner_id": partner.id,
                        "acc_number": iban,
                        "acc_holder_name": self.account_name or partner.name,
                    }
                )
        if not bank or not self.bank_country_id:
            return
        writes = {}
        if self.account_name and not (bank.acc_holder_name or "").strip():
            writes["acc_holder_name"] = self.account_name
        if self.swift and bank.bank_id and not bank.bank_id.bic:
            bank.bank_id.sudo().write({"bic": self.swift})
        if self.bank_country_id:
            if bank.bank_id:
                if not bank.bank_id.country:
                    bank.bank_id.sudo().write({"country": self.bank_country_id.id})
            else:
                name = self.bank_name or self.bank_country_id.name
                res_bank = self.env["res.bank"].sudo().search(
                    [
                        ("name", "=", name),
                        ("country", "=", self.bank_country_id.id),
                    ],
                    limit=1,
                )
                if not res_bank:
                    res_bank = self.env["res.bank"].sudo().create(
                        {
                            "name": name,
                            "country": self.bank_country_id.id,
                            "bic": self.swift or False,
                        }
                    )
                writes["bank_id"] = res_bank.id
        if writes:
            bank.sudo().write(writes)

    def _import_partner_from_wizard(self):
        partner = self._jeeves_partner()
        values = {}
        imported = []
        if self.street:
            values["street"] = self.street
            imported.append("street")
        if self.zip:
            values["zip"] = self.zip
            imported.append("ZIP")
        if self.city:
            values["city"] = self.city
            imported.append("city")
        if self.country_id:
            values["country_id"] = self.country_id.id
            imported.append("country")
        if self.state and self.state != "n/a" and self.country_id:
            state = self.env["res.country.state"].search(
                [
                    ("country_id", "=", self.country_id.id),
                    ("name", "=ilike", self.state),
                ],
                limit=1,
            )
            if state:
                values["state_id"] = state.id
        cleaned = parse_partner_email(self.email)
        if cleaned:
            values["email"] = cleaned
            imported.append("e-mail")
        if self.phone and "phone" in partner._fields:
            values["phone"] = self.phone
            imported.append("phone")
        if values:
            partner.sudo().write(values)
        if self.vendor_id:
            self._remember_vendor_id(self.vendor_id)
            imported.append("Jeeves vendor id")
        self._import_bank(partner)
        if self.bank_country_id:
            imported.append("bank country")
        return imported

    def _reload(self):
        return {
            "type": "ir.actions.act_window",
            "name": self.env._("Jeeves vendor"),
            "res_model": "jeeves.vendor.wizard",
            "res_id": self.id,
            "view_mode": "form",
            "target": "new",
        }

    def action_load_from_jeeves(self):
        self.ensure_one()
        vendor = self._fetch_jeeves_vendor()
        if not vendor:
            raise UserError(
                self.env._(
                    "No Jeeves vendor found for this contact. Set the Jeeves "
                    "vendor id or match e-mail / name first."
                )
            )
        values = self._values_from_jeeves_vendor(vendor)
        if not values:
            raise UserError(
                self.env._("Jeeves returned a vendor without contact details.")
            )
        contact = contact_from_vendor(vendor)
        missing = [
            label
            for key, label in (
                ("phone", "phone"),
                ("street", "street"),
                ("zip", "ZIP"),
                ("city", "city"),
                ("bank_iso3", "bank country"),
            )
            if not contact.get(key)
        ]
        values["match_note"] = self.env._(
            "list_vendors returned e-mail, name and bank country. "
            "Phone and street are not in this MCP tool. "
            "Click “Nach Odoo schreiben” to copy what Jeeves did send."
        )
        values["missing_note"] = (
            self.env._(
                "Not in list_vendors (only in the Jeeves web UI): %s.",
                ", ".join(missing),
            )
            if missing
            else False
        )
        self.write(values)
        return self._reload()

    def action_write_to_odoo(self):
        self.ensure_one()
        imported = self._import_partner_from_wizard()
        if not imported:
            raise UserError(
                self.env._(
                    "Nothing to write. Load from Jeeves or fill phone / "
                    "address first."
                )
            )
        self.match_note = self.env._(
            "Written to the Odoo contact: %s.",
            ", ".join(imported),
        )
        self.missing_note = False
        return self._reload()

    def action_import_to_odoo(self):
        """Back-compat: load from Jeeves, then write the form to the contact."""
        self.action_load_from_jeeves()
        return self.action_write_to_odoo()
