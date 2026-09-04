import importlib.util

if importlib.util.find_spec("odoo"):
    from . import wizards
