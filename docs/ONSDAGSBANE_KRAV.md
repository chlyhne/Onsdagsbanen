# Krav til Onsdagsbanens hjemmeside

Denne fil er kravlisten for erstatningen til Manage2Sail. Den opdateres ved nye krav. Sidst opdateret: 2. november 2026.

## Formål og grundlag

- En hjemmeside til Onsdagsbanen med offentlig adgang til resultater.
- Brug eksisterende Manage2Sail-data som startgrundlag.
- Undersøg og følg Dansk Sejlunions definitioner af DH og omvendt respit.
- Genbrug repoets algoritmer til banelængde, starttider og dommertjans-redress.
- Gør løsningen enkel at sætte i drift; brugeren har endnu ikke domæne eller server.
- Prioritér lav risiko for indtastningsfejl.

## Både og handicap

- Både ligger i én fælles bådliste, som kan genbruges på tværs af serier. Bådens identitet og handicapobjekter deles; tilmelding, bane og løb tilhører den enkelte serie og det enkelte ræs. En eksisterende båd kan tilføjes fra den fælles liste uden at kopiere stamdata eller handicapværdier.
- Understøt seks selvstændige, valgfrie DH-tal: TACIL, TACIM, TACIH, TAUDL, TAUDM og TAUDH.
- Beregn og vis **GPH** i stamdata som `(3 × TACIL + 12 × TACIM + 5 × TACIH) / 20`, afrundet til én decimal i sekunder pr. sømil efter [DH 6.1.1](https://dh.websejler.dk/da/somiletiderne). GPH kræver alle tre cirkeltal; manglende tal må ikke erstattes med TAUD-tal eller nul. GPH udledes af de relevante DH-tal og må ikke påvirkes af ekstra handicap eller straftid. Historisk GPH kan kun udledes af de kendte historiske cirkeltal.
- Manglende DH-tal må ikke automatisk erstattes med andre tal. Den konkrete beregning kræver sit valgte DH-tal.
- Tilmelding til ræs eller serier skal afvises, hvis det relevante handicaptal mangler i et af de valgte ræs. Ræs uden valgt vind bruger mellem vind som standard. GPH kræver alle tre cirkeltal; øvrige uanvendte tal må gerne mangle. Skift af beregningsmål eller vind skal valideres mod alle deltagere og deres relevante gemte grundlag før lagring.
- Handicapfejl skal have en knap til bådens redigering med mulighed for at vende tilbage til samme formular, søgning, markeringer og løb. En historisk rettelse gennem denne genvej må kun supplere manglende tal i det konkrete ræs; allerede anvendte tal og mål-/starttider bevares.
- Handicapændringer under sæsonen må som standard ikke genberegne tidligere resultater.
- **Handicapsæt er selvstændige objekter med en obligatorisk relation til én fælles båd.** Båden peger på sit aktuelle standardsæt, og deltagelser i ræs peger på det valgte sæts ID. Tallene må ikke kopieres til hver serie eller deltagelse. Samme sæt kan anvendes i flere løb og serier; samme båd kan have flere sæt gennem tiden.
- Den bruger, der ændrer et handicap, skal udtrykkeligt kunne vælge genberegning af tidligere resultater.
- Vis konsekvenserne før en udtrykkeligt valgt historisk genberegning; dokumentér ændringen i historikken.
- Et gammelt ræs skal kunne genberegnes længe efter afholdelsen, selv om deltagerne har ændret mål/handicap, skiftet løb/bane eller ikke længere sejler. Brug sejladsens gemte deltagere og beregningsgrundlag; nutidens stamdata må ikke automatisk erstatte historiske værdier.
- Handicapsættet indeholder de kendte DH-tal, inklusive alle seks når de kendes. For ældre importerede data må ukendte historiske tal ikke erstattes med nutidens tal. Identiske sæt for samme båd genbruger ét fælles ID, også når en rettelse giver samme værdier som et eksisterende sæt. Sæt på forskellige både sammenlægges aldrig.
- En rettelse af historisk handicap kan foretages for en bestemt båd i ét bestemt ræs uden at ændre stamdata eller andre ræs. Forhåndsvis før/efter, kræv en begrundelse, gem ændringshistorik, og gør resultatet til kladde for ny offentliggørelse.
- Hvis stamdata udtrykkeligt overføres til historikken, vælger brugeren konkret hvilke ræs der skal ændres.
- **Ekstra handicap:** Et selvstændigt felt i bådens stamdata, fx +20 sekunder. Det er ofte frivilligt og er ikke en straf. De seks normale DH-tal ændres ikke af dette felt.
- Ekstra handicap lægges til præmietiden ved almindelig respit og til starttidspunktet ved jagtstart.
- Ekstra handicap hører til det refererede handicapsæt. Fremtidige ændringer skifter standardsæt og påvirker ikke historiske referencer uden udtrykkeligt valg. Alle tidligere sæt bevares, også når ingen ræs længere bruger dem. Dette erstatter ønsket om automatisk sletning af ubrugte sæt.
- **Straftid:** En separat tid på bådens resultat i en bestemt sejlads. Den ændrer ikke ekstra handicap eller DH i stamdata. Resultatindtaster og administrator kan angive og rette den med resultatet.
- Vis ekstra handicap og straftid hver for sig i relevante visninger og beregninger.
- Ved jagtstart kræver en efterfølgende tidsstraf måltider, så en justeret placering kan beregnes uden gæt.
- Serien har et indstilleligt referencehandicap (standard ved første opsætning: 1000 s/sm).
- Hver sejlads har sit eget indstillelige referencehandicap og arver seriens værdi ved oprettelse.
- Vis en advarsel, hvis sejladserne i samme serie bruger forskellige referencehandicap.
- Gør det muligt udtrykkeligt at sætte samme referencehandicap for hele serien.
- Et referenceskift giver alle både på samme distance samme tidsforskydning og må ikke ændre deres indbyrdes stilling. Hvis importerede løb har forskellige distancer, skal den samlede visning advare om sammenligneligheden.
- Redress skal anvende et ensartet internt referencegrundlag og derefter omregnes til sejladsens valgte reference.
- Vis en advarsel, hvis løb på samme bane på samme sejladsdag har forskellige distancer.
- Vis en advarsel, hvis dagens vindkategori er forskellig mellem løb, også på tværs af lille og stor bane.
- Gør det muligt at ensrette dagens distance for alle løb på en bane og vindkategori for alle løb på begge baner med én handling.

## Serier og sejladser

- Hierarkiet er **serie → ræs (fx R1–R18) → stor/lille bane → løb → både**.
- Ved oprettelse af en serie udfyldes rækken af ræs og løbene på hver bane. Hvert ræs oprettes med begge baner og deres løb, også før dato, bane, handicapvalg eller resultater er kendt.
- **Serier** skal være et selvstændigt arbejdsområde med alle serier og Opret serie. Administration bruges til adgang og samlet historik. Serie og ræs har egne adresser og kontekst, som bevares ved genindlæsning og tilbage/frem.
- Seriens egne faner samler ræsoversigt, serietilmeldinger, **Både og løbsinddeling**, resultater og indstillinger. Tilføj ræs findes i ræsoversigten. Hvert ræs samler opsætning, startlister, måltagning, resultater og historik.
- Serienavn, fratræk og standardreference skal kunne ændres for en eksisterende serie. Årstallet kan ændres, før der er indstillet sejladser; eksisterende sejladser må ikke flyttes mellem år ved en almindelig indstillingsændring. Standardreferenceændring påvirker kun nye sejladser.
- Administrator kan planlægge hele serien i løbsinddelingen. Standardintervallet er hele serien; fremover, bagudrettet og ét ræs kan vælges. En båd kan kun tilhøre ét løb i samme ræs i en serie, kontrolleret med backend-identitet uden afhængighed af dato eller navn. Andre serier spærrer ikke for genbrug. Historiske flytninger på samme bane bevarer tids- og handicapgrundlag og gør berørte resultater til kladder. Fjernelse kræver manuel behandling ét ræs ad gangen. Stor og lille bane har hver sin pointstilling; skift af løb på samme bane giver ikke dubletter i en kombineret stilling.
- Dato, startform, rundingsmærker og DH-valg indstilles for en bane i et eksisterende ræs. Planlagte ræs uden resultater må ikke optræde som gennemførte ræs eller ændre pointstillingen.
- Bevar eksisterende importerede resultater og deres anvendte handicapgrundlag ved overgangen til dette hierarki.
- Hvert ræs har ét valg af beregningsmål for begge baner: **Cirkelmål** (standard), **GPH** eller **Op/ned-mål**. Cirkel og op/ned bruger henholdsvis TACI- og TAUD-tallet for vindkategorien. GPH bruger det beregnede GPH-tal. Valget gemmes på ræset og i sejladsens historiske grundlag.
- Ved GPH er vindkategori **ikke relevant** og kan ikke indstilles, hverken ved oprettelse eller ved senere rettelser af dagens opsætning. Der må ikke gemmes en kunstig vindkategori for GPH.
- Almindelige respitsejladser og omvendte respitsejladser skal kunne indgå i samme serie.
- Kun administratorer må oprette sejladser.
- Understøt lille og stor bane med flere løb på hver bane.
- Serien har separate standardstarttider for stor og lille bane. De nye ræs arver hver deres egne tider; standardforslaget er 18:30:00 og 18:35:00, men forskellen er ikke en regel. Ved Tilføj ræs angives begge tider, og dato/tider kan rettes under Rediger ræs. Ændring af seriens standard påvirker kun nye ræs.
- Ræsindstillinger og baneforløb skal kunne gemmes uden deltagere. Både kan tilføjes bagefter, og beregninger udskydes, til der findes nødvendige handicaptal. Rækkefølgen af opsætning og tilmelding må ikke blokere arbejdet.
- Når dagens bane indtastes som mærkerækkefølge, beregnes distancen automatisk fra repoets mærkekoordinater.
- Opret jagtstarter og beregn bådenes startrækkefølge og starttidspunkter.
- Gem deltagerliste, reference til det anvendte handicapsæt, distance og starttid pr. deltagelse. Gem straftid på resultatet.

## Måltagning

- Listen over måltagne både sorteres løbende efter den faktiske måltid, seneste øverst, på tværs af begge baner. Datoen indgår, så rækkefølgen også er korrekt over midnat. Rettelser og urkorrektion opdaterer rækkefølgen. Registreringer uden måltid vises efter de tidsmålte både.

- Sidens ur kan synkroniseres til nærmeste minut med **Synkronisér ur** og justeres med **−1 minut / +1 minut**. Korrektionen gemmes pr. ræs og gælder begge baner og allerede gemte automatisk tagne tider. Beregnede tider opdateres tilsvarende.
- Manuel indtastning eller rettelse af dato/tid markerer tiden som manuel. Manuelle tider kommer fra tårnets analoge ur og må ikke flyttes ved ursynkronisering. Dette gælder også aktive, ubekræftede indtastninger.

- Måltagning må begynde uden baneopsætning. Måltider og statusser ved bådene gemmes på serveren og bevares ved genindlæsning. Ingen beregnede tider vises, før grundlaget er kendt. Alle baner med deltagere skal være indstillet, før ræset kan afsluttes eller offentliggøres.
- Dato, starttider og baner for det aktuelle ræs skal kunne indstilles direkte fra måltagningssiden. Opsætningen må ikke flytte brugeren til et andet ræs eller ændre de gemte måltider.

- Når Måltagning åbnes, vælges automatisk dagens ikke-afsluttede ræs, ellers det næste ikke-afsluttede ræs. Datovalg tager udgangspunkt i dansk tid. Brugeren kan stadig vælge et andet ræs manuelt.
- **Måltagning foregår samlet for stor og lille bane i samme ræs.** Begge baners både vises i samme søgning og de samme to lister. Bane og løb skal fremgå ved hver båd.
- **Afslut ræs** afslutter alle løb på begge baner samlet. Resterende ikke-måltagne både får en eksplicit DNC-registrering uden opdigtet tid. Gemte tider, manuelt registrerede statusser og Dommertjans bevares. Ubekræftede tider ved bådene skal afklares først.
- Genåbning gælder begge baner, fjerner kun automatisk tildelte DNC og bevarer faktiske måltider og manuelle statusser. Offentliggjorte resultater bliver kladder igen. Dommertid kan først beregnes efter samlet afslutning.
- Vis to lister ved siden af hinanden: **Ikke måltaget** og **Måltaget** (stablet på smalle skærme).
- Ét fælles søgefelt filtrerer begge lister øjeblikkeligt, mens der tastes. Søgningen kører på data i sidens variabler. Uden søgetekst vises alle både.
- Pin-funktionen er bruttolisten: pinnede, ikke-måltagne både står øverst og vises altid, uanset søgeteksten.
- Måltagning starter altid ved at vælge båden og trykke Måltag. Der er ingen funktion til tider uden en valgt båd.
- Knappen **Måltag** ved en ikke-måltaget båd fastholder klokkeslættet fra klikøjeblikket og åbner båden som aktiv over begge lister.
- Måltid og dato har hvert sit felt. Tiden vises som **HH:MM:SS** og datoen i dansk format **DD-MM-ÅÅÅÅ**. Datoen er udfyldt på forhånd og ændres ikke, når kun klokkeslættet rettes. Den præcise klik-tid bevares, indtil brugeren retter dato eller tid.
- Den aktive tid kan rettes før **Bekræft**. Først efter bekræftet lagring flyttes båden til listen med måltagne både.
- **Ret** ved en måltaget båd åbner samme aktive felt. Brugeren kan ændre resultatet eller flytte båden tilbage til ikke måltaget.
- Flere påbegyndte måltagninger må ikke overskrive hinandens klik-tider. Ubekræftede tider og pins bevares ved genindlæsning i samme browser.
- Hurtig søgning på delvist sejlnummer, bådnavn, skipper og alle tilgængelige bådstamdata.
- Vælg båd fra søgeresultater, så identiteten kan kontrolleres.
- Foreslå det aktuelle tidspunkt i tidsfeltet.
- En knap til at tage tiden, idet en båd passerer mållinjen.
- Klokkeslættet fastholdes ved klik på Måltag ved den konkrete båd.
- Understøt manuel tid og resultatstatus, fx DNC, DNS og DNF.
- Startlistevisningen gælder kun omvendt respit-sejladser. Almindelige respitsejladser skal ikke kunne vælges dér. Menupunktet skjules, hvis serien ingen omvendt respit-sejladser har.
- Ved jagtstart skal det være muligt kun at indtaste målgangsrækkefølgen uden tider.
- Gør det muligt at rette/fortryde fejl og rette jagtstartens rækkefølge.
- Beskyt mod dobbeltregistrering, ugyldige tider og samtidige ændringer.
- Tider skal indtastes i egentlige tidsfelter og valideres strengt på serveren. Vilkårlig tekst må ikke gemmes som en tid.
- Accepter komma og punktum som decimaltegn i talfelter, og ret dem automatisk til samme format ved indtastning. Afvis ugyldige eller tvetydige tal frem for at gætte.
- Resultatindtastere såvel som administratorer må offentliggøre resultater/tider.
- Gem kladder adskilt fra offentliggjorte resultater.
- Gem ændringshistorik og vis tydeligt, om en registrering er gemt på serveren.

## Dommertjans / redress

- Tildel redress direkte i hjemmesiden via repoets eksisterende algoritme.
- Dommertid kan først beregnes efter sejladsens afslutning, når alle øvrige deltagere har registreret måltid eller afsluttende status. Ubekræftede registreringer ved bådene skal afklares først. Status **Dommertjans** tildeles ved at finde båden under måltagningen og trykke **Dommertjans**. Den vises uden opdigtet tid. **Afslut ræs beregner automatisk alle dommertider**, efter DNC er tildelt på begge baner. Ingen separat beregningshandling kræves. Hvis modellen mangler et nødvendigt grundlag, skal afslutningen afvises med en konkret forklaring, og ingen delvise ændringer gemmes; ændres observationerne, bevares dommertjans-status og tiden skal beregnes igen. Serveren skal også afvise for tidlig beregning.
- Vis redress tydeligt i resultaterne.
- **Brugerens præcisering:** Ved jagtstart kan redress kun tildeles, hvis der indtastes tider. Ren målgangsrækkefølge er tilstrækkelig til scoring, men ikke til redress.
- Beregn ikke en redress-tid, når modellen mangler nødvendige observationer eller historik.
- Ændringer i dagens observationer skal medføre fornyet redress-beregning.

## Offentlige resultater

- Alle kan se resultater uden login.
- Hold hjemmesiden så simpel som muligt. Resultater skal stå i centrum uden statistikwidgets, kort, infokort eller salgsagtige tekster omkring tabellen.
- Prioritér læsbarhed for ældre sejlere: stor, mørk tekst, høj kontrast, tydelige tabeloverskrifter og kompakt afstand. Undgå svag grå tekst og store dekorative luftområder.
- Kolonnerne skal følge indholdets bredde. Point og andre tal må ikke strækkes unødigt for at fylde skærmen.
- En tabelkolonne må aldrig være smallere end højden af et af dens felter, heller ikke ved et enkelt tegn. Bredden skal tilpasses ved ændret indhold og skærmstørrelse.
- Gennemgå visningen efter gængse principper for design og læsbarhed, herunder kontrast, linjeføring, ensartede afstande og tabelstruktur. Gennemgangen er dokumenteret i [ONSDAGSBANE_DESIGN.md](ONSDAGSBANE_DESIGN.md).
- Besøgende uden login skal kun præsenteres for resultatvisning og muligheden for at logge ind. Arbejdsnavigation vises først for godkendte brugere.
- Vis ét løb som standard. Samlet visning for en bane skal være et aktivt valg.
- Husk seneste valg af serie, bane, løb, visning og sejlads i en indstillingscookie.
- Delbare dybe links skal indeholde alle resultatindstillinger, inklusive dagens tider/pointstilling og den valgte sejlads.
- Valg i et delt link har forrang frem for modtagerens cookie. Tilbage/frem i browseren skal genoprette den tidligere resultatvisning.
- Vælg lille eller stor bane.
- Se hvert enkelt løb separat eller alle løb på den valgte bane kombineret.
- Begge visninger skal findes for dagens tider og for seriens samlede pointstilling.
- Ved dagens tider skal distance og vindkategori stå tydeligt over tabellen for den valgte sejlads og det valgte løb. Ved kombineret visning vises flere værdier, hvis løbene bruger forskellige distancer eller vindkategorier.
- Vis den valgte bane som mærkernes bogstaver i rækkefølge, røde for bagbordsrunding og grønne for styrbordsrunding. Genbrug `rounding_sides` fra `omvendt_respit`, som PDF-startlisten gør. Manglende mærkerækkefølge i importerede data skal markeres som ikke registreret.
- Vis handicapberegning, tider, placeringer, status og point, hvor oplysningerne findes.
- Understøt seriens fratræk og lige point.
- Konsolidér seriens fulde deltagerliste i alle sæsonens sejladser. En sent tilmeldt båd, fx Fly, skal have DNC i tidligere sejladser, selv om kilden hverken har DNC, DNF eller nogen resultatlinje for båden. Det gælder både enkeltløb og kombineret banevisning, inklusive deltagerantal, point og fratræk.
- Kolonneoverskriften for det anvendte handicaptal skal være **Hdcp.**
- Brug repoets nuværende PDF-visninger som fagligt udgangspunkt; resultater skal kunne ses direkte på hjemmesiden og eksporteres.

## Brugere og adgang

- Login via egen Google- eller Facebook-konto; ingen lokale adgangskoder.
- Brugeren opretter selv sin konto ved første login.
- Nye brugere afventer godkendelse fra **hummesse@gmail.com**.
- To roller: **resultatindtaster** og **administrator**.
- Resultatindtaster: Indtast og ret resultater samt offentliggør tider/resultater.
- Administrator: Alle funktioner, herunder oprettelse af serier/sejladser, bådstamdata og redress.
- Ejeren hummesse@gmail.com godkender brugere og tildeler roller.
- Rettigheder skal håndhæves på serveren.
- Til lokal afprøvning skal et midlertidigt administratorlogin kunne aktiveres, så administrationen kan ses uden konfigureret Google/Facebook-login. Preview-identiteten må ikke fungere i normal drift og skal udløbe.

## Kilder

- [Onsdagsbanen 2026 på Manage2Sail](https://www.manage2sail.com/da-DK/event/Onsdagsbanen2026#!/)
- [Dansk Sejlunion: DH-reglens ændringer og de seks sømiletider](https://dansksejlunion.dk/kapsejlads/regler-og-vejledning/aendringer-til-dh-reglen)
- [DH §6.3.1: Resultatberegning, sømiletid](https://dh.websejler.dk/da/somiletid2)
- [DH §6.4.1: Omvendt respit](https://dh.websejler.dk/da/somiletid3)

## Arbejdsstatus

Resultatsiden er forenklet til løbsvalg, visningsvalg og én tabel. Anonyme besøgende har ingen arbejdsnavigation eller widgets. En særskilt browserprøve verificerer standardløb, indstillingscookie, delte links med forrang over cookies, tilbage/frem, linkkopiering, ugyldige indstillinger og mobilvisning.

Implementeringen ligger i `src/onsdagsbane/`. Den aktuelle struktur beskrives i [ONSDAGSBANE_V2.md](ONSDAGSBANE_V2.md). V2 er kontrolleret med 126 backendtests, 27 tests for omvendt respit og browserprøver for både administration og offentlige resultater. Migreringen fra checkpointet bevarer alle 514 registreringer, historiske grundlag, UUID'er og seks resultatvisninger uændret. Browserprøverne dækker blandt andet automatisk løbsinddeling med genforsøg efter tabt svar, genindlæsning, datoopsætning, pins, måltagning på den valgte båd, afslutning og offentliggørelse, historik, profilredigering uden omskrivning af historik, slettebeskyttelse, offentlig deling, browserpræferencer og mobilvisning.

Seriens fulde deltagerliste konsolideres i alle sejladser. Fly har dermed 14 DNC-resultater og 91 nettopoint i Lille bane 2, samt 176 point i den kombinerede Lille bane-stilling. De ændrede deltagerantal påvirker også de øvrige bådes DNC/DNF-point. Oprindelige kildetider bevares, mens point og fratræk beregnes på den konsoliderede deltagerliste.

Der findes lokal preview, import af de fire løb fra Manage2Sail og konfiguration til Docker/Render. Det midlertidige administratorlogin er begrænset til lokal preview og op til to timer. Offentlig drift med Google/Facebook kræver fortsat hosting, udbydernes appnøgler og en loginprøve på den endelige HTTPS-adresse. Docker/Render-konfigurationen er ikke afprøvet i en kørende container her.

Overgangen til seriehierarkiet er kontrolleret mod en databasekopi: alle oprindelige både, deltagelser, måltider og ræsdata er bevaret, og tider, point, placeringer og fratræk er uændrede i de seks eksisterende resultatvisninger.
