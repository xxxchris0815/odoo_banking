# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo import api, models


class OnlineBankStatementProvider(models.Model):
    _inherit = "online.bank.statement.provider"

    @api.model
    def _get_available_services(self):
        seen = set()
        services = []
        for key, label in super()._get_available_services():
            if key in seen:
                continue
            seen.add(key)
            if key == "paypal":
                services.append((key, "PayPal"))
            else:
                services.append((key, label))
        return services
