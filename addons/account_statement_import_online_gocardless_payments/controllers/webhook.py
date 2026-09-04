# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

import json
import logging

from odoo import http
from odoo.http import request

from ..lib.gocardless_payments import (
    GoCardlessConfigError,
    GoCardlessHTTPError,
    verify_webhook_signature,
)

_logger = logging.getLogger(__name__)


class GoCardlessPaymentsWebhook(http.Controller):
    @http.route(
        "/gocardless/payments/webhook",
        type="http",
        auth="public",
        csrf=False,
        methods=["POST"],
    )
    def webhook(self, **_kwargs):
        raw = request.httprequest.get_data() or b""
        signature = request.httprequest.headers.get("Webhook-Signature")
        providers = request.env["online.bank.statement.provider"].sudo().search(
            [("service", "=", "gocardless_payments"), ("active", "=", True)]
        )
        provider = next(
            (
                item
                for item in providers
                if item.passphrase
                and verify_webhook_signature(item.passphrase, raw, signature)
            ),
            None,
        )
        if provider is None:
            _logger.warning("Rejected GoCardless webhook: invalid signature")
            return request.make_response("invalid signature", status=498)

        try:
            payload = json.loads(raw.decode("utf-8") or "{}")
        except ValueError:
            return request.make_response("invalid json", status=400)

        try:
            provider._gc_handle_webhook_events(payload.get("events") or [])
        except (GoCardlessConfigError, GoCardlessHTTPError) as error:
            _logger.exception("GoCardless webhook processing failed")
            return request.make_response(str(error), status=500)
        return request.make_response("ok", status=200)
