# Sejlbåd på kryds — version 1.0

Stationær, tredimensionel RANS med SU2 8.5.0 og SST V1994m. Båden bevæger
sig med 6 knob, sand vind kommer 41° fra styrbord, og den seneste kørsel
har 5 m/s sand vind ved 10 m. Sejlene har separat trim og twist.

## Installation

Kør fra projektets rod med Python 3.11 eller nyere:

```powershell
python -m pip install -r cfd/requirements.txt
python -m cfd.solver --install
```

Installationskommandoen henter den officielle SU2 8.5.0 Windows OpenMP-udgave.
På andre platforme installeres `SU2_CFD` på PATH. Solveren ligger lokalt i
`.cfd-tools/SU2`; binære programmer og store beregningsfiler er ikke i Git.

## Beregning

Den seneste lokale sag er `cfd/output/marine_3d_padded_5ms`. Den indeholder
mesh, initialisering, checkpoints og det fulde resultat. Kun dette resultat
medtages i version 1.0. For at fortsætte fra det seneste checkpoint:

```powershell
python -m cfd.marine_rans run --case cfd/output/marine_3d_padded_5ms --restart --threads 8 --iterations 3000
```

For en ny vindstyrke på samme mesh:

```powershell
python -u -m cfd.run_wind --source cfd/output/marine_3d_padded_5ms --case cfd/output/marine_3d_new_wind --wind-speed 5 --threads 8 --iterations 3000
```

Vælg en ny destinationsmappe. Kommandoen kopierer meshet og opretter
analytisk wind shear ved samtlige knuder og i indløbsprofilerne. Ruhed,
vindretning, bådfart og sejlgeometri bevares. Ingen bådløs beregning udføres.

På en ny checkout genereres et mesh fra de små input i `cfd/reference`:

```powershell
python -m cfd.prepare_compact --case cfd/output/marine_3d_v1 --threads 8
python -m cfd.marine_rans run --case cfd/output/marine_3d_v1 --threads 8 --iterations 3000
```

Den første kommando genererer og kontrollerer mesh og startfelt, men starter
ingen solver. Den anden starter den lange simulering. `--fresh` på
`marine_rans run` arkiverer eksisterende resultater og starter fra det
analytiske startfelt. `--restart` bruger det gemte checkpoint.

Solveren skriver checkpoints hver 100. iteration. Ved afbrydelse kan der
mangle op til 99 iterationer siden sidste checkpoint. Konsollen viser
procent af iterationsbudgettet og en billig konvergensstatus hver 25.
iteration. Solveren kan stoppe før 3000 iterationer, når dens stopkrav er
opfyldt. Dette er stationære iterationer, ikke fysiske tidsskridt.

## Billeder

Den seneste lette resultatpakke er [results/v1_5ms](results/v1_5ms).
Den indeholder plotcache, rapport og de tre fuldt opløste billeder, men
ikke det store mesh eller checkpoint. Billederne kan regenereres direkte:

```powershell
python -m cfd.plot_wind --case cfd/results/v1_5ms
```

For lokale solverresultater erstattes stien med sagens mappe. `--field`
kan være `speed`, `direction` eller `goodness`. Rendereren læser kun
`slice.npz` og `plot_context.json`; den starter hverken solver eller
konvergenskontroller. Opløsningen er 2880 × 2640 pixels.

- `vinddrejning.png`: **Skræller og rummer**, bidraget fra vinddrejning.
- `vindhastighed.png`: **Vindhastighed**, bidraget fra vindstyrke.
- `godhed.png`: **Ændring i VMG**, summen efter den foreskrevne formel:

  **ΔVMG = 0,18 × (ændring i vindstyrke − 0,18 × skræller)**

Vindstyrke og VMG måles i knob; skræller måles i grader. På den viste halse
er positiv drejning mod uret en skræller. Formlen er brugerens tilnærmelse,
ikke en bådpolar beregnet af CFD-solveren.

Alle tre felter centreres på deres respektive arealvægtede median i det
synlige udsnit mindst én meter fra skroget. Farveskalaen er fælles, lineær
og symmetrisk, bestemt af det kombinerede felts største absolutte afvigelse
mindst én meter fra skroget. Hvidt interval er cirka ±0,00875 knob.
Konturer vises ved ±0,10, ±0,20 knob osv., med værdier på linjerne.
Rød er dårligere, blå bedre. Båden er grå og sejlene sorte.

Pilen viser sand vindretning (TWA). Der er ingen koordinatakser, gitter eller
metadata i toppen. Farveskalaen står vandret under billedet med ΔVMG i knob.
Beregningsstatus og numeriske referencer ligger i rapporten.

Vil man udtrække et nyt snit eller se et checkpoint, gøres det særskilt:

```powershell
python -m cfd.plot_data --case cfd/output/marine_3d_padded_5ms --height 4
python -m cfd.plot_data --case cfd/output/marine_3d_padded_5ms --preliminary --checkpoint cfd/output/marine_3d_padded_5ms/restart_000500.dat
```

## Fysik og kontrol

Meshkoordinater: stævn langs +x, bagbord +y, opad +z. Plotkoordinater er
`(-y, x)`, så båden peger opad. Beregningsdomænet er x = [−50, 35],
y = [−50, 40], z = [0, 84] m. Plottet viser fortsat kun x = [−40, 30],
y = [−50, 15] m; de ekstra 20 m opad og mod højre er skjult.

Grænselaget ved båd og sejl har 30 lag med 12 µm første afstand; havet har
45 lag. Havet er fladt og bevæger sig med −6 knob i bådens koordinatsystem.
Ingen bølger simuleres. Den neutrale logprofil bruger z₀ ≈ 0,0000961 m
og SU2's WILCOX1998-ruhed med ks = 30 z₀. Højdeafhængige U, k og omega
initialiseres konsistent med indløbsprofilerne. 40 cm bølger var kontekst for
ruhedsvalget; bølgehøjden fastlægger ikke alene vindruheden.

Efter simuleringen kontrolleres residualer, kræfternes stabilitet,
vægresolution, ændring mellem checkpoints og baggrundsprofilen. Kontrollerne
gentages ikke ved almindelig plotting. Et fejlet krav giver en fejlkode,
men resultater og billeder bevares.

Den seneste 5 m/s-kørsel stoppede efter 592 iterationer. Den samlede status
er `checks_failed`; se den medfølgende rapport for de enkelte kontroller.
En stationær løsning og et pænt vindkort dokumenterer ikke mesh-uafhængighed
eller korrekt sejlfysik. Modellen har idealiserede, stive sejl, ingen krængning,
sejldeformation eller tidsopløste vindstød.

Den numeriske NACA0012-reference er gemt i `cfd/reference/airfoil_validation.json`.
Den er en solverreference, ikke en validering af båden. Den kan undersøges
eller genberegnes med `python -m cfd.validate_airfoil --help`.

## Tests

```powershell
python -m unittest discover -s cfd -p "test_*.py"
```

Testene kontrollerer blandt andet mesh-/snitgeometri, shear-initialisering,
genstart, konvergensrapportering og adskillelsen mellem simulering og plot.
De starter ikke en CFD-beregning.
