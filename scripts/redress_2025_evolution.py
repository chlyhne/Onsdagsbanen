from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    src = root / "src"
    venv_python = root / ".venv" / "bin" / "python"
    python_exec = str(venv_python) if venv_python.is_file() else sys.executable

    cmd = [python_exec, "-m", "m2s_combiner.redress"]
    cmd.extend(sys.argv[1:])

    env = os.environ.copy()
    if src.is_dir():
        existing = env.get("PYTHONPATH")
        env["PYTHONPATH"] = f"{src}{os.pathsep}{existing}" if existing else str(src)

    completed = subprocess.run(cmd, cwd=root, env=env)
    return completed.returncode


if __name__ == "__main__":
    raise SystemExit(main())
