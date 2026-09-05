# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

import json
import logging

from odoo import http
from odoo.http import request

from ..lib.zen_transactions import ZenConfigError, ZenHTTPError, parse_webhook_events

_logger = logging.getLogger(__name__)


class ZenTransfersWebhook(http.Controller):
    @http.route(
        [
            "/zen/webhook/<string:token>",
            "/zen/webhook/<string:token>/",
            "/zen/webhook",
            "/zen/webhook/",
        ],
        type="http",
        auth="public",
        csrf=False,
        methods=["GET", "POST"],
    )
    def webhook(self, token=None, **_kwargs):
        if request.httprequest.method == "GET":
            return request.make_response("ok", status=200)

        raw = request.httprequest.get_data() or b""
        try:
            payload = json.loads(raw.decode("utf-8") or "{}")
        except ValueError:
            return request.make_response("invalid json", status=400)

        providers = (
            request.env["online.bank.statement.provider"]
            .sudo()
            .search([("service", "=", "zen"), ("active", "=", True)])
        )
        if token:
            providers = providers.filtered(lambda rec: rec.zen_webhook_token == token)
            if not providers:
                _logger.warning("Rejected ZEN webhook: unknown token")
                return request.make_response("unknown webhook", status=404)

        events = parse_webhook_events(payload)
        if not events:
            return request.make_response("ok", status=200)

        try:
            for event in events:
                provider = providers._zen_provider_for_account(event.get("account_id"))
                if not provider:
                    _logger.warning(
                        "Rejected ZEN webhook: no provider for account %s",
                        event.get("account_id"),
                    )
                    continue
                provider._zen_handle_webhook_event(event)
        except (ZenConfigError, ZenHTTPError, Exception):
            _logger.exception("ZEN webhook processing failed")
            return request.make_response("error", status=500)
        return request.make_response("ok", status=200)
