# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo import fields, models


class JeevesBulkExportWizard(models.TransientModel):
    _name = "jeeves.bulk.export.wizard"
    _description = "Download Jeeves bulk payments CSV"

    filename = fields.Char(string="Dateiname", readonly=True)
    data = fields.Binary(string="CSV herunterladen", readonly=True)
    line_count = fields.Integer(readonly=True)
    note = fields.Char(readonly=True)
