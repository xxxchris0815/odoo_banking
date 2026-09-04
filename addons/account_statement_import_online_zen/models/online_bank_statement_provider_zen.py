# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

import logging

import requests

from odoo import api, models
from odoo.exceptions import UserError

from ..lib.zen_transactions import (
    ZEN_DEFAULT_API_BASE,
    ZenClient,
    ZenConfigError,
    ZenHTTPError,
)

_logger = logging.getLogger(__name__)


def _requests_get(url, headers):
    response = requests.get(url, headers=headers, timeout=30)
    try:
        payload = response.json()
    except ValueError:
        payload = response.text
    return response.status_code, payload


class OnlineBankStatementProviderZen(models.Model):
    _inherit = "online.bank.statement.provider"

    @api.model
    def _get_available_services(self):
        return super()._get_available_services() + [("zen", "ZEN.COM")]

    def _obtain_statement_data(self, date_since, date_until):
        self.ensure_one()
        if self.service != "zen":
            return super()._obtain_statement_data(date_since, date_until)
        return self._zen_obtain_statement_data(date_since, date_until)

    def _zen_client(self):
        self.ensure_one()
        api_key = self.password
        if not api_key:
            raise UserError(self.env._("Please set the ZEN.COM API key on the provider."))
        return ZenClient(
            api_key,
            api_base=self.api_base or ZEN_DEFAULT_API_BASE,
            account_id=self.username or None,
            iban=self.account_number or None,
            http_get=_requests_get,
        )

    def _zen_obtain_statement_data(self, date_since, date_until):
        self.ensure_one()
        try:
            lines, extras = self._zen_client().obtain_statement_lines(
                date_since, date_until
            )
        except (ZenConfigError, ZenHTTPError) as error:
            raise UserError(str(error)) from error
        journal = self.journal_id
        journal_currency = journal.currency_id or journal.company_id.currency_id
        filtered = []
        for line in lines:
            currency_code = line.pop("currency_code", None)
            if (
                currency_code
                and journal_currency
                and currency_code != journal_currency.name
            ):
                _logger.info(
                    "Skipping ZEN transaction %s with currency %s (journal is %s)",
                    line.get("unique_import_id"),
                    currency_code,
                    journal_currency.name,
                )
                continue
            filtered.append(line)
        if not filtered:
            return [], extras
        return filtered, extras
