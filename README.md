# PIM + Supplier ETL

Kleine, produktionsnahe PIM-Grundversion auf Basis des bestehenden ETL-Projekts. Das System liest Lieferantenlisten aus CSV/XLSX, kann die vorhandene Scraping-/Bereinigungsstrecke weiterverwenden und lädt normalisierte Produktdaten in PostgreSQL. Die Daten werden anschließend in einer Dash-Oberfläche mit Dash AG Grid gepflegt.

## Überblick

- bestehende ETL-Strecke bleibt erhalten unter `app/main.py`, `app/io/`, `app/transform/`
- neues PIM-Datenmodell in PostgreSQL mit SQLAlchemy
- Alembic-Initialmigration für das Schema
- Dash-Admin unter `app/ui/dash_app.py`
- Importdienst `app/etl/pim_import.py` für Clean-Exporte oder direkte Rohimporte
- lokale Asset-Speicherung im Dateisystem, Metadaten in PostgreSQL

## Architektur

```text
app/
  db/          SQLAlchemy-Modelle, Session, Seed
  etl/         PIM-Import auf Basis der bestehenden ETL-Ausgabe
  io/          bestehender CSV/XLSX-Import und Exporte
  services/    Produkt-, Kategorie-, Asset- und Importlogik
  schemas/     Pydantic-Schemas
  ui/          Dash-Admin mit Dash AG Grid
  utils/       PIM-spezifische Laufkonfiguration
alembic/       Migrationen
input/         Beispiel- und Demo-Dateien
output/        ETL-Ausgaben
```

## Datenmodell

- `products`: Stammdaten eines Produkts
- `product_variants`: Varianten mit Preis, Währung, Bestand und Barcode
- `brands`: Markenstammdaten
- `categories`: hierarchische Kategorien
- `product_categories`: Zuordnung Produkt zu Kategorie
- `assets`: Datei-Metadaten für Bilder/PDFs und andere Assets
- `product_translations`: sprachspezifische Titel/Beschreibungen
- `import_jobs`: Importlauf mit Status und Summary
- `import_rows`: Status je importierter Zeile

### Medusa-v2-Schnittstelle

Das Menü `Medusa Schnittstelle` synchronisiert Produkte, Varianten, Bilder, Preise und Übersetzungen über die Medusa Admin REST APIs unter `/admin`. PIM/PAM bleibt Master; CSV-Export ist nur noch Debug-/Migrationshilfe. Medusa-IDs werden in `medusa_sync_mappings` gespeichert, damit spätere Exporte keine Dubletten erzeugen.

Optionale ENV-Fallbacks ohne echte Secrets:

```env
MEDUSA_BASE_URL=http://localhost:9000
MEDUSA_ADMIN_PATH=/admin
MEDUSA_ADMIN_API_TOKEN=
MEDUSA_DEFAULT_LOCALE=de-CH
MEDUSA_DEFAULT_CURRENCY=CHF
```

CLI-Beispiele:

```bash
python -m app.medusa_sync test-connection --connection default
python -m app.medusa_sync dry-run --connection default --product-id 1
python -m app.medusa_sync export --connection default --product-id 1
python -m app.medusa_sync repair-mapping --connection default
```

## Voraussetzungen

- Python 3.12
- PostgreSQL 16 oder kompatibel
- optional Docker / Docker Compose

## Lokales Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python -m playwright install --with-deps chromium
cp .env.example .env
```

Wichtige Variablen in `.env`:

```env
DATABASE_URL=postgresql+psycopg://pim:pim@localhost:5432/pimdb
ASSET_STORAGE_PATH=./data/assets
APP_HOST=0.0.0.0
APP_PORT=8050
DEBUG=false
REQUEST_TIMEOUT_SECONDS=30
BROWSER_TIMEOUT_MS=30000
HEADLESS=true
LOG_LEVEL=INFO
```

## PostgreSQL starten

Mit Docker Compose:

```bash
docker compose up -d postgres
```

Oder direkt lokal mit einer eigenen PostgreSQL-Instanz und passender `DATABASE_URL`.

## Migrationen ausführen

```bash
source .venv/bin/activate
alembic upgrade head
```

## Seed-Daten laden

Für einen schnellen Start:

```bash
python -m app.db.seed
```

Alternativ kann die Demo-Clean-Datei direkt importiert werden:

```bash
python -m app.etl.pim_import --clean-file input/pim_demo_clean.csv --source-name demo-clean
```

## Dash-App starten

```bash
python -m app.ui.dash_app
```

Danach läuft das PIM unter `http://localhost:8050`.

## Cloudflare R2 Assets

Der Bereich `Assets -> R2 Speicher -> Konfiguration` verwaltet die Cloudflare-R2-S3-Konfiguration für den Asset-Uploader. Secrets werden nicht im Klartext an das Frontend zurückgegeben; `Secret Access Key` und `Access Key ID` können dort gesetzt oder ersetzt werden.

Beispielwerte:

```text
Endpoint: https://c1c33248a1d708c368b3c2c9952d993d.r2.cloudflarestorage.com
Bucket: voxster-media
Region: auto
Public Base URL: leer lassen oder z. B. https://media.voxster.ch
```

Für S3-Uploads braucht die App `Endpoint`, `Bucket`, `Region`, `Access Key ID` und `Secret Access Key`. Ein Cloudflare-API-Token `cfat_...` ist dafür nicht der richtige Wert. ENV-Variablen bleiben als Fallback nutzbar:

```env
R2_ENDPOINT=
R2_BUCKET=
R2_REGION=auto
R2_ACCESS_KEY_ID=
R2_SECRET_ACCESS_KEY=
R2_PUBLIC_BASE_URL=
MAX_ASSET_UPLOAD_SIZE_MB=50
```

Nach dem Speichern kann im selben Bereich `Verbindung testen` ausgeführt werden. Der Test listet den Bucket und schreibt/löscht optional eine temporäre Diagnose-Datei.

## Bestehende Upload-/ETL-Weboberfläche starten

Die bestehende Upload-Oberfläche bleibt zusätzlich erhalten:

```bash
python -m app.web
```

Danach unter `http://localhost:8000`.

## ETL-Import starten

### Direkter Import einer bereits bereinigten Datei

```bash
python -m app.etl.pim_import \
  --clean-file output/products_clean.csv \
  --mapping-config config.pim_import.yaml \
  --source-name tintolav-clean
```

### Dry-Run

```bash
python -m app.etl.pim_import \
  --clean-file output/products_clean.csv \
  --mapping-config config.pim_import.yaml \
  --source-name tintolav-clean \
  --dry-run
```

### Rohdatei erst durch bestehende ETL schicken und dann laden

```bash
python -m app.etl.pim_import \
  --input input/products.csv \
  --output-dir output/pim_import_run \
  --mapping-config config.pim_import.yaml \
  --source-name supplier-raw
```

### Beispiel mit Excel-Blattauswahl

```bash
python -m app.etl.pim_import \
  --input input/tintolav_products.csv \
  --sheet-index 0 \
  --source-name tintolav
```

## Dash-Funktionen

- Dashboard mit Kennzahlen und letzten Importjobs
- Produktliste mit Filter, Sortierung und Pagination
- Produktbearbeitung für Titel, Beschreibung, Status, Marke und Kategorien
- Produktarchivierung
- Variantentabelle und Variantenbearbeitung
- Kategorienliste und einfache Kategorienanlage
- Assetliste
- Produktdetail mit Varianten, Assets und Übersetzungen
- Asset-Upload direkt im Produktdetail
- Übersetzungen anlegen/aktualisieren
- Importjobs ansehen und Clean-Dateien direkt aus der UI importieren
- Website-Anreicherungsjob direkt aus der UI starten
- Beschreibung, Packaging, Spezifikationen, technische Merkmale und Asset-Metadaten aus einer Lieferantenwebsite nachziehen

## Website-Anreicherung

Neben dem Clean-Import gibt es jetzt einen zweiten Jobtyp `website_enrichment`.

Im Tab `Importjobs` der Dash-App kannst du:

- eine Start-URL wie `https://tintolav.com/` angeben
- den Lieferantennamen setzen
- die maximale Zahl zu crawlender Seiten begrenzen
- festlegen, ob nur leere Felder ergänzt oder bestehende Daten überschrieben werden
- gezielt Beschreibung, Assets, Packaging, Spezifikationen, technische Merkmale und Source-URLs aktualisieren

Der Job:

- crawlt die Website ab der Start-URL
- extrahiert Produktdaten mit dem bestehenden Scraping-Layer
- matched bevorzugt über SKU, sonst über Handle/Slug
- lädt neue Bilder/PDFs lokal in den Asset-Speicher
- protokolliert Ergebnisse pro besuchter URL in `import_jobs` und `import_rows`

## Tests

```bash
.venv/bin/python -m pytest -q
```

Aktuell geprüft:

- bestehende Reader-/Writer-Tests
- PIM-Mapping
- Produkt-/Varianten-Upsert
- PIM-Import aus `products_clean`

## Docker Compose komplett

Wenn die App direkt im Compose-Setup laufen soll:

```bash
docker compose up --build
```

Das startet:

- `postgres` auf Port `5432`
- `app` auf Port `8050`

Vor dem ersten produktiven Lauf weiterhin sinnvoll:

```bash
docker compose exec app alembic upgrade head
```

## Beispiel-Dateien

- Mapping: [config.pim_import.yaml](/opt/config.pim_import.yaml:1)
- Demo-Import: [input/pim_demo_clean.csv](/opt/input/pim_demo_clean.csv:1)

## Cloudflare R2 Asset Upload

Im Hauptmenü **Assets** gibt es den Bereich **Asset Uploader**. Dateien werden über die S3-kompatible Cloudflare-R2-API in den Bucket `voxster-media` hochgeladen und als normale PIM-Assets mit R2-Metadaten gespeichert.

Erforderliche ENV-Variablen:

```bash
R2_ENDPOINT=https://c1c33248a1d708c368b3c2c9952d993d.r2.cloudflarestorage.com
R2_BUCKET=voxster-media
R2_REGION=auto
R2_ACCESS_KEY_ID=
R2_SECRET_ACCESS_KEY=
R2_PUBLIC_BASE_URL=
MAX_ASSET_UPLOAD_SIZE_MB=50
```

`R2_ACCESS_KEY_ID` und `R2_SECRET_ACCESS_KEY` dürfen nicht im Repository stehen. `R2_PUBLIC_BASE_URL` ist optional; ohne öffentliche Domain erzeugt die Admin-UI interne Download-Links über signierte R2-URLs.

## Bereits vollständig umgesetzt

- PostgreSQL-Datenmodell mit Foreign Keys, Unique Constraints und Indizes
- Alembic-Initialmigration
- SQLAlchemy-Service-Schicht für Produkte, Varianten, Marken, Kategorien und Übersetzungen
- Asset-Metadaten mit lokaler Dateispeicherung und Cloudflare-R2-Upload
- ETL-Importdienst inklusive Importjobs, Importrows und Dry-Run
- Dash-Admin mit Dash AG Grid
- Basis-CRUD für Produkte, Varianten, Kategorien, Übersetzungen und Assets
- Dokumentation und Beispielkonfiguration

## Bewusst einfach gehalten

- kein separates REST-API-Layer, da Dash + Service-Schicht für die Grundversion ausreicht
- keine Benutzer-/Rechteverwaltung
- keine Hintergrundjobs oder Queue
- Asset-Storage lokal oder Cloudflare R2; öffentliche R2-Domain optional
- Kategoriepflege nur als einfache Anlage, ohne Drag-and-drop-Baumeditor
- Import-Mapping regelbasiert, noch kein interaktiver Mapping-Builder
- keine S3-Implementierung, aber Speicherlogik bereits klar abtrennbar

## Nächste sinnvolle Ausbaustufen

- weitere Storage-Adapter neben Cloudflare R2
- Validierungs- und Freigabe-Workflow für Imports
- differenzierte Preislisten pro Markt/Kundengruppe
- Versionshistorie für Produktänderungen
- Volltextsuche über Titel, Beschreibung und Metadaten
