# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo import fields, models


class ResPartner(models.Model):
    _inherit = "res.partner"

    jeeves_vendor_id = fields.Char(
        string="Jeeves vendor",
        index=True,
        copy=False,
        help="Jeeves Vendor Id. Filled on create/update from this partner, "
        "or on the first unique e-mail match from a statement.",
    )

    def action_open_jeeves_vendor_wizard(self):
        self.ensure_one()
        partner = self.commercial_partner_id or self
        return {
            "type": "ir.actions.act_window",
            "name": "Jeeves vendor",
            "res_model": "jeeves.vendor.wizard",
            "view_mode": "form",
            "target": "new",
            "context": {"default_partner_id": partner.id},
        }
