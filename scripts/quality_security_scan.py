#!/usr/bin/env python3
from __future__ import annotations

import pathlib
import re
import subprocess

ROOT = pathlib.Path(__file__).resolve().parents[1]
SKIP = {
    ".github/workflows/ci.yml",
    "scripts/quality_security_scan.py",
}
PATTERNS = (
    re.compile(rb"github_pat_[A-Za-z0-9_]{20,}"),
    re.compile(rb"gh[pousr]_[A-Za-z0-9]{20,}"),
    re.compile(rb"AIza[0-9A-Za-z_-]{20,}"),
    re.compile(rb"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
)


def tracked_files() -> list[pathlib.Path]:
    result = subprocess.run(
        ["git", "-C", str(ROOT), "ls-files", "-z"],
        check=True,
        capture_output=True,
    )
    return [
        ROOT / item.decode("utf-8", "surrogateescape")
        for item in result.stdout.split(b"\0")
        if item
    ]


def main() -> int:
    findings: list[str] = []
    for path in tracked_files():
        rel = path.relative_to(ROOT).as_posix()
        if rel in SKIP or not path.is_file():
            continue
        data = path.read_bytes()
        if any(pattern.search(data) for pattern in PATTERNS):
            findings.append(rel)

    if findings:
        print("SECURITY_SCAN=FAIL")
        for rel in findings:
            print(f"FINDING={rel}")
        return 1

    print("SECURITY_SCAN=PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
