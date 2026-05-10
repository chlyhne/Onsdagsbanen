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

## Noter

- Projektet er bevidst adskilt fra implementering af pointsystemer.
- Næste trin er Python-implementering mod samme datakilde/scraper.
