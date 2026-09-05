# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

import logging

import requests

from odoo import api, models
from odoo.exceptions import UserError

from ..lib.jeeves_mcp import (
    JEEVES_MCP_URL,
    JeevesMCPClient,
    JeevesMCPConfigError,
    JeevesMCPError,
)

_logger = logging.getLogger(__name__)


def _requests_http(method, url, headers, data=None):
    response = requests.request(
        method, url, headers=headers, data=data, timeout=60
    )
    return response.status_code, dict(response.headers), response.text


class OnlineBankStatementProviderJeeves(models.Model):
    _inherit = "online.bank.statement.provider"

    @api.model
    def _get_available_services(self):
        return super()._get_available_services() + [("jeeves", "Jeeves")]

    def _obtain_statement_data(self, date_since, date_until):
        self.ensure_one()
        if self.service != "jeeves":
            return super()._obtain_statement_data(date_since, date_until)
        return self._jeeves_obtain_statement_data(date_since, date_until)

    def _jeeves_client(self):
        self.ensure_one()
        if not self.password or not self.username:
            raise UserError(
                self.env._(
                    "Set the Jeeves MCP API key and the Cash account id "
                    "on the provider."
                )
            )
        return JeevesMCPClient(
            self.password,
            account_id=self.username,
            mcp_url=self.api_base or JEEVES_MCP_URL,
            http_request=_requests_http,
        )

    def _jeeves_obtain_statement_data(self, date_since, date_until):
        self.ensure_one()
        try:
            lines, extras = self._jeeves_client().obtain_statement_lines(
                date_since, date_until
            )
        except (JeevesMCPConfigError, JeevesMCPError) as error:
            raise UserError(str(error)) from error
        journal = self.journal_id
        currency = (journal.currency_id or journal.company_id.currency_id).name
        filtered = []
        for line in lines:
            currency_code = line.pop("currency_code", None)
            if currency_code and currency and currency_code != currency:
                _logger.info(
                    "Skipping Jeeves MCP row %s with currency %s (journal is %s)",
                    line.get("unique_import_id"),
                    currency_code,
                    currency,
                )
                continue
            self._jeeves_assign_partner_line(line)
            filtered.append(line)
        return filtered, extras

    def _jeeves_text(self, value):
        if value in (True, False, None):
            return ""
        return str(value).strip()

    def _jeeves_assign_partner_line(self, line):
        """MCP has no vendor UUID — unique payee name, then stored vendor id."""
        Partner = self.env["res.partner"].sudo()
        name = self._jeeves_text(line.get("partner_name"))
        if not name:
            return
        found = Partner.search([("name", "=ilike", name)], limit=2)
        if len(found) != 1:
            return
        line["partner_id"] = found.id
        if not line.get("partner_name"):
            line["partner_name"] = found.name
