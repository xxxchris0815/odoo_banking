import importlib.util

if importlib.util.find_spec("odoo"):
    from . import controllers
    from . import models
