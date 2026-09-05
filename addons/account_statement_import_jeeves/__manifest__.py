{
    "name": "Bank Statement Import: Jeeves CSV",
    "version": "19.0.1.5.0",
    "category": "Accounting",
    "summary": "Jeeves CSV import, daily MCP pull, and vendor create/update",
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
        "views/online_bank_statement_provider.xml",
        "wizards/jeeves_vendor_wizard_views.xml",
    ],
    "installable": True,
}
