#!/usr/bin/env python3
"""J06 App UI is a thin static client over the J02 transport."""

from __future__ import annotations

from html.parser import HTMLParser
from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[1]
APP_UI = ROOT / "clients" / "app_ui"


class IdCollector(HTMLParser):
    def __init__(self):
        super().__init__()
        self.ids = set()
        self.scripts = []
        self.stylesheets = []

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        if "id" in values:
            self.ids.add(values["id"])
        if tag == "script" and "src" in values:
            self.scripts.append(values["src"])
        if tag == "link" and values.get("rel") == "stylesheet":
            self.stylesheets.append(values.get("href"))


class AppUiClientTests(unittest.TestCase):
    def setUp(self):
        self.html = (APP_UI / "index.html").read_text(encoding="utf-8")
        self.js = (APP_UI / "app.js").read_text(encoding="utf-8")
        self.css = (APP_UI / "styles.css").read_text(encoding="utf-8")

    def test_static_app_has_the_required_interactive_surface(self):
        parser = IdCollector()
        parser.feed(self.html)

        self.assertEqual(["./app.js"], parser.scripts)
        self.assertEqual(["./styles.css"], parser.stylesheets)
        self.assertTrue({
            "query-form",
            "api-url",
            "venue",
            "instrument",
            "start",
            "end",
            "max-rows",
            "connection-state",
            "request-id",
            "row-count",
            "coverage-state",
            "error-panel",
            "result-rows",
            "raw-response",
        }.issubset(parser.ids))

    def test_app_constructs_only_the_j02_wire_envelope(self):
        self.assertIn('schema_version: J02_REQUEST_SCHEMA_VERSION', self.js)
        self.assertIn('"j02-request-v1"', self.js)
        self.assertIn('"j02-response-v1"', self.js)
        self.assertIn('representation: { kind: "trades", version: 1, definition: {} }', self.js)
        self.assertIn("options: {}", self.js)
        self.assertRegex(self.js, r"new WebSocket\(url\)")

    def test_app_uses_websocket_not_storage_or_http_shortcuts(self):
        forbidden = [
            "fetch(",
            "XMLHttpRequest",
            "localStorage",
            "indexedDB",
            "quant_platform",
        ]
        combined = "\n".join((self.html, self.js, self.css))
        for token in forbidden:
            with self.subTest(token=token):
                self.assertNotIn(token, combined)

    def test_rendering_fields_are_canonical_response_fields(self):
        for field in (
            "exchange_ts",
            "price",
            "size",
            "aggressor_side",
            "trade_id",
            "sequence",
            "row_count",
            "coverage",
            "error",
            "request_identity",
        ):
            with self.subTest(field=field):
                self.assertIn(field, self.js)
        self.assertFalse(re.search(r"\bprofit\b|\bpnl\b|\bsignal\b|\border\b", self.js, re.IGNORECASE))


if __name__ == "__main__":
    unittest.main()
