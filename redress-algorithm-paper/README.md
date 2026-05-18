# Godtgørelsesalgoritme (LaTeX)

Dette er et selvstændigt LaTeX-projekt, som beskriver en Bayesiansk/Kalman-lignende metode til godtgørelse ved fravær på grund af tjeneste i kapsejladsudvalg.

## Struktur

- `main.tex`: hoveddokument
- `sections/A-overblik.tex`: formål, intuition og kort notation
- `sections/B-model-opdatering.tex`: model + opdateringsregler
- `sections/C-plot-macros.tex`: fælles plot- og tabelmakroer
- `sections/C-resultater.tex`: resultatafsnit
- `sections/D-2025-boatplots-from-csv.tex`: appendix med CSV-renderede bådplots

## Build

```bash
cd Onsdagsbanen/redress-algorithm-paper
latexmk -pdf main.tex
```

VS Code:

- The workspace now defines a LaTeX Workshop recipe named `Parallel externalized PGF build`.
- Pressing the LaTeX build/play button on `main.tex` runs one `pdflatex` pass to emit the externalization list, rebuilds stale `tikzcache/*.pdf` figures in parallel via `build_external_plots.py`, and finishes with `latexmk` so the generated plot PDFs are included in the final document.
- The recipe calls `python3`, so that executable needs to be available on `PATH` in both Windows and Linux.

Equivalent manual flow:

```bash
pdflatex -synctex=1 -interaction=nonstopmode -file-line-error -shell-escape main.tex
python3 ./build_external_plots.py --tex-file ./main.tex
latexmk -synctex=1 -interaction=nonstopmode -file-line-error -shell-escape -pdf main.tex
```

## Noter

- Projektet er bevidst adskilt fra implementering af pointsystemer.
- Næste trin er Python-implementering mod samme datakilde/scraper.
