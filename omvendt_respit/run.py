"""Lav startliste til omvendt respit: python3 omvendt_respit/run.py."""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys


def main() -> int:
    root = Path(__file__).resolve().parent.parent
    venv_python = root / ".venv" / "bin" / "python"
    python_exec = str(venv_python) if venv_python.is_file() else sys.executable
    env = os.environ.copy()
    env["PYTHONPATH"] = str(root) + (
        os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else ""
    )
    return subprocess.run(
        [python_exec, "-m", "omvendt_respit", *sys.argv[1:]], env=env
    ).returncode


if __name__ == "__main__":
    raise SystemExit(main())
