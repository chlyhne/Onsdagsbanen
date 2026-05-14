from __future__ import annotations

import argparse
from collections.abc import Sequence
from pathlib import Path

from .constants import Q_OBJECTIVE_CHOICES
from .constants import Q_OBJECTIVE_DEFAULT
from .pipeline import run_pipeline


def build_parser() -> argparse.ArgumentParser:
	parser = argparse.ArgumentParser(description="Run the redress analysis pipeline.")
	parser.add_argument(
		"--q-objective",
		choices=Q_OBJECTIVE_CHOICES,
		default=Q_OBJECTIVE_DEFAULT,
		help="Objective used for q fitting: rmse or mle (default).",
	)
	parser.add_argument(
		"--output-dir",
		default="analysis",
		help="Directory for generated CSV and TeX artifacts.",
	)
	parser.add_argument(
		"--with-qdiag",
		action="store_true",
		help="Run q diagnostics sweep and export redress_2025_q_diagnostics.csv with full scores.",
	)
	return parser


def main(argv: Sequence[str] | None = None) -> int:
	parser = build_parser()
	args = parser.parse_args(list(argv) if argv is not None else None)
	return run_pipeline(
		output_dir=Path(args.output_dir),
		q_objective=str(args.q_objective),
		run_q_diagnostics=bool(args.with_qdiag),
	)


__all__ = ["build_parser", "main"]
