# Onsdagsbane Afslutningssejlads

Selvstændig beregner med tre moduler og to manuelt redigerbare CSV-filer. Hele kørslen er lokal: ingen Manage2Sail, API-kald eller automatisk opdatering.

## Filer

```text
omvendt_respit/
  run.py                     Start programmet
  __main__.py                Kommandolinje og sammenkobling
  course.py                  Modul 1: mærker → banelængde og rundingsretning
  starts.py                  Modul 2: deltagere + banelængde → starttider
  pdf.py                     Modul 3: startliste + starttidspunkt + bane → PDF
  resources/
    participants.csv         Alle 31 både og deres tre handicaptal
    marks.csv                Mærkerne A–G, I, J og X
  tests/test_pursuit.py       Automatiske tests
  requirements.txt           Python-afhængigheder
```

## Kør

Fra projektets hovedmappe:

```bash
python3 omvendt_respit/run.py --route A,D,F --start 18:30 --date 2026-09-23
```

Hvis du allerede står inde i `omvendt_respit/`, brug:

```bash
python3 run.py --route A,D,F --start 18:30 --date 2026-09-23
```

Uden argumenter spørger programmet efter indstillingerne:

```bash
python3 omvendt_respit/run.py
```

Standardoutput er `output/startlister/Starttider.pdf`, relativt til den mappe du kører fra. Alternativ fil vælges med `--output-pdf FIL`.

`--start-index` vælger, hvilken båd i den sorterede PDF-startliste der starter på tidspunktet i `--start`. Standard er **1**. Fx starter båd nummer 5 kl. 18:30 med:

```bash
python3 omvendt_respit/run.py --route A,D,F --start 18:30 --start-index 5 --date 2026-09-23
```

Tidligere både starter tilsvarende før tidspunktet, og tidsforskellene bevares. Indekset tælles efter fravalg af deltagere og gælder den samlede PDF-liste, også med `--separate-groups`. Ved interaktiv kørsel vælges nummeret med samme standardværdi.

`run.py` bruger projektets `.venv`, hvis den findes. Ved selvstændig installation:

```bash
python -m pip install -r omvendt_respit/requirements.txt
python -m omvendt_respit --route A,D,F --start 18:30 --date 2026-09-23
```

Mappen kan kopieres til et andet sted; kør kommandoerne fra dens nye overmappe.

## Ret CSV-filerne manuelt

CSV-filerne er UTF-8 med komma som kolonneseparator. Behold kolonneoverskrifterne. Brug normalt punktum som decimaltegn; decimal-komma skal stå i et citeret CSV-felt.

### Deltagere: `resources/participants.csv`

```csv
competitor,boat_name,boat_type,sail_number,series,TACIL,TACIM,TACIH,source
Harpelunde Senior,Fly,Hansen Spidsgatter,D9,Lille bane 2,953.0,735.2,648.1,Onsdagsbanen 2025
```

Ret navn, båd, bådtype (`boat_type`) eller handicaptal direkte. Kolonnen `boat_type` kan udelades i ældre CSV-filer; så er feltet tomt i PDF’en. Tilføj eller slet rækker for at ændre feltet. Deltagernavne skal være entydige, og alle tre handicaptal skal være positive. `source` er et valgfrit notefelt, som ikke påvirker beregningen.

GPH beregnes fra de tre kolonner, så der ikke findes et ekstra GPH-felt, som kan blive forældet:

```text
GPH = (3 × TACIL + 12 × TACIM + 5 × TACIH) / 20
```

De 31 deltagere blev hentet én gang fra Onsdagsbanen 2026. For Harpelunde Senior blev tallene hentet fra 2025: 953,0 / 735,2 / 648,1. Oprindelsen står i CSV’ens `source`-kolonne. Fremtidige ændringer sker kun ved at redigere CSV-filen. Den tidligere fil med supplerende tal er erstattet af denne samlede deltagerfil.

Vælg en anden deltagerfil med `--participants-csv FIL`. Fravælg midlertidigt med `--exclude "NAVN"` (kan gentages).

### Mærker: `resources/marks.csv`

```csv
name,latitude,longitude
X,56.2437000000,10.3445500000
```

Positionerne er WGS84-decimalgrader, nord/øst positive. Tilføj eller ret mærker direkte. Navnene skal være entydige; J og X kræves til startlinjen.

A–G, I og J stammer fra [Kaløvig Bådelaugs banekort](https://kblsail.dk/wp-content/uploads/2024/09/banekort-og-waypoints.pdf). X er brugeroplyst: 56°14.622'N 10°20.673'E. Links er kun dokumentation og hentes ikke under kørslen.

Vælg en anden mærkefil med `--marks-csv FIL`.

## Beregning og udskrift

Angiv kun rundingsmærker, fx `A,D,F`. Startlinjen J–X og mål ved X er implicitte. Første ben er korteste afstand fra første mærke til linjestykket J–X: projektionen, hvis den ligger indenfor, ellers nærmeste endemærke. De efterfølgende ben er afstandene mellem mærkerne og fra sidste mærke til X. Der bruges WGS84-afstande uden krydstillæg eller ekstra rundingsdistance. En sømil er 1852 meter.

```text
starttillæg = (største GPH i feltet − bådens GPH) × banelængde
starttid i PDF = angivet tidspunkt + starttillæg − den valgte båds starttillæg
```

GPH og distance beholdes uafrundet. Starttillæg afrundes til hele sekunder; halve sekunder rundes op. Ens starttider bevares. Starter efter midnat markeres med dagtillæg. Metoderne følger [GPH-vægtningen](https://dh.websejler.dk/da/somiletiderne) og [omvendt respit](https://dh.websejler.dk/da/somiletid3).

PDF’en viser titel, dato, banelængde i sømil, `Bane:` og de farvede bogstaver samt tre kolonner: **Starttid**, **Båd/Deltager** og **Bådtype**. Grøn er styrbord, rød er bagbord. Den mindste drejning mellem indgående og udgående kurs afgør rundingssiden. Ved 180° videreføres forrige retning; ved første mærke bruges bagbord. En helt lige passage følger samme regel. `--first-rounding starboard` ændrer standarden for en tvetydig første runding.

Titlen er som standard **Onsdagsbane Afslutningssejlads**; ændres med `--title`. Datoen følger det starttidspunkt, der gives til PDF-generatoren.

`--distance 5.2` kan bruges i stedet for mærker; så vises ingen bane på PDF’en. `--separate-groups` beregner Stor og Lille Bane hver for sig med samme distance og første start. Standard er ét samlet felt.

## Kald de tre moduler fra Python

```python
from datetime import datetime
from pathlib import Path
import omvendt_respit
from omvendt_respit.course import read_marks, calculate_course
from omvendt_respit.starts import read_participants, calculate_starts
from omvendt_respit.pdf import write_pdf

resources = Path(omvendt_respit.__file__).parent / "resources"

# 1. Mærker → bane og banelængde.
marks = read_marks(resources / "marks.csv")
course = calculate_course(marks, ["A", "D", "F"])

# 2. Deltagere + banelængde → starttider.
boats = read_participants(resources / "participants.csv")
first_start = datetime(2026, 9, 23, 18, 30)
starts = calculate_starts(boats, course.distance_nm, first_start)

# 3. PDF; tidspunktet kan justeres uden genberegning af respitten.
write_pdf(Path("output/startlister/Starttider.pdf"), starts, first_start, course=course)
write_pdf(Path("output/startlister/Starttider-19.pdf"), starts,
          datetime(2026, 9, 23, 19, 0), course=course, start_index=5)
```

## Tests

Fra overmappen:

```bash
python -m unittest discover -s omvendt_respit/tests -v
```

Testene omfatter CSV-validering, manuelle handicapændringer, projektion, rundingsretning, GPH, starttider, PDF og en fuld kørsel med netværksadgang blokeret.
