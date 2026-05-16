# Chemische Klassifizierungen im PIM

## WGK

WGK steht fuer Wassergefaehrdungsklasse nach AwSV. Sie beschreibt den Gewaesserschutz und ist nicht mit ADR zu verwechseln.

Erlaubte Werte:

- `nwg`: nicht wassergefaehrdend
- `awg`: allgemein wassergefaehrdend
- `WGK 1`: schwach wassergefaehrdend
- `WGK 2`: deutlich wassergefaehrdend
- `WGK 3`: stark wassergefaehrdend

Typischer SDB-Abschnitt: 15.1, teilweise Abschnitt 12.

## Lagerklasse

Die Lagerklasse / LGK folgt TRGS 510 und beschreibt Lagerung und Zusammenlagerung. Sie ist nicht die ADR-Klasse.

LGK 9 ist nicht besetzt und darf nicht gespeichert werden.

Typischer SDB-Abschnitt: 7.2, teilweise Abschnitt 15.1.

## ADR

ADR beschreibt Transport/Gefahrguttransport. ADR-Daten wie UN-Nummer, Klasse und Verpackungsgruppe gehoeren fachlich in Abschnitt 14 des SDB und bleiben im PIM getrennt von WGK und Lagerklasse.

## Anreicherung

Die Funktion `Aus SDB anreichern` liest WGK und Lagerklasse deterministisch aus vorhandenen SDB-Daten am Produkt. Erkannte Werte werden nur als Vorschlag angezeigt und erst nach `Vorschlag übernehmen` gespeichert. Manuell gesetzte Werte werden nicht automatisch überschrieben.
