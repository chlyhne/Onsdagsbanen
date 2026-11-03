# Startdata fra Manage2Sail

`onsdagsbane.sqlite3` er en renset kopi af den aktuelle båd- og løbsdatabase med de supplerede TACIL/M/H-tal. Ved første serverstart kopieres den til den skrivbare driftsdatabase; filen i Git bruges aldrig som driftsdatabase. Eksisterende serverdata bevares ved senere opstart. Kopien omfatter ingen brugerkonti, sessioner eller OAuth-loginforsøg. Den opdateres udtrykkeligt med `python -m onsdagsbane.snapshot --source <driftsdatabase>`.

JSON-filerne nedenfor er det oprindelige arkiv og en reserveimport, hvis databasekopien ikke er tilgængelig.

De fire JSON-filer er offentlige API-svar hentet 2. november 2026 fra Onsdagsbanen 2026:

`https://www.manage2sail.com/api/event/f72fb377-79d4-4b4b-8484-d6687df25441/regattaresult/<klasse-id>`

| Fil | Løb | Klasse-id |
| --- | --- | --- |
| lille-1.json | Lille bane 1 | b09744df-74b1-48ec-bcd0-c4fe8d7d3010 |
| lille-2.json | Lille bane 2 | 65b8bc3e-0cc8-4a98-9fbf-e062f89ef4bd |
| stor-1.json | Stor bane 1 | a89c7a7d-7691-4df0-97e8-2085645c6412 |
| stor-2.json | Stor bane 2 | df005613-fcf9-429f-80a0-ddb9888c6dc4 |

Importen bevarer oprindelige sejltider, beregnede tider, handicap, distancer og starttider. DH-stamdata suppleres fra `omvendt_respit/resources/participants.csv`. Manglende op/ned-tal forbliver tomme.

WindSpeedType-mappingen 0 → let, 1 → mellem, 2 → hård er verificeret mod 514 sammenfald mellem API'ets anvendte Hdcp og de tre lokale TACIL/M/H-tal (90 let, 394 mellem, 30 hård). Det er en observation om dette datasæt.

Den oprindelige JSON-import indeholder 31 både og 514 resultatregistreringer fordelt på 34 bane/sejlads-kombinationer. Den aktuelle SQLite-kopi indeholder også de senere gemte ændringer. Senere opstarter overskriver ikke driftsdatabasen fra startdata.
