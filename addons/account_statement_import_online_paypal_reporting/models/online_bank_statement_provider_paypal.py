# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

import logging

import requests

from odoo import api, models
from odoo.exceptions import UserError

from ..lib.paypal_transactions import (
    PAYPAL_API_BASE,
    PayPalClient,
    PayPalConfigError,
    PayPalHTTPError,
)

_logger = logging.getLogger(__name__)


def _requests_http(method, url, headers, data=None):
    response = requests.request(
        method, url, headers=headers, data=data, timeout=60
    )
    try:
        payload = response.json()
    except ValueError:
        payload = response.text
    return response.status_code, payload


class OnlineBankStatementProviderPayPal(models.Model):
    _inherit = "online.bank.statement.provider"

    @api.model
    def _get_available_services(self):
        services = [
            service
            for service in super()._get_available_services()
            if service[0] != "paypal"
        ]
        return services + [("paypal", "PayPal")]

    def _obtain_statement_data(self, date_since, date_until):
        self.ensure_one()
        if self.service != "paypal":
            return super()._obtain_statement_data(date_since, date_until)
        return self._paypal_obtain_statement_data(date_since, date_until)

    def _paypal_client(self):
        self.ensure_one()
        if not self.username or not self.password:
            raise UserError(
                self.env._(
                    "Please set the PayPal Client ID and Secret on the provider."
                )
            )
        return PayPalClient(
            self.username,
            self.password,
            api_base=self.api_base or PAYPAL_API_BASE,
            http_request=_requests_http,
        )

    def _paypal_obtain_statement_data(self, date_since, date_until):
        self.ensure_one()
        journal = self.journal_id
        currency = (journal.currency_id or journal.company_id.currency_id).name
        try:
            lines, extras = self._paypal_client().obtain_statement_lines(
                date_since, date_until, currency=currency
            )
        except (PayPalConfigError, PayPalHTTPError) as error:
            raise UserError(str(error)) from error
        filtered = []
        for line in lines:
            currency_code = line.pop("currency_code", None)
            line.pop("partner_email", None)
            if (
                currency_code
                and currency
                and currency_code != currency
            ):
                _logger.info(
                    "Skipping PayPal transaction %s with currency %s (journal is %s)",
                    line.get("unique_import_id"),
                    currency_code,
                    currency,
                )
                continue
            filtered.append(line)
        return filtered, extras
