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

Den oprindelige 3D-sag er importeret i
`cfd/cases/marine_3d_padded_5ms/`, opdelt i input, checkpoints, felter,
historik, logs og resultater. Zip-arkivet bevares i `cfd/archives/`.
De store råfiler er ikke med i Git eller i den lette resultatpakke.
Solverkommandoerne herunder bruger fortsat det ældre, flade sagsformat
(`cfd/output/...`) og kræver en sag forberedt i det format.
For at fortsætte fra et checkpoint i en flad sag:

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

En gennemsigtig, drejelig 3D-visning af den gemte geometri kan laves uden
at køre solveren:

```bash
python -m cfd.plot_geometry_3d --case cfd/results/v1_5ms
```

HTML, PNG og kildeoplysninger gemmes i resultatpakkens `visualizations/`.
Sejlenes krumning og twist hentes fra den gemte geometri. Visningen er
foreløbig kun geometri: `slice.npz` indeholder ét vandret snit ved 4 m og
kan ikke bestemme en 3D-blob med separeret strømning. Det kræver det
oprindelige tredimensionelle felt (`flow.vtk`/`flow.vtu` eller
`restart.dat` sammen med `yacht.msh`).

Den importerede sags fulde felt kan nu bruges til 3D-visningen med
tilbagestrømning ved sejlenes læside:

```bash
python -m cfd.separation_3d --case cfd/cases/marine_3d_padded_5ms --output cfd/results/v1_5ms/visualizations
```

Programmet læser det oprindelige mesh og det binære checkpoint, kontrollerer
koordinater og hastighedsskala og udtrækker volumener med tangentialhastighed
langs sejlets lokale krumning under −0,05 m/s. Søgningen er begrænset til
den positive kamberside mellem forlig, agterlig, fod og top, højst 0,75 lokal
kordelængde fra sejlet. Det er den forventede læside for denne vindretning.
Den viste tilbagestrømning er en indikator på separation; kriteriet finder
ikke nødvendigvis hele det separerede strømningsområde i tre dimensioner.
Den gemte beregning er stadig foreløbig (`checks_failed`, iteration 592).

Der køres ingen solver. Prismeknudernes rækkefølge konverteres fra meshio
til VTK; de oprindelige celler opdeles i tetraedre og klippes efter
hastighed og position. Volumen, kildefilernes SHA-256 og kriteriet gemmes i
`separation_3d.json`. Ingen udglatning eller forøgelse af blobens tykkelse
anvendes. Det binære `separation_3d.npz` gemmer de fuldt opløste overflader.
HTML-filen er interaktiv og virker uden internet; PNG viser både hele båden
og et nærbillede. Begge kan regenereres fra den lille cache alene:

```bash
python -m cfd.separation_3d --output cfd/results/v1_5ms/visualizations --from-cache
```

Begge sider kan undersøges med samme kriterium og korrekt fysisk tykkelse:

```bash
python -m cfd.separation_3d --case cfd/cases/marine_3d_padded_5ms --output cfd/results/v1_5ms/visualizations --sides both
python -m cfd.separation_3d --output cfd/results/v1_5ms/visualizations --sides both --from-cache
```

Det gemmer separate filer med præfikset `separation_3d_both_sides`.
Rød/orange viser læsiden og blå/turkis luvsiden. Knapper kan vise hver side
for sig. `--sides windward` laver kun luvsiden; standarden er fortsat
`--sides leeward`. Hastighedsgrænsen ændres med `--reverse-speed` i m/s.
Kildedata, målte volumener, knudetællinger og grænser gemmes i JSON-filen.

Et selvstændigt, gemt script beregner kræfter og kontrollerer vægfriktionen
på begge sider uden at starte en ny simulering:

```bash
python -m cfd.sail_loads --case cfd/cases/marine_3d_padded_5ms --output cfd/results/v1_5ms/diagnostics
```

`sail_loads.py` integrerer tryk og friktion fra det binære checkpoint på
det oprindelige mesh. Fortegn og skala kontrolleres mod SU2's samlede
kraftvektor i `history.csv`. Fremdrift er kraft i +x; sidekraft i +y er
mod bagbord. Skrogmarkøren omfatter også masten. Det er luftkræfter;
vandmodstand og bådens fartligevægt er ikke beregnet her.
Programmet gemmer en læsbar rapport, en kraftfigur og JSON med kilders
SHA-256, tryk-/friktionsbidrag og kontrolstatus i `diagnostics/`.

Rapporten måler også andelen af hver brede sejlflade med omvendt
vægfriktion, `C_f,t < −0,00001`, uden endeflader. Det er et supplerende
separationsmål og skal ikke sidestilles med hele den separerede flade.
Den aktuelle beregnings residualer, kraftstabilitet, feltstabilitet og
vægresolution består; den samlede status fejler på baggrundsprofilens
bevarelse. Resultaterne er derfor fortsat foreløbige.

Analyseprogrammerne ligger permanent som kildefiler i `cfd/`, med tests
i `cfd/tests/`; de er ikke midlertidige konsolskripter.

Fokkens indgangsvinkel kan sammenholdes med den lokale strømning og
vægfriktionen længere tilbage langs profilet med det gemte program:

```bash
python -m cfd.jib_entry --case cfd/cases/marine_3d_padded_5ms --height 4 --output cfd/results/v1_5ms/diagnostics/jib_entry
```

`jib_entry.py` bevarer geometrien og læser det eksisterende 3D-resultats
vandrette snit. AOA opgives som AWA minus profilretningen, særskilt for
korden, forligets eksakte tangent og sekanten over de første 5 %.
Vindprøver foran forliget indeholder også sejlenes egen induktion; de
er ikke en isoleret, uforstyrret indløbsvinkel. Vinden på selve no-slip-
væggen er nul og definerer derfor ingen AOA der.

Programmet skærer også det oprindelige overflademesh og udtrækker
vægfriktion og tryk i flere højder samt hastighedsprofiler ved forskellige
kordepositioner i det gemte snit. Positiv vægfriktion efter et negativt
område støtter lokal genhæftning; den udelukker ikke al 3D-tværstrømnings-
separation. Figur, rapport, SHA-256 og alle prøver som CSV gemmes til
genbrug. `--height` skal matche højden i sagens gemte `slice.npz`.

## Rødt/blåt vindfelt i HTML-vieweren

Den interaktive vindviewer viser VMG-skønnet og de to bidrag fra vindstyrke
og vinddrejning i 3D. Rød er dårligere og blå bedre, med præcis samme
lineære skala og hvide interval som de gemte 2D-kort:

```bash
python -m cfd.wind_viewer_3d
```

Den læser den importerede 3D-sag og gemmer `wind_3d.html`, `wind_3d.npz`
og `wind_3d.json` i `cfd/results/v1_5ms/visualizations/`. HTML-filen virker
uden internet. Vælg felt, 3D-visning, vandret snit eller begge; højden og
synligheden kan ændres, og der er kameraknapper til nærbillede, hele feltet
og visning ovenfra. Snittet ved 4 m bruger det oprindelige snits trekanter
og genskaber 2D-kortets numeriske farveværdier. De øvrige snit bruger det
samme 3D-visningsgitter som de rumlige farveflader.

Hastigheden interpoleres i de oprindelige fluidceller på et gitter med
0,5 m afstand. Først derefter beregnes vinddrejning, vindstyrke og VMG.
Baggrundsvinden følger den foreskrevne vindprofil med højden. Nulpunktet
følger den arealvægtede median i hver højde, beregnet fra ensartede
XY-celler mindst 1 m fra skrogets projektion og forankret i det præcise
2D-nulpunkt ved 4 m. Farveskalaen er fælles for alle højder og felter.
Farverne er således relative vindændringer og brugerens VMG-skøn.

3D-fladerne er niveauflader ved ±0,025, ±0,05, ±0,10, ±0,20, ±0,40 knob
og farveskalaens endepunkter. Celler, som berører manglende eller
udefinerede prøver, udelades. Der fyldes ikke hen over skroget eller andre
solide huller med nærmeste naboværdier. Gitteret er til visning af vindfeltet;
mikroskopiske separationsbobler undersøges med `separation_3d` i stedet.
Der er ikke kørt nogen ny simulering eller ændret på den gemte geometri.
Beregningsstatus, nulreferencer, gitterafstand, niveauer og kilders SHA-256
gemmes i JSON-filen. Begge slags HTML-viewere er bevaret.

HTML kan genskabes fra den kompakte cache alene:

```bash
python -m cfd.wind_viewer_3d --from-cache
```

Programmet er gemt i `cfd/wind_viewer_3d.py`, interpolation i
`cfd/volume_data.py` og HTML/JavaScript i
`cfd/viewer_templates/wind_3d.html`. En valgfri browserkontrol gennemgår
feltvalg, højde, præcist 4 m-snit, synlighed og kameraknapper:

```bash
python -m cfd.tests.browser_wind_viewer
```

Browserkontrollen kræver Chromium. Dens midlertidige filer og skærmbillede
ligger i `build/previews/wind_3d/`.

Git indeholder de to udvalgte, selvstændige HTML-filer `wind_3d.html` og
`separation_3d_both_sides.html` samt det samlede vægtede 2D-billede. De kan
åbnes direkte efter checkout uden rådata, Python eller netværk. Store
solverresultater, lokale datacacher og mellemresultater medtages ikke.
Genberegning kræver de relevante lokale cacher eller det oprindelige
mesh og checkpoint; almindelig visning af HTML-filerne gør ikke.

## Samlet 2D-kort vægtet med sejlenes korder

De eksisterende 3D-lag kan samles til ét kort, hvor vægten i hver højde er
summen af storsejlets og fokkens lokale kordelængde:

```bash
python -m cfd.weighted_wind_2d
```

For hvert vandret punkt beregnes
`F_mean = integral(F(z) * (c_main(z) + c_jib(z)) dz) / integral(c_main(z) + c_jib(z)) dz`.
En sejlprofil vægter nul under dens fod og over dens top. For standardbåden
indgår højderne 1,15–11,65 m, og fokken stopper med at bidrage ved 9,65 m.
Det samlede areal under kordekurverne er 32,578 m²: 20,202 fra storsejlet
og 12,376 fra fokken. Der indgår ingen ekstra vægtning med vindstyrke.

Programmet læser `visualizations/wind_3d.npz`; det gennemsnitsberegner
VMG-værdierne i lagene før farvelægning. Hastighedsvektorer eller RGB-farver
gennemsnitsberegnes ikke. VMG-skala, hvidt interval og lagenes hidtidige
nulreferencer bevares, og der fratrækkes ingen ny median efter gennemsnittet.
Felterne fra vindstyrke og vinddrejning gemmes også i den kompakte cache.

Lagene interpoleres lineært i højden. Integralproduktet med den lineære
korde beregnes med to Gauss-punkter pr. interval og deles præcist ved
alle laghøjder samt sejlenes fod og top. Det medtager delvise nederste og
øverste lag og undgår at udjævne fokkens ophør ind i højder over dens top.
Det er også korrekt ved ulige afstand mellem lagene. Vandrette punkter
med manglende aktive højder maskeres; vægtene fordeles ikke til andre lag.

Resultatet ligger i `cfd/results/v1_5ms/weighted_2d/`:

- `godhed.png`: Det samlede, kordevægtede VMG-kort i samme format som 4 m-kortet.
- `weighted_wind.npz`: Gennemsnittene, dækningsgraderne og de beregnede vægte.
- `weighted_wind.json`: Metode, højder, kordernes arealer, farveskala og kilders SHA-256.
- `height_weights.csv`: Højde, hver kordelængde, integralvægt og normaliseret vægt.

Sejlomridsene i billedet er fortsat 4 m-snittet som placeringsreference;
farvefeltet er gennemsnittet af hele det aktive højdespænd. Scriptet er
gemt i `cfd/weighted_wind_2d.py`, og beregningen starter ingen solver.
Billedet kan genskabes fra gennemsnitscachen alene:

```bash
python -m cfd.weighted_wind_2d --from-cache
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
