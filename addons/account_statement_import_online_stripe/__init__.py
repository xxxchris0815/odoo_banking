import importlib.util

if importlib.util.find_spec("odoo"):
    from . import controllers
    from . import models


def post_init_hook(env):
    env["online.bank.statement.provider"]._stripe_assign_missing_tokens()
