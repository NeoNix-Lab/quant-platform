# Scope: DG-C / A14 — L2 contract, continuity and publication v1

Status: **CLOSED — governance closeout #442 (2026-10-18). Part A closed by #367 / PR #370
(ADR-0070 ACCEPTED). Part B: A14a-A14e implemented and merged into `implement/l2`,
with the v1 golden accepted at `1cdd78f` (11/11 claims, PR #413/#430).
A14 and A14a-A14e are credited COMPLETE on this integration line. A14f/A14g remain
held and outside the closure. Promotion to `main` is a separate wave closeout PR.**

Scope kind: governance-only closeout #442, followed by the integration and
governance-reconciliation mandate #444 under `AGENTS.md`. No new runtime semantics,
accepted ADR, frozen contract, schema or fixture changes.
No new implementation issue is opened or authorized by this closeout.

## Historical scope archive

The repository convention rewrites `SCOPE.md` in place and preserves earlier versions
in `git log -- SCOPE.md` (DG-K precedent: governance #381). This CLOSED document is
the v1 archive on `implement/l2`; no new active cycle is opened. The earlier DG-C
design gate (#364/#367/#371), Amendment 1 reconciliation (#404), DG-K and Wave 8
scopes remain in Git history. The accepted DG-K/L2 scope-reconciliation debt
is paid by #444 below, preserving both closeouts before the final L2 promotion.
No published branch is rebased by this issue.

## Integration reconciliation (issue #444)

The L2 governance closeout is merged by PR #443 (`01063914492262ba966708415228f6b922fdf581`).
This reconciliation merges captured main `693d765fda5fcf2bcf38bbdbfdba6f88c9670d7c` into that
history, without rebasing or changing accepted semantics. DG-K/DG-K2 are already
on main through #434/#437; their accepted credits and proof bounds are retained.
The final L2 wave promotion to main remains separate; no deployment occurs here.

Both inventories are preserved: L2's 111 atoms / 84 COMPLETE and main's 112 / 93
combine to **119 atoms / 99 COMPLETE (83.2%)**,
with 107 decided, 3 OPEN_BLOCKING and 9 OPEN_DEFERABLE. No new atom or
capability is invented: seven L2 subatoms and eight S atoms were already registered
on their respective lines. A14f/A14g, S08 and J11 remain held/deferred unchanged.
The eight L2 triggers and the five DG-K2 trigger groups below authorize no work.

The only manually resolved test conflict unions the parents' canonical serializer
call-site declarations (L2 event-size encoding plus DG-K/DG-K2 sites). Both original
inventory expectations remain enforced, and the frozen historical fixture is unchanged.
All runtime/tool/ADR/schema changes in this merge come directly from the captured
parent histories and Git's automatic merge; no new implementation is added.

## DG-K2 closeout inherited from main (#409)

| Atom | Merged evidence (into `implement/dgk2`) | Bound of the credit |
|---|---|---|
| `S01` | PR #398 (issue #391) | `application/admitted_input_authority.py`: dataset selector only; manifest from the open scan metadata; whole-file in-memory delivery. |
| `S02` | PR #400 (issue #392) | `application/result_import_composition.py`: the six-role registry and the service factory; output and staging bounds not measured on a real output. |
| `S03` | PR #405 (issue #395) | `application/api_host_composition.py`, `tools/api_server.py`: opt-in families, mTLS, principals file; `jobs` composed without a result resolver. |
| `S04` | PR #414 (issue #393) | `infra/pki/`, `docs/engineering/DECK_PKI_OPERATIONS.md`; no CRL or OCSP. |
| `S05` | PR #418 (issue #394) | `application/deck_result_bundle.py`; the provider identity is attribution evidence; a bundle names one admission. |
| `S06` | PR #421 (issue #396) | `infra/systemd/` units, `docs/engineering/DECK_HANDOFF_RUNBOOK.md`, `docs/engineering/SERVER_DECK_COMPATIBILITY.md`; no memory limit on the API unit. |
| `S07` | PR #427 (issue #397) part 1, PR #428 the record of part 2 | Part 1: `PostgresSplitTopology`, 14 tests in the CI job of the runtime stores. Part 2: one happy path on the smallest real partition (3 122 bytes), executed by the maintainer and reported from the session log, on a pull-request commit. |
| Amendment | ADR-0072 Amendment 1 (issue #431, PR #432) | Delivery bounds raised to 512 MiB per file, 1 GiB and 16 files per admission, 1.5 GiB retained, 600 s; measured cost of a delivery recorded. |

Findings recorded by the proof (see `docs/engineering/DGK2_SPLIT_TOPOLOGY_PROOF.md`): 719 of the 2 116 daily partitions of the real dataset exceeded the original member bound (the largest is 252.8 MiB); the server environment lacked a declared dependency; the server checkout was moved by hand to a pull-request commit and the ownership and mode of the canonical data tree were changed recursively, wider than the runbook states, with the previous owners and modes not recorded.

### DG-K2 not claimed

- No first data-local J03 handler (`S08` is not authorized; the registry ships empty).
- No deck user interface. For this wave the deck's access point is a notebook on the deck machine that uses `clients/deck/deck_handoff_client.py` and the `S05` module; a deck TUI, CLI or local web UI is not built.
- Omega is not authorized for any family (ADR-0067 amendment needed).
- The feature identity is not server-defined: the manifest's `definition_identities` is empty, and the deck-declared feature-provider identity is attribution evidence that K13 cannot cross-check.
- Accepted-artifact inputs are not available; the input is a dataset selector.
- The deck is pinned to the server's commit, but the server does not refuse an admission made before a promotion: that admission stays valid and its result is accepted (an operator obligation, in the runbook).
- Delivery is whole-file and in memory: at most 512 MiB per file, 1 GiB and 16 files per admission, about 4 GiB of API memory at the total bound. There is no delivery by member or by range, no deck-side stream of days, and a result bundle names one admission, so a period longer than one admission is several results.
- The supervised proof is one happy path. No fault of the hermetic proof was repeated on the real host; the largest real day (252.8 MiB) was never delivered there; concurrent deliveries and a real network between deck and server were not measured; the server ran a pull-request commit, not a commit of `main`.
- The API runs with read access to the whole `marketdata` tree through the group (accepted for v1 by the maintainer) and its unit has no memory limit.
- No Golden proof is claimed for this wave.

The statement that the topology is deployable must carry the bounds of ADR-0072 Amendment 1 and the findings above.

### DG-K2 triggers recorded at closeout (each names a condition that reopens a decision; none is authorized by this closeout)

1. **Feature identity.** In v1 the server passes the deck no feature identity. Trigger to open a design gate: the FEI line (#363, composite technical feature provider) delivers a server-side feature definition identity that can populate `definition_identities`, or the maintainer names feature traceability as a requirement. Related, not scheduled: accepted-artifact inputs (ADR-0072 section 1) and `J11` (not planned, owner decision 2026-10-10).
2. **First data-local handler (`S08`).** Its issue must also add, for `j02-job-v1`, a result-body resolver together with the shared J14 store, and add `j02.jobs.read` to `framed_resume_scopes` (the API host composes jobs without framing in v1, ADR-0072 section 3 as implemented by #395).
3. **ADR-0072 triggers:** accepted-artifact inputs (a server-side store with a resolver); a deck on a commit other than the server's; time-based expiry (a timestamp column and a sweep); a binary or incremental transport family for partitions beyond the whole-file framing (delivery by member or by range); a closed registry of operation and profile identities.
4. **Measured bounds.** Measured and recorded: the real dataset has 2 116 daily partitions (median 21.5 MiB, p99 117.3 MiB, maximum 252.8 MiB), 719 exceeded 32 MiB and 161 exceeded 64 MiB, which led to ADR-0072 Amendment 1. Not measured: the 8 MiB output bound on a real output, concurrent deliveries, a real network, and the largest real day on the real server. Trigger: the first deployment after the amendment records the delivery of the largest real day (peak API memory and time), or a real partition above the new bounds appears.
5. **Deck access point.** Any later deck-hosted interface must listen on loopback only, because whoever reaches it inherits the deck credential's scopes (the server sees one fingerprint). Triggers, none authorized: (a) a deck TUI or CLI that wraps the facade; (b) a local web UI served from the deck, which is also the only way to use the existing App UI against the mTLS server (a page cannot present the deck's client certificate itself); (c) UI-initiated heavy compute, with three options for the maintainer: an interface near the deck, a queue the deck reads on its own initiative (needs an ADR-0057 amendment: server-to-deck delegation and remote workers pulling from the server queue are out of scope of DG-K2), or leaving runs manual. Condition to reopen: the App UI must start real runs.

---


## Objective

Reconcile DG-C / A14 v1 to the strongest propositions proven by merged evidence,
with script-derived counts, explicit limits and named reopening conditions.

## Authority

- Governance: `AGENTS.md`; #364, #371, #404, #442 and reconciliation #444.
- DG-K/DG-K2 closeouts #381/#409, ADR-0068/0069/0072 with their accepted amendments;
  promoted histories #434/#437, with the bounds preserved below.
- [ADR-0070](docs/decisions/ADR-0070-l2-contract-continuity-and-publication-v1.md),
  accepted #367 / PR #370; Amendment 1 (#402 / PR #403, per-run partitions);
  Amendment 2 (#416 / PR #417, source-file boundaries and UTC-day ownership).
- [ADR-0049 Amendment 1](docs/decisions/ADR-0049-retention-deletion-authority-v1.md)
  (#423 / PR #424, redundant source-copy disposal, never the last copy).
- Existing ADR-0018/0019/0020/0022/0023/0025/0029/0032/0040/0043/0044/0050/0054/0066
  contract, identity, coverage, provenance, publication, runtime and transport bounds.
- [Capability DAG](docs/product/CAPABILITY_DAG.md),
  [Capability Map](docs/product/CAPABILITY_MAP.md), [Roadmap](docs/product/ROADMAP.md),
  [Open Decisions](docs/architecture/OPEN_DECISIONS.md#dg-c--market-data-depth).

## Owner decisions

1. Aggregated price levels only; no order-level, queue-position or L3/MBO semantics,
   no synthetic book from trades. A13 is not a prerequisite of A14.
2. Bybit only, effective depth 200; v1 historical ingest includes consumer read;
   v2 live ingest and ob500 projection remain separate held follow-ons.
3. Numeric `seq` orders events; `exchange_ts = cts` is a certified monotone property,
   `provider_ts = ts`, `u` signals continuity; regressions fail closed.
4. Amendment 1: one anchored continuity run per partition `dt=YYYY-MM-DD/seg=NNN`,
   one coverage document per complete run and per known gap. `degraded` is a
   producer cut signal, never a published L2 lifecycle state in v1.
5. Amendment 2: UTC day owns events by `exchange_ts`, strictly congruent with
   `trade-v1`. Contiguous source-head/tail exclusions stay attributable in the
   retained archive; they are not automatic publication into an adjacent day.
   Head/tail gaps are explicit at 1 µs resolution. Only A14f may close the measured
   file seam using predecessor-bound continuation and the adjacent source tail.
6. ADR-0049 Amendment 1: one redundant physical copy may be removed only after all
   independent-copy, canonical-byte, protection, audit and durability guards pass.
   Logical source evidence retains UNIQUE_SOURCE permanent retention; the last
   physical copy is never removable. A second copy is supplied by the operator.
7. `implement/l2` remains cut from `implement/dgk` (original branch point `9db9637`);
   promotion follows DG-K closeout and reconciles `SCOPE.md` by hand. Issue #442
   applies the DG-K credit-at-integration-closeout precedent: COMPLETE here does
   not assert promotion to `main`.

## Part A — Design gate, CLOSED

ADR-0070 #367 / PR #370 resolves the eight DG-C questions and ratifies
`l2-book-event-v1` unchanged. Amendments 1 and 2 are operative over the superseded
boundary/publication text. No accepted ADR is rewritten by this reconciliation.

## Part B — Implementation inventory and unchanged held gates

| Atom | Batch | State on implement/l2 | Governing authority and merged evidence | Bound / gate |
|---|---|---|---|---|
| A14a | v1 | COMPLETE | ADR-0070 §1, §2, §5, §7; #374 / PR #375 | Bybit ordering/certification profile; bounded E2-E4 measurements in #430. |
| A14b | v1 | COMPLETE | ADR-0070 §3, §4, Amendment 1; #376 / PR #379 | Parquet materializer/scanner, uint64 exact round-trip, per-run paths. |
| A14c | v1 | COMPLETE | ADR-0070 §4, §5, Amendment 1; #399 / PR #401 | Certification/publication/catalog bridge; PostgreSQL proof without DDL change. |
| A14d | v1 | COMPLETE | ADR-0070 §2, §4, §7, Amendments 1-2; #410 / PR #411; conformed by #416 / PR #417 | Historical ob200; source-edge routing/evidence, UTC-day ownership, head/tail known_gap, byte-identical re-acquisition. |
| A14e | v1 | COMPLETE | ADR-0070 §6; #407 / PR #408 | l2_book_events@1 read; per-partition origin, gaps never filled, byte bound. |
| A14f | v2 | FROZEN / MISSING — held | ADR-0070 §8, Amendment 1 item 9 and Amendment 2 decision 6; requires A14d,A11 | Original start gate unchanged: A14 closed (v1 golden accepted) **and an approved bounded soak plan**. No soak plan approved here. Outside A14 closure; predecessor/certifier/catalog mechanics require its own issue. |
| A14g | follow-on | FROZEN / MISSING — held | ADR-0070 §9; requires A14b,A14c,A14d | Original start gate unchanged: A14d merged **and the E5 real-day parity test planned**. No parity plan approved here. Outside A14 closure. |

## Closeout result (issue #442)

All implementation PRs and amendments above are merged ancestors of the captured
`origin/implement/l2` baseline `1cdd78f47c5054dc02bc44bb637e26104b067c83`.
The golden harness is #412 / PR #413; #429 / PR #430 fixes source-event accounting.
The accepted [clean real-day result on PR #430](https://github.com/NeoNix-Lab/quant-platform/pull/430#issuecomment-6241364632)
is the closeout evidence, not the earlier 10/11 run or the intermediate database failure.

- Archive: `2025-12-01_BTCUSDT_ob200.data.zip`, SHA-256
  `27b6cdc5f2976e7f8a9c2f02fe03eb57b170efe3a861748fafc53ae72d2816d3`.
- 11/11 golden claims pass. Source accounting: **863,929 = 863,916 stored + 13
  excluded post-midnight events**. Those 13 remain raw source evidence.
- One eligible/certified-pass/published-valid partition `dt=2025-12-01/seg=001`;
  read count 863,916 equals stored count; no event outside its partition range.
  Read hash equals partition hash:
  `canonical-content-hash-v1:sha256:e36b3baee239694497a87a3135487934b5f67b8a62d3849611b718a4d7b9f232`.
- One file-head `known_gap` `[2025-12-01T00:00:00Z, 2025-12-01T00:00:01.112Z)`;
  complete run starts at 00:00:01.112Z and ends at 2025-12-02T00:00:00Z.
  Read gaps equal the gap documents; the read starts anchored; consumer window
  equals scan, 600 = 600; **5 files reproduce byte-identically**.
- E2-E4: one day, no provider restart, mid-day re-snapshot, u gap, absent-level
  delete or crossed state; one adjacent-file seam measured, next file prefix only.
  The full-file certifier flags the closing out-of-day snapshot as duplicate_sequence:1;
  its event count is 863,928, while source_events counts all 863,929 records. The
  in-day canonical input excludes the tail; there is no in-day duplicate exemption.
- E5 cost: **4,310.6 s** wall, including **279.4 s** measurement; peak working set
  **9,080 MB**, peak pagefile **9,935 MB**, one run on one Windows host. No bound
  follows. Original E5 ob500 projection parity remains open for A14g.
- Environment: disposable PostgreSQL, injected path resolver for a placeholder
  storage root on Windows (catalog abs_path is absolute POSIX). An earlier rerun
  failed at PostgreSQL after the cluster stopped uncleanly, cause unestablished;
  the successful clean run restarted from the beginning and supplies all credits.
- Disposal #425 / PR #426: credited as an **operational tool**, outside A14 atoms
  and without new K09 credit. A scratch copy was removed with PERMITTED audit and
  K06 PROTECTED while original and independent physical copy remained hash-identical.
  No real copy was disposed. Its PostgreSQL proof is not wired into CI.

## Reopening triggers — none authorized

The [eight named conditions T1-T8](docs/architecture/OPEN_DECISIONS.md#l2-v1-reopening-triggers-442)
record provider restart, a differing second seam, the file-head gap/A14f, ingest
cost/backfill or host memory budget, unknown provider retention, disposal-tool
registration/CI, missing trade-side disposal and a production-like POSIX proof.
Every condition is NOT AUTHORIZED by #442. No follow-on scope or issue is opened.

## Not claimed

- No live L2 (A14f), no ob500 or other effective depth (A14g), no second venue.
- No population-level continuity, discontinuity-rate or provider-restart guarantee.
- No gap-free L2 across UTC midnight; the approximately 1.1 s observed gap remains
  explicit, its duration is not a fixed tolerance or a promise about other days.
- The independent second archive copy is an operator action, not a platform guarantee.
  Provider retention is unknown; the last source copy is never removable.
- Disposal tooling is not part of the A14 closure and adds no atom or K09 credit;
  no trade-archive disposal (`tools/sync_symbol.py` does not exist).
- No production-like POSIX/topology proof, multi-day backfill or memory/throughput bound.
- No promotion to main, runtime/deployment change, new implementation authorization,
  synthetic snapshot, order-level/L3/MBO or quantitative client logic.

## Exit criteria and verification

Issue #442 reconciles the earlier Part B promotion wording to the DG-K precedent:
A14a-A14e are implemented and merged into implement/l2, the v1 golden is accepted,
and this governance closeout credits COMPLETE here. Promotion to main remains the
separate final wave closeout PR; A14f/A14g are outside the v1 closure.

The original #442 governance closeout changed five planning files and only six
implementation credits (A14 plus A14a-A14e). Reconciliation #444 imports captured
main without reverting those credits or main's independent P/S completion. Counts
are recomputed from the two-parent DAG union and checked with expanded Map rows
and all four published inventory blocks. Held/deferred parent rows stay unchanged.
Only the five planning files and the canonical test inventory differ from Git's
automatic merge result; no accepted ADR, frozen contract/schema/fixture is edited.

Required commands from repository root:

```powershell
python -c "from pathlib import Path; s=Path('SCOPE.md').read_text(encoding='utf-8'); exec(s.split('```python\n',1)[1].split('```',1)[0])"
python tools/check_markdown_links.py
python tests/test_canonical_v1.py
python tests/test_package_boundaries_v1.py
python tools/workflow.py preflight --base implement/l2
python tools/run_tests.py
git diff --check
```

### Executable governance verification

The following verifier is documentation-local, not a new runtime/tool or atom.
It derives the inventory from both captured parents, preserves all accepted credits
and held rows, then checks the expanded Map and all four printed checkpoints.
It also checks the six-file manual-resolution allowlist against the automatic merge tree.

```python
from collections import Counter
from pathlib import Path
import re
import subprocess

l2_oid = "01063914492262ba966708415228f6b922fdf581"
main_oid = "693d765fda5fcf2bcf38bbdbfdba6f88c9670d7c"
automatic = subprocess.run(
    ["git", "merge-tree", "--write-tree", l2_oid, main_oid],
    capture_output=True, encoding="utf-8")
assert automatic.returncode == 1, automatic.stderr
auto_tree = automatic.stdout.splitlines()[0]
dag_path = "docs/product/CAPABILITY_DAG.md"
map_path = "docs/product/CAPABILITY_MAP.md"
files = [dag_path, map_path, "docs/product/ROADMAP.md",
         "docs/architecture/OPEN_DECISIONS.md"]

def show(ref, path):
    return subprocess.check_output(["git", "show", f"{ref}:{path}"], encoding="utf-8")

def rows(text):
    result = {}
    for line in text.splitlines():
        cells = [c.strip() for c in line.strip("|").split("|")]
        if len(cells) == 8 and re.fullmatch(r"[A-Z]\d{2}[a-z]?", cells[0]):
            assert cells[0] not in result, cells[0]
            result[cells[0]] = cells
    return result

l2 = rows(show(l2_oid, dag_path))
main = rows(show(main_oid, dag_path))
dag = rows(Path(dag_path).read_text(encoding="utf-8"))
expected_rows = main | {a: row for a, row in l2.items()
                        if a == "A13" or a.startswith("A14")}
assert dag == expected_rows
assert dag.keys() == l2.keys() | main.keys()
for parent in (l2, main):
    for atom, row in parent.items():
        if row[6] == "COMPLETE":
            assert dag[atom][6] == "COMPLETE", atom
for atom in ("A14f", "A14g"):
    assert dag[atom] == l2[atom], atom
for atom in ("S08", "J11"):
    assert dag[atom] == main[atom], atom

mapped, mapped_decisions = {}, {}
for line in Path(map_path).read_text(encoding="utf-8").splitlines():
    cells = [c.strip() for c in line.strip("|").split("|")]
    if len(cells) == 7 and re.fullmatch(r"[A-Z]\d{2}[a-z]?(,[A-Z]\d{2}[a-z]?)*", cells[5]):
        for atom in cells[5].split(","):
            assert atom not in mapped, atom
            mapped[atom], mapped_decisions[atom] = cells[3], cells[2]
assert mapped == {a: row[6] for a, row in dag.items()}
n = len(dag)
decision = Counter(row[5] for row in dag.values())
impl = Counter(row[6] for row in dag.values())
assert set(impl) <= {"COMPLETE", "PARTIAL", "MISSING"}
f = decision["FROZEN"] + decision["RESOLVED"]
b, d = decision["OPEN_BLOCKING"], decision["OPEN_DEFERABLE"]
assert set(decision) <= {"FROZEN", "RESOLVED", "OPEN_BLOCKING", "OPEN_DEFERABLE"}
map_decision = Counter(mapped_decisions.values())
assert set(map_decision) <= set(decision)
assert map_decision["FROZEN"] + map_decision["RESOLVED"] == f
assert map_decision["OPEN_BLOCKING"] == b and map_decision["OPEN_DEFERABLE"] == d
expected = {
    "TOTAL_ATOMS": str(n), "CLASSIFIED_ATOMS": str(f+b+d),
    "UNCLASSIFIED_GAPS": str(n-f-b-d),
    "SEMANTIC_FROZEN_OR_RESOLVED": f"{f} / {n} = {f/n:.1%}",
    "OPEN_BLOCKING": str(b), "OPEN_DEFERABLE": str(d),
    "ROADMAP_DEFINED": f"{f} + {b} + {d} = {f+b+d}",
    "ROADMAP_PLANNING_COMPLETENESS": f"{f+b+d} / {n} = {(f+b+d)/n:.0%}",
    "IMPLEMENTATION_COMPLETE": f"{impl['COMPLETE']} / {n} = {impl['COMPLETE']/n:.1%}",
}
for file in files:
    text = Path(file).read_text(encoding="utf-8")
    for key, value in expected.items():
        matches = re.findall(rf"^{key}\s*=\s*(.+)$", text, re.M)
        assert matches == [value], (file, key, matches, value)
    print(file, "two-parent inventory PASS")
opened = Path(files[-1]).read_text(encoding="utf-8")
triggers = [line for line in opened.splitlines() if re.match(r"\| T[1-8] —", line)]
assert len(triggers) == 8 and all("NOT AUTHORIZED" in line for line in triggers)
assert set(re.findall(r"^\| T([1-8]) —", opened, re.M)) == set("12345678")
original_triggers = [line for line in show(l2_oid, files[-1]).splitlines()
                     if re.match(r"\| T[1-8] —", line)]
assert triggers == original_triggers
main_scope = show(main_oid, "SCOPE.md")
archive = main_scope.split("## Closeout result (issue #409)", 1)[1].split("## Authority", 1)[0]
archive = archive.replace("## Not claimed", "### DG-K2 not claimed")
archive = archive.replace("## Triggers recorded at closeout", "### DG-K2 triggers recorded at closeout")
assert archive in Path("SCOPE.md").read_text(encoding="utf-8")
manual = set(subprocess.check_output(
    ["git", "diff", "--name-only", auto_tree], encoding="utf-8").splitlines())
allowed = set(files) | {"SCOPE.md", "tests/test_canonical_v1.py"}
assert manual <= allowed, manual
print("TOTAL", n, "DECIDED", f, "BLOCKING", b, "DEFERABLE", d,
      "COMPLETE", impl["COMPLETE"], "PARTIAL", impl["PARTIAL"],
      "MISSING", impl["MISSING"], "TRIGGERS", len(triggers))
```

## Stop conditions

Stop and report if reconciliation would claim an unproven capability COMPLETE,
contradict an accepted ADR beyond transcription, or produce inventory counts that
disagree across the four files. A differing later seam is the ADR-0070 Amendment 2
stop condition, never permission to merge source records or relax certification.
