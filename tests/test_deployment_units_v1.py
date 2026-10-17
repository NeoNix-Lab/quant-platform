"""The API and worker units, their environment examples and the handoff runbook (issue #396, ADR-0072 section 6).

The files are checked against what the hosts actually read: an environment example may only name variables that
``tools/api_server.py`` or ``tools/job_worker.py`` read, and the units must keep the hardening of the live-ingest unit
they are modeled on. Starting the units on a real host is ADR-0072 section 7 (S07), not this test.
"""

from __future__ import annotations

import ast
from pathlib import Path
import re
import sys
import unittest


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
SYSTEMD = ROOT / "infra" / "systemd"
DOCS = ROOT / "docs" / "engineering"

MODEL = SYSTEMD / "quant-platform-live-ingest.service"
API_UNIT = SYSTEMD / "quant-platform-api.service"
WORKER_UNIT = SYSTEMD / "quant-platform-worker.service"
API_ENV = SYSTEMD / "quant-platform-api.env.example"
WORKER_ENV = SYSTEMD / "quant-platform-worker.env.example"
RUNBOOK = DOCS / "DECK_HANDOFF_RUNBOOK.md"
COMPATIBILITY = DOCS / "SERVER_DECK_COMPATIBILITY.md"

API_HOST = ROOT / "tools" / "api_server.py"
WORKER_HOST = ROOT / "tools" / "job_worker.py"
SHARED_RUNTIME = ROOT / "src" / "quant_platform" / "application" / "job_worker_runtime.py"

# Interpreter settings, not read by the hosts but not secrets or platform settings either.
INTERPRETER_VARIABLES = {"PYTHONUNBUFFERED", "PYTHONDONTWRITEBYTECODE"}
# Directives of the live-ingest unit that are about the process, not about hardening.
HARDENING_DIRECTIVES = (
    "NoNewPrivileges", "CapabilityBoundingSet", "AmbientCapabilities", "ProtectSystem", "ProtectHome",
    "PrivateTmp", "ProtectKernelTunables", "ProtectControlGroups", "RestrictNamespaces", "RestrictAddressFamilies",
    "UMask",
)


def parse_unit(path: Path) -> dict[str, dict[str, list[str]]]:
    sections: dict[str, dict[str, list[str]]] = {}
    current: dict[str, list[str]] | None = None
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith(("#", ";")):
            continue
        if line.startswith("[") and line.endswith("]"):
            current = sections.setdefault(line[1:-1], {})
            continue
        key, separator, value = line.partition("=")
        if current is None or not separator:
            raise AssertionError(f"{path.name}: not a unit line: {raw!r}")
        current.setdefault(key.strip(), []).append(value.strip())
    return sections


def parse_environment(path: Path) -> dict[str, str]:
    """The assignments of an environment file, without comments; one value per name."""
    values: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        name, separator, value = line.partition("=")
        if not separator or not re.fullmatch(r"[A-Z][A-Z0-9_]*", name):
            raise AssertionError(f"{path.name}: not an assignment: {raw!r}")
        if name in values:
            raise AssertionError(f"{path.name}: {name} is assigned twice")
        values[name] = value.strip().strip('"')
    return values


def environment_names_read(*sources: Path) -> set[str]:
    """Every name a source passes to ``os.environ.get``."""
    names: set[str] = set()
    for source in sources:
        for node in ast.walk(ast.parse(source.read_text(encoding="utf-8"))):
            if (
                isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "get"
                and isinstance(node.func.value, ast.Attribute) and node.func.value.attr == "environ"
                and node.args and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str)
            ):
                names.add(node.args[0].value)
    return names


def single(section: dict[str, list[str]], key: str) -> str:
    values = section.get(key, [])
    if len(values) != 1:
        raise AssertionError(f"{key} must be set exactly once, found {values}")
    return values[0]


class DeploymentUnitTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.model = parse_unit(MODEL)["Service"]
        cls.api = parse_unit(API_UNIT)
        cls.worker = parse_unit(WORKER_UNIT)
        cls.units = {"api": cls.api, "worker": cls.worker}

    # -- acceptance 1: modeled on the live-ingest unit -------------------------------------

    def test_each_unit_runs_as_its_own_non_root_user(self) -> None:
        users = {}
        for name, unit in self.units.items():
            service = unit["Service"]
            user, group = single(service, "User"), single(service, "Group")
            self.assertNotIn(user, {"root", single(self.model, "User")}, name)
            self.assertEqual(user, group, name)
            users[name] = user
        self.assertEqual(2, len(set(users.values())), "the API and the worker do not share a user")

    def test_each_unit_keeps_every_hardening_directive_of_the_live_ingest_unit(self) -> None:
        for directive in HARDENING_DIRECTIVES:
            self.assertIn(directive, self.model, f"{directive} is no longer in the model unit")
        for name, unit in self.units.items():
            for directive in HARDENING_DIRECTIVES:
                with self.subTest(unit=name, directive=directive):
                    self.assertEqual(self.model[directive], unit["Service"].get(directive))
        for unit in self.units.values():
            service = unit["Service"]
            self.assertEqual("strict", single(service, "ProtectSystem"))
            self.assertEqual("0027", single(service, "UMask"))
            self.assertEqual("true", single(service, "NoNewPrivileges"))

    def test_each_unit_runs_its_own_host_with_its_own_environment_file(self) -> None:
        expected = {
            "api": ("/opt/market-platform/tools/api_server.py", "/etc/quant-platform/api-server.env"),
            "worker": ("/opt/market-platform/tools/job_worker.py", "/etc/quant-platform/worker.env"),
        }
        for name, (host, environment_file) in expected.items():
            service = self.units[name]["Service"]
            with self.subTest(name):
                self.assertEqual(environment_file, single(service, "EnvironmentFile"))
                self.assertTrue(single(service, "ExecStart").endswith(f" {host}"), single(service, "ExecStart"))
                self.assertEqual("/opt/market-platform", single(service, "WorkingDirectory"))
                self.assertEqual("SIGTERM", single(service, "KillSignal"))

    def test_the_units_name_a_stable_documentation_ref_that_exists(self) -> None:
        for name, unit in self.units.items():
            reference = single(unit["Unit"], "Documentation")
            prefix = "https://github.com/NeoNix-Lab/quant-platform/blob/main/"
            with self.subTest(name):
                self.assertTrue(reference.startswith(prefix), reference)
                self.assertTrue((ROOT / reference[len(prefix):]).is_file(), reference)

    # -- acceptance 2: the worker's ordering and exit rule ----------------------------------

    def test_the_worker_is_ordered_after_the_api_and_postgresql_and_follows_the_api(self) -> None:
        unit = self.worker["Unit"]
        ordered = single(unit, "After").split()
        self.assertIn("quant-platform-api.service", ordered)
        self.assertIn("postgresql.service", ordered)
        # ADR-0072 section 6 asks for ordering and an explicit restart of the pair, not for lifecycle coupling between
        # the two: no PartOf=/BindsTo=/Requires=... of one unit may name the other. (Other targets are not constrained.)
        names = {"api": "quant-platform-api.service", "worker": "quant-platform-worker.service"}
        for key, other_name in (("api", names["worker"]), ("worker", names["api"])):
            for section_name, section in self.units[key].items():
                for directive in ("PartOf", "BindsTo", "Requires", "Requisite", "PropagatesStopTo", "PropagatesReloadTo"):
                    with self.subTest(unit=key, section=section_name, directive=directive):
                        for value in section.get(directive, []):
                            self.assertNotIn(other_name, value.split())
        api_unit = self.api["Unit"]
        self.assertNotIn("quant-platform-worker.service", single(api_unit, "After").split())
        self.assertNotIn("quant-platform-worker.service", " ".join(sum(api_unit.values(), [])))
        self.assertIn("postgresql.service", single(api_unit, "After").split())

    def test_restart_on_failure_does_not_hide_a_commit_mismatch(self) -> None:
        # Exit 3 is a failure the unit must neither treat as success nor refuse to restart: the pair converges.
        for name, unit in self.units.items():
            service = unit["Service"]
            with self.subTest(name):
                self.assertEqual("on-failure", single(service, "Restart"))
                for key in ("SuccessExitStatus", "RestartPreventExitStatus", "RestartForceExitStatus"):
                    self.assertNotIn(key, service)

    def test_the_worker_exit_codes_the_documents_state_are_the_hosts(self) -> None:
        source = WORKER_HOST.read_text(encoding="utf-8")
        for constant, code in (("EXIT_LOCK_HELD", 2), ("EXIT_COMMIT_MISMATCH", 3), ("EXIT_LOCK_LOST", 4)):
            self.assertRegex(source, rf"{constant} = {code}\b")
        api = API_HOST.read_text(encoding="utf-8")
        for constant, code in (("EXIT_RUNTIME_NOT_CONFIGURED", 2), ("EXIT_COMPOSITION_REFUSED", 4)):
            self.assertRegex(api, rf"{constant} = {code}\b")
        # The API records its commit before it listens: the record is made before the transport starts.
        self.assertLess(api.index("_record_commit(args.runtime_dsn)"), api.index("await run_api_transport_server("))

    def test_the_worker_can_write_its_lock_and_the_api_writes_nothing(self) -> None:
        environment = parse_environment(WORKER_ENV)
        writable = single(self.worker["Service"], "ReadWritePaths").split()
        self.assertIn(environment["QP_WORKER_LOCK_DIR"], writable)
        self.assertNotIn("ReadWritePaths", self.api["Service"])

    # -- acceptance 1 and 6: the environment examples ---------------------------------------

    def test_each_environment_example_names_only_variables_its_host_reads(self) -> None:
        cases = (
            ("api", API_ENV, environment_names_read(API_HOST, SHARED_RUNTIME)),
            ("worker", WORKER_ENV, environment_names_read(WORKER_HOST, SHARED_RUNTIME)),
        )
        for name, path, read in cases:
            assigned = set(parse_environment(path))
            with self.subTest(name):
                self.assertTrue(read, "the host reads no environment variable?")
                self.assertEqual(set(), assigned - read - INTERPRETER_VARIABLES, "a variable no host reads")

    def test_each_environment_example_covers_what_its_host_requires(self) -> None:
        api = parse_environment(API_ENV)
        for name in (
            "CATALOG_DSN", "QP_RUNTIME_DSN", "QP_API_HOST", "QP_API_PORT", "QP_API_ALLOW_NON_LOOPBACK",
            "QP_API_TLS_CERTIFICATE", "QP_API_TLS_PRIVATE_KEY", "QP_API_CLIENT_TRUST_ANCHOR",
            "QP_API_PRINCIPALS_FILE", "QP_API_FAMILIES",
        ):
            self.assertIn(name, api)
        for family in ("admitted-input", "result-import"):
            self.assertIn(family, api["QP_API_FAMILIES"].split(","))
        self.assertEqual("127.0.0.1", api["QP_API_HOST"], "a non-loopback bind is the operator's explicit choice")
        self.assertEqual("false", api["QP_API_ALLOW_NON_LOOPBACK"])
        worker = parse_environment(WORKER_ENV)
        for name in ("QP_RUNTIME_DSN", "QP_WORKER_LOCK_DIR", "QP_WORKER_POLL_SECONDS"):
            self.assertIn(name, worker)

    def test_the_commit_comes_from_the_checkout_not_from_the_environment_file(self) -> None:
        for path in (API_ENV, WORKER_ENV):
            self.assertNotIn("QP_GIT_COMMIT", parse_environment(path), path.name)

    def test_the_logins_are_the_unit_roles_of_the_database_init(self) -> None:
        roles = (ROOT / "db" / "init" / "005_runtime_roles.sh").read_text(encoding="utf-8")
        for path, login in ((API_ENV, "platform_api"), (WORKER_ENV, "platform_worker")):
            with self.subTest(login):
                self.assertIn(f"CREATE ROLE {login} ", roles)
                dsn = parse_environment(path)["QP_RUNTIME_DSN"]
                self.assertIn(f"user={login} ", dsn + " ")
        self.assertIn("user=platform_api ", parse_environment(API_ENV)["CATALOG_DSN"] + " ")

    # -- acceptance 1: no secret in a unit or an example ------------------------------------

    def test_no_unit_or_example_holds_a_secret(self) -> None:
        for path in (API_UNIT, WORKER_UNIT, API_ENV, WORKER_ENV):
            text = path.read_text(encoding="utf-8")
            with self.subTest(path.name):
                self.assertNotRegex(text, r"(?i)password\s*=")
                self.assertNotIn("PGPASSWORD", text)
                # Built, not written: the repository-wide scan for key material would otherwise find this file.
                self.assertNotIn("PRIVATE " + "KEY-----", text)
                self.assertNotRegex(text, r"(?i)(token|secret|api[_-]?key)\s*=")
        for path in (API_ENV, WORKER_ENV):
            for name, value in parse_environment(path).items():
                if name.endswith("_DSN"):
                    with self.subTest(path.name, name=name):
                        self.assertIn("passfile=/etc/quant-platform/", value)
                        self.assertNotRegex(value, r"(?i)\bpassword=")

    # -- acceptance 3 and 4 and 5: the documents say what the ADR says ---------------------------------

    def test_promotion_keeps_clean_tree_and_fast_forward_and_restarts_the_pair_together(self) -> None:
        script = (ROOT / "infra" / "promote-main.sh").read_text(encoding="utf-8")
        self.assertIn("git merge --ff-only", script)
        self.assertIn('[[ -z "$(git status --porcelain)" ]]', script)
        self.assertNotIn("systemctl", script, "promotion does not deploy or restart by itself")
        text = RUNBOOK.read_text(encoding="utf-8")
        self.assertIn("clean-tree and fast-forward-only", text)
        self.assertIn("sudo systemctl restart quant-platform-api.service quant-platform-worker.service", text)
        self.assertIn("The deck checks out the same commit before a run", text)
        self.assertIn("infra/promote-main.sh --inspect-only", text)

    def test_promotion_brings_every_server_unit_to_the_same_declared_commit(self) -> None:
        # ADR-0068 section 8: ingest, API and worker of one server run the same commit. The live-ingest unit also
        # declares it (QP_LIVE_INGEST_CODE_REF), so the promotion must update that variable and restart the unit.
        text = RUNBOOK.read_text(encoding="utf-8")
        promotion = text[text.index("## Promotion"):text.index("## A run, step by step")]
        self.assertIn('DEPLOYED_SHA="$(git rev-parse HEAD)"', promotion)
        self.assertIn("QP_LIVE_INGEST_CODE_REF=${DEPLOYED_SHA}", promotion)
        self.assertIn("sudo systemctl restart quant-platform-live-ingest.service", promotion)
        self.assertLess(promotion.index("QP_LIVE_INGEST_CODE_REF=${DEPLOYED_SHA}"),
                        promotion.index("sudo systemctl restart quant-platform-live-ingest.service"))
        self.assertIn("ADR-0068 section 8", promotion)
        live_ingest = (DOCS / "LIVE_INGEST_SERVER_DEPLOYMENT.md").read_text(encoding="utf-8")
        self.assertIn("QP_LIVE_INGEST_CODE_REF=${DEPLOYED_SHA}", live_ingest, "the variable the runbook sets is that one")

    def test_both_runbooks_leave_every_unit_able_to_reach_its_files_in_either_order(self) -> None:
        # Two supported procedures create /etc/quant-platform: this runbook and the live-ingest one. Whichever runs
        # last, the directory must let all three service users search it, and the files each procedure installs must
        # stay unreadable to the others.
        parent = re.compile(r"^sudo install -d -m (0\d{3}) -o (\w+) -g (\w+) /etc/quant-platform$", re.MULTILINE)
        live = (DOCS / "LIVE_INGEST_SERVER_DEPLOYMENT.md").read_text(encoding="utf-8")
        mine = RUNBOOK.read_text(encoding="utf-8")
        install = mine[mine.index("## Install (once per host)"):mine.index("## Promotion")]
        steps = {"deck": parent.findall(install), "live": parent.findall(live)}
        for name, found in steps.items():
            self.assertEqual(1, len(found), f"{name}: one install of the shared directory")
        # qp-api is also in marketdata (the read group of the canonical data, see the canonical-data test below).
        groups = {"mkt-transform": {"marketdata"}, "qp-api": {"qp-api", "marketdata"}, "qp-worker": {"qp-worker"}}

        def can_search(user: str, mode: int, owner: str, group: str) -> bool:
            return bool(
                (mode & 0o001) or (mode & 0o010 and group in groups[user]) or (mode & 0o100 and owner == user)
            )

        for order in (("deck", "live"), ("live", "deck")):
            final = steps[order[-1]][0]
            mode, owner, group = int(final[0], 8), final[1], final[2]
            for user in groups:
                with self.subTest(order=order, user=user):
                    self.assertTrue(can_search(user, mode, owner, group), f"{user} cannot reach /etc/quant-platform")
            self.assertEqual(0, mode & 0o004, "others may search but not list the directory")
        self.assertEqual(steps["deck"], steps["live"], "both procedures set the same owner and mode")
        # The files each procedure installs there are not readable by others, whichever group owns them.
        files = re.compile(r"install -m (0\d{3}) -o root -g ([\w-]+)\s+\S+\s+(/etc/quant-platform/\S+)")
        installed = []
        for text in (install, live):
            installed += files.findall(text.replace("\\\n", " "))
        self.assertTrue({"/etc/quant-platform/api-server.env", "/etc/quant-platform/worker.env",
                         "/etc/quant-platform/live-ingest-server.env"} <= {target for _, _, target in installed})
        for mode, group, target in installed:
            if target.endswith(".env"):
                with self.subTest(target=target):
                    self.assertEqual(0, int(mode, 8) & 0o007, f"{target} readable by others")

    def test_the_install_procedure_lets_each_service_user_reach_the_files_it_opens(self) -> None:
        text = RUNBOOK.read_text(encoding="utf-8")
        install = text[text.index("## Install (once per host)"):text.index("## Promotion")]
        # The directory is shared by users of different groups: every one needs the search bit on it.
        mode = re.search(r"install -d -m (0\d{3}) -o root -g root /etc/quant-platform\n", install)
        self.assertIsNotNone(mode, "the shared directory is root:root and listed with its mode")
        self.assertTrue(int(mode.group(1), 8) & 0o001, "others can traverse /etc/quant-platform")
        # The live-ingest runbook creates the same directory the same way; the procedure says so (and a test below
        # runs both in either order).
        self.assertIn("live-ingest runbook creates", install)
        # Everything the API and the worker open, as the right user, and the segregation of secrets.
        api_opens = ("api.pgpass", "principals.json", "tls/server.crt", "tls/server.key", "tls/ca.crt")
        for name in api_opens:
            self.assertIn(name, install, name)
        self.assertIn('sudo -u qp-api test -r "$f"', install)
        self.assertIn("sudo -u qp-worker test -r /etc/quant-platform/worker.pgpass", install)
        self.assertIn("sudo -u qp-worker test -r /etc/quant-platform/tls/server.key", install)
        # The paths the environment examples open are the ones the procedure installs and checks.
        api_env = parse_environment(API_ENV)
        for variable in ("QP_API_TLS_CERTIFICATE", "QP_API_TLS_PRIVATE_KEY", "QP_API_CLIENT_TRUST_ANCHOR",
                         "QP_API_PRINCIPALS_FILE"):
            self.assertTrue(api_env[variable].startswith("/etc/quant-platform/"), variable)
            self.assertIn(api_env[variable].removeprefix("/etc/quant-platform/"), install, variable)
        self.assertIn("/etc/quant-platform/tls", install)
        self.assertIn("-g qp-api /etc/quant-platform/tls", install)
        live = parse_unit(MODEL)
        self.assertEqual("mkt-transform", single(live["Service"], "User"))
        self.assertIn("sudo -u mkt-transform test -r /etc/quant-platform/live-ingest-server.env", install)

    def test_the_api_can_read_and_never_write_the_canonical_partitions_the_provisioning_creates(self) -> None:
        # admitted-input opens the catalog's partition paths with stat() and read_bytes() as the API user; the
        # provisioned layout gives canonical/ to mkt-transform:marketdata 2750, so the API user needs that group.
        provision = (ROOT / "infra" / "provision-marketdata.sh").read_text(encoding="utf-8")
        match = re.search(r'for d in canonical canonical/trades[^;]*; do\n\s+dir "\$MD/\$d" (\S+)\s+(\d{4})\n', provision)
        self.assertIsNotNone(match, "the provisioning layout of canonical/")
        owner, mode = match.group(1), int(match.group(2), 8)
        group = re.search(r"groupadd --system (\w+)", provision).group(1)
        self.assertIn(f'acl "$MD/canonical" g:{group}:rx', provision)
        self.assertEqual("mkt-transform", owner)
        self.assertEqual(0o2750, mode)
        runbook = RUNBOOK.read_text(encoding="utf-8")
        install = runbook[runbook.index("## Install (once per host)"):runbook.index("## Promotion")]
        added = re.search(r"^sudo usermod -aG (\w+) qp-api$", install, re.MULTILINE)
        self.assertIsNotNone(added, "the API user joins the read group")
        api_groups = {"qp-api", added.group(1)}

        def access(groups: set[str], what: int) -> bool:  # what: 4 read, 2 write, 1 search
            return bool((mode & (what << 3)) and group in groups)

        self.assertTrue(access(api_groups, 4) and access(api_groups, 1), "the API reads and traverses")
        self.assertFalse(access(api_groups, 2), "the group gives no write")
        self.assertFalse(access({"qp-worker"}, 4) or access({"qp-worker"}, 1), "the worker has no access")
        self.assertEqual(0, mode & 0o007, "others have nothing: the data is not made world-readable")
        self.assertFalse(access({"qp-api"}, 4), "without the group the documented install fails to read")
        # The API unit adds no write path and keeps the read-only file system.
        service = self.api["Service"]
        self.assertNotIn("ReadWritePaths", service)
        self.assertEqual("strict", single(service, "ProtectSystem"))
        # The procedure checks the access before the first start: read, no write, and the worker kept out.
        for check in ('sudo -u qp-api test -r "$root" -a -x "$root"', 'sudo -u qp-api test -w "$root" &&',
                      'sudo -u qp-worker test -x "$root" &&', 'sudo -u qp-api head -c 1 "$sample"'):
            self.assertIn(check, install, check)

    def test_the_documents_describe_the_result_check_k13_performs_not_a_stronger_one(self) -> None:
        source = (ROOT / "src" / "quant_platform" / "application" / "governed_result_import.py").read_text(
            encoding="utf-8")
        self.assertIn("bundle.deck_code_identity != record.manifest.git_identity", source)
        self.assertIn("run_spec.code_identity.identity != manifest.git_identity", source)
        # If the import ever reads the current deployment record, these documents must change with it.
        self.assertNotIn("deployment_commit", source)
        compat = COMPATIBILITY.read_text(encoding="utf-8")
        runbook = RUNBOOK.read_text(encoding="utf-8")
        self.assertIn("does **not** compare\n   it with the API's *current* commit", compat)
        self.assertIn("operator obligation", compat)
        self.assertIn("does **not** compare it with the API's current commit", runbook)
        self.assertIn("abandon earlier admissions", runbook)
        self.assertIn("A promotion does not make the server refuse an older admission", runbook)
        for text in (compat, runbook):
            self.assertNotIn("the commit it recorded)", text)
            self.assertNotIn("the commit no longer matches", text)

    def test_the_runbook_states_the_worker_exit_rule(self) -> None:
        text = RUNBOOK.read_text(encoding="utf-8")
        self.assertIn("**exits `3`**", text)
        self.assertIn("before it listens", text)
        self.assertIn("`Restart=on-failure` restarts", text)
        self.assertIn("does not mask the refusal", text)

    def test_the_compatibility_rule_is_the_three_statements_of_the_adr(self) -> None:
        text = COMPATIBILITY.read_text(encoding="utf-8")
        self.assertIn("for **every message family the deck speaks**", text)
        self.assertIn("the version it speaks is the\n   version the server composes", text)
        self.assertIn("accepts a result only when the deck's commit equals the commit of its\n   admission", text)
        self.assertIn("The package version decides nothing", text)

    def test_the_compatibility_table_names_the_versions_in_the_code(self) -> None:
        text = COMPATIBILITY.read_text(encoding="utf-8")
        sources = {
            "ADMITTED_INPUT_FAMILY": ROOT / "clients" / "deck" / "deck_handoff_client.py",
            "RESULT_IMPORT_FAMILY": ROOT / "clients" / "deck" / "deck_handoff_client.py",
            "J14_FAMILY": ROOT / "clients" / "deck" / "deck_handoff_client.py",
            "J02_STRATEGY_FAMILY": ROOT / "src/quant_platform/application/strategy_transport.py",
            "J02_VALIDATION_FAMILY": ROOT / "src/quant_platform/application/validation_transport.py",
            "J02_TRAINING_FAMILY": ROOT / "src/quant_platform/application/training_transport.py",
            "J02_JOB_FAMILY": ROOT / "src/quant_platform/application/job_transport.py",
        }
        for constant, path in sources.items():
            match = re.search(rf'^{constant}\s*=\s*"([^"]+)"', path.read_text(encoding="utf-8"), re.MULTILINE)
            with self.subTest(constant):
                self.assertIsNotNone(match, constant)
                self.assertIn(f"`{match.group(1)}`", text)

    def test_the_runbook_covers_the_deck_procedure_and_the_amendment_1_failure_procedures(self) -> None:
        text = RUNBOOK.read_text(encoding="utf-8")
        steps = ("**Select a commit.**", "**`admit`.**", "**`deliver` and verify.**", "**Run.**",
                 "**Build the result.**", "**`upload_output`.**", "**`submit`.**")
        positions = [text.index(step) for step in steps]
        self.assertEqual(sorted(positions), positions, "the steps are in the order of the procedure")
        self.assertIn("**Record it**", text)
        self.assertIn("**Lost response", text)
        self.assertIn("end_pending_attempt(admission_id)", text)
        self.assertIn("**Stale acknowledgement.**", text)
        self.assertIn("`delivery_attempt_mismatch`", text)
        self.assertIn("**it must not be repeated**", text)

    def test_j14_is_described_as_a_transport_not_as_a_configurable_family(self) -> None:
        from quant_platform.application.api_host_composition import (
            ApiHostCompositionError, FAMILY_NAMES, parse_families,
        )
        text = COMPATIBILITY.read_text(encoding="utf-8")
        j14 = re.search(r'^J14_FAMILY\s*=\s*"([^"]+)"',
                        (ROOT / "clients" / "deck" / "deck_handoff_client.py").read_text(encoding="utf-8"),
                        re.MULTILINE).group(1)
        with self.assertRaises(ApiHostCompositionError) as refused:
            parse_families(j14)
        self.assertTrue(str(refused.exception).startswith("unknown_family"))
        for family in FAMILY_NAMES:
            self.assertIn(f"`{family}`", text, family)
        self.assertIn("is\n**not** a value of `QP_API_FAMILIES`", text)
        # The J14 store is built where admitted-input is composed, and nowhere else.
        host = (ROOT / "src" / "quant_platform" / "application" / "api_host_composition.py").read_text(encoding="utf-8")
        self.assertEqual(1, host.count("FramedResultTransferStore("))
        self.assertRegex(host, r'if "admitted-input" in families:\n\s+transfers = FramedResultTransferStore')

    def test_the_client_methods_the_runbook_names_exist(self) -> None:
        client = (ROOT / "clients" / "deck" / "deck_handoff_client.py").read_text(encoding="utf-8")
        for name in ("from_credentials", "admit", "fetch_inputs", "end_pending_attempt", "upload_output", "submit",
                     "manifest"):
            self.assertRegex(client, rf"def {name}\(", name)
        module = (ROOT / "src" / "quant_platform" / "application" / "deck_result_bundle.py").read_text(
            encoding="utf-8")
        for name in ("inspect_workload_tree", "build_deck_result"):
            self.assertRegex(module, rf"def {name}\(", name)
        self.assertIn("delivery_attempt_mismatch", (ROOT / "src").joinpath(
            "quant_platform", "application", "admitted_input_transport.py").read_text(encoding="utf-8")
            + (ROOT / "src" / "quant_platform" / "application" / "admitted_input.py").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
