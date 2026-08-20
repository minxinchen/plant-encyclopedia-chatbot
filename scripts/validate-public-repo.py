#!/usr/bin/env python3
"""Fail closed when the proposed public Git tree contains unsafe artifacts.

The validator examines Git's tracked and untracked-but-not-ignored candidate
set. Ignored local secrets and source PDFs are never opened.

author: Nio (Master)
date: 2026-08-20
"""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
REQUIRED = {
    "README.md",
    "LICENSE",
    "NOTICE.md",
    ".gitignore",
    ".env.example",
}
FORBIDDEN_NAMES = {".env", ".env.local", "id_rsa", "id_ed25519"}
FORBIDDEN_SUFFIXES = {
    ".pdf", ".sqlite", ".db", ".npy", ".npz", ".pem", ".p12",
    ".tif", ".tiff", ".jp2",
}
SECRET_PATTERNS = {
    "google_api_key": re.compile(rb"AIza[0-9A-Za-z_-]{30,}"),
    "github_token": re.compile(rb"gh[oprsu]_[0-9A-Za-z]{30,}"),
    "private_key": re.compile(rb"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
}
MAX_FILE_BYTES = 10 * 1024 * 1024


def candidate_paths() -> list[Path]:
    result = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
        cwd=ROOT,
        check=True,
        capture_output=True,
    )
    return [ROOT / item.decode("utf-8") for item in result.stdout.split(b"\0") if item]


def main() -> None:
    errors: list[str] = []
    paths = candidate_paths()
    relative = {path.relative_to(ROOT).as_posix() for path in paths}
    errors.extend(f"missing_required_file:{name}" for name in sorted(REQUIRED - relative))

    scanned = 0
    for path in paths:
        rel = path.relative_to(ROOT)
        rel_text = rel.as_posix()
        if not path.is_file():
            continue
        if ".git" in rel.parts:
            errors.append(f"nested_git_metadata:{rel_text}")
            continue
        if path.name in FORBIDDEN_NAMES or path.suffix.lower() in FORBIDDEN_SUFFIXES:
            errors.append(f"forbidden_public_artifact:{rel_text}")
            continue
        size = path.stat().st_size
        if size > MAX_FILE_BYTES:
            errors.append(f"file_over_10_mib:{rel_text}:{size}")
            continue
        data = path.read_bytes()
        scanned += 1
        for label, pattern in SECRET_PATTERNS.items():
            if pattern.search(data):
                errors.append(f"possible_{label}:{rel_text}")

    result = {
        "status": "PASS" if not errors else "FAIL",
        "candidate_files": len(paths),
        "files_scanned": scanned,
        "maximum_file_bytes": MAX_FILE_BYTES,
        "errors": sorted(set(errors)),
    }
    print(json.dumps(result, ensure_ascii=False))
    if errors:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
