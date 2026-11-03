# Gennemgang af resultatvisningen

Kontrolleret 2. november 2026. Gennemgangen dækker den offentlige resultatvisning og de valgte indstillinger på mobil og større skærme.

## Fund og rettelser

| Område | Fund | Rettelse |
| --- | --- | --- |
| Kolonnebredder | Tabellen strakte tal ud for at fylde hele skærmen. Pointkolonnen var 132 px på en 1440 px skærm. | Tabellen følger indholdets bredde og brugerens feltregel: hver kolonne er mindst lige så bred som højden af dens højeste felt. Reglen genberegnes ved ændret indhold og skærmstørrelse; skrifterne formindskes ikke. |
| Visuel vægt | En mørk pointkolonne tiltrak meget opmærksomhed. | Dagens point vises med fed skrift. Kun seriens samlede point får en afdæmpet baggrund. |
| Typografi | Flere små skrifter og svage tekstfarver. | Sort tekst, 17 px tabelværdier, 18 px bådnavne, 16 px skippernavne og tydelige overskrifter. |
| Linjeføring | Tal og tekst havde forskellige behov for plads. | Tekst står til venstre, sammenlignelige tal til højre. Tider og point bruger tabellariske cifre. |
| Sammenhæng | Flere lag af stilrettelser gav modstridende regler. | Resultatvisningens regler er samlet i én blok med ensartede afstande. |
| Informationsrækkefølge | Administrative oplysninger tog plads fra resultaterne. | Løb, visning og sejlads står først. Bane, distance og vindkategori står på en kompakt linje over tabellen. |
| Tabelstruktur | Kolonner skulle være entydige for hjælpemidler. | Tabelbeskrivelse og kolonneoverskrifter med `scope="col"`. |
| Tastatur | En bred tabel skal kunne rulles uden mus. | Tabelområdet kan få tastaturfokus, når indholdet er bredere end området. Fokus har en tydelig blå kant. |
| Farvede banemærker | Rød/grøn alene er ikke tilstrækkeligt for alle. | Bogstaverne har også et lille B eller S for bagbord/styrbord samt en fuld tekstbeskrivelse. Rundingsretningerne kommer fra repoets PDF-algoritme. |
| Smalle skærme | Den brede tabel skal ikke skubbe hele siden ud over skærmen. | Kontrolelementerne ombrydes; tabellen ruller vandret i sit eget område. |

## Kontrol

- Dagens tider og samlet stilling er afprøvet ved 320, 390, 768 og 1440 px skærmbredde.
- De kontrollerede tabeltekster, labels og noter har mindst **12,12:1** i kontrast. Normal tekst kræver mindst **4,5:1** efter WCAG 1.4.3.
- Banemærkernes røde og grønne tekst er kontrolleret separat mod hvid baggrund; begge har over 4,5:1.
- Sammenlignelige tal er højrestillede. Kolonneoverskrifterne har `scope="col"`.
- Der er ingen vandret rulning af hele siden ved de kontrollerede bredder. Brede tabeller kan rulles inden i tabelområdet og betjenes med tastaturet.
- Der er også foretaget visuel kontrol med 200 % forstørrelse af siden.
- Cookie, delte links og browserhistorik er fortsat afprøvet i browseren.

Det er en afgrænset gennemgang af disse visninger og egenskaber. En fuld WCAG-vurdering kræver også gennemgang af de øvrige sider, tilstande og hjælpeteknologier.

## Fagligt grundlag

- [GOV.UK Design System: tabeller](https://design-system.service.gov.uk/components/table/) — sammenlignelige tal højrestilles; tabeller får tydelige overskrifter og beskrivelse.
- [W3C: tekstkontrast](https://www.w3.org/WAI/WCAG22/Understanding/contrast-minimum.html) — mindst 4,5:1 for normal tekst.
- [W3C: brug af farver](https://www.w3.org/WAI/WCAG22/Understanding/use-of-color.html) — betydning må ikke formidles alene med farve.
- [W3C: tabellers struktur](https://www.w3.org/WAI/tutorials/tables/) — semantiske tabeloverskrifter og relation mellem data og overskrift.
- [W3C: ombrydning på smalle skærme](https://www.w3.org/WAI/WCAG22/Understanding/reflow.html) — resten af siden skal ombrydes; egentlige datatabeller kan have behov for vandret rulning.
