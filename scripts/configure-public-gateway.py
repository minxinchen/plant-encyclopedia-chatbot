#!/usr/bin/env python3
"""Create the local public-gateway token without printing it."""

from __future__ import annotations

import argparse
import os
import secrets
import tempfile
from pathlib import Path


KEY = "PLANT_PUBLIC_GATEWAY_TOKEN"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--env-file", type=Path, required=True)
    args = parser.parse_args()
    path = args.env_file.expanduser().resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    existing = next((line.split("=", 1)[1] for line in lines if line.startswith(KEY + "=")), "")
    if len(existing) >= 32:
        print("公開 gateway token 已存在；未變更。")
        return
    token = secrets.token_urlsafe(48)
    updated = [line for line in lines if not line.startswith(KEY + "=")]
    updated.append(f"{KEY}={token}")
    handle, temporary = tempfile.mkstemp(prefix=path.name + ".", dir=path.parent)
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            stream.write("\n".join(updated) + "\n")
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    print("公開 gateway token 已安全建立。")


if __name__ == "__main__":
    main()
