# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

import logging

from odoo import models
from odoo.exceptions import UserError

from ..lib.jeeves_csv import (
    JeevesCSVError,
    detect_jeeves_csv,
    parse_jeeves_csv,
    statement_from_rows,
)

_logger = logging.getLogger(__name__)


class AccountStatementImport(models.TransientModel):
    _inherit = "account.statement.import"

    def _parse_file(self, data_file):
        if detect_jeeves_csv(data_file):
            try:
                lines = parse_jeeves_csv(data_file)
            except JeevesCSVError as error:
                raise UserError(str(error)) from error
            currency, account_number, statements = statement_from_rows(lines)
            if not statements or not statements[0].get("transactions"):
                raise UserError(
                    self.env._(
                        "The Jeeves CSV contained no posted transactions. "
                        "Pending authorizations are skipped on purpose."
                    )
                )
            return currency, account_number, statements
        return super()._parse_file(data_file)
