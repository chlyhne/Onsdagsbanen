# Opdateret: to timer fra første start

Arrangøren har korrigeret tidsrummet til **18:30–20:30**, dvs. 120 minutter
for stor bane og 115 for lille. Efterårets tidligere starter ændrer ikke
varigheden. Den tidligere oplysning om 90 minutter er trukket tilbage.

Den relevante kørsel ligger nu i **`two_hours/`**. Genkør præcis med:

```bash
.venv/bin/python scripts/analyze_time_limit_distribution.py --available-minutes 120 --target-minutes 75 90 105 --allow-late-finishers --output-dir analysis/results/time_limit/two_hours
```

Ni registrerede fuldførte sejladser ligger efter den nominelle frist (6 på
lille, 3 på stor). De bevares som observerede sejltider, også i likelihooden.
Flaget `--allow-late-finishers` er nødvendigt for dette; uden flag stopper
programmet ved konflikten. DNF antaget tidsrelaterede censureres ved den
nominelle frist. Dette er en følsomhedsantagelse om en ikke helt konsekvent
praktiseret eller registreret grænse, ikke dokumentation for årsagerne.

Med DNF-vægt 1 (alle DNF antaget tidsgrænse-DNF) er de beregnede risici:

| Planlagt middel før ændring | Bane | Før | Længere, samme orden | Længere, byttet |
|---|---|---:|---:|---:|
| 75 min | Lille | 0,17 % | 0,21 % | 0,14 % |
| 75 min | Stor | 0,24 % | 0,36 % | 0,57 % |
| 90 min | Lille | 1,59 % | 1,45 % | 0,88 % |
| 90 min | Stor | 2,22 % | 2,22 % | 3,25 % |
| 105 min | Lille | 17,75 % | 12,17 % | 6,89 % |
| 105 min | Stor | 11,37 % | 8,93 % | 13,90 % |

Dette er ikke en rekonstruktion af de faktiske DNF-procenter. Alle dagene er
normaliseret til samme planlagte middel i hvert scenarie. Forskelle i
arrangørens vindskøn og ekstra aftagende vind er ikke inkluderet.

Ved uændret startorden er risikoneutral middelvarighed 90 minutter på stor
og 86,25 på lille: `ekstra_sejltid * oprindeligt_tidsvindue / ekstra_frist`.
Under disse mål vokser sejltiderne forholdsmæssigt mere end fristen og risikoen
stiger. Over disse mål falder risikoen. Det gælder enhver fast fordelingsform
ved proportional skalering; fordelingen afgør størrelsen af ændringen.

Ved startbytte er de tilsvarende grænser 120 minutter på stor og 69 minutter
på lille. Lille får 25 minutter ekstra rådighedstid, stor kun 15.

Ved et 90-minuttersmål og DNF-vægt 1 giver samme startorden ca. 7,1 forventede
overskridelser over 379 bådsejladser mod 8,1 ved bytte. Men ved DNF-vægt 0 er
retningen omvendt (1,9 mod 1,6). Ukendte DNF-årsager betyder derfor, at dette
ikke giver en sikker samlet anbefaling om startorden.

Nedenfor står metodebeskrivelsen og historikken fra de tidligere scenarier.
Filerne i `distribution/` samt 90-minutterskontrollen er tidligere undersøgelser,
ikke resultater under det nu bekræftede to-timersvindue.

---

# Tidsgrænser og startrækkefølge

Den oprindelige deterministiske beregning i `analyze_time_limit.py` og CSV-filerne
her i mappen er bevaret som en indledende analyse. **De bruges ikke som grundlag
for konklusioner om DNF.** Den nye fordelingsanalyse ligger i `distribution/`.

## Genkørsel

Fra projektroden:

```bash
.venv/bin/python scripts/analyze_time_limit_distribution.py
.venv/bin/python -m unittest discover -s tests -p 'test_time_limit_distribution.py'
```

Den første følsomhedskørsel brugte antagne tidsvinduer på 130, 140 og 150 minutter fra første
start til fælles tidsgrænse. Lille bane har fem minutter mindre. De er **ikke
bekræftede starttider**. Planlagte middel-sejltider før forlængelsen er 75, 90 og
105 minutter. De er ligeledes følsomhedsscenarier, ikke arrangørens oplyste mål.

Eksempel med ét antaget tidsvindue og én planlagt varighed:

```bash
.venv/bin/python scripts/analyze_time_limit_distribution.py --available-minutes 140 --target-minutes 90 --output-dir /tmp/time-limit-example
```

Når faktiske tider foreligger, bruges en CSV med én række pr. dato og bane:

```text
race_date,group,available_minutes
2026-04-29,Stor Bane,140
2026-04-29,Lille Bane,135
```

Ovenstående er et format-eksempel, ikke bekræftede værdier. Filen skal indeholde
alle de analyserede datoer/banegrupper. `available_minutes` er tidsgrænse minus
banens egen starttid. Kør med `--schedule FIL.csv`. Modellen forudsætter fortsat,
at byttet startrækkefølge giver lille bane fem minutter og tager fem fra stor.

Andre parametre: `--year`, `--extra-minutes`, `--deadline-extension`,
`--dnf-weights` og `--output-dir`. Parametre og SHA256 af input gemmes.
Genkørsel overskriver kun filerne i den valgte outputmappe.

## Model

Datagrundlaget er redress-modellens gemte tilstande i
`analysis/results/redress_2025_state_history.csv`; filen omfatter også 2026.
De 17 sejladser i 2026 har 186 fuldførte + 15 DNF på stor og 168 + 10 på lille.
Der er ikke hentet opdaterede resultater på nettet.

For hver båd bruges redress-modellens prædiktive marginalfordeling:

```
Y_i = log(handicapkorrigeret tid)
Y_i ~ Normal(mu_dag + x_prior_i + sqrt(r_dag)*gamma_prior_i,
             p_prior_i + r_dag*(1+p_gamma_prior_i))
T_i = exp(Y_i) - (1000-hdcp_i)*length_nm_i
```

Normalfordelingens anden parameter ovenfor er varians. Dagens `mu` og `r`
tilpasses igen; bådenes historiske tilstande og usikkerheder holdes faste.
En fuldførende båd bidrager med log-tidens normal-likelihood. En DNF antaget
forårsaget af tidsgrænsen bidrager med `log P(T_i > tidsgrænsen)`.

Dette er en **marginal/composite likelihood**, ikke en genimplementering af
redress-filterets fulde kovariansstruktur eller en genfiltrering af historikken.
Den genbruger fordeling, bådtilstande, gamma-led og prædiktiv marginalvarians.
Den korrigerer dagens fit for de antagne censurerede DNF, men korrigerer ikke
allerede gemte bådtilstande for tidligere års censurering. Der beregnes ikke
konfidensintervaller for de tilpassede parametre. Dommertjans-modellen ændres ikke.

DNF-årsager mangler. Derfor køres vægte 0, 0,5 og 1 på DNF-log-likelihooden:
0 ignorerer informationen, 1 behandler alle DNF som tidsoverskridelser.
0,5 er alene en mellemliggende følsomhedsvægt. Det er **ikke** en blandingsmodel
for DNF-årsager og er ikke det samme som at identificere halvdelen af bådene
som tidsgrænse-DNF. Resultaterne er ikke statistiske øvre/nedre grænser.

Fordelingen for feltet består af bådenes forskudte lognormalfordelinger, med
lige vægt pr. båd. Historisk aktive både er fuldførende plus DNF; DNS/DNC
medregnes ikke. For hver dag og bane multipliceres hele sejltidsfordelingen
med `planlagt_middel / modelmiddel`. Det repræsenterer arrangørens valg af
banelængde, så middelværdien kompenseres. Efter forlængelse bruges
`(planlagt_middel + 15) / modelmiddel`. Relativ spredning og form fastholdes;
den absolutte spredning vokser med banelængden.

Risikoen beregnes analytisk fra hele fordelingen for hvert af tre scenarier:
oprindelig bane og frist, længere bane med 20 minutter ekstra, samt samme
ændring med omvendt startorden. Sandsynligheder summeres til forventede antal
og vægtes på sæsonniveau med antallet af deltagende både. Dette kræver ikke
uafhængighed mellem bådene, men forudsiger ikke sandsynligheden for et bestemt
samlet antal DNF. Andre årsager til DNF indgår ikke i de beregnede antal.

## Fortolkning

`distribution/report.md` viser alle scenarier. `summary.csv` har sæsontal;
`race_scenarios.csv` har resultater pr. dag. `fit_diagnostics.csv` viser bl.a.
tilpasset varians og oprindelig varians. `skipped.csv` viser antagelser, der
strider mod en observeret fuldført sejltid. En kørsel med uforenelige tidsgrænser stoppes, så problematiske aftener ikke
udelades fra resultaterne uden videre.

Eksempel fra standardkørslen: Med **antaget** 130 minutter fra første start til
frist, planlagt middel på 105 minutter og alle DNF behandlet som tidsoverskridelser:

| Bane | Før | +15 minutter, samme startorden | +15 minutter, byttet |
|---|---:|---:|---:|
| Lille | 5,71 % | 4,57 % | 2,77 % |
| Stor | 5,98 % | 5,49 % | 7,44 % |

Det er en illustrativ, middel-normaliseret sammenligning; tallene er **ikke** en
prognose under bekræftede sejladsbestemmelser. I dette eksempel gavner ombytning
lille og skader stor. Sammenlignet med længere bane og samme startorden stiger
summen fra ca. 19,2 til 19,9 forventede overskridelser over de 379 bådsejladser.
En forskel på under én forventet overskridelse er ikke en robust anbefaling.

På stor bane 24. juni stiger den tilpassede daglige log-varians fra ca. 0,00183
uden DNF-information til ca. 0,00643 med alle DNF censureret ved det antagne
130-minuttersvindue. Det illustrerer, hvorfor fuldførernes spredning ikke er nok.

Vejret i den ekstra sejltid, ændret strøm, ændret trafik ved startbytte og fejl
i arrangørens vindskøn er ikke modelleret. Dagsvariation i fordelingsformen
bevares, men middel-normaliseringen antager, at arrangøren rammer den planlagte
middelværdi. Ved afvigelser bør man supplere med højere/lavere middel-scenarier.
Den forskudte lognormalmodel kan give en lille sandsynlighed for negative
sejltider; maksimum rapporteres i diagnostikken (ca. 0,005 % i standardkørslen).

Før en beslutningsanbefaling mangler bekræftede tidsvinduer, planlagt middel-
sejltid og om muligt angivelse af hvilke DNF der skyldtes tidsgrænsen.

## Ny oplysning: 90 minutter fra første start

Arrangøren har oplyst 20:30, eller 90 minutter efter første start, også ved
tidligere efterårsstart. Scriptets standard er nu 90 minutter. Det er endnu
uafklaret, om dette er sidste frist for alle både eller en anden grænse.

Hvis det er fælles sidste frist, har stor bane 90 minutter og lille 85. Det
strider mod 36 fuldførte bådsejladser på stor og 40 på lille i de gemte 2026-data.
Se `finishers_after_90_minute_limit.csv`. Den 24. juni er selv de første
registrerede sejltider ca. 99,4 minutter på stor og 96,9 på lille.

En genkørsel med 90 minutter stopper derfor med en datakonflikt og gemmer
`incompatible_deadlines.csv`. Eksisterende filer i `distribution/` er de
tidligere **130/140/150-minutters følsomhedsscenarier**, ikke en analyse af den
nu oplyste regel. Vi må afklare reglen eller datagrundlaget før konklusioner.
