# alpha-research as a consumer of quant-platform: needs, requests, concerns, options

**Status:** input to a design phase. Not an ADR, not normative, not a governance document; it changes no scope, roadmap or
capability row. It asks questions and proposes options; every decision stays with the platform maintainer and its ADRs.
**Date:** 2026-10-19. **Author:** the alpha-research worker session (consumer side).
**Read against:** `main` @ `ef14279` and `implement/fid` @ `a416a23` (ADR-0073 and its atoms M01, M02, M04, M05). I read the code and the
ADRs; I did not run `implement/fid`.

## 0. Purpose, and how to answer

alpha-research (`NeoNix-Lab/alpha-research`) is the real consumer of this platform: it asks the server for trades through the deck
protocol, builds features, detects events, measures forward outcomes and, in the end, wants to register results (K13) and measure
significance (DSR, PBO). This note collects what it needs from the platform, what worries it, and the options I can see, so that the
platform side and the consumer side design the next steps together instead of finding each other's assumptions in review.

Each topic below has the same shape: **Need**, **Why / evidence**, **Concern**, **Options**, **Question**. Please answer per topic in a
new section at the end of this file (`## Platform response`), or in the issue you prefer. "Not now" and "no" are useful answers.
Topics are ordered by how much they block alpha-research, not by size.

Where I say *verified* I read it in the code or the ADR at the commits above; where I say *not verified* I did not.

## 1. Where alpha-research stands (facts)

Merged on `implement/wave-1` of alpha-research:

- **#13** a verified, persistent, content-addressed cache of delivered inputs (a `DELIVERED` admission cannot be delivered again, so the
  cache is the only copy). **#14** reproducible environments by path: the platform is used in place through `QP_PLATFORM_CHECKOUT`,
  never `pip install`ed (an install writes `egg-info` into the checkout and a dirty checkout is refused), with a `doctor` that records
  the platform commit and its Git state before and after the import. **#15** Hypothesis 001 expressed with the platform's research types
  (E02 observable, F01 hypothesis, F02 events, F03 outcomes, F04 studies) with a parity test against alpha-research's own detector.
- Open in alpha-research: #7 (register results with independent provenance; blocked by quant-platform#441), #10 (sealed feature
  artifacts and canonical event study), #11 (measured significance), #12 (CI), and #16 to #23 (acquisition at scale, delivery
  recovery, cache inventory, bars through the platform kernel, offline charts, a local register of ideas and studies, a list of
  feature ideas).

What alpha-research imports from the platform goes through one module (`platform_bridge`). It uses: `features` (E02 definitions and
observations), `research` (F01 to F04), `validation` (DSR, PBO, comparable trial panels), `strategy`, `representation` (footprint),
and, lazily, `application` (deck admission manifest, workload tree, `build_deck_result`, K12/K13 pieces).

Its deck flow today: clean checkout at the server commit, `admit`, `fetch_inputs`, verify every member against the manifest, keep the
bytes in a local cache, build bars with taker volumes **with its own code**, run its own detector and its own forward-outcome audit.

## 2. Topic 1 - ADR-0073: alignment, and the path to a commit the server runs

**Need.** To consume `order_flow.failed_aggression@1` and `bar-flow-v1` from the platform and retire alpha-research's local copies
(ADR-0073 section 7), keeping one equivalence test.

**Why / evidence.** ADR-0073 is ACCEPTED (2026-10-18) and says alpha-research retires its local bars with taker volumes,
`detect_failed_aggression` and the divergent H001 texts. The code is on `implement/fid`, not on `main`; PR #480 is open there.

**Concern.**

- I built PR #15 before I had read ADR-0073: it reads `main` only. Its observable is `order_flow.failed_aggression_signed_imbalance`
  (signed ratio, input `candle.taker_volumes`, imbalance threshold in the `EventSpec`, float comparison). It diverges from the ADR on
  three points: the value (signed ratio against the integer signal `+1/-1/0`), where the threshold lives (the ADR puts it in the
  definition, so a sweep is a set of definitions), and the arithmetic (exact decimal against float). I will replace it; I do not
  want two H001 observables.
- The deck is pinned to the server's commit (ADR-0072 section 5). Until the server runs a commit that contains M01 to M05, a deck
  using `definitions` is refused as `invalid_request` (the closed field set). alpha-research cannot finish its side before then.
- Float against exact: where alpha-research's detector compares a float ratio with a float threshold, the ADR compares `D >= i * T`
  in exact decimals. They can disagree at a boundary. The equivalence test must include those cases; I will write it, but I do not
  know which side you consider authoritative if they differ (I assume the platform's).

**Options.**

- A. Promote `implement/fid` to `main` and to the server together, then alpha-research moves to that commit.
- B. alpha-research works against `implement/fid` meanwhile (`QP_PLATFORM_CHECKOUT` already allows it), and treats results as
  non-final until the server runs the commit.

**Question.** Which commit will the server run and when, and is B acceptable (the deck would admit nothing against today's server)?
Is there anything in M01 to M05 that you expect to change before promotion?

## 3. Topic 2 - sweeps, trial panels and "one admission per set of definitions"

**Need.** H001's falsification criteria include `dsr_passed` and `pbo_max`; both need many comparable trials (parameter values,
timeframes). The research must be able to run a grid cheaply and to attribute each trial.

**Why / evidence.** ADR-0073 section 1: changing a threshold gives a different definition identity; Consequences: "Parameter sweeps are
different definitions ... a distinct identity and a distinct admission; there is no shared 'family' identity in v1", with a trigger to
revisit. `definition_identities` is part of the manifest, so each distinct set is a distinct admission and a distinct delivery.
Amendment 1 adds: one bar duration per admission, so a study over 1m, 5m and 30m is three deliveries of the same trades (accepted cost,
point 5).

**Concern.**

- A threshold grid of, say, 7 values times a wick grid of 5 is 35 definitions. At most 8 definitions fit in one `admit`, so several
  admissions, each delivering up to 1 GiB of the same trades, against `max_in_flight_transfers` 32 and `retention_seconds` 600.
- The alternative is to compute the whole grid locally on one delivery and declare all of it. ADR-0073 section 6 allows a run to declare
  more definitions than the manifest admitted, but only the admitted ones are server-attested. So a PBO panel would mix attested and
  unattested trials. I do not know whether that is acceptable to you, and I have not checked what `ComparableTrialPanel` and
  `evaluate_pbo_v1` require of trial identities.

**Options.**

- A. Keep as is; the research attests a small, deliberate set per admission (at most 8) and computes the rest locally, and the report says
  which trials were attested.
- B. A "family" identity (the ADR's revisit trigger): one admission names a parameterized family and the grid points are derived.
- C. Attest the grid in the run (K13 references) without putting it in the manifest.

**Question.** What is the intended practice for a sweep that feeds DSR and PBO? Is the number of attested trials something the platform
cares about, or is attestation only for the trials that become results?

## 4. Topic 3 - what is attributed for events, outcomes and studies

**Need.** A registered result should carry the identity of the hypothesis, the event specification, the outcome specifications and the
event study it came from, not only the feature definition.

**Why / evidence.** ADR-0073 section 1: an `EventSpec` is bound to a `hypothesis_id` (a research object the platform does not own), so it
is "not a second server-known definition", and K13 does not inspect `EventSpec` identities. The console's `register()` builds a
`StudyIdentity` with `Ref("research", ...)`, `Ref("objective", ...)` and a `Protocol(Ref("protocol", ...))`, which look like natural places.

**Concern.** Without a place for them, the registered run says "these feature definitions were admitted" but nothing about which
hypothesis or study they served; the F01 to F04 identities live only in alpha-research's report.

**Options.**

- A. Map `research` to the F01 `HypothesisSpec` identity, `protocol` to the F04 `EventStudySpec` identity, `objective` to a research
  statement; no new platform field.
- B. Carry them as additional references of a new kind (like `feature-definition`) that K13 stores but does not compare.
- C. Leave them in the output payload only (the report), with its checksum.

**Question.** What are `research`, `objective` and `protocol` intended to hold, and is A a faithful use of them?

## 5. Topic 4 - a provider that lives in alpha-research (quant-platform#441)

**Need.** To register an H001 result whose code is alpha-research's, with alpha-research's own repository and commit as provenance.

**Why / evidence.** alpha-research #7 depends on #441, open. ADR-0073 section 7 says that case is "not decided here". The S05 rule
today is that a deck result names one server commit, and the provider identity is attribution evidence only.

**Concern.** Everything alpha-research wants to register is, by construction, code outside this repository. Until #441 is decided,
nothing the consumer produces can be registered with honest provenance, and I will not fake it with a placeholder commit.

**Options.** A. Wait for #441. B. Pin alpha-research as a second source in the bundle (repository, commit, source digest), which is
what #441 appears to ask for. C. Register only the platform-side part (the attested definitions and the admission) and keep the
research code identity in the report until B exists.

**Question.** Where does #441 stand, and which of A to C do you see?

## 6. Topic 5 - bars from a delivery, without hand-building the evidence

**Need.** `aggregate_historical_bar_flow` (and the candle kernel before it) needs source evidence: dataset identity, ordering policy,
eligible and finalized coverage, natural partitions, manifest and content hashes. A deck consumer has all of it, but spread over the
admission manifest and the delivered members.

**Why / evidence.** The exploratory deck console rebuilds this evidence by hand from the cached partitions' manifests
(`HistoricalCandleSourceEvidence(...)` with about ten fields). alpha-research would have to do the same for `HistoricalBarFlowSourceEvidence`.

**Concern.** Each consumer re-deriving evidence is a place for a silent mismatch between what the server admitted and what the deck
computed on. It is the part ADR-0073 section 5 delegates to "the deck's clean tree pinned to the server's commit".

**Options.** A. A library function in the deck workload library (S05) that takes the verified admission and the delivered members and
returns the evidence (and optionally the bars). B. A documented recipe plus a test in this repository. C. Leave it to consumers.

**Question.** Is A in scope for a later atom, or should alpha-research implement it and offer it back?

## 7. Topic 6 - acquiring ranges larger than one admission

**Need.** H001 needs months, eventually years. Admission bounds (ADR-0072 Amendment 1): 16 members, 512 MiB per member, 1 GiB, 32
in-flight transfers, 600 s retention.

**Why / evidence.** The exploratory console fought all of these in notebooks: `member_count_exceeded` (a month is about 30 daily
partitions), `transfer_refused` after 32 transfers within the retention, and `DELIVERED`-without-a-local-copy. alpha-research issue #16
asks for a planner in the library (split, adaptive retry, backoff, resumable index).

**Concern.**

- The bounds are "configuration, not wire format", and I found no call that returns them. The client has to hard-code them or learn
  by being refused.
- The refusal reasons exist (`member_count_exceeded`, byte reasons, `transfer_refused`) but I am not sure they are a stable, documented
  contract for clients; `transfer_refused` does not say how long to wait.
- With `definitions` the request must be aligned to the bar duration (`definition_interval_misaligned`), so a planner has to cut at
  multiples of the duration since the UTC epoch. That is fine (daily partitions, durations up to 1 day), but it is a new constraint the
  planner must know.

**Options.** A. Document the refusal reasons as a client contract and add the retention (or a retry hint) to `transfer_refused`.
B. Expose the configured bounds in the `admit` error or in a status call. C. Keep as is; the consumer treats the ADR as the source.

**Question.** May a client rely on the reasons and on the numbers in ADR-0072 Amendment 1, or are they internal?

## 8. Topic 7 - a delivered admission whose local copy is lost

**Need.** A sanctioned way to recover after losing a delivered range.

**Why / evidence.** A `DELIVERED` admission cannot be delivered again. The exploratory console recovers by changing `operation_identity`,
which changes the manifest digest and yields a new admission. `admitted_input_authority.py` says operation and profile identities are
"carried verbatim from the request ... never an authorization", and that a closed registry of them is not provided in v1. So it works.

**Concern.** It works because nothing interprets the field, not because anyone designed it as a recovery path. If the one-time delivery
rule has an intent beyond bookkeeping (bounding egress, attribution, an audit trail), then recovering by renaming the operation
sidesteps it, and I do not want to build a feature on a loophole. ADR-0069 Amendment 1 and PR #373 give a delivery-attempt identity
and an admission-gated resume for a delivery that failed, which does not cover a delivery that succeeded and was then lost.

**Options.** A. Declare the renamed operation a supported recovery and document the convention. B. A first-class "re-delivery of a
delivered admission" under an explicit, audited reason. C. No recovery: the consumer must keep the bytes (alpha-research already has a
verified cache, #13) and treat loss as final.

**Question.** Which of A to C is intended? alpha-research #17 (explicit, recorded recovery) waits on the answer.

## 9. Topic 8 - forward outcomes: direction, intrabar extremes, gaps

**Need.** To run H001's study with the platform's `evaluate_outcome` and `run_event_study` and get the same kind of figures as the audit.

**Why / evidence (verified).** The audit measures a forward return from the event bar's close, and MFE and MAE from the **highs and lows**
of the next N bars, signed by direction, minus a cost. The platform's `OutcomeSpec` has `forward_return`, `high_low_excursion` and
`extrema`; path metrics are computed on the `price_reference` (for example `close`) and are not directional. And `bar-flow-v1` omits a
bucket with no trades (`empty_bucket_policy: omit`) while `evaluate_outcome` walks a fixed grid and reports `INSUFFICIENT_COVERAGE` on a gap.

**Concern.**

- The audit's numbers cannot come out of `evaluate_outcome` as it is: different path price, no direction, no cost. I do not want to
  change the figures to fit; I would rather keep the audit's own measurement and say so (as PR #15 does).
- The audit counts bars by position, so a gap is silently skipped; the platform counts grid steps, so a gap is a coverage failure. On
  BTCUSDT at 1m gaps are rare, at 1s or at the edges they are not. Filling a gap with the previous price would fabricate an
  observation.

**Options.** A. Keep both measurements and report the difference. B. Add a directional high/low excursion metric (a new `MetricKind`,
which is an F03 change and so an ADR matter). C. Keep costs and direction in the research layer (ADR-0007) and use the platform only for
the population and the forward return.

**Question.** Is a directional intrabar excursion something F03 should have, or should it stay a research computation? And is a gap
meant to be `INSUFFICIENT_COVERAGE` for the consumer to count and report?

## 10. Topic 9 - the ideas that need more than the current bar

**Need.** The exploratory console computes Ichimoku (Tenkan 9, Kijun 26, Senkou B 52, shift 26) at three timeframes on price and on
delta/CVD, session VWAP with bands, delta-weighted VWAP, Market Profile (POC, value area, initial balance, tails) and delta/CVD
candles. alpha-research has no wish to own them; the list is in alpha-research issue #23.

**Concern.** ADR-0073 covers a feature of the current bar (`support = current()`, `minimum_history = 0`, no warm-up). Ichimoku needs
a window and a displacement, VWAP needs a session anchor and running state, a profile needs a session. Those are the subject of the
open FEI design gate (#363) and of E07 (#368). Also, a lagging line (Chikou) drawn at its original date uses the future; its
causal availability needs care. I do not know whether these fit the "closed registry in code" of ADR-0073 section 5 or wait for
FEI.

**Options.** A. Wait for FEI before proposing any of them. B. Propose one at a time through the registry once FEI settles windowed
support. C. A short "how to propose a definition" note: what a consumer must supply (formula, causal floor, finality, golden vectors).

**Question.** Is there a process for a consumer to propose a definition, and which of these would you consider first, if any?

## 11. Topic 10 - what the consumer may import, and how it installs

**Need.** A stable, light import surface and an installation that does not dirty the checkout.

**Why / evidence (verified for alpha-research PR #14).** Importing `quant_platform.application.*` loads the PostgreSQL job store and the
websocket transports, so a consumer without `psycopg` or `websockets` cannot import the deck pieces; alpha-research made those names
lazy. `inspect_workload_tree` uses `git status --porcelain=v2 --untracked-files=normal`, so an untracked file makes the tree dirty,
while an ignored file does not. `.gitignore` and `pyproject.toml` have no `egg-info` entry, so a `pip install` of the platform from its
own checkout dirties it.

**Concern.** alpha-research depends on the platform's module layout (about a hundred names through one bridge: 96 pure ones and 15 from `application`). A refactor here breaks the
bridge, which is fine if I learn it from a test and not from a failed run.

**Options.** A. Add `*.egg-info/` (and `build/`) to the platform's `.gitignore`, so an editable install does not dirty the tree.
B. Declare which modules are consumer-facing (a list in `CONSUMER_API.md`), and keep their imports free of server-only packages.
C. A conformity test in this repository that imports the consumer-facing names with the server-only packages blocked.

**Question.** Is A acceptable (it is a one-line change you may prefer to make yourself), and is B or C of interest?

## 12. Topic 11 - sealed feature artifacts (alpha-research #10)

**Need / why.** #10 was written to seal an E04 `FeatureArtifact` for H001 with a catalog-owned `FeatureSetDefinitionIdentity`.

**What I verified.** `FeatureSetDefinitionIdentity` is the natural key `(slug, version)` of a `feature_set_definitions` row. The class
does not check that the row exists, and I found no Python path that registers one (the only set, `h01_imbalance@1`, is a constant plus
ADR-0035). `seal_feature_artifact` needs at least one bound output partition of layer `features`, which the data plane publishes. And
ADR-0073 section 9 says there is no server-side feature materialization and that accepted-artifact inputs stay unavailable. So a
consumer cannot seal an artifact without inventing partition identities, which #10 forbids.

**Concern.** Under ADR-0073 a deck result is attributed by definition identity in the manifest, not by an artifact. I think #10 as
written does not describe a supported path, and I intend to rewrite it.

**Question.** Please confirm that no consumer-sealed `FeatureArtifact` is intended for H001, or tell me what route you expect.

## 13. What alpha-research will do meanwhile, and what it will not

Independent of the answers (no platform dependency): the acquisition planner design (#16), cache inventory and removal (#18),
the offline chart (#20), the local register of ideas and studies (#21, #22).

Waiting on the answers: the consumption of `failed_aggression@1` and `bar-flow-v1` and the retirement of the local copies (topics 1
and 5), #10 (topic 11), #7 (topics 3 and 4), #17 (topic 7), #19 (topic 5).

I will not: register anything with invented provenance; change a platform contract from this side; work around the one-time
delivery rule; present a local equivalence test as a platform attestation.

## 14. Suggested order

1. Topic 1 (the commit the server will run), because every consumer step depends on it.
2. Topic 4 (where #441 stands) and topic 11 (is the artifact route closed), which change what is worth building.
3. Topic 2 (sweeps and panels), which decides the shape of the research's trial machinery.
4. The rest, in any order.

## Platform response

_(to be filled in by the platform side)_
