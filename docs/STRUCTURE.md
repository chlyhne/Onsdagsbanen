# Projektets mapper

Kildekode, manuelt input, resultater og arbejdsfiler har hver deres mapper.

| Mappe | Indhold |
| --- | --- |
| `src/m2s_combiner/` | Python-kildekode til resultater og godtgørelse |
| `scripts/` | Kørselsprogrammer for sæsoner, analyse og mail |
| `data/` | Deltagerregister og manuelle dommertjans-oplysninger |
| `config/email/` | Det krypterede modtagerregister |
| `.local/secrets/` | Lokale adgangsoplysninger og private modtagerlister, uden for Git |
| `.local/runtime/` | Midlertidige mailfiler under automatiske kørsler |
| `assets/` | Faste billeder, herunder QR-koden |
| `output/results/` | Færdige resultat-PDF'er for sæsonerne |
| `output/startlister/` | Færdige startlister |
| `analysis/results/` | Gemte CSV-data og genererede TeX-tabeller |
| `analysis/models/` | Gemte modeltilpasninger; bruges igen ved næste analyse |
| `redress-algorithm-paper/` | LaTeX-kilder og byggeprogrammer |
| `redress-algorithm-paper/assets/` | Illustrationer, som indgår i rapporten |
| `redress-algorithm-paper/build/` | LaTeX-hjælpefiler, figurberegninger og byggelogfiler |
| `redress-algorithm-paper/output/` | Den færdige PDF samt tidligere udgaver i `archive/` |
| `cfd/` | CFD-kildekode, med tests i `cfd/tests/` |
| `cfd/reference/` | Faste input og solverreferencer |
| `cfd/results/` | Små, gemte resultatpakker til 3D-modellen |
| `cfd/cases/` | Lokale 2D- og importerede 3D-beregningssager med strukturen nedenfor |
| `cfd/archives/` | Bevarede zip-arkiver med store, importerede CFD-resultater; uden for Git |
| `build/legacy-latex/` | Bevarede hjælpefiler fra tidligere LaTeX-kørsler i projektroden |
| `.cfd-tools/SU2/` | Installerede solverprogrammer |
| `.cfd-tools/downloads/` | Downloadarkiver og midlertidig udpakning af solverprogrammer |

Lokale sager er opdelt sådan:

```text
cfd/cases/section_2d_two_boats/
  input/         Geometri, vindindstillinger, solverkonfiguration og mesh
  checkpoints/   Gemte solvertilstande til genstart
  fields/        Eksporterede strømningstal
  history/       Iterationshistorik, residualreference og tidligere kørsler
  logs/          Solverlog og konsoludskrifter
  results/       Vindkort, rapport, plotdata og opstillingsbillede
```

`checkpoints/` og `input/` er nødvendige for at fortsætte en beregning.
`results/` indeholder selvstændige plotdata, så billeder kan regenereres
uden at starte solver eller genberegne konvergens. Beregningsmapperne er
lokale og er ikke med i Git. Ældre flade sager kan stadig læses.

Den importerede 3D-sag ligger i `cfd/cases/marine_3d_padded_5ms/`.
Den originale zip er bevaret i `cfd/archives/`. De små, selvstændige
3D-visninger og deres overfladedata ligger i
`cfd/results/v1_5ms/visualizations/`.
Sejlkræfter, kontrol af begge sejlflader og den tilhørende kraftfigur
ligger i `cfd/results/v1_5ms/diagnostics/`. De genbrugelige programmer
er `cfd/separation_3d.py`, `cfd/plot_geometry_3d.py` og `cfd/sail_loads.py`;
kørselskommandoerne står i `cfd/README.md`.
HTML-vindvieweren dannes af `cfd/wind_viewer_3d.py` og den gemte skabelon
`cfd/viewer_templates/wind_3d.html`. Dens kompakte 3D-felter og HTML ligger
også i `cfd/results/v1_5ms/visualizations/`. Browserkontrollers hjælpefiler
og skærmbilleder ligger i `build/previews/`.
Det samlede kordevægtede 2D-kort ligger i
`cfd/results/v1_5ms/weighted_2d/`, sammen med vægte og gennemsnitsdata.
Det genskabes med `cfd/weighted_wind_2d.py`.

Kør fra projektroden:

```bash
python scripts/run_2026.py
python scripts/redress_2025_evolution.py
python redress-algorithm-paper/build.py
python -m cfd.section_2d plot --case cfd/cases/section_2d_two_boats
python -m cfd.section_2d run --case cfd/cases/section_2d_two_boats --restart --threads 4 --iterations 3000
```

Oprydningen har flyttet eksisterende resultater og genstartsfiler til de
nye mapper. Den ændrer ikke beregningsmodellernes fysik eller datagrundlag.
