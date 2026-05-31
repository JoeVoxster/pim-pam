# PIM/PAM Anleitung

Stand: 14. Mai 2026

Diese Anleitung beschreibt, was du im aktuellen PIM/PAM-System machen kannst und wo die wichtigsten Funktionen im GUI zu finden sind.

## Inhaltsverzeichnis

1. [Grundprinzip](#1-grundprinzip)
2. [Start und Orientierung](#2-start-und-orientierung)
3. [Produkte verwalten](#3-produkte-verwalten)
4. [Varianten verwalten](#4-varianten-verwalten)
5. [Bulk-Bearbeitung](#5-bulk-bearbeitung)
6. [Produkttexte und Beschreibungen](#6-produkttexte-und-beschreibungen)
7. [Übersetzungen](#7-übersetzungen)
8. [Web-Anreicherung](#8-web-anreicherung)
9. [Assets und Cloud Storage](#9-assets-und-cloud-storage)
10. [Chemie und SDB](#10-chemie-und-sdb)
11. [Kanal-Kategorien und Vertriebskanäle](#11-kanal-kategorien-und-vertriebskanäle)
12. [Importjobs und ETL](#12-importjobs-und-etl)
13. [Dubletten und Produkt-Merge](#13-dubletten-und-produkt-merge)
14. [Medusa Schnittstelle](#14-medusa-schnittstelle)
15. [Prozessstatus und Sicherheit](#15-prozessstatus-und-sicherheit)
16. [Typische Arbeitsabläufe](#16-typische-arbeitsabläufe)
17. [Wichtige Regeln](#17-wichtige-regeln)
18. [Stichwortverzeichnis](#18-stichwortverzeichnis)

## 1. Grundprinzip

PIM/PAM ist das führende System für Produktdaten. Hier pflegst du Produkte, Varianten, Texte, Übersetzungen, Assets, Chemiedaten, Kanalzuordnungen und Medusa-Exportdaten.

Medusa ist das Zielsystem für den Shop. Daten sollen zuerst im PIM/PAM geprüft und gepflegt werden. Danach werden sie kontrolliert nach Medusa exportiert.

Wichtige Grundsätze:

- Nichts Wichtiges wird ungeprüft automatisch überschrieben.
- Web-Anreicherung erzeugt zuerst Vorschläge oder Kandidaten.
- Schreibprozesse haben Dry-Run, Vorschau oder Bestätigung, soweit vorhanden.
- Bei Bulk-Änderungen wird vor Apply ein JSON-Backup geschrieben.
- PIM/PAM bleibt Master; Medusa erhält synchronisierte Daten.

## 2. Start und Orientierung

### Hauptmenü

Links im Hauptmenü findest du:

- Dashboard
- Produkte
- Chemie
- Varianten
- Kanal-Kategorien
- Vertriebskanäle
- Externe Kanal-Kategorien
- Assets
- Importjobs
- Attribute
- Familien
- Übersetzungen
- Regeln / Anreicherung
- Dubletten / Produkt-Merge
- Medusa Schnittstelle

### Globale Aktionen oben

Oben im Header stehen:

- Dashboard
- Daten neu laden
- ETL Upload UI

`Daten neu laden` aktualisiert die Ansicht. Nutze das nach Imports, Exports oder längeren Prozessen.

## 3. Produkte verwalten

Pfad: `Produkte`

Im Produkte-Grid kannst du Produkte anzeigen, filtern, sortieren und markieren.

Wichtige Spalten:

- Produkt-ID
- SKU / Artikelnummer
- Titel
- Foto / Asset
- Sprache / Originalsprache
- Variantenwerte
- Brand / Marke
- Status
- Preise
- Währung
- Aktualisiert am

### Produkt bearbeiten

Unterhalb des Grids gibt es den Bereich `Produkt bearbeiten`.

Dort kannst du pflegen:

- Produkt-ID
- SKU
- Originaltitel
- Marke
- Status
- Originalsprache
- Chemieprodukt ja/nein
- Kanal für Produkt-Kategorien
- Kanal-Kategorien
- Kurzbeschreibung
- Beschreibung in Markdown
- Quell-URLs
- Final URL

Buttons:

- `Neu anlegen`
- `Speichern`
- `Archivieren`
- `Zur Chemieansicht`

### Produkt-Detailtabs

Beim Produkt gibt es Detailbereiche:

- Übersicht
- Varianten
- Assets
- Kanäle / Listings
- Übersetzungen

## 4. Varianten verwalten

Pfad: `Varianten`

Varianten sind die verkaufbaren Einheiten eines Produkts, z. B. `10 kg`, `1 l`, `500 ml`.

Im Varianten-Grid kannst du bearbeiten:

- Variantentitel
- Optionsname
- Optionswert
- Packaging / Gebinde
- Verkaufspreis
- Einkaufspreis
- Währung
- Lagerbestand
- Barcode
- Status

Buttons:

- `Variante bearbeiten`
- `Markierte Varianten bearbeiten`
- `Kanal-Aktionen`
- `Varianten-Listings`
- `Ausgewählte Varianten archivieren`
- `Ausgewählte Varianten löschen`
- `Auswahl aufheben`

### Staffelpreise

Im Variantenbereich kannst du Staffelpreise anlegen oder aktualisieren.

Typische Felder:

- Preis-Typ
- Mindestmenge
- Maximalmenge
- Preis
- Währung

## 5. Bulk-Bearbeitung

Bulk-Bearbeitung bedeutet: mehrere Produkte oder Varianten markieren und gemeinsam ändern.

### Produkte gemeinsam ändern

Pfad: `Produkte` → Produkte markieren → `Markierte Produkte bearbeiten`

Aktuell unterstützt:

- Originalsprache
- Marke / Brand
- Status
- Chemieprodukt ja/nein

Bedienung:

1. Produkte im Grid markieren.
2. `Markierte Produkte bearbeiten` klicken.
3. Bei `Zu ändernde Felder` nur die Felder anhaken, die wirklich geändert werden sollen.
4. Neue Werte setzen.
5. Optional `Nur leere Werte füllen` aktivieren.
6. `Vorschau erzeugen` prüfen.
7. `Änderungen anwenden` klicken.
8. Bestätigung bestätigen.

Vor dem Speichern wird ein Backup geschrieben:

```text
/opt/output/bulk_edit_backups/
```

### Varianten gemeinsam ändern

Pfad: `Varianten` → Varianten markieren → `Markierte Varianten bearbeiten`

Aktuell unterstützt:

- Status
- Verkaufspreis
- Verkaufswährung
- Einkaufspreis
- Einkaufswährung
- Lagerbestand
- Barcode / EAN
- Optionsname
- Optionswert
- Packaging / Gebinde

Wichtig:

- Nur angehakte Felder werden geändert.
- Leere Felder werden nicht automatisch geschrieben, wenn sie nicht als Änderung gewählt sind.
- Mit `Nur leere Werte füllen` bleiben bestehende Werte erhalten.

## 6. Produkttexte und Beschreibungen

### Kurzbeschreibung

Kurzbeschreibung ist reiner Text:

- ein klarer Satz
- kein HTML
- kein Markdown
- ideal 120 bis 180 Zeichen
- maximal 250 Zeichen

### Beschreibung

Beschreibung wird als Markdown gepflegt.

Empfohlenes Format:

```markdown
Kurzer Einleitungstext zum Produkt.

### Eigenschaften

- Punkt 1
- Punkt 2

### Anwendung / Hinweis

- Hinweis 1
```

Regeln:

- Keine erfundenen technischen Angaben.
- Keine rechtlich heiklen Chemieangaben aus Marketingtexten ableiten.
- Keine leeren Überschriften.
- Zeilenumbrüche bewusst setzen.

### Beschreibungen aus Final URLs importieren

Pfad: `Produkte` → `Beschreibungen aus Final URLs importieren`

Funktion:

- liest die Final URL eines Produkts
- extrahiert Beschreibung
- erstellt Kurzbeschreibung
- formatiert Langbeschreibung als Markdown
- unterstützt beliebige Domains, nicht nur voxster.ch

Optionen:

- Markierte Produkte
- Einzelne Produkt-ID testen
- Alle Produkte mit Final URL prüfen
- Dry-Run
- Apply ausführen
- Bestehende Texte überschreiben

Standard ist Dry-Run. Ohne Apply wird nichts gespeichert.

Reports werden geschrieben nach:

```text
/opt/output/
```

## 7. Übersetzungen

Pfade:

- `Produkte` → `Übersetzungen erstellen`
- `Produkte` → Produktdetail → Tab `Übersetzungen`
- Hauptmenü `Übersetzungen`

### Übersetzungen erstellen

Du kannst für markierte Produkte Übersetzungen erzeugen.

Optionen:

- Ausgangssprache
- Zielsprache(n)
- Bestehende Übersetzungen überschreiben
- Originalsprache überschreiben
- Zugehörige Varianten mitübersetzen

Die Übersetzung nutzt aktuell OpenAI, wenn der Provider aktiv ist.

### Markdown bei Übersetzungen

Beschreibungstexte mit Markdown bleiben strukturiert:

- `###` Überschriften bleiben Überschriften
- Bulletpoints bleiben Bulletpoints
- Links behalten die URL
- Code-Blöcke werden nicht übersetzt
- Kurzbeschreibung bleibt reiner Text

Wenn die KI Markdown beschädigt, wird die Antwort blockiert oder einmal mit strengeren Markdown-Regeln erneut versucht.

### Manuelle Übersetzungen

Im Produktdetail kannst du Übersetzungen manuell speichern:

- Sprache
- Titel
- Kurzbeschreibung
- Beschreibung
- SEO-Titel
- SEO-Beschreibung
- Slug

Für Varianten:

- Variante
- Sprache
- Titel
- Optionslabel
- Gebindelabel

## 8. Web-Anreicherung

### Fehlende Produktdaten anreichern

Pfad: `Produkte` → Produkte markieren → `Fehlende Produktdaten anreichern`

Ziel:

- fehlende Kurzbeschreibung
- Beschreibung
- SEO-Titel
- SEO-Beschreibung
- technische Felder als Kandidaten

Wichtig:

- Es wird zuerst eine Vorschau erzeugt.
- Bestehende Werte werden nicht automatisch überschrieben.
- Der Benutzer entscheidet, welche Vorschläge übernommen werden.

Quellen:

- Final URL
- Quell-URLs
- Supplier-Extractor, z. B. Tintolav
- konfigurierte Domains
- Suchhinweise

### Regeln / Anreicherung / Preisregeln

Pfad: `Regeln / Anreicherung`

Hier gibt es die Produktdaten-Webanreicherung als regelbasierten Bereich.

Funktionen:

- Produktbeschreibung im Web suchen
- Kurzbeschreibung im Web suchen
- Langbeschreibung im Web suchen
- SEO-Titel suchen
- SEO-Beschreibung suchen
- technische Daten suchen
- alle fehlenden Texte suchen
- Anreicherung mit Vorschau starten

### Tintolav-Extractor

Für Tintolav-Seiten werden strukturierte Sektionen erkannt:

- Description
- How To Use
- Quantity for use
- Warning
- Ingredients
- Ingredient Search
- Function
- Packaging
- Bilder
- PDFs

Gefundene Daten werden als Kandidaten behandelt und nicht blind gespeichert.

## 9. Assets und Cloud Storage

Pfad: `Assets`

Im Asset-Bereich kannst du Dateien verwalten, hochladen, auswählen, löschen und nach Object Storage übertragen.

### Asset Grid

Funktionen:

- Assets anzeigen
- Assets markieren
- alle sichtbaren Assets auswählen
- alle sichtbaren Assets abwählen
- markierte Assets löschen
- lokale Assets nach Object Storage hochladen

Bulk-Aktionen vorbereitet, aber teilweise noch deaktiviert:

- Asset-Typ ändern
- Produkt verknüpfen
- Sprache setzen
- Status ändern
- Links kopieren

### Asset Uploader

Felder:

- Asset-Typ
- Produkt-ID optional
- Sprache optional
- Titel
- Beschreibung
- Dateien per Drag & Drop oder Auswahl

Button:

- `Upload zu Object Storage starten`

### R2 / Object Storage Konfiguration

Button:

- `R2-Speicher Conf`

Konfigurierbar:

- Aktiviert
- Storage Provider
- Endpoint
- Bucket
- Region
- Access Key ID
- Secret Access Key
- Public Base URL
- Pfad-Präfix
- Upload max. Dateigrösse
- erlaubte Dateitypen

Wichtig:

- Secrets werden nicht im Klartext angezeigt.
- Upload funktioniert nur, wenn Storage korrekt konfiguriert ist.
- Public Base URL wird für exportierbare Bild-URLs genutzt, z. B. `https://media.voxster.ch`.

### Fehlende Produkt-Assets anreichern

Pfad: `Produkte` → Produkte markieren → `Fehlende Produkt-Assets anreichern`

Gesucht werden:

- Produktbilder
- Verpackungsbilder
- Anwendungsbilder
- PDFs
- SDB / SDS
- TDS / technische Datenblätter
- Produktdatenblätter

Regeln:

- Bilder bekommen keinen Sprachcode im Dateinamen.
- PDFs bekommen nach Möglichkeit Sprachcode im Dateinamen.
- Duplikate werden erkannt und nicht doppelt gespeichert.
- Kleine/irrelevante Bilder werden gefiltert.

## 10. Chemie und SDB

Pfad: `Chemie`

Der Chemiebereich ist für chemische Produkte, Sicherheitsdaten und rechtlich relevante Dokumente.

### Chemieprodukt

Ein Produkt kann als Chemieprodukt markiert werden:

- im Produktformular unter `Chemieprodukt`
- im Bulk-Dialog `Markierte Produkte bearbeiten`
- im Chemie-Detail

### Chemie-Detail

Tabs:

- Allgemein
- Chemie-Stammdaten
- Kennzeichnung / Sicherheit
- Physikalische / technische Daten
- Vertrieb / Freigabe
- Internet
- SDB

### SDB / Sicherheitsdatenblatt

Funktionen:

- SDB speichern
- Rohtext und Abschnitte leeren
- Quelle/PDF deterministisch übernehmen
- SDB mit ChatGPT normieren
- SDB deterministisch validieren und PDF generieren
- Dokument als geprüft markieren
- Dokument archivieren
- Dokument als PDF generieren
- SDB-Übersetzung erstellen
- SDB-Entwurf für Region erstellen
- SDB-Prompts verwalten

Wichtige Regel:

Chemie-/Sicherheitsdaten dürfen nicht aus Marketingtexten erfunden werden. H-Sätze, P-Sätze, WGK, Lagerklasse oder Gefahrstoffklassifizierung müssen aus belastbaren Quellen stammen und geprüft werden.

## 11. Kanal-Kategorien und Vertriebskanäle

### Kanal-Kategorien

Pfad: `Kanal-Kategorien`

Funktionen:

- Kategorienbaum anzeigen
- Produkte einer Kategorie anzeigen
- Kategorie anlegen
- Kategorie bearbeiten
- Kategorie löschen

### Vertriebskanäle

Pfad: `Vertriebskanäle`

Funktionen:

- Vertriebskanal speichern
- Kanal-Export erzeugen

### Externe Kanal-Kategorien

Pfad: `Externe Kanal-Kategorien`

Funktionen:

- externe Kategorien anzeigen
- Baum aufklappen/einklappen
- Produkte dieser Kategorie anzeigen
- Kanal-Kategorie speichern

### Kanal-Aktionen im Produktgrid

Im Produkte-Grid:

- Kanal-Aktionen
- Produkt-Listings
- Kanal-Kategorien
- Varianten-Listings

Damit steuerst du, ob Produkte und Varianten auf bestimmten Kanälen aktiv, erlaubt oder bestimmten Kategorien zugeordnet sind.

## 12. Importjobs und ETL

Pfad: `Importjobs`

Funktionen:

- PIM-Import starten
- Website-Anreicherung starten
- Importjobs prüfen
- Website-Crawler für Produkte öffnen
- Website-Crawler für Varianten öffnen

Zusätzlich gibt es oben:

- `ETL Upload UI`

### Website-Crawler für Produkte / Varianten

Im Menü `Importjobs` gibt es die Spezialwerkzeuge:

- `Website-Crawler für Produkte`
- `Website-Crawler für Varianten`

Diese Funktionen entsprechen der alten technischen Anreicherung für markierte Produkte oder Varianten. Sie arbeiten mit Resolvern wie `Tintolav Katalog-Resolver` oder `Generischer Crawl`.

Für normale Produkttexte solltest du im Menü `Produkte` bevorzugt `Fehlende Produktdaten anreichern` verwenden, weil diese Funktion mit Vorschau, Quellen, Confidence und kontrollierter Übernahme arbeitet.

Typische Nutzung:

1. Datei importieren.
2. Importjob prüfen.
3. Produkte im Grid kontrollieren.
4. Fehlende Texte/Assets anreichern.
5. Übersetzungen erstellen.
6. Nach Medusa exportieren.

## 13. Dubletten und Produkt-Merge

Pfad: `Dubletten / Produkt-Merge`

Ziel:

- doppelte Produkte finden
- Master-Produkt bestimmen
- Merge vorab prüfen
- Dubletten kontrolliert archivieren

Funktionen:

- Dublettenerkennung starten
- Dry-Run / Vorschau erstellen
- Merge bestätigen
- Ignorieren
- Produkt als Master setzen
- alle Produkte einer Dubletten-Gruppe auswählen
- alle Produkte einer Dubletten-Gruppe abwählen

Wichtig:

- Merge ist nicht destruktiv.
- Dubletten werden archiviert und mit `merged_into_product_id` verknüpft.
- Assets, Varianten und Preise können zum Master übernommen werden.
- Konflikte werden angezeigt.

## 14. Medusa Schnittstelle

Pfad: `Medusa Schnittstelle`

PIM/PAM synchronisiert Produkte nach Medusa über Admin REST APIs.

Tabs:

- Verbindung
- Exportumfang
- Sync
- Logs

### Verbindung

Konfigurierbar:

- Base URL
- Admin Path
- Auth Type
- API Token
- Timeout
- Retries

Button:

- `Verbindung testen`

### Exportumfang

Optionen:

- Produkte
- Varianten
- Optionen
- Bilder
- SEO
- Metadata
- Übersetzungen
- Default Preise
- Preislisten
- Staffelpreise
- Inventory
- IDs nach Export zurückschreiben
- Mapping Repair vor Export

### Sync

Produktauswahl:

- markierte Produkte aus Produktliste
- einzelne Produkt-ID
- Filter, z. B. aktive Produkte
- maximale Produktanzahl
- Force Update

Buttons:

- Dry Run
- Export starten
- IDs aus Medusa zurückladen

### Wichtig für Medusa

- CSV ist nur Debug-/Migrationshilfe.
- Produktiver Sync läuft über Admin API.
- Medusa IDs werden in PIM/PAM zurückgeschrieben.
- Zweiter Export soll keine Duplikate erzeugen.
- Bilder werden über Public Asset Base URL gebaut.

## 15. Prozessstatus und Sicherheit

Oben im GUI gibt es eine globale Prozessstatus-Anzeige.

Sie zeigt:

- Bereit
- Läuft
- Erfolgreich abgeschlossen
- Fehler
- Abgebrochen

Bei laufendem Prozess:

- Hinweis: Prozess läuft, bitte warten
- Startzeit
- Dauer
- Prozessname
- Optionen
- Fortschritt
- Logmeldungen

Integrierte lange Prozesse:

- Fehlende Produktdaten anreichern
- Fehlende Produkt-Assets anreichern
- Beschreibungen aus Final URLs importieren

Ziel:

- keine Mehrfachklicks
- keine parallelen Schreibprozesse
- klare Rückmeldung, ob Dry-Run oder Apply lief

## 16. Typische Arbeitsabläufe

### A. Neue Produkte aus Import aufbereiten

1. `Importjobs` oder `ETL Upload UI` öffnen.
2. Import starten.
3. Produkte im Menü `Produkte` prüfen.
4. Fehlende Beschreibungen über `Beschreibungen aus Final URLs importieren` holen.
5. Fehlende Produktdaten über Web-Anreicherung vorschlagen lassen.
6. Fehlende Assets anreichern.
7. Produkttexte kontrollieren.
8. Übersetzungen erstellen.
9. Kanal-Listings setzen.
10. Medusa Dry Run ausführen.
11. Medusa Export starten.

### B. Viele Produkte von Sprache en auf de-CH setzen

1. In `Produkte` nach Sprache `en` filtern.
2. Produkte markieren.
3. `Markierte Produkte bearbeiten` öffnen.
4. Feld `Originalsprache` anhaken.
5. Wert `de-CH` wählen.
6. `Vorschau erzeugen`.
7. Wenn korrekt: `Änderungen anwenden`.

### C. Brand für mehrere Produkte ändern

1. Produkte markieren.
2. `Markierte Produkte bearbeiten`.
3. Feld `Marke / Brand` anhaken.
4. Brand eingeben.
5. Vorschau prüfen.
6. Apply bestätigen.

### D. Variantenpreise gemeinsam ändern

1. Menü `Varianten` öffnen.
2. Varianten markieren.
3. `Markierte Varianten bearbeiten`.
4. Feld `Verkaufspreis` und/oder `Verkaufswährung` anhaken.
5. Werte setzen.
6. Vorschau prüfen.
7. Apply bestätigen.

### E. Produktbilder nach Object Storage hochladen

1. Menü `Assets` öffnen.
2. R2/Object Storage Konfiguration prüfen.
3. Assets markieren.
4. `Ausgewählte lokale Assets nach R2 hochladen`.
5. Ergebnis prüfen: hochgeladen, übersprungen, Fehler.
6. Public URLs im Medusa Export prüfen.

### F. Chemieprodukt prüfen

1. Produkt im Produktformular als `Chemieprodukt` markieren.
2. `Zur Chemieansicht` öffnen.
3. SDB/Assets prüfen.
4. Internet-Anreicherung nur als Vorschlag verwenden.
5. SDB deterministisch validieren.
6. PDF generieren.
7. Manuell prüfen/freigeben.

### G. Produkt nach Medusa exportieren

1. Produkt in `Produkte` markieren.
2. `Medusa Schnittstelle` öffnen.
3. Produktauswahl `Markierte Produkte aus Produktliste` wählen.
4. Dry Run starten.
5. Logs prüfen.
6. Export starten.
7. Medusa-ID und Varianten-IDs prüfen.

## 17. Wichtige Regeln

### Nie blind überschreiben

Bestehende Produkttexte, Übersetzungen oder Chemiedaten sollen nicht ohne Vorschau oder Bestätigung überschrieben werden.

### Originalsprache korrekt setzen

`Originalsprache` bedeutet: Sprache der Basisdaten am Produkt. Wenn die Beschreibung deutsch ist, sollte sie z. B. `de-CH` sein. Wenn die Beschreibung englisch ist, `en`.

### Chemie besonders vorsichtig

Keine H-/P-Sätze, WGK, Lagerklassen oder Gefahrstoffdaten aus Marketingtexten erfinden.

### Assets sauber unterscheiden

PDFs, SDBs, Produktbilder und Verpackungsbilder sind unterschiedliche Asset-Typen.

### Medusa nicht direkt bearbeiten

Produktive Shopdaten sollen über PIM/PAM synchronisiert werden, nicht manuell in Medusa gepflegt werden, ausser für kontrollierte Tests.

## 18. Stichwortverzeichnis

### A

- Anreicherung: Produktdaten oder Assets automatisch suchen und als Vorschlag übernehmen.
- Apply: Schreibender Modus, Daten werden gespeichert.
- Asset: Datei wie Bild, PDF, SDB oder Datenblatt.
- Asset Uploader: Upload-Bereich im Menü Assets.

### B

- Backup: JSON-Sicherung vor Bulk-Apply.
- Brand: Marke oder Hersteller eines Produkts.
- Bulk-Bearbeitung: gemeinsame Änderung mehrerer Produkte oder Varianten.

### C

- Chemieprodukt: Produkt mit chemisch/sicherheitsrelevanten Daten.
- Cloud Storage: Object Storage für Assets, z. B. R2 oder Bunny.

### D

- Dashboard: Übersicht über Kennzahlen und Importjobs.
- Dry-Run: Testlauf ohne Speichern.
- Dubletten: doppelte oder ähnliche Produkte.

### E

- ETL: Import- und Transformationsprozess.
- Export: Ausgabe von Daten für Kanal, CSV oder Medusa.

### F

- Final URL: bevorzugte Produkt-Quellseite für Beschreibung/Anreicherung.

### G

- Gebinde: Verpackungs-/Varianteneinheit, z. B. 10 kg.

### H

- Handle: stabiler Produkt-Slug für Shop/Medusa.

### I

- Importjob: Protokollierter Importlauf.

### K

- Kanal: Vertriebskanal, z. B. voxster.ch, POS, Chemie Shop.
- Kanal-Kategorie: Kategorie je Vertriebskanal.
- Kurzbeschreibung: kurzer reiner Text ohne Markdown.

### L

- Listing: Freigabe/Verfügbarkeit eines Produkts oder einer Variante je Kanal.

### M

- Markdown: Format für Langbeschreibungen mit Überschriften und Bulletpoints.
- Medusa: Zielsystem für den Shop.
- Merge: Zusammenführen von Dubletten.

### O

- Object Key: Speicherpfad einer Datei im Object Storage.
- Originalsprache: Basissprache des Produktdatensatzes.

### P

- PIM/PAM: Produkt- und Asset-Management-System.
- Preview: Vorschau vor Übernahme.
- Public Base URL: öffentliche Basis-URL für Assets.

### R

- R2: Cloudflare R2 Object Storage.
- Regeln / Anreicherung: Bereich für regelbasierte Web-Anreicherung.

### S

- SDB: Sicherheitsdatenblatt.
- SEO-Beschreibung: Suchmaschinenbeschreibung.
- SEO-Titel: Suchmaschinentitel.
- SKU: Artikelnummer.
- Source URL: Quell-URL oder Importquelle.
- Staffelpreis: Mengenpreis je Variante.

### T

- TDS: Technical Data Sheet.
- Translation: Übersetzung.

### U

- Übersetzung: sprachabhängige Produkt- oder Variantentexte.

### V

- Variante: verkaufbare Einheit eines Produkts.
- Varianten-Listing: Kanalverfügbarkeit einer Variante.

### W

- Web-Anreicherung: Internetbasierte Suche nach Produktdaten.
- WGK: Wassergefährdungsklasse.
