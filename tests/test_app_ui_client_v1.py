#!/usr/bin/env python3
"""J06 App UI is a thin static client over the J02 transport."""

from __future__ import annotations

from html.parser import HTMLParser
from pathlib import Path
import re
import shutil
import subprocess
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

    def test_javascript_protocol_logic_executes_with_fake_websocket(self):
        node = shutil.which("node")
        if node is None:
            self.skipTest("node is required to execute the static App UI JavaScript behavior test")

        probe = r"""
const assert = require("assert");
const fs = require("fs");
const vm = require("vm");

const appPath = process.argv[1];
const element = () => ({
  addEventListener() {},
  appendChild() {},
  replaceChildren() {},
  hidden: false,
  textContent: "",
  className: "",
  innerHTML: ""
});
const context = {
  console,
  addEventListener() {},
  crypto: { randomUUID: () => "fixed-uuid" },
  document: {
    getElementById: () => element(),
    createElement: () => element()
  },
  FormData: class {},
  WebSocket: class {}
};
context.window = context;
context.globalThis = context;
vm.createContext(context);
vm.runInContext(fs.readFileSync(appPath, "utf8"), context);

class FakeSocket {
  constructor() {
    this.listeners = {};
    this.sent = [];
    this.closed = false;
  }
  addEventListener(name, callback) {
    this.listeners[name] = callback;
  }
  send(message) {
    this.sent.push(JSON.parse(message));
  }
  close() {
    this.closed = true;
  }
  emit(name, payload) {
    this.listeners[name](payload);
  }
}

(async () => {
  const api = context.window.J06AppUi;
  assert.ok(api);

  const request = api.buildRequest({
    venue: "bybit",
    instrument: "BTCUSDT",
    start: "2024-01-01T00:00:00Z",
    end: "2024-01-01T01:00:00Z"
  });
  assert.equal(request.schema_version, "j02-request-v1");
  assert.equal(request.request_id, "j06-app-ui-fixed-uuid");
  assert.deepEqual(request.query.representation, { kind: "trades", version: 1, definition: {} });
  assert.deepEqual(request.query.options, {});

  let socket = null;
  const success = api.requestOverWebSocket("ws://api", request, (url) => {
    socket = new FakeSocket();
    socket.url = url;
    return socket;
  });
  socket.emit("open", {});
  assert.equal(socket.url, "ws://api");
  assert.equal(socket.sent.length, 1);
  assert.equal(socket.sent[0].schema_version, "j02-request-v1");
  socket.emit("message", { data: JSON.stringify({
    schema_version: "j02-response-v1",
    request_id: request.request_id,
    status: "ok",
    result: { row_count: 0, coverage: { complete: true }, data: [] }
  }) });
  assert.equal((await success).status, "ok");
  assert.equal(socket.closed, true);

  const failed = api.requestOverWebSocket("ws://api", request, () => {
    socket = new FakeSocket();
    return socket;
  });
  socket.emit("error", {});
  const errorResponse = await failed;
  assert.equal(errorResponse.status, "error");
  assert.equal(errorResponse.error.code, "connection_failed");

  const closed = api.requestOverWebSocket("ws://api", request, () => {
    socket = new FakeSocket();
    return socket;
  });
  socket.emit("close", { wasClean: false });
  const closeResponse = await closed;
  assert.equal(closeResponse.error.code, "connection_failed");

  const malformed = api.requestOverWebSocket("ws://api", request, () => {
    socket = new FakeSocket();
    return socket;
  });
  socket.emit("message", { data: "{not-json" });
  const malformedResponse = await malformed;
  assert.equal(malformedResponse.error.code, "invalid_transport_response");

  console.log("J06_APP_UI_JS_BEHAVIOR: PASS");
})().catch((error) => {
  console.error(error);
  process.exit(1);
});
"""
        result = subprocess.run(
            [node, "-e", probe, str(APP_UI / "app.js")],
            capture_output=True,
            text=True,
            timeout=10,
        )
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        self.assertIn("J06_APP_UI_JS_BEHAVIOR: PASS", result.stdout)


if __name__ == "__main__":
    unittest.main()
