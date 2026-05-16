# Medusa Kategorie-Produkt-Positionssync

## Zweck

PIM/PAM ist Master fuer die Reihenfolge von Produkten innerhalb einer Kanal-Kategorie. MedusaJS speichert diese Reihenfolge nur und bekommt sie ueber den PIM-Endpunkt.

Der Sync ist bewusst dreistufig modelliert:

1. Kanal-Kategorien aus PIM/PAM nach Medusa exportieren.
2. Produkte und Varianten aus PIM/PAM nach Medusa exportieren.
3. Kategorie-Produkt-Positionen aus PIM/PAM nach Medusa senden.

So stehen fuer den Positionssync die benoetigten Medusa-IDs bereits in den PIM/PAM Sync-Mappings.

## Endpoint

`POST /admin/pim/category-product-positions`

Der bestehende Medusa Admin Client verwendet die in PIM/PAM gespeicherte Medusa-Konfiguration. Fuer Secret API Keys wird der vorhandene Header `Authorization: Basic <token>` verwendet.

## JSON-Schema

Das strikte Schema liegt unter:

`schemas/pim-category-product-position-sync.schema.json`

Vor dem Senden validiert PIM/PAM den Payload gegen diese Struktur. Extra-Felder werden abgelehnt.

Das Produktexport-Schema liegt unter:

`schemas/pim-product-export.schema.json`

Es dokumentiert das PIM/PAM-Exportmodell fuer Produkte, Varianten, Kategorie-IDs, Medien, Downloads, Attribute und Metadata.

## Kanal-Kategorie-Export

Kanal-Kategorien werden aus `ChannelCategory` nach Medusa Product Categories exportiert. PIM/PAM speichert die Rueckgabe als Sync-Mapping:

`MedusaSyncMapping.entity_type = "channel_category"`

Wichtige Felder:

- `local_entity_id`: PIM/PAM `channel_categories.id`
- `medusa_id`: Medusa `pcat_...`
- `medusa_handle`: normalisierter Kategorie-Handle
- `medusa_external_id`: PIM/PAM `external_category_id`

Unterkategorien bekommen `parent_category_id`, wenn die Parent-Kategorie bereits exportiert und gemappt ist.

## Produktexport

Der Produktexport reichert bekannte Kategorie-Mappings im Medusa-Payload an:

```json
{
  "categories": [
    { "id": "pcat_01ABCDEF1234567890" }
  ],
  "metadata": {
    "pim_category_mappings": [
      {
        "pimpam_category_id": "78",
        "medusa_category_id": "pcat_01ABCDEF1234567890",
        "sales_channel_id": 1,
        "position": 10
      }
    ]
  }
}
```

Damit werden Produkte in Medusa bereits mit bekannten Kategorien verbunden. Die exakte Reihenfolge wird danach separat ueber den Positionssync gesendet.

## Standardpayload

```json
{
  "product_category_id": "pcat_01ABCDEF1234567890",
  "source": "pim_pam",
  "items": [
    { "product_id": "prod_01AAA111", "position": 10 },
    { "product_id": "prod_01BBB222", "position": 20 }
  ]
}
```

## Sales-Channel-Payload

Wenn kanalabhaengige Sortierung aktiv ist und eine Medusa Sales-Channel-ID bekannt ist, sendet PIM/PAM:

```json
{
  "sales_channel_id": "sc_01ABCDEF1234567890",
  "product_category_id": "pcat_01ABCDEF1234567890",
  "source": "pim_pam",
  "items": [
    { "product_id": "prod_01AAA111", "position": 10 }
  ]
}
```

## Fallback mit Handles

IDs haben Prioritaet. Handles werden nur verwendet, wenn eine Medusa-ID noch nicht in den Sync-Mappings vorhanden ist:

```json
{
  "sales_channel_handle": "voxster.ch",
  "category_handle": "waschmittel",
  "source": "pim_pam",
  "items": [
    { "product_handle": "produkt-a", "position": 10 }
  ]
}
```

## Interne Datenstruktur

Die Position wird nicht global am Produkt gespeichert. Sie liegt auf der Zuordnung:

`ProductCategoryMapping.channel_category_id + ProductCategoryMapping.product_id + ProductCategoryMapping.sales_channel_id + ProductCategoryMapping.position`

## Positionen

Leere Positionen und `null` werden zu `9999`. String-Werte wie `"10"` werden als Integer `10` gesendet. Negative Positionen und Kommazahlen werden vor dem Senden abgelehnt. Doppelte Positionen sind erlaubt, doppelte Produkte im gleichen Payload nicht.

## Sync-Ablauf

1. Kategorien und Produkte nach Medusa exportieren.
2. Medusa-IDs in den PIM/PAM Sync-Mappings speichern oder per Mapping-Repair zurueckladen.
3. Kanal-Kategorie-Produkt-Zuordnungen in PIM/PAM pruefen.
4. Kategorie-Produkt-Positionen per Positionssync senden.

## Dry-Run

Im Medusa-Reiter gibt es einen Positionssync-Dry-Run. Er baut und validiert den Payload, schreibt ihn in die Sync-Logs, sendet aber nichts an Medusa.

## Fehlerfaelle

PIM/PAM sendet nicht, wenn keine Kategorie gefunden wird, keine Produkte zugeordnet sind, ein Produkt doppelt im Payload vorkommt, eine Position ungueltig ist oder weder IDs noch Handles fuer Kategorie/Produkte verfuegbar sind.

## Beispiel curl

```bash
curl -X POST '<MEDUSA_BACKEND_URL>/admin/pim/category-product-positions' \
  -H 'Authorization: Basic <MEDUSA_SECRET_API_KEY>' \
  -H 'Content-Type: application/json' \
  -d '{
    "product_category_id": "pcat_01ABCDEF1234567890",
    "source": "pim_pam",
    "items": [
      { "product_id": "prod_01AAA111", "position": 10 },
      { "product_id": "prod_01BBB222", "position": 20 }
    ]
  }'
```
