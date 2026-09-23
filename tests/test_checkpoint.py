from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from auto_checkpoint import cli


def command(*args: str, cwd: Path | None = None, check: bool = True):
    return subprocess.run(
        args,
        cwd=cwd,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=check,
    )


class Fixture:
    def __init__(self, root: Path, name: str = "work"):
        self.repo = root / name
        self.remote = root / f"{name}.git"
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
        (self.repo / "base.txt").write_text("base\n")
        command("git", "-C", str(self.repo), "add", "--", "base.txt")
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

    def invoke(self, *extra: str) -> int:
        return cli.main(
            [
                "--state-root",
                str(self.state),
                "checkpoint",
                "--repo",
                str(self.repo),
                "--task-id",
                "TEST-1",
                "--message",
                "safe integration checkpoint",
                *extra,
            ]
        )

    def manifest(self) -> tuple[Path, dict]:
        path = next(self.state.glob("*/*/manifest.json"))
        return path, json.loads(path.read_text())


class CheckpointTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def test_default_state_root_is_generic(self):
        old = os.environ.get("XDG_STATE_HOME")
        try:
            os.environ["XDG_STATE_HOME"] = "/tmp/example-state"
            self.assertEqual(
                cli.default_state_root(),
                Path("/tmp/example-state/auto-checkpoint"),
            )
        finally:
            if old is None:
                os.environ.pop("XDG_STATE_HOME", None)
            else:
                os.environ["XDG_STATE_HOME"] = old

    def test_task_file_selection_and_wip_preservation(self):
        f = Fixture(self.root)
        (f.repo / "task.txt").write_text("task\n")
        (f.repo / "wip.txt").write_text("wip\n")
        self.assertEqual(
            f.invoke(
                "--file",
                "task.txt",
                "--preserve-wip",
                "wip.txt",
            ),
            0,
        )
        names = command(
            "git",
            "-C",
            str(f.repo),
            "show",
            "--pretty=",
            "--name-only",
            "HEAD",
        ).stdout.split()
        self.assertEqual(names, ["task.txt"])
        self.assertTrue((f.repo / "wip.txt").exists())

    def test_unknown_file_blocks(self):
        f = Fixture(self.root)
        (f.repo / "task.txt").write_text("task\n")
        (f.repo / "mystery.txt").write_text("unknown\n")
        self.assertEqual(f.invoke("--file", "task.txt"), 2)
        self.assertFalse(f.state.exists())

    def test_secret_and_private_rejection(self):
        f = Fixture(self.root)
        private_names = (
            ".env",
            "access-token.txt",
            "calendar-token.json",
            "client_secret-pilot.json",
            "id_rsa",
            "id_ed25519",
        )
        for name in private_names:
            with self.subTest(name=name):
                (f.repo / name).write_text("private test fixture\n")
                self.assertEqual(f.invoke("--file", name), 2)
                (f.repo / name).unlink()

        private = f.repo / "private.txt"
        private.write_text("not a secret\n")
        os.chmod(private, 0o600)
        self.assertEqual(f.invoke("--file", "private.txt"), 2)

    def test_secret_pattern_blocks(self):
        f = Fixture(self.root)
        (f.repo / "task.txt").write_text(
            "password=abcdefghijklmnop\n"
        )
        self.assertEqual(f.invoke("--file", "task.txt"), 2)

    def test_backup_manifest_and_sha256(self):
        f = Fixture(self.root)
        (f.repo / "task.txt").write_text("content\n")
        self.assertEqual(
            f.invoke(
                "--file",
                "task.txt",
                "--validate",
                "test -f task.txt",
            ),
            0,
        )
        path, data = f.manifest()
        self.assertEqual(data["validation"], "PASS")
        self.assertEqual(
            data["files"]["task.txt"],
            cli.sha256(path.parent / "files/task.txt"),
        )
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_validation_failure_creates_no_commit_or_backup(self):
        f = Fixture(self.root)
        before = command(
            "git",
            "-C",
            str(f.repo),
            "rev-parse",
            "HEAD",
        ).stdout
        (f.repo / "task.txt").write_text("content\n")
        self.assertEqual(
            f.invoke("--file", "task.txt", "--validate", "false"),
            2,
        )
        self.assertEqual(
            command(
                "git",
                "-C",
                str(f.repo),
                "rev-parse",
                "HEAD",
            ).stdout,
            before,
        )
        self.assertFalse(f.state.exists())

    def test_commit_and_push_heads_equal(self):
        f = Fixture(self.root)
        (f.repo / "task.txt").write_text("content\n")
        self.assertEqual(f.invoke("--file", "task.txt"), 0)

        local = command(
            "git",
            "-C",
            str(f.repo),
            "rev-parse",
            "HEAD",
        ).stdout.strip()
        remote = command(
            "git",
            "--git-dir",
            str(f.remote),
            "rev-parse",
            "refs/heads/main",
        ).stdout.strip()

        self.assertEqual(local, remote)
        self.assertIn(
            "task(TEST-1):",
            command(
                "git",
                "-C",
                str(f.repo),
                "log",
                "-1",
                "--pretty=%s",
            ).stdout,
        )

    def test_push_failure_queue_and_retry(self):
        f = Fixture(self.root)
        (f.repo / "task.txt").write_text("content\n")
        command(
            "git",
            "-C",
            str(f.repo),
            "remote",
            "set-url",
            "--push",
            "origin",
            str(self.root / "missing/remote.git"),
        )

        self.assertEqual(f.invoke("--file", "task.txt"), 3)
        _, data = f.manifest()
        self.assertTrue(data["remote_sync_pending"])

        command(
            "git",
            "-C",
            str(f.repo),
            "remote",
            "set-url",
            "--push",
            "origin",
            str(f.remote),
        )
        self.assertEqual(
            cli.main(
                [
                    "--state-root",
                    str(f.state),
                    "retry-push",
                ]
            ),
            0,
        )
        self.assertFalse((f.state / "pending-push.jsonl").exists())

    def test_multi_repo_independent_commits(self):
        a = Fixture(self.root, "repo-a")
        b = Fixture(self.root, "repo-b")
        b.state = a.state

        for f in (a, b):
            (f.repo / "task.txt").write_text(f.repo.name + "\n")
            self.assertEqual(f.invoke("--file", "task.txt"), 0)

        commits = [
            command(
                "git",
                "-C",
                str(f.repo),
                "rev-parse",
                "HEAD",
            ).stdout
            for f in (a, b)
        ]
        self.assertNotEqual(*commits)
        self.assertEqual(
            len(list(a.state.glob("*/*/manifest.json"))),
            2,
        )

    def test_recovery_list_verify_restore_and_commit(self):
        f = Fixture(self.root)
        (f.repo / "task.txt").write_text("recover me\n")
        self.assertEqual(f.invoke("--file", "task.txt"), 0)
        path, _ = f.manifest()
        checkpoint = path.parent.name

        self.assertEqual(
            cli.main(
                [
                    "--state-root",
                    str(f.state),
                    "recovery",
                    "list",
                ]
            ),
            0,
        )
        self.assertEqual(
            cli.main(
                [
                    "--state-root",
                    str(f.state),
                    "recovery",
                    "verify",
                    checkpoint,
                ]
            ),
            0,
        )

        target = self.root / "restore"
        self.assertEqual(
            cli.main(
                [
                    "--state-root",
                    str(f.state),
                    "recovery",
                    "restore-files",
                    checkpoint,
                    "--target",
                    str(target),
                    "--file",
                    "task.txt",
                ]
            ),
            0,
        )
        self.assertEqual(
            (target / "task.txt").read_text(),
            "recover me\n",
        )

        self.assertEqual(
            cli.main(
                [
                    "--state-root",
                    str(f.state),
                    "recovery",
                    "recover-commit",
                    checkpoint,
                    "--repo",
                    str(f.repo),
                ]
            ),
            0,
        )

    def test_restore_does_not_overwrite_by_default(self):
        f = Fixture(self.root)
        (f.repo / "task.txt").write_text("checkpoint\n")
        self.assertEqual(f.invoke("--file", "task.txt"), 0)
        path, _ = f.manifest()
        checkpoint = path.parent.name

        target = self.root / "restore"
        target.mkdir()
        (target / "task.txt").write_text("keep me\n")

        self.assertEqual(
            cli.main(
                [
                    "--state-root",
                    str(f.state),
                    "recovery",
                    "restore-files",
                    checkpoint,
                    "--target",
                    str(target),
                    "--file",
                    "task.txt",
                ]
            ),
            2,
        )
        self.assertEqual(
            (target / "task.txt").read_text(),
            "keep me\n",
        )

    def test_git_diff_check_rejects_whitespace(self):
        f = Fixture(self.root)
        (f.repo / "task.txt").write_text("bad trailing space \n")
        self.assertEqual(f.invoke("--file", "task.txt"), 2)

    def test_explicit_git_add_and_no_force_push(self):
        source = Path(cli.__file__).read_text()
        self.assertNotIn('"add", "."', source)
        self.assertNotIn('"add", "-A"', source)
        self.assertNotIn('"--force"', source)
        self.assertIn(
            'git(repo, "add", "--", *sorted(task_files))',
            source,
        )

    def test_remote_url_credentials_are_sanitized(self):
        self.assertEqual(
            cli.sanitize_url(
                "https://user:secret@example.invalid/repo.git"
            ),
            "https://example.invalid/repo.git",
        )
        self.assertEqual(
            cli.sanitize_url(
                "git@example.invalid:owner/repo.git"
            ),
            "example.invalid:owner/repo.git",
        )

    def test_offsite_copy_and_one_way_check(self):
        f = Fixture(self.root)
        log = self.root / "rclone.log"
        fake = self.root / "rclone"
        fake.write_text(
            "#!/bin/sh\n"
            "printf '%s\\n' \"$*\" >> \"$RCLONE_TEST_LOG\"\n"
            "exit 0\n"
        )
        fake.chmod(0o700)
        os.environ["RCLONE_TEST_LOG"] = str(log)

        (f.repo / "task.txt").write_text("content\n")
        self.assertEqual(
            f.invoke(
                "--file",
                "task.txt",
                "--offsite",
                "TestRemote:checkpoints",
                "--rclone-bin",
                str(fake),
            ),
            0,
        )

        calls = log.read_text()
        self.assertEqual(calls.count("copy "), 2)
        self.assertEqual(calls.count("check "), 2)
        self.assertIn("--one-way", calls)
        self.assertNotIn("sync", calls)

        manifest_path, data = f.manifest()
        self.assertEqual(data["offsite_backup_status"], "PASS")
        self.assertEqual(
            json.loads(
                manifest_path.read_text()
            )["offsite_backup_status"],
            "PASS",
        )


if __name__ == "__main__":
    unittest.main()
