"""DG-N T02 part 4: the template unit, its environment template and the runbook (ADR-0074 sections 3 and 6).

Static checks of the artifacts. They prove the files are right, not that a unit starts on the host: installing and enabling are
operator steps, and the two-instance and real-provider proofs are parts 5 and 6 of T02.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
import re
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from quant_platform.source_adapters.bybit import BYBIT_LINEAR_TRADE_INSTRUMENTS_V1  # noqa: E402

SYSTEMD = ROOT / "infra" / "systemd"
PLAIN = SYSTEMD / "quant-platform-live-ingest.service"
PLAIN_ENV = SYSTEMD / "quant-platform-live-ingest.env.example"
TEMPLATE = SYSTEMD / "quant-platform-live-ingest@.service"
TEMPLATE_ENV = SYSTEMD / "quant-platform-live-ingest@.env.example"
RUNBOOK = ROOT / "docs" / "engineering" / "LIVE_INGEST_SERVER_DEPLOYMENT.md"
NOT_BTC = tuple(item for item in BYBIT_LINEAR_TRADE_INSTRUMENTS_V1 if item != "BTCUSDT")

# The existing BTCUSDT unit and environment template, as on implement/fid before T02 part 4 (git show, LF).
PLAIN_UNIT_SHA256 = "bf5b9df14f9547874bd59b1d013df4f45253996fd8803da17abbb342434c26dc"
PLAIN_ENV_SHA256 = "4f8a2e2ceb648403a60c4f98854597aa7c18729d3dd824ee411338715bdde101"


def text(path: Path) -> str:
    return path.read_bytes().decode("utf-8").replace("\r\n", "\n")


def digest(path: Path) -> str:
    return hashlib.sha256(text(path).encode("utf-8")).hexdigest()


def unit_lines(path: Path) -> dict[str, list[str]]:
    """``Section.Key -> values`` of a unit file, comments and blank lines dropped."""

    result: dict[str, list[str]] = {}
    section = ""
    for raw in text(path).split("\n"):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("[") and line.endswith("]"):
            section = line[1:-1]
            continue
        key, _, value = line.partition("=")
        result.setdefault(f"{section}.{key}", []).append(value)
    return result


def env_variables(path: Path) -> dict[str, str]:
    result = {}
    for raw in text(path).split("\n"):
        line = raw.strip()
        if line and not line.startswith("#") and "=" in line:
            key, _, value = line.partition("=")
            result[key] = value
    return result


def options(exec_start: str) -> dict[str, str]:
    """``--option -> value`` of an ExecStart line (every option here takes one value)."""

    tokens = exec_start.split()
    return {token: tokens[index + 1] for index, token in enumerate(tokens) if token.startswith("--")}


class ExistingBtcusdtFilesTests(unittest.TestCase):
    def test_the_existing_unit_and_environment_template_are_unchanged_byte_for_byte(self) -> None:
        self.assertEqual(PLAIN_UNIT_SHA256, digest(PLAIN))
        self.assertEqual(PLAIN_ENV_SHA256, digest(PLAIN_ENV))

    def test_the_existing_unit_still_has_no_instrument_option(self) -> None:
        self.assertNotIn("--instrument", text(PLAIN))


class TemplateUnitTests(unittest.TestCase):
    def setUp(self) -> None:
        self.template = unit_lines(TEMPLATE)
        self.plain = unit_lines(PLAIN)

    def test_the_instrument_is_the_instance_name_and_always_on_the_command_line(self) -> None:
        (exec_start,) = self.template["Service.ExecStart"]
        self.assertEqual("%i", options(exec_start)["--instrument"])
        self.assertEqual(1, exec_start.count("--instrument"))
        self.assertIn("(%i)", self.template["Unit.Description"][0])
        self.assertEqual(["/etc/quant-platform/live-ingest-server-%i.env"], self.template["Service.EnvironmentFile"])

    def test_the_checkpoint_file_name_comes_from_the_instance_not_from_the_environment(self) -> None:
        (exec_start,) = self.template["Service.ExecStart"]
        self.assertEqual("${QP_LIVE_INGEST_CHECKPOINT_DIR}/live-ingest-server-v1-%i.json", options(exec_start)["--checkpoint-path"])
        self.assertNotIn("QP_LIVE_INGEST_CHECKPOINT_PATH", text(TEMPLATE))
        self.assertNotIn("QP_LIVE_INGEST_CHECKPOINT_PATH", text(TEMPLATE_ENV))

    def test_the_instance_btcusdt_cannot_start(self) -> None:
        self.assertEqual(['/usr/bin/test "%i" != "BTCUSDT"'], self.template["Service.ExecStartPre"])

    def test_identity_and_hardening_equal_the_existing_unit(self) -> None:
        different = {"Unit.Description", "Unit.Documentation", "Service.EnvironmentFile", "Service.ExecStart",
                     "Service.ExecStartPre"}
        self.assertEqual({k: v for k, v in self.plain.items() if k not in different},
                         {k: v for k, v in self.template.items() if k not in different})
        for needed in ("Service.User", "Service.Group", "Service.NoNewPrivileges", "Service.ProtectSystem",
                       "Service.ReadWritePaths", "Service.Restart", "Service.KillSignal", "Install.WantedBy"):
            self.assertIn(needed, self.template)
        self.assertEqual(["strict"], self.template["Service.ProtectSystem"])
        self.assertEqual(["mkt-transform"], self.template["Service.User"])

    def test_the_options_are_the_existing_units_plus_the_two_instance_specific_ones(self) -> None:
        template = options(self.template["Service.ExecStart"][0])
        plain = options(self.plain["Service.ExecStart"][0])
        instance_specific = {"--instrument", "--checkpoint-path"}
        self.assertEqual(set(plain) | {"--instrument"}, set(template))
        self.assertEqual({k: v for k, v in plain.items() if k not in instance_specific},
                         {k: v for k, v in template.items() if k not in instance_specific})

    def test_no_bounded_proof_flag_and_no_secret(self) -> None:
        content = text(TEMPLATE)
        for forbidden in ("--max-cycles", "PGPASSWORD", "--dsn"):
            self.assertNotIn(forbidden, content)

    def test_documentation_points_at_a_stable_ref(self) -> None:
        (documentation,) = self.template["Unit.Documentation"]
        self.assertTrue(documentation.startswith("https://github.com/NeoNix-Lab/quant-platform/blob/main/"))
        self.assertTrue(documentation.endswith("ADR-0074-multi-instrument-bybit-linear-trades-v1.md"))
        self.assertTrue((ROOT / "docs" / "decisions" / "ADR-0074-multi-instrument-bybit-linear-trades-v1.md").is_file())


class EnvironmentTemplateTests(unittest.TestCase):
    def test_every_variable_the_unit_uses_is_defined_and_every_one_defined_is_used(self) -> None:
        used = set(re.findall(r"\$\{(QP_LIVE_INGEST_[A-Z_]+)\}", text(TEMPLATE)))
        defined = {name for name in env_variables(TEMPLATE_ENV) if name.startswith("QP_LIVE_INGEST_")}
        self.assertEqual(used, defined)

    def test_it_is_non_secret_and_carries_no_instrument(self) -> None:
        content = text(TEMPLATE_ENV)
        variables = env_variables(TEMPLATE_ENV)
        for forbidden in ("PGPASSWORD", "INSTRUMENT", "api_key", "secret"):
            self.assertNotIn(forbidden, "\n".join(f"{k}={v}" for k, v in variables.items()))
        self.assertEqual("/home/mkt-transform/.pgpass", variables["PGPASSFILE"])
        self.assertEqual("REPLACE_WITH_DEPLOYED_GIT_SHA", variables["QP_LIVE_INGEST_CODE_REF"])
        self.assertEqual("/srv/marketdata/canonical/_k10_checkpoints", variables["QP_LIVE_INGEST_CHECKPOINT_DIR"])
        for instrument in BYBIT_LINEAR_TRADE_INSTRUMENTS_V1:
            self.assertNotIn(instrument, "\n".join(f"{k}={v}" for k, v in variables.items()))
        self.assertIn("do not create live-ingest-server-BTCUSDT.env", content)

    def test_it_matches_the_existing_template_apart_from_the_checkpoint(self) -> None:
        template, plain = env_variables(TEMPLATE_ENV), env_variables(PLAIN_ENV)
        self.assertEqual(set(plain) - {"QP_LIVE_INGEST_CHECKPOINT_PATH"}, set(template) - {"QP_LIVE_INGEST_CHECKPOINT_DIR"})
        for name in set(plain) & set(template):
            self.assertEqual(plain[name], template[name], name)


class RunbookTests(unittest.TestCase):
    def setUp(self) -> None:
        self.runbook = text(RUNBOOK)
        self.section = self.runbook.split("## Additional Bybit Linear Instruments", 1)[1]
        self.plain_section = self.section.replace("**", "").lower()  # emphasis and case do not matter to the conditions

    def test_the_existing_runbook_content_is_kept(self) -> None:
        for kept in ("## Install Or Update", "## Rollback", "quant-platform-live-ingest.service", "git reset --hard",
                     "Do not add `PGPASSWORD`"):
            self.assertIn(kept, self.runbook)

    def test_it_names_the_template_the_files_and_the_four_instruments(self) -> None:
        for needed in ("quant-platform-live-ingest@<INSTRUMENT>.service", "live-ingest-server-<INSTRUMENT>.env",
                       "live-ingest-server-v1-<INSTRUMENT>.json", "--instrument %i", "ExecStartPre"):
            self.assertIn(needed, self.section)
        # the sentence that lists the instruments, not a mention elsewhere in the section
        listing = self.section.split("other instruments of the allow-list", 1)[1].split("run as instances", 1)[0]
        for instrument in NOT_BTC:
            self.assertIn(f"`{instrument}`", listing)
        self.assertNotIn("`BTCUSDT`", listing)

    def test_it_states_the_conditions(self) -> None:
        for condition in (
            "one live-ingest process per instrument",
            "`BTCUSDT` stays on `quant-platform-live-ingest.service`",
            "do not create `live-ingest-server-BTCUSDT.env`",
            "at least 365 days",
            "#378",
            "CheckpointDomainMismatch",
            "exit status 2",
            "ADR-0044",
            "Nothing here is installed or enabled by the repository",
        ):
            self.assertIn(condition.lower(), self.plain_section, condition)

    def test_the_install_block_never_targets_btcusdt_and_adds_no_secret(self) -> None:
        self.assertIn("never BTCUSDT", self.section)
        self.assertNotIn("PGPASSWORD=", self.section)
        self.assertIn("test -z \"$(git status --porcelain)\"", self.section)
        for destructive in ("git reset", "git clean", "rm -rf", "git stash"):
            self.assertNotIn(destructive, self.section)


if __name__ == "__main__":
    unittest.main()
