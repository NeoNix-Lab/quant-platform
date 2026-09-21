# ADR-0040 — Bybit live trades acquisition v1

**Status:** ACCEPTED
**Date:** 2026-09-21

## Context

A11 is the first live market-data acquisition vertical. Historical Bybit BTCUSDT trade ingestion, `trade-v1`, deterministic Bybit ordering/eligibility, canonical publication, coverage, quality lifecycle, repair and source protection already exist and remain authoritative.

The selected live source is Bybit public linear `publicTrade.BTCUSDT`. Current provider documentation exposes trade time `T`, trade ID `i`, side `S`, size `v`, price `p` and cross-sequence `seq`; message timestamp `ts` is provider-system generation time. For Futures/Spot, multiple messages may share one `seq`. The public WebSocket does not define a durable arbitrary resume cursor. Bybit also exposes a bounded recent-public-trades REST window (up to 1000 rows for linear), carrying execution ID, time and sequence.

Provider references at decision time:

- https://bybit-exchange.github.io/docs/v5/websocket/public/trade
- https://bybit-exchange.github.io/docs/v5/ws/connect
- https://bybit-exchange.github.io/docs/v5/market/recent-trade

These references are evidence for provider facts, not a substitute for this platform contract.

## Decision

### 1. First vertical

A11 v1 is intentionally limited to:

```text
venue       bybit
category    linear
instrument  BTCUSDT
dataset     trades
schema      trade-v1
source      publicTrade.BTCUSDT
```

No generic provider framework is introduced.

### 2. Canonical mapping

For each accepted live trade:

```text
venue           = bybit
instrument      = BTCUSDT
exchange_ts     = provider trade time T
price           = p
size            = v
aggressor_side  = normalized taker side S
trade_id        = i
sequence        = seq
receive_ts      = null   (v1)
```

Bybit message `ts` is not `receive_ts`: it is provider-system data-generation time. A local receive clock is acquisition/observability evidence, not canonical trade identity. The live path must not invent source semantics for convenience.

### 3. Trade identity and canonical ordering

The canonical Bybit trade key remains compatible with the accepted historical first vertical:

```text
TradeKeyV1 = (venue, instrument, exchange_ts, trade_id)
```

Within one Bybit BTCUSDT dataset, canonical total order remains `(exchange_ts, trade_id)`.

`seq` is preserved as source evidence/record field but is not the canonical trade identity and is not assumed to be a gap-free `+1` cursor. The provider explicitly allows multiple messages to share one sequence value.

### 4. Duplicate semantics

For two observations with the same `TradeKeyV1`:

- if the economic/source payload required by `trade-v1` is equivalent, the later occurrence is an idempotent duplicate and produces no second canonical economic trade;
- if price, size, aggressor side or other identity-relevant source evidence conflicts, acquisition fails closed with a data-integrity conflict;
- last-writer-wins is forbidden.

The transport is therefore allowed to be at-least-once while the canonical economic effect is idempotent.

### 5. Historical/live cutover

Historical and live paths converge into one canonical history. The cutover is bound to the last accepted historical canonical `TradeKeyV1` for the selected first vertical.

```text
key <= cutover_key  -> historical authority
key >  cutover_key  -> live authority
```

The live connection may begin before the cutover and buffer overlap evidence. In the overlap, equivalent duplicate keys confirm convergence; conflicting payload for the same key fails closed. No live-only storage/catalog/identity system is permitted.

### 6. Connection/session semantics

The live runtime distinguishes transport/session health from canonical trade state. A minimal session lifecycle is:

```text
DISCONNECTED -> CONNECTED -> SUBSCRIBED -> ACQUIRING
```

Subscription acknowledgement and heartbeat/pong evidence are operational/session evidence. Disconnect is explicit; it is never silently reinterpreted as a complete coverage interval.

### 7. Reconnect and bounded reconciliation

After a disconnect, A11 v1 may use the provider's bounded recent-public-trades REST endpoint plus newly buffered WebSocket trades to re-establish continuity from the last durable canonical trade key.

If the last durable trade key is found inside the bounded recent window, all later REST/WS trades are canonicalized, ordered and deduplicated under this ADR before continuity is considered re-established.

If the last durable key cannot be found, the platform cannot prove the missing interval from this evidence. It must:

- mark the affected coverage interval/segment non-complete with explicit interruption evidence;
- never fabricate completeness, ordering continuity or a synthetic resume cursor;
- start/re-establish a subsequent governed live segment only after the interruption is represented explicitly.

A11 v1 does **not** claim arbitrary-duration lossless replay.

### 8. Coverage semantics

Trade presence or absence is not coverage authority:

```text
zero trades observed != proven gap
zero trades observed != proven completeness
```

Complete live coverage requires attributable acquisition evidence for the claimed interval, including an established subscription/session, validated consumption of received source messages, no unresolved transport interruption and no unresolved integrity conflict. Existing declared-coverage semantics remain authoritative; `transport_interruption` / continuity contradictions prevent a conflicting complete assertion.

### 9. Relationship to repair

A10 remains the canonical repair capability. This ADR does not invent a new repair engine. A bounded reconnect may reconcile a gap only when provider evidence proves continuity. A larger/unprovable interruption remains an explicit gap until a separately governed repair source/path can prove and fill it.

Exactly how such gaps are sourced and closed when the bounded recent-public-trades window is insufficient is intentionally **not resolved by this ADR** and is tracked as an open DG-B proposition.

### 10. Delivery guarantee

A11 v1 promises:

```text
transport acquisition: at-least-once
canonical economic effect: idempotent / one accepted trade per TradeKeyV1
```

It does not claim exactly-once WebSocket delivery.

## Excluded

This ADR does not define or authorize:

- B06 live consumer/DataGateway cursor;
- second venue/multi-provider resolution;
- L1/L2/L3/MBO feeds;
- Strategy/Execution/paper/live trading;
- generic stream/broker/job frameworks;
- arbitrary replay guarantees unsupported by provider evidence;
- K10 persisted checkpoint shape (separate authority);
- K09 deletion authority.

## Consequences

- DG-B semantics required for A11 implementation are frozen.
- A11 implementation may proceed only after K08 proof satisfies its declared dependency.
- Provider gaps that exceed bounded reconciliation remain explicit, auditable non-complete intervals rather than silently lost data.
- The unresolved long-gap remediation proposition remains visible in `OPEN_DECISIONS.md` and must not be inferred from this ADR.
