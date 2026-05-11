"""Redress analysis pipeline for Manage2Sail race data."""

from .cli import main
from .pipeline import run_pipeline

__all__ = ["main", "run_pipeline"]
