# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

import json
import logging

from odoo import http
from odoo.http import request

from ..lib.stripe_transactions import StripeConfigError, StripeHTTPError

_logger = logging.getLogger(__name__)


class StripeReportingWebhook(http.Controller):
    @http.route(
        ["/stripe/webhook/<string:token>", "/stripe/webhook/<string:token>/"],
        type="http",
        auth="public",
        csrf=False,
        methods=["GET", "POST"],
    )
    def webhook(self, token, **_kwargs):
        if request.httprequest.method == "GET":
            return request.make_response("ok", status=200)

        provider = (
            request.env["online.bank.statement.provider"]
            .sudo()
            .search(
                [
                    ("service", "=", "stripe"),
                    ("stripe_webhook_token", "=", token),
                    ("active", "=", True),
                ],
                limit=1,
            )
        )
        if not provider:
            _logger.warning("Rejected Stripe webhook: unknown token")
            return request.make_response("unknown webhook", status=404)

        raw = request.httprequest.get_data() or b""
        try:
            event = json.loads(raw.decode("utf-8") or "{}")
        except ValueError:
            return request.make_response("invalid json", status=400)

        try:
            accepted = provider._stripe_handle_webhook(
                dict(request.httprequest.headers), raw, event
            )
        except (StripeConfigError, StripeHTTPError, Exception):
            _logger.exception("Stripe webhook processing failed")
            return request.make_response("error", status=500)
        if not accepted:
            return request.make_response("invalid signature", status=400)
        return request.make_response("ok", status=200)
