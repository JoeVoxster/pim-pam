# Vertriebskanäle im PIM/PAM

## Zielbild

Das PIM/PAM bleibt Master für:

- Produkte
- Varianten
- Assets
- Chemie-/SDB-Daten
- interne Kategorien

Zusätzlich werden Vertriebskanäle dauerhaft im PIM gespeichert. Dadurch wird nicht erst beim Export entschieden, in welchen Shop oder Marktplatz ein Artikel gehört.

## Neue Kernobjekte

- `sales_channels`
  - Stammdaten der Zielkanäle wie `voxster`, `pos`, `chemie_shop`, `otto`, `ebay`
- `product_channel_listings`
  - kanalbezogene Freigabe und Aktivierung eines Produkts
- `variant_channel_listings`
  - kanalbezogene Steuerung einer Variante, z. B. `channel_sku`, `channel_ean`, `shippable`, `hazardous_goods`
- `channel_categories`
  - externe Kanal-/Shop-Kategorien je Vertriebskanal
- `product_category_mappings`
  - Zuordnung eines Produkts zu externen Kanal-Kategorien
- `product_translations`
  - produktbezogene Sprachversionen ohne Produktduplikate
- `variant_translations`
  - variantenbezogene Sprachversionen ohne Variantenduplikate

## Regeln

- Ein Produkt existiert genau einmal als Masterdatensatz.
- Es gibt keine Produktduplikate pro Sprache.
- Interne Kategorien bleiben getrennt von externen Kanal-Kategorien.
- Importierte Artikel werden nicht gelöscht, sondern per Status gesteuert:
  - `imported`
  - `draft`
  - `ready`
  - `published`
  - `inactive`
  - `archived`

## Exportlogik

Ein Kanalexport berücksichtigt nur:

- Produkt-Listings mit `allowed = true`
- Produkt-Listings mit `is_active = true`
- Produkt-Listings innerhalb von `active_from` / `active_until`
- Produkt-Listings mit `publication_status = published`
- Varianten-Listings mit `allowed = true`
- Varianten-Listings mit `is_active = true`
- Varianten-Listings mit `publication_status = published`

Außerdem wird das Kanal-Kategorie-Mapping berücksichtigt.

Bei Übersetzungen gilt:

- wenn für die gewünschte Sprache kanalübergreifende Produkt-/Variantenübersetzungen vorhanden sind, werden diese verwendet
- sonst fällt der Export auf die normalen Masterfelder zurück

## UI

Neu im Admin:

- Menü `Vertriebskanäle`
- Menü `Kanal-Kategorien`
- Reiter `Kanäle / Listings` am Produkt
- Kanal-Export direkt im Menü `Vertriebskanäle`
- Produkt- und Varianten-Übersetzungen im Produktdetail

Im Produkt-Reiter `Kanäle / Listings` können gepflegt werden:

- Produkt pro Kanal:
  - erlaubt
  - aktiv
  - Publikationsstatus
  - Aktiv-von / Aktiv-bis
- externe Kategorie pro Kanal
- Varianten pro Kanal:
  - erlaubt
  - aktiv
  - Publikationsstatus
  - Preis aktiv
  - versandfähig
  - Gefahrgut
  - LQ
  - Kanal-SKU
  - Kanal-EAN

Im Produkt-Reiter `Übersetzungen` können gepflegt werden:

- Produkt:
  - Sprache
  - Titel
  - Kurzbeschreibung
  - Beschreibung
  - SEO-Titel
  - SEO-Beschreibung
  - Slug
- Variante:
  - Sprache
  - Titel
  - Optionslabel
  - Gebindelabel
