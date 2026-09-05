{
    "name": "Online Bank Statements: PayPal",
    "version": "19.0.1.1.0",
    "category": "Accounting",
    "summary": "PayPal Transaction Search into bank statements (names, fees, withdrawals)",
    "author": "Expect Magic",
    "website": "https://github.com/xxxchris0815/odoo_banking",
    "license": "AGPL-3",
    "depends": ["account_statement_import_online"],
    "data": ["views/online_bank_statement_provider.xml"],
    "post_init_hook": "post_init_hook",
    "installable": True,
}
