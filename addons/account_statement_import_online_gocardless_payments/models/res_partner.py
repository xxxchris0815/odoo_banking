# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo import fields, models


class ResPartner(models.Model):
    _inherit = "res.partner"

    gocardless_customer_id = fields.Char(
        string="GoCardless customer",
        index=True,
        copy=False,
        help="GoCardless customer id (CUxxx). Filled on the first unique "
        "e-mail or IBAN match; later collections use this id.",
    )
