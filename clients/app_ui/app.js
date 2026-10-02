(function () {
  "use strict";

  const J02_REQUEST_SCHEMA_VERSION = "j02-request-v1";
  const J02_RESPONSE_SCHEMA_VERSION = "j02-response-v1";

  function buildRequest(values) {
    return {
      schema_version: J02_REQUEST_SCHEMA_VERSION,
      request_id: values.requestId || makeRequestId(),
      query: {
        venue: values.venue,
        instrument: values.instrument,
        start: values.start,
        end: values.end,
        representation: { kind: "trades", version: 1, definition: {} },
        options: {}
      }
    };
  }

  function makeRequestId() {
    if (globalThis.crypto && typeof globalThis.crypto.randomUUID === "function") {
      return `j06-app-ui-${globalThis.crypto.randomUUID()}`;
    }
    return `j06-app-ui-${Date.now()}-${Math.random().toString(16).slice(2)}`;
  }

  function readForm(form) {
    const data = new FormData(form);
    return {
      apiUrl: String(data.get("apiUrl") || "").trim(),
      venue: String(data.get("venue") || "").trim(),
      instrument: String(data.get("instrument") || "").trim(),
      start: String(data.get("start") || "").trim(),
      end: String(data.get("end") || "").trim(),
      maxRows: Number(data.get("maxRows") || 25)
    };
  }

  function requestOverWebSocket(url, request, socketFactory = defaultSocketFactory) {
    return new Promise((resolve) => {
      const socket = socketFactory(url);
      socket.addEventListener("open", () => {
        socket.send(JSON.stringify(request));
      });
      socket.addEventListener("message", (event) => {
        try {
          resolve(JSON.parse(event.data));
        } catch (exc) {
          resolve(transportError(request, "invalid_transport_response", "J02 response was not valid JSON", exc));
        } finally {
          socket.close();
        }
      });
      socket.addEventListener("error", () => {
        resolve(transportError(request, "connection_failed", "unable to reach J02 transport"));
      });
      socket.addEventListener("close", (event) => {
        if (!event.wasClean) {
          resolve(transportError(request, "connection_failed", "J02 transport closed before a clean response"));
        }
      });
    });
  }

  function defaultSocketFactory(url) {
    // ADR-0050 Amendment 1 (#247): the browser WebSocket API has no
    // configurable max message size (unlike Python's `websockets` library),
    // so there is no equivalent of J02/J05's explicit max_size to set here.
    // The RESULT_TOO_LARGE refusal on the server is what protects this
    // client from ever receiving an oversized frame in the first place.
    return new WebSocket(url);
  }

  function transportError(request, code, message, exc) {
    return {
      schema_version: J02_RESPONSE_SCHEMA_VERSION,
      request_id: request.request_id,
      status: "error",
      error: {
        code,
        message,
        context: exc ? { exception_type: exc.name || "Error", reason: String(exc.message || exc) } : {},
        request_identity: null
      }
    };
  }

  function renderResponse(response, maxRows) {
    setText("request-id", `request_id=${response.request_id || "-"}`);
    setText("raw-response", JSON.stringify(response, null, 2));
    if (response.status === "ok") {
      renderSuccess(response.result || {}, maxRows);
    } else if (response.status === "error") {
      renderError(response.error || {});
    } else {
      renderError({ code: "invalid_transport_response", message: "unknown response status", context: {} });
    }
  }

  function renderSuccess(result, maxRows) {
    setState("OK", "ok");
    hideError();
    const rows = Array.isArray(result.data) ? result.data : [];
    const coverage = result.coverage || {};
    setText("row-count", `rows=${result.row_count ?? rows.length}`);
    setText("coverage-state", `coverage=${coverage.complete === true ? "complete" : "partial"}`);
    const limited = rows.slice(0, Math.max(1, maxRows));
    renderRows(limited);
  }

  function renderError(error) {
    setState("Error", "error");
    setText("row-count", "rows=-");
    setText("coverage-state", "coverage=-");
    const panel = document.getElementById("error-panel");
    panel.hidden = false;
    panel.textContent = `${error.code || "error"}: ${error.message || "request failed"}`;
    renderRows([]);
  }

  function renderRows(rows) {
    const body = document.getElementById("result-rows");
    body.replaceChildren();
    if (rows.length === 0) {
      const empty = document.createElement("tr");
      empty.innerHTML = '<td colspan="6" class="empty">No rows</td>';
      body.appendChild(empty);
      return;
    }
    for (const row of rows) {
      const tr = document.createElement("tr");
      for (const key of ["exchange_ts", "price", "size", "aggressor_side", "trade_id", "sequence"]) {
        const td = document.createElement("td");
        td.textContent = row[key] == null ? "" : String(row[key]);
        tr.appendChild(td);
      }
      body.appendChild(tr);
    }
  }

  function setState(text, kind) {
    const state = document.getElementById("connection-state");
    state.textContent = text;
    state.className = `pill ${kind}`;
  }

  function setText(id, text) {
    document.getElementById(id).textContent = text;
  }

  function hideError() {
    const panel = document.getElementById("error-panel");
    panel.hidden = true;
    panel.textContent = "";
  }

  async function onSubmit(event) {
    event.preventDefault();
    const values = readForm(event.currentTarget);
    const request = buildRequest(values);
    hideError();
    setState("Connecting", "neutral");
    setText("request-id", `request_id=${request.request_id}`);
    const response = await requestOverWebSocket(values.apiUrl, request);
    renderResponse(response, values.maxRows);
  }

  function boot() {
    document.getElementById("query-form").addEventListener("submit", onSubmit);
  }

  window.J06AppUi = {
    buildRequest,
    makeRequestId,
    requestOverWebSocket,
    renderResponse,
    transportError
  };
  window.addEventListener("DOMContentLoaded", boot);
}());
