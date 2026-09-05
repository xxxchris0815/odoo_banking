{
    "name": "Bank Statement Import: Jeeves CSV",
    "version": "19.0.1.10.0",
    "category": "Accounting",
    "summary": "Jeeves CSV/MCP pull, vendor sync, and bill matching",
    "author": "Expect Magic",
    "website": "https://github.com/xxxchris0815/odoo_banking",
    "license": "AGPL-3",
    "depends": [
        "account_statement_import_file",
        "account_statement_import_online",
    ],
    "data": [
        "security/ir.model.access.csv",
        "views/res_partner.xml",
        "views/account_move.xml",
        "views/online_bank_statement_provider.xml",
        "wizards/jeeves_vendor_wizard_views.xml",
        "wizards/jeeves_bulk_export_wizard_views.xml",
    ],
    "installable": True,
}
