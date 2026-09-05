# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo import fields, models


class ResPartner(models.Model):
    _inherit = "res.partner"

    paypal_payer_id = fields.Char(
        string="PayPal payer",
        index=True,
        copy=False,
        help="PayPal account id of this contact. Filled on the first unique "
        "e-mail match; later payments use this id.",
    )
