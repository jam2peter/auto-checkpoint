from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from auto_checkpoint import cli


def command(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        args,
        text=True,
        capture_output=True,
        check=check,
    )


class QualityFixture:
    def __init__(self, root: Path):
        self.repo = root / "work"
        self.remote = root / "remote.git"
        self.state = root / "state"
        self.repo.mkdir()

        command("git", "init", "-b", "main", str(self.repo))
        command("git", "init", "--bare", str(self.remote))
        command("git", "-C", str(self.repo), "config", "user.name", "Test")
        command(
            "git",
            "-C",
            str(self.repo),
            "config",
            "user.email",
            "test@example.invalid",
        )

        (self.repo / "base.txt").write_text("base\n", encoding="utf-8")
        (self.repo / ".gitignore").write_text(
            ".quality-gate/\n",
            encoding="utf-8",
        )
        config_dir = self.repo / ".jampeter"
        config_dir.mkdir()
        (config_dir / "quality-gate.toml").write_text(
            """version = 1
profile = "integration-test"

[report]
path = ".quality-gate/report.json"

[[gate]]
id = "tests"
phase = "test"
command = "true"
required = true
""",
            encoding="utf-8",
        )

        command("git", "-C", str(self.repo), "add", ".")
        command("git", "-C", str(self.repo), "commit", "-m", "initial")
        command(
            "git",
            "-C",
            str(self.repo),
            "remote",
            "add",
            "origin",
            str(self.remote),
        )
        command("git", "-C", str(self.repo), "push", "-u", "origin", "main")

    def quality(self) -> None:
        command(
            "quality-gate",
            "run",
            "--repo",
            str(self.repo),
            "--config",
            ".jampeter/quality-gate.toml",
        )

    def checkpoint(self) -> int:
        return cli.main(
            [
                "--state-root",
                str(self.state),
                "checkpoint",
                "--repo",
                str(self.repo),
                "--task-id",
                "QG-1",
                "--message",
                "quality gate integration",
                "--file",
                "task.txt",
                "--quality-report",
                ".quality-gate/report.json",
                "--quality-config",
                ".jampeter/quality-gate.toml",
            ]
        )


class QualityGateIntegrationTests(unittest.TestCase):
    def setUp(self):
        if shutil.which("quality-gate") is None:
            self.skipTest("quality-gate executable is not installed")
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def test_current_pass_report_allows_checkpoint(self):
        fixture = QualityFixture(self.root)
        (fixture.repo / "task.txt").write_text("validated\n", encoding="utf-8")
        fixture.quality()

        self.assertEqual(fixture.checkpoint(), 0)

        manifest_path = next(fixture.state.glob("*/*/manifest.json"))
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        self.assertEqual(manifest["quality_gate"]["status"], "PASS")
        self.assertTrue(manifest["quality_gate"]["git_state_fingerprint"])

    def test_stale_report_blocks_before_commit(self):
        fixture = QualityFixture(self.root)
        (fixture.repo / "task.txt").write_text("validated\n", encoding="utf-8")
        fixture.quality()

        head_before = command(
            "git",
            "-C",
            str(fixture.repo),
            "rev-parse",
            "HEAD",
        ).stdout.strip()

        (fixture.repo / "task.txt").write_text(
            "changed after quality gate\n",
            encoding="utf-8",
        )

        self.assertEqual(fixture.checkpoint(), 2)
        head_after = command(
            "git",
            "-C",
            str(fixture.repo),
            "rev-parse",
            "HEAD",
        ).stdout.strip()

        self.assertEqual(head_before, head_after)
        self.assertFalse(fixture.state.exists())


if __name__ == "__main__":
    unittest.main()
