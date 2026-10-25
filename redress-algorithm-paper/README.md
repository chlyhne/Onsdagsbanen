# Godtgørelsesalgoritme (LaTeX)

Dette er et selvstændigt LaTeX-projekt, som beskriver en Bayesiansk/Kalman-lignende metode til godtgørelse ved fravær på grund af tjeneste i kapsejladsudvalg.

## Struktur

- `main.tex`: hoveddokument
- `assets/`: faste illustrationer
- `build/latex/`: hjælpefiler og TikZ-figurer
- `build/logs/`: byggelogfiler
- `output/main.pdf`: færdig rapport
- `sections/A-overblik.tex`: formål, intuition og kort notation
- `sections/B-model-opdatering.tex`: model + opdateringsregler
- `sections/C-plot-macros.tex`: fælles plot- og tabelmakroer
- `sections/C-resultater.tex`: resultatafsnit
- `sections/D-2025-boatplots-from-csv.tex`: appendix med CSV-renderede bådplots

## Build

`build_external_plots.py` only renders figures from local analysis files. It does
not download results from Manage2Sail. To refresh the data and rebuild the paper,
run this from the repository root (the directory containing `scripts/`):

```bash
python3 scripts/redress_2025_evolution.py
python3 redress-algorithm-paper/build.py
```

Despite its historical name, the analysis script fetches all configured seasons,
including 2026. It reuses the fitted model parameters in the Q cache while
recomputing predictions and exports from the fetched results.

The figure builder checks local TeX and analysis CSV modification times as well
as TikZ metadata. Use `--force` to rebuild every figure or `--dry-run` to inspect
what would rebuild. `build.py` runs the initial LaTeX pass, builds figures, and finishes with latexmk.
The final PDF is `output/main.pdf`. The appendix selection file contains manually set
axis bounds; review those against `analysis/results/boat_plot_data/boat_plot_manifest.csv`
when adding races, and review prose describing specific results.

To compile already refreshed local data:

```bash
python3 redress-algorithm-paper/build.py
```

VS Code:

The LaTeX Workshop build recipe runs `build.py`. All compiler output and
externalized figures go to `build/`; the finished PDF is copied to `output/`.
The same build can be run from the paper directory:

```bash
python3 build.py
```

## Noter

- Projektet er bevidst adskilt fra implementering af pointsystemer.
- Analysedata genereres med `scripts/redress_2025_evolution.py` fra projektets rodmappe.
