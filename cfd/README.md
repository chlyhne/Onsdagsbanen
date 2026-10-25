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

## Filstruktur for lokale 2D-sager

Lokale sager ligger i `cfd/cases/`. Hver sag har `input/` til geometri,
konfiguration og mesh, `checkpoints/` til genstartsfiler, `fields/` til
solvereksporter, `history/` til iterationshistorik, `logs/` til logfiler og
`results/` til vindkort, rapport og plotdata. Kommandoernes `--case` peger
på selve sagsmappen. Ældre flade mapper og den gemte 3D-resultatpakke kan
stadig læses. Se også [projektets mappestruktur](../docs/STRUCTURE.md).

## Selvstændig 2D-version

`section_2d` beregner strømningen omkring et vandret snit af storsejl,
forsejl og mast med samme inkompressible SST-model. Standardhøjden er 4 m.
Geometri, trim, twist og vindprofil hentes fra `cfd/reference`; `--source`
kan i stedet pege på en gemt 3D-sag med `geometry.json` og `marine_case.json`.
Det er en ny beregning i to dimensioner. Vindprofilen ved den valgte højde
bestemmer både relativ vind og turbulens ved indløbet.

```bash
python -m cfd.section_2d prepare --case cfd/cases/section_2d --height 4
python -m cfd.section_2d run --case cfd/cases/section_2d --threads 2 --iterations 3000
```

`prepare` opretter et ægte 2D-mesh med grænselag og en solverkonfiguration.
Der køres ingen solver ved forberedelse. Vælg en ny mappe for hver geometri
eller vindstyrke. Eksempel med 7 m/s sand vind ved profilens referencehøjde
(normalt 10 m), 41° sand vindvinkel og 6 knobs bådfart:

```bash
python -m cfd.section_2d prepare --case cfd/cases/section_2d_7ms --wind-speed 7 --wind-angle 41 --boat-speed 6
```

`--height` skal skære begge sejl (1,15–9,65 m for standardgeometrien).
`--mesh-size` og `--sail-size` styrer opløsningen i det frie felt og langs
sejlene. Efter kørsel gemmes residual-, y+- og feltstabilitetskontroller i `results/report.json`,
og de tre sædvanlige vindkort får en **2D**-markering. Fejlede kontroller
giver en fejlkode; resultat og billeder bevares. Fortsæt et gemt resultat
med `run --restart`. 2D-solveren kører det valgte iterationsbudget;
de samlede krav kontrolleres efter kørslen, så flow- og kraftkonvergens
alene ikke afslutter beregningen, mens turbulensen stadig ændrer sig.
Efterbehandling og plotting kan køres særskilt:

```bash
python -m cfd.section_2d analyse --case cfd/cases/section_2d
python -m cfd.section_2d plot --case cfd/cases/section_2d
```

For to identiske både på samme kurs bruges `--second-boat` ved forberedelse.
Standardplaceringen er 0,75 bådlængde agterude og 2 bådbredder til luv,
målt mellem bådenes centre langs og på tværs af bådene. For standardbåden
(10 × 3 m) er forskydningen 7,5 m agterude og 6 m til luv; bådene har
2,5 m overlap i længderetningen. Ved vind fra styrbord ligger båd 2 mod
styrbord, altså til højre og lidt længere nede i vindkortet.

```bash
python -m cfd.section_2d prepare --source cfd/cases/section_2d --case cfd/cases/section_2d_two_boats --second-boat
python -m cfd.section_2d run --case cfd/cases/section_2d_two_boats --threads 4 --iterations 6000
```

`--source` kan også være en tidligere 2D-sag; dens geometri og vindprofil
genbruges. Afstandene kan ændres med `--aft-lengths` og `--windward-beams`.
Begge både har separate sejl- og mastflader i solvermeshet og indgår i
vægkontrollerne. Vindkort og farvereference tager højde for begge skrogomrids.

2D-modellen har ingen vertikal strømning, tiphvirvler eller havoverflade.
Skrogets omrids i vindkortet er kun en visuel reference; i denne højde
indgår masten og sejlene som faste flader. Kræfter gælder pr. meter
sejlhøjde. Resultatet kan bruges til at undersøge samspillet mellem sejlene,
men mesh-uafhængighed og overensstemmelse med 3D-modellen er ikke dokumenteret.

## Tests

```powershell
python -m unittest discover -s cfd/tests -p "test_*.py"
```

Testene kontrollerer blandt andet mesh-/snitgeometri, shear-initialisering,
genstart, konvergensrapportering og adskillelsen mellem simulering og plot.
De starter ikke en CFD-beregning.
