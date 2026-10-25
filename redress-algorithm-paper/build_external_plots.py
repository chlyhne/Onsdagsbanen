from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import as_completed
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class FigureTarget:
    figure_name: str
    pdf_path: Path
    log_path: Path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build externalized TikZ figures in parallel.")
    parser.add_argument("--tex-file", required=True, help="Path to the main TeX document.")
    parser.add_argument("--jobs", type=int, default=0, help="Maximum parallel pdflatex jobs. 0 uses cpu_count()-1.")
    parser.add_argument("--dry-run", action="store_true", help="List figures that would be rebuilt without running pdflatex.")
    parser.add_argument("--force", action="store_true", help="Rebuild every listed figure regardless of timestamps.")
    return parser.parse_args()


def resolve_job_limit(requested_jobs: int) -> int:
    if requested_jobs > 0:
        return requested_jobs
    cpu_count = os.cpu_count() or 1
    return max(1, cpu_count - 1)


def figure_cache_path(document_dir: Path, figure_name: str, suffix: str) -> Path:
    return document_dir / "build" / "latex" / Path(figure_name).with_suffix(suffix)


def collect_targets(document_dir: Path, document_stem: str, figure_list_path: Path, *, force: bool = False) -> tuple[list[str], list[FigureTarget]]:
    if not figure_list_path.is_file():
        raise FileNotFoundError(f"Missing figure list '{figure_list_path}'. Run the initial pdflatex pass first.")

    figure_names = [line.strip() for line in figure_list_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    if not figure_names:
        return [], []

    # TikZ's md5 covers the picture source, but not the contents of CSV tables
    # or shared macros. Conservatively invalidate all figures when these change.
    inputs = list(document_dir.rglob("*.tex"))
    analysis_dir = document_dir.parent / "analysis"
    inputs.extend(analysis_dir.rglob("*.csv"))
    inputs.extend(analysis_dir.rglob("*.tex"))
    latest_input_ns = max((path.stat().st_mtime_ns for path in inputs), default=0)

    targets: list[FigureTarget] = []
    for figure_name in figure_names:
        pdf_path = figure_cache_path(document_dir, figure_name, ".pdf")
        md5_path = figure_cache_path(document_dir, figure_name, ".md5")
        if not md5_path.is_file():
            raise FileNotFoundError(
                f"Missing md5 file '{md5_path}'. The initial pdflatex pass did not finish writing externalization metadata."
            )
        needs_build = True
        if pdf_path.is_file():
            needs_build = force or max(md5_path.stat().st_mtime_ns, latest_input_ns) > pdf_path.stat().st_mtime_ns
        if needs_build:
            targets.append(
                FigureTarget(
                    figure_name=figure_name,
                    pdf_path=pdf_path,
                    log_path=figure_cache_path(document_dir, figure_name, ".log"),
                )
            )

    return figure_names, targets


def run_target(pdflatex_command: str, document_dir: Path, document_stem: str, target: FigureTarget) -> tuple[FigureTarget, int]:
    tex_invocation = rf"\def\tikzexternalrealjob{{{document_stem}}}\input{{{document_stem}}}"
    completed = subprocess.run(
        [
            pdflatex_command,
            "-halt-on-error",
            "-interaction=batchmode",
            "-output-directory=build/latex",
            "-jobname",
            target.figure_name,
            tex_invocation,
        ],
        cwd=document_dir,
        check=False,
    )
    return target, int(completed.returncode)


def main() -> int:
    args = parse_args()
    tex_path = Path(args.tex_file).resolve()
    document_dir = tex_path.parent
    document_stem = tex_path.stem
    figure_list_path = document_dir / "build" / "latex" / f"{document_stem}.figlist"

    figure_names, targets = collect_targets(document_dir, document_stem, figure_list_path, force=args.force)
    if not figure_names:
        print(f"No externalized TikZ figures were listed in {figure_list_path}.")
        return 0

    if not targets:
        print("All externalized TikZ figures are already up to date with local inputs.")
        print("To fetch new Manage2Sail data first, run scripts/redress_2025_evolution.py from the repository root.")
        return 0

    job_limit = resolve_job_limit(int(args.jobs))
    print(
        f"Found {len(figure_names)} externalized TikZ figures; rebuilding {len(targets)} with up to {job_limit} parallel pdflatex jobs."
    )

    if args.dry_run:
        for target in targets:
            print(f"DRY RUN {target.figure_name}")
        return 0

    pdflatex_command = shutil.which("pdflatex")
    if not pdflatex_command:
        raise RuntimeError("Could not locate 'pdflatex' on PATH.")

    failures: list[FigureTarget] = []
    with ThreadPoolExecutor(max_workers=job_limit) as executor:
        future_map = {
            executor.submit(run_target, pdflatex_command, document_dir, document_stem, target): target
            for target in targets
        }
        for future in as_completed(future_map):
            target = future_map[future]
            print(f"Building {target.figure_name}")
            finished_target, return_code = future.result()
            if return_code != 0:
                failures.append(finished_target)

    if failures:
        for failed_target in failures:
            print(f"Externalized build failed for {failed_target.figure_name}. See {failed_target.log_path}", file=sys.stderr)
        return 1

    print("Finished building externalized TikZ figures.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
