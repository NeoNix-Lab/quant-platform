"""Governance proof for A07's accepted historical acquisition v1 boundary."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ADR = ROOT / "docs" / "decisions" / "ADR-0054-a07-historical-acquisition-day-boundary-v1.md"
CAPABILITY_DAG = ROOT / "docs" / "product" / "CAPABILITY_DAG.md"
CAPABILITY_MAP = ROOT / "docs" / "product" / "CAPABILITY_MAP.md"
DECISIONS_INDEX = ROOT / "docs" / "decisions" / "README.md"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_a07_day_boundary_decision_is_recorded():
    text = _read(ADR)

    assert "**Status:** ACCEPTED" in text
    assert "one-UTC-day, Bybit BTCUSDT linear historical acquisition" in text
    assert "caller-owned day-by-day loop" in text
    assert "does not implement native date-range acquisition" in text
    assert "Closes issue #252" in text


def test_a07_capability_entries_name_day_by_day_v1_boundary():
    dag = _read(CAPABILITY_DAG)
    capability_map = _read(CAPABILITY_MAP)

    assert (
        "| A07 | Bybit historical acquisition | Source/Producer | A01 | A08,K06 | "
        "FROZEN | PARTIAL | One-UTC-day BTCUSDT-linear v1 profile; "
        "multi-day consumers loop day-by-day; Ingest/ADR-0023/ADR-0054 |"
    ) in dag
    assert (
        "| Data | Historical Bybit acquisition | FROZEN | PARTIAL | Source/Producer | "
        "A07 | One-UTC-day BTCUSDT-linear v1 profile; multi-day/date-range "
        "consumers loop day-by-day. ADR-0054; not a general ingest runtime. |"
    ) in capability_map


def test_adr_0054_indexed_in_decisions_readme():
    text = _read(DECISIONS_INDEX)

    assert "| 0054 | A07 historical acquisition day-boundary v1 | Accepted |" in text
