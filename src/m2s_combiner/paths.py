"""Repository locations for permanent input and generated reports."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / 'data'
RESULTS = ROOT / 'output' / 'results'
ANALYSIS = ROOT / 'analysis' / 'results'
MODELS = ROOT / 'analysis' / 'models'
