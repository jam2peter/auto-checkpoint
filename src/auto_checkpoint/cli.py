from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from urllib.parse import urlsplit, urlunsplit

BLOCKED_NAMES = {
    ".env",
    "rclone.conf",
    "client_secret.json",
    "calendar-token.json",
    "id_rsa",
    "id_ed25519",
    "credentials.json",
}
BLOCKED_PARTS = ("token", "client_secret", "private_key", "credential", "oauth")
SECRET_PATTERNS = (
    re.compile(rb"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    re.compile(
        rb"(?i)(?:api[_-]?key|secret|password|access[_-]?token)"
        rb"\s*[:=]\s*['\"]?[A-Za-z0-9_./+\-]{12,}"
    ),
    re.compile(rb"gh[pousr]_[A-Za-z0-9]{20,}"),
)


class Blocked(RuntimeError):
    pass


def default_state_root() -> Path:
    xdg = os.environ.get("XDG_STATE_HOME", "").strip()
    if xdg:
        return Path(xdg).expanduser() / "auto-checkpoint"
    return Path.home() / ".local" / "state" / "auto-checkpoint"


def run(
    cmd: list[str],
    cwd: Path | None = None,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        cmd,
        cwd=cwd,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=check,
    )


def git(
    repo: Path,
    *args: str,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    return run(["git", "-C", str(repo), *args], check=check)


def safe_rel(value: str) -> str:
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts or str(path) in ("", "."):
        raise Blocked(f"invalid task file: {value}")
    return str(path)


def sanitize_url(url: str) -> str:
    if "://" in url:
        parts = urlsplit(url)
        host = parts.hostname or ""
        if parts.port:
            host += f":{parts.port}"
        return urlunsplit((parts.scheme, host, parts.path, parts.query, ""))
    if "@" in url and ":" in url:
        return url.split("@", 1)[1]
    return url


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def changed_paths(repo: Path) -> set[str]:
    out = git(repo, "status", "--porcelain=v1", "-z").stdout
    fields = out.split("\0")
    result: set[str] = set()
    i = 0
    while i < len(fields) and fields[i]:
        entry = fields[i]
        code, path = entry[:2], entry[3:]
        if code[0] in "RC" or code[1] in "RC":
            i += 1
            path = fields[i]
        result.add(path)
        i += 1
    return result


def private_reason(path: Path) -> str | None:
    low = path.name.lower()
    if low in BLOCKED_NAMES or any(part in low for part in BLOCKED_PARTS):
        return "blocked filename"
    if (
        path.exists()
        and not path.is_symlink()
        and stat.S_IMODE(path.stat().st_mode) == 0o600
    ):
        return "private mode 0600"
    return None


def scan_file(path: Path) -> str | None:
    if not path.exists():
        return None
    if path.is_symlink() or not path.is_file():
        return "symlink or non-regular file"
    data = path.read_bytes()
    for pattern in SECRET_PATTERNS:
        if pattern.search(data):
            return "secret pattern"
    return None


def repository_guard(repo: Path) -> tuple[str, str]:
    top = Path(git(repo, "rev-parse", "--show-toplevel").stdout.strip()).resolve()
    if top != repo.resolve():
        raise Blocked("repository path is not its Git top level")

    if git(repo, "diff", "--cached", "--quiet", check=False).returncode != 0:
        raise Blocked("pre-existing staged changes")

    for marker in ("MERGE_HEAD", "REBASE_HEAD", "CHERRY_PICK_HEAD", "REVERT_HEAD"):
        if git(
            repo,
            "rev-parse",
            "-q",
            "--verify",
            marker,
            check=False,
        ).returncode == 0:
            raise Blocked(f"Git operation pending: {marker}")

    branch = git(
        repo,
        "symbolic-ref",
        "--short",
        "HEAD",
        check=False,
    ).stdout.strip()
    if not branch:
        raise Blocked("detached HEAD")

    head = git(repo, "rev-parse", "HEAD").stdout.strip()
    return branch, head


def resolve_remote(repo: Path, name: str, branch: str) -> tuple[str, str]:
    url_result = git(repo, "remote", "get-url", "--push", name, check=False)
    if url_result.returncode:
        raise Blocked(f"remote unavailable: {name}")
    url = url_result.stdout.strip()

    upstream = git(
        repo,
        "rev-parse",
        "--abbrev-ref",
        "--symbolic-full-name",
        "@{upstream}",
        check=False,
    )
    if upstream.returncode == 0 and upstream.stdout.strip() != f"{name}/{branch}":
        raise Blocked(f"unexpected upstream: {upstream.stdout.strip()}")

    remote_head = git(
        repo,
        "ls-remote",
        "--heads",
        name,
        f"refs/heads/{branch}",
        check=False,
    )
    if remote_head.returncode == 0 and remote_head.stdout.strip():
        sha = remote_head.stdout.split()[0]
        if (
            git(
                repo,
                "merge-base",
                "--is-ancestor",
                sha,
                "HEAD",
                check=False,
            ).returncode
            != 0
        ):
            raise Blocked("remote branch diverges from local HEAD")

    return url, "AVAILABLE" if remote_head.returncode == 0 else "UNAVAILABLE"


def write_json(path: Path, value: dict) -> None:
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(
        json.dumps(value, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.chmod(temp, 0o600)
    temp.replace(path)


def validate_task_files(repo: Path, task_files: set[str]) -> None:
    diff_check = git(
        repo,
        "diff",
        "--check",
        "--",
        *sorted(task_files),
        check=False,
    )
    if diff_check.returncode:
        raise Blocked("CHECKPOINT_STATUS=BLOCKED_VALIDATION git diff --check")

    for rel in sorted(task_files):
        path = repo / rel
        if not path.exists():
            continue
        data = path.read_bytes()
        for line in data.splitlines():
            if line.endswith((b" ", b"\t")) or line.startswith(
                (b"<<<<<<< ", b"=======", b">>>>>>> ")
            ):
                raise Blocked(
                    "CHECKPOINT_STATUS=BLOCKED_VALIDATION "
                    f"git diff --check file={rel}"
                )


def checkpoint(args: argparse.Namespace) -> int:
    repo = Path(args.repo).resolve()
    state_root = Path(args.state_root).expanduser().resolve()

    branch, head_before = repository_guard(repo)

    task_files = {safe_rel(item) for item in args.file}
    preserved = {safe_rel(item) for item in args.preserve_wip}
    generated = {safe_rel(item) for item in args.generated}
    actual = changed_paths(repo)

    unknown = sorted(actual - task_files - preserved - generated)
    missing = sorted(task_files - actual)

    if unknown or missing:
        raise Blocked(f"UNKNOWN_FILES={','.join(unknown + missing)}")
    if not task_files:
        raise Blocked("no TASK_FILES")

    for rel in sorted(task_files):
        path = repo / rel
        reason = private_reason(path) or scan_file(path)
        if reason:
            raise Blocked(f"SECRET_OR_PRIVATE={rel} ({reason})")

    validate_task_files(repo, task_files)

    test_results = []
    for command in args.validate:
        proc = subprocess.run(
            command,
            cwd=repo,
            shell=True,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
        test_results.append(
            {
                "command": command,
                "status": "PASS" if proc.returncode == 0 else "FAIL",
            }
        )
        if proc.returncode:
            raise Blocked(
                f"CHECKPOINT_STATUS=BLOCKED_VALIDATION command={command}"
            )

    remote_url, remote_access = resolve_remote(repo, args.remote, branch)

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    repo_name = re.sub(r"[^A-Za-z0-9._-]", "_", repo.name)
    backup = state_root / repo_name / timestamp
    backup.mkdir(parents=True, mode=0o700)
    os.chmod(state_root, 0o700)
    os.chmod(backup.parent, 0o700)

    files_dir = backup / "files"
    files_dir.mkdir(mode=0o700)

    hashes: dict[str, str | None] = {}
    for rel in sorted(task_files):
        src, dst = repo / rel, files_dir / rel
        if src.exists():
            dst.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            shutil.copy2(src, dst, follow_symlinks=False)
            os.chmod(dst, 0o600)
            hashes[rel] = sha256(dst)
        else:
            hashes[rel] = None

    git_state = backup / "git-state.txt"
    git_state.write_text(
        f"branch={branch}\n"
        f"head_before={head_before}\n"
        f"TASK_FILES={','.join(sorted(task_files))}\n"
        f"WIPS_PRESERVED={','.join(sorted(preserved))}\n"
        "UNKNOWN_FILES=\n",
        encoding="utf-8",
    )
    os.chmod(git_state, 0o600)

    manifest = {
        "schema_version": 1,
        "timestamp": timestamp,
        "project": repo.name,
        "repo_path": str(repo),
        "task_id": args.task_id,
        "branch": branch,
        "head_before": head_before,
        "files": hashes,
        "tests": test_results,
        "validation": "PASS",
        "commit_message": args.message,
        "git_remote": {
            "name": args.remote,
            "url_sanitized": sanitize_url(remote_url),
            "access": remote_access,
        },
        "wips_preserved": sorted(preserved),
        "generated_files": sorted(generated),
        "unknown_files": [],
        "commit_hash": None,
        "push_status": "NOT_ATTEMPTED",
        "local_head": None,
        "remote_head": None,
        "sync_status": "NOT_ATTEMPTED",
        "remote_sync_pending": False,
        "offsite_backup_status": "SKIPPED",
    }
    manifest_path = backup / "manifest.json"
    write_json(manifest_path, manifest)

    git(repo, "add", "--", *sorted(task_files))
    try:
        staged_raw = git(
            repo,
            "diff",
            "--cached",
            "--name-only",
            "-z",
        ).stdout.strip("\0")
        staged = set(staged_raw.split("\0")) if staged_raw else set()
        if staged != task_files:
            raise Blocked("staged file set differs from TASK_FILES")
        git(
            repo,
            "commit",
            "-m",
            f"task({args.task_id}): {args.message}",
        )
    except Exception:
        git(
            repo,
            "restore",
            "--staged",
            "--",
            *sorted(task_files),
            check=False,
        )
        raise

    commit_hash = git(repo, "rev-parse", "HEAD").stdout.strip()
    manifest["commit_hash"] = commit_hash
    manifest["local_head"] = commit_hash

    push = git(repo, "push", args.remote, branch, check=False)
    if push.returncode:
        manifest["push_status"] = "FAILED"
        manifest["sync_status"] = "PENDING"
        manifest["remote_sync_pending"] = True

        queue = state_root / "pending-push.jsonl"
        with queue.open("a", encoding="utf-8") as handle:
            handle.write(
                json.dumps(
                    {
                        "repo": str(repo),
                        "remote": args.remote,
                        "branch": branch,
                        "commit": commit_hash,
                        "manifest": str(manifest_path),
                    }
                )
                + "\n"
            )
        os.chmod(queue, 0o600)
    else:
        remote_after = git(
            repo,
            "ls-remote",
            "--heads",
            args.remote,
            f"refs/heads/{branch}",
            check=False,
        )
        remote_hash = (
            remote_after.stdout.split()[0]
            if remote_after.returncode == 0 and remote_after.stdout
            else None
        )
        manifest["push_status"] = (
            "PASS" if remote_hash == commit_hash else "FAILED_VERIFY"
        )
        manifest["remote_head"] = remote_hash
        manifest["sync_status"] = (
            "PASS" if remote_hash == commit_hash else "MISMATCH"
        )
        manifest["remote_sync_pending"] = remote_hash != commit_hash

    if args.offsite:
        manifest["offsite_backup_status"] = "IN_PROGRESS"
        write_json(manifest_path, manifest)

        offsite = run(
            [args.rclone_bin, "copy", str(backup), args.offsite],
            check=False,
        )
        if offsite.returncode == 0:
            checked = run(
                [
                    args.rclone_bin,
                    "check",
                    str(backup),
                    args.offsite,
                    "--one-way",
                ],
                check=False,
            )
            if checked.returncode == 0:
                manifest["offsite_backup_status"] = "PASS"
                write_json(manifest_path, manifest)

                final_copy = run(
                    [args.rclone_bin, "copy", str(backup), args.offsite],
                    check=False,
                )
                final_check = run(
                    [
                        args.rclone_bin,
                        "check",
                        str(backup),
                        args.offsite,
                        "--one-way",
                    ],
                    check=False,
                )
                if final_copy.returncode or final_check.returncode:
                    manifest["offsite_backup_status"] = "FAILED_FINAL_VERIFY"
            else:
                manifest["offsite_backup_status"] = "FAILED_VERIFY"
        else:
            manifest["offsite_backup_status"] = "FAILED_COPY"

    write_json(manifest_path, manifest)

    print(f"TASK_FILES={','.join(sorted(task_files))}")
    print(f"WIPS_PRESERVED={','.join(sorted(preserved))}")
    print("UNKNOWN_FILES=")
    print("VALIDATION=PASS")
    print(f"BACKUP_CREATED={backup}")
    print("COMMIT_CREATED=YES")
    print(f"COMMIT_HASH={commit_hash}")
    print(f"PUSH_STATUS={manifest['push_status']}")
    print(f"LOCAL_HEAD={manifest['local_head']}")
    print(f"REMOTE_HEAD={manifest['remote_head'] or 'UNKNOWN'}")
    print(f"SYNC_STATUS={manifest['sync_status']}")
    print("LOCAL_CHECKPOINT_SAFE=YES")
    print(
        "REMOTE_SYNC_PENDING="
        + ("YES" if manifest["remote_sync_pending"] else "NO")
    )
    print(f"OFFSITE_BACKUP_STATUS={manifest['offsite_backup_status']}")
    print(f"RECOVERY_POINT={backup}")

    offsite_ok = (
        not args.offsite or manifest["offsite_backup_status"] == "PASS"
    )
    return 0 if manifest["push_status"] == "PASS" and offsite_ok else 3


def manifests(root: Path) -> list[Path]:
    return sorted(root.glob("*/*/manifest.json"), reverse=True)


def find_checkpoint(root: Path, checkpoint_id: str) -> Path:
    matches = [
        path
        for path in manifests(root)
        if path.parent.name == checkpoint_id or str(path.parent) == checkpoint_id
    ]
    if len(matches) != 1:
        raise Blocked("checkpoint not found or ambiguous")
    return matches[0]


def retry(args: argparse.Namespace) -> int:
    queue = Path(args.state_root).expanduser() / "pending-push.jsonl"
    if not queue.exists():
        print("PENDING_PUSH=0")
        return 0

    remaining = []
    for line in queue.read_text(encoding="utf-8").splitlines():
        item = json.loads(line)
        repo = Path(item["repo"])

        proc = git(
            repo,
            "push",
            item["remote"],
            item["branch"],
            check=False,
        )
        remote = git(
            repo,
            "ls-remote",
            "--heads",
            item["remote"],
            f"refs/heads/{item['branch']}",
            check=False,
        )
        remote_hash = (
            remote.stdout.split()[0]
            if remote.returncode == 0 and remote.stdout
            else None
        )

        if proc.returncode or remote_hash != item["commit"]:
            remaining.append(item)
            continue

        manifest_path = Path(item["manifest"])
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
        data["push_status"] = "PASS"
        data["remote_head"] = remote_hash
        data["sync_status"] = "PASS"
        data["remote_sync_pending"] = False
        write_json(manifest_path, data)

    if remaining:
        queue.write_text(
            "".join(json.dumps(item) + "\n" for item in remaining),
            encoding="utf-8",
        )
        os.chmod(queue, 0o600)
    else:
        queue.unlink()

    print(f"PENDING_PUSH={len(remaining)}")
    return 0 if not remaining else 3


def recovery(args: argparse.Namespace) -> int:
    root = Path(args.state_root).expanduser()

    if args.recovery_cmd == "list":
        for path in manifests(root):
            data = json.loads(path.read_text(encoding="utf-8"))
            print(
                f"{path.parent.name}\t{data['project']}\t"
                f"{data['task_id']}\t{data.get('commit_hash')}"
            )
        return 0

    manifest_path = find_checkpoint(root, args.checkpoint)
    data = json.loads(manifest_path.read_text(encoding="utf-8"))

    if args.recovery_cmd == "inspect":
        print(json.dumps(data, indent=2, sort_keys=True))
        return 0

    if args.recovery_cmd == "verify":
        failures = []
        for rel, expected in data["files"].items():
            path = manifest_path.parent / "files" / rel
            if expected is not None and (
                not path.is_file() or sha256(path) != expected
            ):
                failures.append(rel)
        print(
            "SHA256_VALIDATION="
            + ("PASS" if not failures else "FAIL")
        )
        return 0 if not failures else 1

    if args.recovery_cmd == "restore-files":
        target = Path(args.target).resolve()
        selected = [safe_rel(item) for item in args.file]
        for rel in selected:
            if rel not in data["files"] or data["files"][rel] is None:
                raise Blocked(f"file unavailable in checkpoint: {rel}")

            src = manifest_path.parent / "files" / rel
            dst = target / rel
            if dst.exists() and not args.overwrite:
                raise Blocked(f"target exists: {dst}")
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)

        print(f"FILES_RESTORED={len(selected)}")
        return 0

    if args.recovery_cmd == "recover-commit":
        repo = Path(args.repo).resolve()
        commit = data.get("commit_hash")
        if not commit or git(
            repo,
            "cat-file",
            "-e",
            f"{commit}^{{commit}}",
            check=False,
        ).returncode:
            raise Blocked("commit unavailable")

        name = f"recovery/{data['task_id']}-{manifest_path.parent.name}"
        git(repo, "branch", name, commit)
        print(f"RECOVERY_BRANCH={name}")
        return 0

    raise Blocked("unsupported recovery command")


def parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="auto-checkpoint",
        fromfile_prefix_chars="@",
    )
    parser.add_argument(
        "--state-root",
        default=str(default_state_root()),
        help="Private checkpoint state root (default: XDG state directory).",
    )

    sub = parser.add_subparsers(dest="cmd", required=True)

    create = sub.add_parser("checkpoint")
    create.add_argument("--repo", required=True)
    create.add_argument("--task-id", required=True)
    create.add_argument("--message", required=True)
    create.add_argument("--file", action="append", required=True)
    create.add_argument("--preserve-wip", action="append", default=[])
    create.add_argument("--generated", action="append", default=[])
    create.add_argument("--validate", action="append", default=[])
    create.add_argument("--remote", default="origin")
    create.add_argument("--offsite")
    create.add_argument("--rclone-bin", default="rclone")

    sub.add_parser("retry-push")

    recovery_parser = sub.add_parser("recovery")
    recovery_sub = recovery_parser.add_subparsers(
        dest="recovery_cmd",
        required=True,
    )
    recovery_sub.add_parser("list")

    for name in ("inspect", "verify"):
        command = recovery_sub.add_parser(name)
        command.add_argument("checkpoint")

    restore = recovery_sub.add_parser("restore-files")
    restore.add_argument("checkpoint")
    restore.add_argument("--target", required=True)
    restore.add_argument("--file", action="append", required=True)
    restore.add_argument("--overwrite", action="store_true")

    recover_commit = recovery_sub.add_parser("recover-commit")
    recover_commit.add_argument("checkpoint")
    recover_commit.add_argument("--repo", required=True)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        if args.cmd == "checkpoint":
            return checkpoint(args)
        if args.cmd == "retry-push":
            return retry(args)
        return recovery(args)
    except (
        Blocked,
        subprocess.CalledProcessError,
        OSError,
        json.JSONDecodeError,
    ) as exc:
        print(
            f"AUTO_CHECKPOINT=BLOCKED\nCAUSE={exc}",
            file=sys.stderr,
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
