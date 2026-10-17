# Scope: DG-C / A14 — L2 contract, continuity and publication v1

Status: **CLOSED — governance closeout #442 (2026-10-18). Part A closed by #367 / PR #370
(ADR-0070 ACCEPTED). Part B: A14a-A14e implemented and merged into `implement/l2`,
with the v1 golden accepted at `1cdd78f` (11/11 claims, PR #413/#430).
A14 and A14a-A14e are credited COMPLETE on this integration line. A14f/A14g remain
held and outside the closure. Promotion to `main` is a separate wave closeout PR.**

Scope kind: governance-only reconciliation explicitly authorized by issue #442 and
`AGENTS.md`. No runtime, accepted ADR, frozen contract, schema or fixture changes.
No new implementation issue is opened or authorized by this closeout.

## Historical scope archive

The repository convention rewrites `SCOPE.md` in place and preserves earlier versions
in `git log -- SCOPE.md` (DG-K precedent: governance #381). This CLOSED document is
the v1 archive on `implement/l2`; no new active cycle is opened. The earlier DG-C
design gate (#364/#367/#371), Amendment 1 reconciliation (#404), DG-K and Wave 8
scopes remain in Git history. DG-K and L2 scope reconciliation at promotion remains
accepted maintainer debt; no published branch is rebased by this issue.

## Objective

Reconcile DG-C / A14 v1 to the strongest propositions proven by merged evidence,
with script-derived counts, explicit limits and named reopening conditions.

## Authority

- Governance: `AGENTS.md`; #364, #371, #404 and #442.
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

The governance change is restricted to SCOPE.md and the four planning files. Counts
are recomputed from DAG table rows by script and cross-checked with expanded Map
rows and all four published inventory blocks. Only six implementation states change
(A14 plus A14a-A14e); the held DAG rows for A14f/A14g are byte-identical to baseline.
No ADR, contract, runtime, tool, test or fixture is modified.

Required commands from repository root:

```powershell
python -c "from pathlib import Path; s=Path('SCOPE.md').read_text(encoding='utf-8'); exec(s.split('```python\n',1)[1].split('```',1)[0])"
python tools/check_markdown_links.py
python tools/workflow.py preflight --base implement/l2
git diff --check
```

### Executable governance verification

The following verifier is documentation-local, not a new runtime/tool or atom.
It derives each metric from the tables, then checks the four printed checkpoints
and the six permitted credit changes against the captured authoritative baseline.

```python
from collections import Counter
from pathlib import Path
import re
import subprocess

baseline = "1cdd78f47c5054dc02bc44bb637e26104b067c83"
dag_path = "docs/product/CAPABILITY_DAG.md"
map_path = "docs/product/CAPABILITY_MAP.md"
files = [dag_path, map_path, "docs/product/ROADMAP.md",
         "docs/architecture/OPEN_DECISIONS.md"]

def rows(text):
    result = {}
    for line in text.splitlines():
        cells = [c.strip() for c in line.strip("|").split("|")]
        if len(cells) == 8 and re.fullmatch(r"[A-Z]\d{2}[a-z]?", cells[0]):
            assert cells[0] not in result, cells[0]
            result[cells[0]] = cells
    return result

dag = rows(Path(dag_path).read_text(encoding="utf-8"))
before = rows(subprocess.check_output(
    ["git", "show", f"{baseline}:{dag_path}"], encoding="utf-8"))
assert dag.keys() == before.keys()
changed = {a for a in dag if dag[a][6] != before[a][6]}
assert changed == {"A14", "A14a", "A14b", "A14c", "A14d", "A14e"}
for atom in dag:
    assert dag[atom][:6] == before[atom][:6], atom
    assert dag[atom][6] in {"COMPLETE", "PARTIAL", "MISSING"}
    if atom in changed:
        assert dag[atom][6] == "COMPLETE"
for atom in ("A14f", "A14g"):
    assert dag[atom] == before[atom], atom

mapped = {}
mapped_decisions = {}
for line in Path(map_path).read_text(encoding="utf-8").splitlines():
    cells = [c.strip() for c in line.strip("|").split("|")]
    if len(cells) == 7 and re.fullmatch(r"[A-Z]\d{2}[a-z]?(,[A-Z]\d{2}[a-z]?)*", cells[5]):
        for atom in cells[5].split(","):
            assert atom not in mapped, atom
            mapped[atom] = cells[3]
            mapped_decisions[atom] = cells[2]
assert mapped == {a: row[6] for a, row in dag.items()}

n = len(dag)
decision = Counter(row[5] for row in dag.values())
impl = Counter(row[6] for row in dag.values())
f = decision["FROZEN"] + decision["RESOLVED"]
b, d = decision["OPEN_BLOCKING"], decision["OPEN_DEFERABLE"]
assert set(decision) <= {"FROZEN", "RESOLVED", "OPEN_BLOCKING", "OPEN_DEFERABLE"}
map_decision = Counter(mapped_decisions.values())
assert set(map_decision) <= set(decision)
assert map_decision["FROZEN"] + map_decision["RESOLVED"] == f
assert map_decision["OPEN_BLOCKING"] == b
assert map_decision["OPEN_DEFERABLE"] == d
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
    print(file, "inventory PASS")
for atom, prs in {"A14a": [375], "A14b": [379], "A14c": [401],
                  "A14d": [411, 417], "A14e": [408]}.items():
    assert all(f"#{pr}" in dag[atom][7] for pr in prs), atom
opened = Path(files[-1]).read_text(encoding="utf-8")
triggers = [line for line in opened.splitlines() if re.match(r"\| T[1-8] —", line)]
assert len(triggers) == 8 and all("NOT AUTHORIZED" in line for line in triggers)
allowed = set(files) | {"SCOPE.md"}
changed_files = set(subprocess.check_output(
    ["git", "diff", "--name-only", baseline], encoding="utf-8").splitlines())
assert changed_files == allowed, changed_files
print("TOTAL", n, "DECIDED", f, "BLOCKING", b, "DEFERABLE", d,
      "COMPLETE", impl["COMPLETE"], "PARTIAL", impl["PARTIAL"],
      "MISSING", impl["MISSING"], "TRIGGERS", len(triggers))
```

## Stop conditions

Stop and report if reconciliation would claim an unproven capability COMPLETE,
contradict an accepted ADR beyond transcription, or produce inventory counts that
disagree across the four files. A differing later seam is the ADR-0070 Amendment 2
stop condition, never permission to merge source records or relax certification.
