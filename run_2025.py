#!/usr/bin/env python3
from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def main() -> int:
    root = Path(__file__).resolve().parent
    cmd = [
        sys.executable,
        "-m",
        "m2s_combiner.cli",
        "--event-url",
        "https://www.manage2sail.com/da-dk/event/Onsdagsbanen2025#!/",
        "--output-dir",
        ".",
        "--output-pdf",
        "Results2025.pdf",
    ]
    completed = subprocess.run(cmd, cwd=root)
    return completed.returncode


if __name__ == "__main__":
    raise SystemExit(main())
