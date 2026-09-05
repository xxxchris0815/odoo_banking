# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo import fields, models


class ResPartner(models.Model):
    _inherit = "res.partner"

    jeeves_vendor_id = fields.Char(
        string="Jeeves vendor",
        index=True,
        copy=False,
        help="Jeeves Vendor Id. Filled on the first unique e-mail match; "
        "later CSV rows use this id.",
    )
