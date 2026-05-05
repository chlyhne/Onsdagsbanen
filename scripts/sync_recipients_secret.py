#!/usr/bin/env python3
from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path


EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _load_recipients(path: Path) -> list[str]:
    if not path.exists():
        raise ValueError(f"Recipients file not found: {path}")

    recipients: list[str] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if not EMAIL_RE.match(line):
            raise ValueError(f"Invalid recipient line in {path}: {line}")
        recipients.append(line)

    deduped = list(dict.fromkeys(recipients))
    if not deduped:
        raise ValueError(f"No recipients found in file: {path}")
    return deduped


def _run(cmd: list[str], *, stdin_text: str | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        cmd,
        input=stdin_text,
        capture_output=True,
        text=True,
        check=False,
    )


def _repo_from_origin() -> str | None:
    result = _run(["git", "remote", "get-url", "origin"])
    if result.returncode != 0:
        return None

    remote = result.stdout.strip()
    if not remote:
        return None

    # Supports:
    # - https://github.com/owner/repo.git
    # - git@github.com:owner/repo.git
    match = re.search(r"github\.com[:/](?P<owner>[^/]+)/(?P<repo>[^/]+?)(?:\.git)?$", remote)
    if not match:
        return None

    return f"{match.group('owner')}/{match.group('repo')}"


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Sync recipients.txt to a GitHub Actions secret via gh CLI.",
    )
    parser.add_argument(
        "--recipients-file",
        default="recipients.txt",
        help="Path to recipients file (default: recipients.txt)",
    )
    parser.add_argument(
        "--secret-name",
        default="M2S_RECIPIENTS",
        help="GitHub Actions secret name (default: M2S_RECIPIENTS)",
    )
    parser.add_argument(
        "--repo",
        help="GitHub repo in owner/name format. Defaults to origin remote.",
    )
    args = parser.parse_args()

    recipients = _load_recipients(Path(args.recipients_file))
    payload = "\n".join(recipients)

    repo = args.repo or _repo_from_origin()
    if not repo:
        raise ValueError(
            "Could not infer repo from git origin. Pass --repo owner/name."
        )

    # Use stdin to avoid shell escaping issues and prevent accidental logging.
    cmd = ["gh", "secret", "set", args.secret_name, "--repo", repo, "--body", "-"]
    result = _run(cmd, stdin_text=payload)

    if result.returncode != 0:
        details = (result.stderr or result.stdout).strip()
        raise RuntimeError(f"gh secret set failed: {details}")

    print(
        f"Updated {args.secret_name} in {repo} with {len(recipients)} recipient(s) "
        f"from {args.recipients_file}."
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        raise SystemExit(1)
