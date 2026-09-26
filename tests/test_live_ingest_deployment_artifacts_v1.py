from __future__ import annotations

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parent.parent
SERVICE = ROOT / "infra" / "systemd" / "quant-platform-live-ingest.service"
ENV_EXAMPLE = ROOT / "infra" / "systemd" / "quant-platform-live-ingest.env.example"
RUNBOOK = ROOT / "docs" / "engineering" / "LIVE_INGEST_SERVER_DEPLOYMENT.md"


class LiveIngestDeploymentArtifactTests(unittest.TestCase):
    def test_systemd_unit_runs_existing_server_under_k02_identity(self):
        text = SERVICE.read_text(encoding="utf-8")

        self.assertIn("User=mkt-transform", text)
        self.assertIn("Group=marketdata", text)
        self.assertIn("WorkingDirectory=/opt/market-platform", text)
        self.assertIn("/opt/market-platform/tools/live_ingest_server.py", text)
        self.assertIn("--checkpoint-path ${QP_LIVE_INGEST_CHECKPOINT_PATH}", text)
        self.assertIn("Restart=on-failure", text)
        self.assertIn("KillSignal=SIGTERM", text)
        self.assertIn("NoNewPrivileges=true", text)
        self.assertNotIn("--max-cycles", text)
        self.assertNotIn("PGPASSWORD", text)

    def test_env_example_is_non_secret_and_complete_for_cli(self):
        text = ENV_EXAMPLE.read_text(encoding="utf-8")

        for name in (
            "PGHOST=127.0.0.1",
            "PGPORT=5433",
            "PGUSER=market_catalog_writer",
            "PGDATABASE=market_catalog",
            "PGPASSFILE=/home/mkt-transform/.pgpass",
            "QP_LIVE_INGEST_STORAGE_ROOT=/srv/marketdata",
            "QP_LIVE_INGEST_STORAGE_ROOT_ID=hot",
            "QP_LIVE_INGEST_CHECKPOINT_PATH=",
            "QP_LIVE_INGEST_CODE_REF=REPLACE_WITH_DEPLOYED_GIT_SHA",
            "QP_LIVE_INGEST_PRODUCER=live-ingest-server-v1",
            "QP_LIVE_INGEST_RECENT_LIMIT=1000",
            "QP_LIVE_INGEST_MAX_MESSAGES_PER_CYCLE=1000",
            "QP_LIVE_INGEST_MAX_SECONDS_PER_CYCLE=20",
            "QP_LIVE_INGEST_CYCLE_INTERVAL_SECONDS=5",
        ):
            self.assertIn(name, text)

        self.assertNotIn("PGPASSWORD", text)
        self.assertNotIn("****", text)

    def test_runbook_preserves_git_promotion_and_scope_boundaries(self):
        text = RUNBOOK.read_text(encoding="utf-8")

        self.assertIn("GitHub `origin` as the code authority", text)
        self.assertIn("test -z \"$(git status --porcelain)\"", text)
        self.assertIn("git merge --ff-only", text)
        self.assertIn("Do not add `PGPASSWORD`", text)
        self.assertIn("user=mkt-transform", text)
        self.assertIn("is_root=False", text)
        self.assertIn("B06 live consumer cursors", text)
        self.assertIn("Do not run", text)
        self.assertIn("git reset --hard", text)


if __name__ == "__main__":
    unittest.main()
