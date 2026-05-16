# VALIDATION_REPORT

## Gefundene Fehler

- `section_1` wurde aus Rohtext und strukturierten Feldern doppelt aufgebaut; dadurch konnten `1.2`, `1.3` und `1.4` mehrfach erscheinen.
- Lieferant und Hersteller wurden in `1.3` nicht sauber getrennt; Herstellerdaten aus Quellen wie Hänseler konnten in den Lieferantenblock rutschen.
- `section_9` konnte echte Werte und Platzhalter für dasselbe Feld gleichzeitig enthalten.
- `section_14` erlaubte widersprüchliche Umweltgefahren-Angaben wie `UMWELTGEFÄHRDEND` und gleichzeitig `Gemäss vorliegenden Daten prüfen`.
- `section_15` und `section_16` konnten aus Rohtext und Standardfeldern doppelte Hinweise/Revisionsnotizen aufbauen.
- Release-/Freigabe-Builds konnten Review-Marker und Review-Status weiterhin in den PDF-Kopf übernehmen.

## Behobene Fehler

- Kanonische SDB-Normalisierung in `app/services/sdb_support.py` eingeführt:
  - `merge_sections()`
  - `dedupe_paragraphs()`
  - `resolve_supplier_vs_manufacturer()`
  - `resolve_transport_consistency()`
  - `suppress_placeholder_when_real_value_exists()`
- `section_1` wird jetzt kanonisch aufgebaut:
  - `1.2` genau einmal
  - `1.3` mit VOXSTER als Lieferant
  - Hersteller nur separat als `Hersteller (laut Quelle)`, wenn vorhanden
  - `1.4` mit Tox Info Suisse
- `section_9` rendert jedes Pflichtfeld genau einmal; echte Werte verdrängen Platzhalter.
- `section_14` rendert genau eine konsistente Transportstruktur `14.1` bis `14.7`.
- Vor PDF-Render greifen Validierungsregeln für:
  - fehlende Pflichtabschnitte
  - doppelte Unterabschnitte
  - fehlende Telefon-/E-Mail-Angaben in `1.3`
  - fehlende `identified_uses` in `1.2`
  - Transportkonflikte
  - Review-Marker in Release-Builds
- `app/pdf/sdb_renderer.py` unterdrückt Review-Status in Release-Builds im Header und Meta-Block.
- GHS-Piktogramme werden weiter als Assets eingebettet.

## Verbleibende manuelle Review-Punkte

- Fachliche Prüfung der Stoffklassifikation, CH-spezifischen Rechtsgrundlagen und Transportdaten bleibt notwendig.
- Herstellerangaben aus Quell-PDFs werden heuristisch extrahiert; bei ungewöhnlichen Layouts kann manuelle Nachpflege nötig sein.
- LLM-normalisierte Inhalte bleiben review-pflichtig, auch wenn die formale Struktur jetzt validiert wird.
- Aktueller Live-Datensatz `1419 / CHEM-DEMO-001` enthält in Abschnitt 14 noch einen Alt-Konflikt zwischen konkreter Umweltgefahren-Angabe und Prüfhinweis. Nach den neuen Regeln bleibt der Datensatz deshalb bis zur fachlichen Bereinigung `REVIEW_REQUIRED`.

## Beispiel-Output für VOXSTER-Endfassung

### Abschnitt 1.3 / 1.4

```text
1.3 Einzelheiten zum Lieferanten, der das Sicherheitsdatenblatt bereitstellt
VOXSTER GmbH
Obere Ifangstrasse 10
8215 Hallau
CH
Telefon: +41 52 680 11 80
E-Mail der für das SDB verantwortlichen Person: info@voxster.ch

Hersteller (laut Quelle):
Hänseler AG
Industriestrasse 35
9100 Herisau
Telefon Hersteller: +41 71 353 58 58
E-Mail Hersteller: sdb@haenseler.ch

1.4 Notrufnummer
Tox Info Suisse (Schweiz): 145 (Schweiz) / +41 44 251 51 51
```

### Abschnitt 14

```text
14.1 UN-Nummer oder ID-Nummer: 1791
14.2 Ordnungsgemässe UN-Versandbezeichnung: HYPOCHLORITLOESUNG
14.3 Transportgefahrenklassen: 8
14.4 Verpackungsgruppe: II
14.5 Umweltgefahren: UMWELTGEFAEHRDEND
14.6 Besondere Vorsichtsmassnahmen für den Verwender: Schutzmassnahmen gemäss Abschnitt 7 und 8 beachten.
14.7 Massengutbeförderung auf dem Seeweg gemäss IMO-Instrumenten: Nicht anwendbar bzw. keine Daten verfügbar.
```
