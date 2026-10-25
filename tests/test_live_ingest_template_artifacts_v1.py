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

# The existing BTCUSDT unit and environment template, as on implement/fid before T02 part 4 (git show, LF). DG-O O03 added the session
# options to both: the guard removes exactly that addition and compares the rest with the original, byte for byte.
PLAIN_UNIT_SHA256 = "bf5b9df14f9547874bd59b1d013df4f45253996fd8803da17abbb342434c26dc"
PLAIN_ENV_SHA256 = "4f8a2e2ceb648403a60c4f98854597aa7c18729d3dd824ee411338715bdde101"
O03_EXEC_SUFFIX = (" --mode=${QP_LIVE_INGEST_MODE} --slice-seconds=${QP_LIVE_INGEST_SLICE_SECONDS}"
                   " --slice-max-trades=${QP_LIVE_INGEST_SLICE_MAX_TRADES} --buffer-messages=${QP_LIVE_INGEST_BUFFER_MESSAGES}")
O03_ENV_NAMES = ("QP_LIVE_INGEST_MODE", "QP_LIVE_INGEST_SLICE_SECONDS", "QP_LIVE_INGEST_SLICE_MAX_TRADES",
                 "QP_LIVE_INGEST_BUFFER_MESSAGES")


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
    """``--option -> value`` of an ExecStart line (every option here takes one value, as ``--option value`` or ``--option=value``)."""

    tokens = exec_start.split()
    result = {}
    for index, token in enumerate(tokens):
        if token.startswith("--"):
            name, equals, value = token.partition("=")
            result[name] = value if equals else tokens[index + 1]
    return result


def without_o03(path: Path) -> str:
    """The artifact as it was before the session options (the exact O03 additions removed)."""

    content = text(path)
    if path.suffix == ".service":
        assert content.count(O03_EXEC_SUFFIX) == 1
        return content.replace(O03_EXEC_SUFFIX, "")
    kept = []
    skipping = False
    for line in content.split("\n"):
        if line.startswith("# Session mode (ADR-0075"):
            skipping = True
        if skipping and (line.startswith("QP_LIVE_INGEST_PRESSURE_AVAILABLE_BYTES")):
            skipping = False
        if not skipping:
            kept.append(line)
    return "\n".join(kept)


class ExistingBtcusdtFilesTests(unittest.TestCase):
    def test_the_existing_unit_and_environment_template_are_unchanged_apart_from_the_session_options(self) -> None:
        self.assertEqual(PLAIN_UNIT_SHA256, hashlib.sha256(without_o03(PLAIN).encode("utf-8")).hexdigest())
        self.assertEqual(PLAIN_ENV_SHA256, hashlib.sha256(without_o03(PLAIN_ENV).encode("utf-8")).hexdigest())

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


class SessionOptionsTests(unittest.TestCase):
    """DG-O O03: both units and both environment templates carry the session mode and its three bounds."""

    @staticmethod
    def render(exec_start: str, environment: dict[str, str], instance: str = "ETHUSDT") -> list[str]:
        """What systemd would run: ``${VAR}`` is the variable's value, an unset one is the empty string, ``%i`` the instance."""

        rendered = re.sub(r"\$\{([A-Z_]+)\}", lambda match: environment.get(match.group(1), ""), exec_start.replace("%i", instance))
        return rendered.split()

    def run_cli(self, argv: list[str]):
        import io
        from contextlib import redirect_stdout
        from types import SimpleNamespace

        sys.path.insert(0, str(ROOT / "tools"))
        import live_ingest_server as cli

        seen = []
        original = cli.run_live_ingest_server
        cli.run_live_ingest_server = lambda config, **kw: seen.append(config) or SimpleNamespace(status=cli.SERVER_STOPPED, cycles=0, signals=())
        try:
            with redirect_stdout(io.StringIO()):
                self.assertEqual(0, cli.main(argv))
        finally:
            cli.run_live_ingest_server = original
        return seen[0]

    def units(self):
        return ((PLAIN, PLAIN_ENV, "BTCUSDT"), (TEMPLATE, TEMPLATE_ENV, "ETHUSDT"))

    def test_both_units_pass_the_mode_and_the_three_bounds_from_the_environment(self) -> None:
        for unit, _env, _instance in self.units():
            (exec_start,) = unit_lines(unit)["Service.ExecStart"]
            self.assertTrue(exec_start.endswith(O03_EXEC_SUFFIX), unit.name)
            found = options(exec_start)
            self.assertEqual("${QP_LIVE_INGEST_MODE}", found["--mode"])
            self.assertEqual("${QP_LIVE_INGEST_SLICE_SECONDS}", found["--slice-seconds"])
            self.assertEqual("${QP_LIVE_INGEST_SLICE_MAX_TRADES}", found["--slice-max-trades"])
            self.assertEqual("${QP_LIVE_INGEST_BUFFER_MESSAGES}", found["--buffer-messages"])

    def test_both_environment_templates_default_to_cycles_with_the_bounds_left_empty(self) -> None:
        for _unit, env, _instance in self.units():
            variables = env_variables(env)
            self.assertEqual("cycles", variables["QP_LIVE_INGEST_MODE"], env.name)
            for name in O03_ENV_NAMES[1:]:
                self.assertEqual("", variables[name], (env.name, name))
            self.assertIn("ADR-0075", text(env))

    def test_the_example_environments_start_the_existing_cycle_loop_in_both_units(self) -> None:
        for unit, env, instance in self.units():
            argv = self.render(unit_lines(unit)["Service.ExecStart"][0], env_variables(env), instance)
            config = self.run_cli(argv[2:])
            self.assertIsNone(config.continuous_session, unit.name)
            self.assertEqual(instance, config.instrument)

    def test_an_environment_file_that_predates_the_options_still_gives_a_valid_command_in_the_cycle_mode(self) -> None:
        for unit, env, instance in self.units():
            old = {k: v for k, v in env_variables(env).items() if k not in O03_ENV_NAMES}
            argv = self.render(unit_lines(unit)["Service.ExecStart"][0], old, instance)
            config = self.run_cli(argv[2:])
            self.assertIsNone(config.continuous_session, unit.name)

    def test_the_continuous_mode_and_its_bounds_reach_the_session_from_either_environment(self) -> None:
        for unit, env, instance in self.units():
            new = {**env_variables(env), "QP_LIVE_INGEST_MODE": "continuous", "QP_LIVE_INGEST_SLICE_SECONDS": "120",
                   "QP_LIVE_INGEST_SLICE_MAX_TRADES": "9000", "QP_LIVE_INGEST_BUFFER_MESSAGES": "4000"}
            argv = self.render(unit_lines(unit)["Service.ExecStart"][0], new, instance)
            session = self.run_cli(argv[2:]).continuous_session
            self.assertEqual((120, 9000, 4000), (session.slice_seconds, session.slice_max_trades, session.buffer_messages))
            default = {**env_variables(env), "QP_LIVE_INGEST_MODE": "continuous"}
            session = self.run_cli(self.render(unit_lines(unit)["Service.ExecStart"][0], default, instance)[2:]).continuous_session
            self.assertEqual((300, 50_000, 20_000), (session.slice_seconds, session.slice_max_trades, session.buffer_messages))


class ContinuousRunbookTests(unittest.TestCase):
    """DG-O O03: the runbook operates the continuous session and describes the one-hour proof."""

    def setUp(self) -> None:
        self.runbook = text(RUNBOOK)
        self.section = self.runbook.split("### Continuous session (ADR-0075", 1)[1].split("\n## Rollback", 1)[0]

    def test_it_no_longer_says_the_session_is_not_enabled(self) -> None:
        self.assertNotIn("in the code, not enabled", self.runbook)
        self.assertNotIn("belong to `O03`", self.runbook)

    def test_it_documents_every_variable_and_flag_with_its_default(self) -> None:
        for name in O03_ENV_NAMES:
            self.assertIn(f"`{name}`", self.section)
        for flag in ("--mode", "--slice-seconds", "--slice-max-trades", "--buffer-messages"):
            self.assertIn(f"`{flag}`", self.section)
        for default in ("| 300 |", "| 50000 |", "| 20000 |", "| `cycles` |"):
            self.assertIn(default, self.section)
        self.assertIn("exit status 2", self.section)  # a bound without the continuous mode is refused, not ignored

    def test_it_keeps_the_cycles_as_the_default_and_describes_the_rollback_and_the_operator_boundary(self) -> None:
        plain = " ".join(self.section.replace("**", "").split())  # line breaks do not matter
        for condition in ("The default is `cycles`", "Rollback to the cycles", "operator step", "enables nothing",
                          "an environment file that predates these variables still starts the unit"):
            self.assertIn(condition, plain, condition)

    def test_it_describes_the_proof_its_tool_and_the_stop_conditions(self) -> None:
        plain = " ".join(self.section.replace("**", "").split())
        for needed in ("tools/live_window_evidence.py", "tools/fetch_bybit_trade_archive.py", "at least one hour",
                       "disposable", "Stop conditions", "undeclared loss", "ordering violation", "BTCUSDT"):
            self.assertIn(needed, plain, needed)
        for tool in ("live_window_evidence.py", "fetch_bybit_trade_archive.py", "live_ingest_server.py"):
            self.assertTrue((ROOT / "tools" / tool).is_file(), tool)

    def test_it_states_what_an_operator_sees_of_the_session(self) -> None:
        for signal in ("phase=interruption", "phase=slice", "buffer_high_water", "slice_lag_ms", "ordering_violations",
                       "ADR-0044", "unclaimed span"):
            self.assertIn(signal, self.section)


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
