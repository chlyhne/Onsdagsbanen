#!/usr/bin/env python3
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


def main() -> int:
    root = Path(__file__).resolve().parent
    src = root / "src"
    venv_python = root / ".venv" / "bin" / "python"
    python_exec = str(venv_python) if venv_python.is_file() else sys.executable
    cmd = [
        python_exec,
        "-m",
        "m2s_combiner.cli",
        "--event-url",
        "https://www.manage2sail.com/nl/event/43565da6-2ecc-441f-b3ab-f1f00adc646c#!/",
        "--class-names",
        "Stor bane 1, Stor bane 2, Stor bane 3",
        "--class-names",
        "Lille bane 1, Lille bane 2, Lille bane 3",
        "--output-dir",
        ".",
        "--output-pdf",
        "Results2024.pdf",
    ]
    cmd.extend(sys.argv[1:])
    env = os.environ.copy()
    if src.is_dir():
        existing = env.get("PYTHONPATH")
        env["PYTHONPATH"] = f"{src}{os.pathsep}{existing}" if existing else str(src)
    completed = subprocess.run(cmd, cwd=root, env=env)
    return completed.returncode


if __name__ == "__main__":
    raise SystemExit(main())
