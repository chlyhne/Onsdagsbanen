# Onsdagsbanen: brug og drift

Kravlisten findes i [ONSDAGSBANE_KRAV.md](ONSDAGSBANE_KRAV.md). Webappen ligger i `src/onsdagsbane/` og ændrer ikke den eksisterende PDF-pipeline.

## Lokal afprøvning

Kør fra repoets rod med det eksisterende Python-miljø:

```bash
.venv/bin/python -m onsdagsbane --demo
```

Åbn `http://127.0.0.1:8090`. Demoen har administratorfunktioner og sin egen database i `build/onsdagsbane/demo-v1.sqlite3`. Demo kan kun bindes til loopback. Den bruges ikke af Docker- eller Render-konfigurationen.

Normal lokal opstart med rigtigt login:

```bash
.venv/bin/python -m onsdagsbane
```

Normal opstart bruger `build/onsdagsbane/app.sqlite3`. På første opstart indlæses de medfølgende offentlige Manage2Sail-data; efterfølgende er databasen autoritativ. Der foretages ikke automatisk genimport eller overskrivning fra Manage2Sail.

Til et offentligt udseende lokalt preview med et midlertidigt administratorlogin:

```bash
.venv/bin/python -m onsdagsbane --preview-admin --db build/onsdagsbane/preview.sqlite3
```

Vælg **Log ind → Log ind som administrator**. Adgangen er lokal, udløber senest to timer efter serverstart og findes kun i serverens hukommelse. Log ud og log ind igen for at skifte mellem offentlig visning og administrator. En normal server eller Docker/Render-udrulningen accepterer ikke denne preview-identitet. Flaget tillader kun binding til loopback.

## Arbejdsgange

- **Resultater:** Den offentlige side indeholder kun resultatvalgene og tabellen. Ét løb er valgt som standard. I løbsvalget findes også alle løb kombineret for lille/stor bane. Vælg pointstilling eller dagens tider, og ved dagens tider den konkrete sejlads. Kladders resultater kan kun ses af godkendte brugere. PDF-eksport indeholder offentliggjorte resultater.
- **Startlister:** Se starttider, DH, ekstra handicap og bane. Startlister er offentlige før resultaterne offentliggøres; de røber ikke kladders måltider.
- **Flere serier:** Administration viser alle serier i en selvstændig Serier-sektion. Opret serie tilføjer en ny serie, og Vælg skifter aktiv serie uden at blande ræs eller både.
- **Navigation og indstillinger:** Ny serie ved serievælgeren opretter en ny serie. Rediger serie ændrer den valgte series navn, fratræk og standardreference. Tilføj ræs ligger direkte i både administrationen og serieoversigten. Indstil bane åbner opsætningen af en bane i et allerede planlagt ræs.
- **Serie:** Opret først serien med ræs, fx R1–R18, og løbene på stor og lille bane. Sæsonplanen viser hvert ræs med begge baner og løbene under dem. Klik på et løb for at tilføje både for hele serien eller kun det ræs. Vælg Cirkelmål (standard), GPH eller Op/ned-mål for hvert ræs. Valget gælder begge baner. Vind vælges kun ved cirkel- og op/ned-mål; ved GPH er vindfeltet slået fra. Indstil derefter dato, startform og baneforløb på den relevante bane; først da er der en sejlads klar til måltagning. Eksisterende importerede resultater får denne struktur uden at ændre tider og handicaptal.
- **Fælles bådliste:** På Serie åbner **Genbrug både fra en anden serie** den samme bådfordeling som et klik på et løb. I venstre træ vælges målserie, bane og løb. I højre side filtreres på kildeserie. Markér bådene, og tryk **← Tilføj**. Både genbruges med deres fælles identitet og handicapsæt; resultater kopieres ikke mellem serier. Nye serie-tilmeldinger og løbsinddelingen gemmes samlet, så en fejl ikke efterlader delvise tilmeldinger. Hver deltagelse refererer sit valgte handicapsæt med ID; gamle sæt bevares.
- **Løbsinddeling:** Venstre side viser træet med serier, baner, løb og ræs samt bådene i det valgte løb. Højre side viser både, der kan tilføjes. Søgningen matcher flere ord på tværs af sejlnummer, navn, skipper, bådtype, klub og handicap ligesom ved måltagning. Markeringer bevares under søgning; **Markér alle viste** ændrer kun de viste både. **← Tilføj** og **→ Fjern** gemmer straks ændringen på serveren. Status skifter fra **Gemmer…** til **Gemt**, og først da flyttes bådene i visningen. Der er ingen knapper til forhåndsvisning, nulstilling eller manuel lagring. Ved fejl bevares markeringerne, så pilen kan prøves igen; gentagelse efter et tabt svar opretter ikke dubletter. **Hele serien** er standard; et ræs i træet vælger kun dét ræs. Vælg eventuelt fremover eller bagudrettet fra ræset. Pilene ændrer kun de markerede både og bevarer øvrige bådes forskellige inddelinger i de valgte ræs. Både fra andre løb flyttes automatisk i det valgte interval, så en båd kun tilhører ét løb pr. ræs. Nye ræs arver inddelingen fra seriens seneste planlagte ræs. Andre serier påvirkes ikke.
- **Ledige eller allerede fordelte både:** Standardlisten til højre viser kun både, som ikke er i noget løb i de valgte ræs i målserien. Ved **Hele serien** skjules en båd, selv hvis den kun er fordelt i ét af seriens ræs. Ved valg af et enkelt ræs eller et interval tæller kun disse ræs. Fluebenet **Medtag allerede fordelte både** viser dem igen og giver mulighed for at flytte dem til det valgte løb. Tilføj-pilen fjerner automatisk tilmeldingen til andre løb i netop de berørte ræs. Slås fluebenet fra, fjernes markeringer af både, som ikke længere er ledige. Andre, selvstændige seriers tilmeldinger spærrer ikke for genbrug. Kontrollen bygger på permanente backend-UID'er for ræs, aldrig på navne eller datoer. Databasen forhindrer tilmelding til to løb i samme ræs.
- **Historisk løbsinddeling:** Slå **Medtag allerede fordelte både** til, og flyt en båd ved at vælge den i det nye løb på samme bane og bruge tilføj-pilen. Ændringen gemmes automatisk. Måltider, statusser, starttider, distancer og handicapsæt bevares. Berørte resultatpoint genberegnes i det nye løb, og sejladserne bliver kladder til ny offentliggørelse. Afsluttede ræs beholder deres afslutningsstatus. Historiske deltagere kan ikke afmeldes her, og baneskift med registreringer kræver en anden arbejdsgang. Hele pilens ændring valideres og gemmes samlet; en afvisning i ét ræs ruller alle dens ændringer tilbage.
- **Starttider:** Standardstart for stor og lille bane indstilles på serien og arves af nye ræs. Rediger ræs ændrer dato og starttider kun dér, før måltagning begynder. Ræs og bane kan gemmes uden både; tilmelding kan ske bagefter.
- **Både og løbsinddeling:** Administrator kan oprette, redigere og slette både. I tabellen står **Rediger** og **Slet** i første kolonne, så de er synlige uden at rulle gennem DH-tallene. I bådfordelingen har hver båd også rediger- og sletknapper. Fælles bådoplysninger kan redigeres, selv om båden ikke er tilmeldt målserien. Efter redigering eller sletning vender man tilbage til samme løb og søgning. Seks DH-tal er valgfrie. GPH beregnes fra TACIL, TACIM og TACIH med vægte 3:12:5 og vises med én decimal; uden alle tre vises, at grundlaget mangler. Se [DH 6.1.1](https://dh.websejler.dk/da/somiletiderne), kontrolleret 2. november 2026. En beregning blokeres, hvis det konkret valgte tal mangler. Ekstra handicap gemmes separat.
- **Slet båd fra systemet:** Sletning gælder hele fælleslisten, ikke kun den valgte serie. Den er kun mulig, når båden er fjernet fra samtlige ræs i alle serier, herunder planlagte ræs uden dato og gemte deltagelser uden en aktuel løbstilmelding. Så længe båden er med i et ræs, viser dialogen kun en liste med serie, ræs, bane og løb samt en lukkeknap. Den har ingen knap til automatisk afmelding eller sletning. Navigér selv til hvert ræs i løbsinddelingen og fjern båden enkeltvis. Fjern-pilen kræver **Kun [ræs]**; samlet afmelding fra hele serien eller et interval er blokeret. Flytning mellem løb med tilføj-pilen kan fortsat gælde en hel serie eller et interval. Når ingen ræs refererer til båden, kan **Slet båd fra systemet** efter bekræftelse fjerne båden, dens handicapsæt og eventuelle tomme serietilmeldinger. Sletning fjerner aldrig ræs-tilmeldinger eller resultater. Hvis båden tilmeldes et ræs, mens slettedialogen er åben, bliver sletningen blokeret igen.
- **Måltagning:** Dagens åbne ræs vælges automatisk, ellers det næste åbne ræs. Måltagning sker samlet for stor og lille bane. Ved hver båd vises bane og løb. Ét fælles søgefelt filtrerer listerne Ikke måltaget og Måltaget øjeblikkeligt i browseren; tom søgning viser alle. Pin en båd for at holde den øverst i Ikke måltaget, også når den ikke matcher søgningen.
- **Båd først:** Tryk Måltag i bådens række, når den krydser linjen. Klik-tidspunktet fastholdes i det aktive felt over listerne. Måltiden vises som HH:MM:SS, og datoen står separat som DD-MM-ÅÅÅÅ. Ret tiden om nødvendigt, og tryk Bekræft. Først når serveren har bekræftet lagringen, flyttes båden til Måltaget. Flere påbegyndte måltagninger bevarer hver deres tid.
- **Tid først:** Brug Tag tid nu uden valgt båd, eller Indtast tid uden båd. Søg derefter båden, og tryk Måltag for at knytte den valgte tid til den. Gennemgå tiden i det aktive felt og bekræft. Ufordelte tidsstempler kan vælges igen eller kasseres; hvert tidsstempel kan kun bruges én gang.
- **Ret/fortryd:** Ret ved en måltaget båd åbner den aktive registrering. Ret og bekræft, eller vælg Flyt tilbage til ikke måltaget. Pins og ubekræftede tider opbevares lokalt pr. sejlads, så en genindlæsning ikke mister forberedte målgangstider. Bekræftede tider ligger på serveren.
- **Dommertid:** Find båden under måltagning og tryk Dommertjans. Afslut ræs tildeler DNC og beregner derefter dommertider automatisk. Hvis nødvendige tider eller historik mangler, afvises afslutningen med forklaring; der gemmes ikke en delvis afslutning.
- **Startlister:** Kun omvendt respit-sejladser vises. Menupunktet skjules for serier uden omvendt respit.
- **Jagtstart:** Kun rækkefølge gemmer en placering uden at opfinde en tid. Pilene retter rækkefølgen. Redress kræver tidsmålte målgangsbåde og brugbar historik; ved jagtstart skal alle målgangsbåde have tider, hvis redress ønskes.
- **Afslut ræs:** Afslutter alle løb på begge baner og giver DNC til de resterende både. Dommertider beregnes automatisk som del af afslutningen. Afklar løse/ubekræftede tider først. Genåbn ræs fjerner kun automatiske DNC, bevarer gemte måltider og manuelle statusser og gør begge baners resultater til kladder.
- **Offentliggørelse:** Både resultatindtaster og administrator kan offentliggøre og åbne for rettelser. Både uden resultat får DNC efter udtrykkelig bekræftelse. Ufordelte tidsstempler skal tildeles eller kasseres først.
- **Historisk beregningsgrundlag:** På Måltagning vælges det gamle ræs og Beregningsgrundlag. Bevar alle gemte handicaptal, eller ret én af de historiske deltagere, også hvis båden nu sejler en anden bane. Angiv årsag og forhåndsvis ændringerne. Kun dette ræs genberegnes og bliver en kladde. Deltagere, starttider og måltider bevares; stamdata og andre ræs ændres ikke. Ukendte historiske DH-tal skal oplyses udtrykkeligt, før en ny vindkategori kan bruges.
- **Historisk handicapændring:** Som standard berøres kun nye sejladser. Vælg genberegning i bådformularen og forhåndsvis ændringerne, hvis eksisterende deltagelser skal ændres. Berørte sejladser bliver kladder, og redress skal beregnes på ny.
- **Reference:** Administrator kan ændre seriens standard, sætte en individuel sejladsreference eller udtrykkeligt ensrette hele serien. Tidsskiftet er ens for både på samme distance, så point og lighed i tider bevares.
- **Dagens distance og vind:** Administrator kan sætte en mærkerækkefølge for alle løb på én bane og vindkategori på tværs af begge baner. Forhåndsvisning er nødvendig. Ændrede resultater bliver kladder til fornyet kontrol og offentliggørelse.
- **Godkendelser:** Første Google-login med `hummesse@gmail.com` opretter ejeren. Kun denne Google-identitet kan godkende andre konti og tildele roller. Andre konti er afventende ved første login. Google- og Facebook-konti sammenkædes ikke automatisk via mailadressen.

Resultatvalgene gemmes i cookien `ob_results` i ét år. Cookien indeholder kun serie, bane, løb, visning og valgt sejlads. Den giver ingen adgang eller rettigheder. URL'en opdateres med de samme indstillinger, så adressen kan deles direkte eller via **Kopiér link**. Eksempel:

```text
/?series=1&course=stor&class=Stor+bane+1&view=daily&race=18
```

Et delt links valg har forrang frem for modtagerens cookie. `class=all` vælger den samlede visning på den angivne bane; `view=overall` vælger pointstilling, mens `view=daily` vælger dagens tider. Browserens tilbage/frem-knapper skifter mellem tidligere resultatvalg. Indtastnings- og administrationsmenuen vises kun for godkendte brugere. Kladder kan ikke deles med den offentlige linkknap.

## Beregninger og historik

Almindelig respit bruger DH §6.3.1: `CT = round(ET + (reference − DH) × distance) + ekstra_handicap + straftid`. DH-tal er s/sm, distance er sømil og afrunding er til nærmeste hele sekund med halve op. Jagtstart bruger repoets `calculate_starts`; en båds ekstra handicap lægges til dens starttid. Jagtstart scores efter målgangsrækkefølgen. Straftid er et separat felt på resultatet for én sejlads; det arves aldrig fra båden. Ved jagtstart kræver straftid måltider for alle målgangsbåde, så den justerede placering kan beregnes.

Seriens fulde deltagerliste indgår i alle sejladser ved beregning af resultater. En båd, der først dukker op i kilden sidst på sæsonen, får DNC i alle tidligere manglende resultatlinjer. Det konsoliderede deltagerantal bruges også til DNC/DNF-point for de øvrige både. Det gælder både enkeltløb og kombinerede baner, og seriens fratræk beregnes på den konsoliderede stilling. Kildens tider og registreringer bevares; oprindelige nettopoint kan afvige fra den korrigerede stilling. Resultatpoint beregnes i den valgte løbsvisning. Laveste point vinder. Pointlighed på dagen deles efter A7; serielighed afgøres efter A8. Seriens fratræk anvendes, men DNE kan ikke fratrækkes. Disse indstillinger skal stemme med klubbens sejladsbestemmelser, før systemet bruges til officielle resultater.

Hver deltagelse refererer sit anvendte handicapsæt og gemmer distance og starttid. Fremtidige stamdataændringer flytter ikke historiske referencer. Udtrykkelige historiske rettelser og ændringer af vind/distance kan påvirke stillingen og gemmes derfor med forhåndsvisning og ændringshistorik. Faktiske måltider bevares.

Ved historisk omberegning af en jagtstart med måltider bruges de faktiske starter og måltider til at beregne de nye DH-tider og den justerede rækkefølge. Uden måltider blokeres omberegningen. Før nogen er kommet i mål, kan den planlagte startliste beregnes på ny.

Redress-adapteren kalder direkte `m2s_combiner.redress.model.run_group_filter` og repoets `_enrich_history`, med de eksisterende modelparametre og starttilstande. Den bruger tidligere offentliggjorte tidsmålte sejladser i serien og dagens observationer. Tider omregnes til reference 1000 uden ekstra handicap før modelkørslen og tilbage til sejladsens valgte reference og bådens ekstra handicap bagefter. Rene rangobservationer indgår ikke som opdigtede tider. Nye observationer eller rettelser på dagen fjerner den beregnede dommertid, men bevarer status Dommertjans som afventer beregning, så Afslut ræs kan beregne den igen på det nye grundlag.

## Anbefalet enkel drift: Render med vedvarende disk

Der medfølger Docker-konfiguration samt `deploy/onsdagsbane/render.yaml`. Opret en Render Web Service med Dockerfile `deploy/onsdagsbane/Dockerfile`, repoets rod som build context, og en vedvarende disk på `/data`. Databasen ligger i `/data/onsdagsbane.sqlite3`.

Render kræver en betalt service til en vedvarende disk. Uden disken forsvinder databaseændringer ved genstart eller genudrulning. Se [Render: persistent disks](https://render.com/docs/disks). Appen kan automatisk bruge den [RENDER_EXTERNAL_URL, som Render stiller til rådighed](https://render.com/docs/environment-variables); et eget domæne kræver `BASE_URL=https://dit-domæne.dk`.

Der er ikke oprettet en Render-konto, købt hosting eller udgivet en offentlig hjemmeside som del af denne ændring. Før offentlig drift skal udbydernes appnøgler oprettes, lægges i serverens miljø og afprøves på den faktiske HTTPS-adresse.

### Google og Facebook

Opret en webklient hos Google og en loginapp hos Meta. Konfigurér nøjagtige callback-adresser:

```text
https://din-hjemmeside/auth/google/callback
https://din-hjemmeside/auth/facebook/callback
```

Sæt følgende miljøvariabler på serveren:

```text
BASE_URL=https://din-hjemmeside
ONSDAGSBANE_DB=/data/onsdagsbane.sqlite3
GOOGLE_CLIENT_ID=...
GOOGLE_CLIENT_SECRET=...
FACEBOOK_CLIENT_ID=...
FACEBOOK_CLIENT_SECRET=...
FACEBOOK_GRAPH_VERSION=v25.0
```

Google bruger authorization code, PKCE, state, serverudveksling og verificeret mail fra userinfo. Facebook bruger serverudveksling, state, bruger-id og `appsecret_proof`; mailadressen skal deles. Apphemmeligheder sendes ikke til browseren. Sessioner udløber efter 12 timer, og loginforsøg efter 10 minutter. Produktion på HTTPS bruger Secure/HttpOnly/SameSite-cookies. Serveren kontrollerer rettigheder og CSRF ved skrivning.

Følg [Googles vejledning](https://developers.google.com/identity/protocols/oauth2/web-server) og [Metas vejledning](https://developers.facebook.com/docs/facebook-login/guides/advanced/manual-flow/) for domæner, appstatus og nødvendige godkendelser. Den valgte Meta API-version skal være understøttet af din app.

### Docker på egen server

Kopiér `deploy/onsdagsbane/.env.example` til `.env` i samme mappe og udfyld værdierne. Kør fra repoets rod:

```bash
docker compose -f deploy/onsdagsbane/compose.yaml up --build -d
```

Tjenesten bindes til `127.0.0.1:8090`; en HTTPS-reverseproxy foran den bruger den samme adresse, som `BASE_URL` angiver. Der kører én Gunicorn-proces med fire tråde. SQLite serialiserer skrivninger og afviser forældede rettelser via versionsnumre.

### Backup

Databasen indeholder både resultater, brugeradgang og ændringshistorik. Gem løbende en backup uden for serveren. Brug SQLite's online backup-funktion frem for at kopiere kun databasefilen, mens WAL-skrivninger foregår:

```python
import sqlite3
with sqlite3.connect('/data/onsdagsbane.sqlite3') as source:
    with sqlite3.connect('/data/backup.sqlite3') as target:
        source.backup(target)
```

## Verifikation

```bash
.venv/bin/python -m unittest discover -s tests/onsdagsbane -v
.venv/bin/python -m unittest discover -s omvendt_respit/tests -v
node --check src/onsdagsbane/static/app.js
```

`tests/onsdagsbane/results-browser.cjs` kontrollerer den anonyme side: standardløb, cookie, delte links med forrang, tilbage/frem, kopiering og mobilvisning. Kør den med en normal lokal server på port 8093, eller angiv serveradressen i `ONSDAGSBANE_BASE_URL`. Den eksisterende `browser.cjs` afprøver indtastningsarbejdet mod en isoleret demo.

Rigtige OAuth-login og containerudrulning kræver ekstern opsætning og er ikke verificeret mod brugerens konti. Den lokale browserkontrol og automatiske tests af API, rettigheder og beregninger erstatter ikke denne sidste driftsprøve.
