# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo import fields, models


class ResPartner(models.Model):
    _inherit = "res.partner"

    stripe_customer_id = fields.Char(
        string="Stripe customer",
        index=True,
        copy=False,
        help="Stripe customer id (cus_…). Filled on the first unique "
        "e-mail match; later charges use this id.",
    )

    _sql_constraints = [
        (
            "stripe_customer_id_uniq",
            "unique(stripe_customer_id)",
            "This Stripe customer is already linked to another contact.",
        ),
    ]
