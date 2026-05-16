from __future__ import annotations

import re
from copy import deepcopy


SDB_SECTION_TITLES: dict[int, str] = {
    1: "Bezeichnung des Stoffs bzw. Gemischs und des Unternehmens",
    2: "Mögliche Gefahren",
    3: "Zusammensetzung / Angaben zu Bestandteilen",
    4: "Erste-Hilfe-Massnahmen",
    5: "Massnahmen zur Brandbekämpfung",
    6: "Massnahmen bei unbeabsichtigter Freisetzung",
    7: "Handhabung und Lagerung",
    8: "Begrenzung und Überwachung der Exposition / persönliche Schutzausrüstung",
    9: "Physikalische und chemische Eigenschaften",
    10: "Stabilität und Reaktivität",
    11: "Toxikologische Angaben",
    12: "Umweltbezogene Angaben",
    13: "Hinweise zur Entsorgung",
    14: "Angaben zum Transport",
    15: "Rechtsvorschriften",
    16: "Sonstige Angaben",
}

SECTION_9_PROPERTY_DEFAULTS: list[tuple[str, str, str]] = [
    ("appearance", "Aggregatzustand/Form", "nicht verfügbar"),
    ("color", "Farbe", "nicht verfügbar"),
    ("odor", "Geruch", "nicht verfügbar"),
    ("melting_point", "Schmelzpunkt/Gefrierpunkt", "nicht verfügbar"),
    ("boiling_point", "Siedebeginn und Siedebereich", "nicht verfügbar"),
    ("flammability", "Entzündbarkeit", "nicht anwendbar"),
    ("lower_explosion_limit", "Untere Explosionsgrenze", "nicht anwendbar"),
    ("upper_explosion_limit", "Obere Explosionsgrenze", "nicht anwendbar"),
    ("flash_point", "Flammpunkt", "nicht anwendbar"),
    ("auto_ignition_temperature", "Selbstentzündungstemperatur", "nicht verfügbar"),
    ("decomposition_temperature", "Zersetzungstemperatur", "nicht verfügbar"),
    ("ph_value", "pH-Wert", "nicht verfügbar"),
    ("viscosity", "Viskosität", "nicht verfügbar"),
    ("solubility", "Löslichkeit", "nicht verfügbar"),
    ("partition_coefficient", "Verteilungskoeffizient n-Oktanol/Wasser (log Pow)", "nicht verfügbar"),
    ("vapour_pressure", "Dampfdruck", "nicht verfügbar"),
    ("density", "Dichte und/oder relative Dichte", "nicht verfügbar"),
    ("particle_characteristics", "Partikeleigenschaften", "nicht anwendbar"),
    ("voc_content_percent", "VOC-Gehalt", "nicht verfügbar"),
]

SDB_FIELD_DEFAULTS: dict[str, dict[str, object]] = {
    "section_1": {
        "identified_uses": "",
        "uses_advised_against": "",
        "supplier_name": "",
        "supplier_address": "",
        "supplier_phone": "",
        "supplier_email": "",
        "supplier_responsible_person": "",
        "manufacturer_name": "",
        "manufacturer_address": "",
        "manufacturer_phone": "",
        "manufacturer_email": "",
        "emergency_phone": "",
    },
    "section_8": {
        "swiss_exposure_limits": "",
    },
    "section_9": {key: fallback for key, _, fallback in SECTION_9_PROPERTY_DEFAULTS},
    "section_13": {
        "swiss_disposal_notes": "",
        "waste_code": "",
    },
    "section_14": {
        "un_number_14_1": "",
        "shipping_name_14_2": "",
        "transport_class_14_3": "",
        "packing_group_14_4": "",
        "environmental_hazards_14_5": "",
        "special_precautions_for_user": "",
        "bulk_transport_marpol_ibor_imo_or_equivalent_14_7": "",
    },
    "section_15": {
        "regulations_ch": "",
        "chemical_safety_assessment": "",
    },
    "section_16": {
        "revision_notes": "",
        "abbreviations_and_acronyms": "",
        "h_statement_wording_if_needed": "",
        "modern_normative_references": "",
    },
}

REVIEW_MARKERS = (
    "review-entwurf",
    "review required",
    "review_required",
    "review_draft",
    "fachliche prüfung erforderlich",
    "fachliche pruefung erforderlich",
    "automatisch aufbereitete ch-review-ausgabe",
)

PLACEHOLDER_MARKERS = ("nicht verfügbar", "nicht verfuegbar", "nicht anwendbar", "gemäss vorliegenden daten prüfen", "gemaess vorliegenden daten pruefen")

SECTION_1_1_LABEL_MAP: dict[str, str] = {
    "artikelnr": "Artikel-Nr.",
    "stoffnr": "Stoffnr.",
    "egnr": "EG-Nr.",
    "registrierungsnr": "Registrierungsnr.",
    "casnr": "CAS-Nr.",
    "ufi": "UFI",
}


def default_sdb_sections() -> dict[str, dict[str, object]]:
    result: dict[str, dict[str, object]] = {}
    for index, title in SDB_SECTION_TITLES.items():
        key = f"section_{index}"
        result[key] = {
            "title": title,
            "content": "",
            "fields": deepcopy(SDB_FIELD_DEFAULTS.get(key, {})),
        }
    return result


def merge_sdb_sections(raw_sections: dict | None) -> dict[str, dict[str, object]]:
    base = default_sdb_sections()
    for key, value in (raw_sections or {}).items():
        if key not in base:
            continue
        if isinstance(value, dict):
            title = str(value.get("title") or base[key]["title"]).strip() or base[key]["title"]
            content = str(value.get("content") or "").strip()
            fields = deepcopy(base[key]["fields"])
            incoming_fields = value.get("fields")
            if isinstance(incoming_fields, dict):
                for field_name, default_value in fields.items():
                    raw_value = incoming_fields.get(field_name)
                    fields[field_name] = _merge_sdb_field_value(raw_value, default_value)
                for field_name, raw_value in incoming_fields.items():
                    if field_name not in fields:
                        fields[field_name] = _merge_sdb_field_value(raw_value, "")
        else:
            title = base[key]["title"]
            content = str(value or "").strip()
            fields = deepcopy(base[key]["fields"])
        base[key] = {"title": title, "content": content, "fields": fields}
    return base


def _merge_sdb_field_value(raw_value: object, default_value: object = "") -> object:
    if raw_value in (None, ""):
        return default_value
    if isinstance(raw_value, (dict, list, bool, int, float)):
        return raw_value
    return str(raw_value).strip()


def merge_sections(raw_sections: dict | None) -> dict[str, dict[str, object]]:
    return merge_sdb_sections(raw_sections)


def prepare_sdb_sections_for_render(
    sections_json: dict | None,
    *,
    review_status: str | None = None,
    issuer_name: str | None = None,
    issuer_address_line1: str | None = None,
    issuer_address_line2: str | None = None,
    issuer_postal_code: str | None = None,
    issuer_city: str | None = None,
    issuer_country_code: str | None = None,
    issuer_phone: str | None = None,
    issuer_email: str | None = None,
    product_context: dict[str, object] | None = None,
) -> dict[str, dict[str, object]]:
    sections = merge_sections(sections_json)
    product_context = product_context or {}

    section_1 = sections["section_1"]
    section_1_fields = section_1["fields"]
    section_1_blocks = parse_section_1_content(str(section_1["content"] or ""))
    supplier_resolution = resolve_supplier_vs_manufacturer(
        section_1_blocks,
        supplier={
            "name": issuer_name or section_1_fields.get("supplier_name") or "VOXSTER GmbH",
            "address_line1": issuer_address_line1 or "",
            "address_line2": issuer_address_line2 or "",
            "postal_code": issuer_postal_code or "",
            "city": issuer_city or "",
            "country_code": issuer_country_code or "",
            "phone": issuer_phone or section_1_fields.get("supplier_phone") or "",
            "email": issuer_email or section_1_fields.get("supplier_email") or "",
        },
    )
    section_1_fields["identified_uses"] = _coalesce(
        _clean_section_1_2_value(_product_group_identified_use(product_context)),
        _clean_section_1_2_value(section_1_fields.get("identified_uses")),
        _clean_section_1_2_value(_extract_labeled_value(section_1_blocks.get("subsection_1_2", ""), "Relevante identifizierte Verwendungen")),
        _extract_clean_section_1_2_bucket(section_1_blocks.get("subsection_1_2", ""), bucket="identified"),
        "Nicht verfügbar.",
    )
    section_1_fields["uses_advised_against"] = _coalesce(
        _clean_section_1_2_value(_product_group_uses_advised_against(product_context)),
        _clean_section_1_2_value(section_1_fields.get("uses_advised_against")),
        _clean_section_1_2_value(_extract_labeled_value(section_1_blocks.get("subsection_1_2", ""), "Verwendungen, von denen abgeraten wird")),
        _extract_clean_section_1_2_bucket(section_1_blocks.get("subsection_1_2", ""), bucket="advised"),
        "Keine Daten verfügbar.",
    )
    address_lines = [
        supplier_resolution["supplier"]["address_line1"],
        supplier_resolution["supplier"]["address_line2"],
        " ".join(
            part
            for part in [
                supplier_resolution["supplier"]["postal_code"],
                supplier_resolution["supplier"]["city"],
            ]
            if part
        ).strip(),
        supplier_resolution["supplier"]["country_code"],
    ]
    section_1_fields["supplier_name"] = supplier_resolution["supplier"]["name"]
    section_1_fields["supplier_address"] = "\n".join(line for line in address_lines if line)
    section_1_fields["supplier_phone"] = _coalesce(supplier_resolution["supplier"]["phone"], "nicht verfügbar")
    section_1_fields["supplier_email"] = _coalesce(supplier_resolution["supplier"]["email"], "nicht verfügbar")
    section_1_fields["supplier_responsible_person"] = _coalesce(
        section_1_fields.get("supplier_responsible_person"),
        section_1_fields.get("supplier_email"),
        "nicht verfügbar",
    )
    section_1_fields["manufacturer_name"] = _coalesce(supplier_resolution["manufacturer"].get("name"))
    section_1_fields["manufacturer_address"] = _coalesce(supplier_resolution["manufacturer"].get("address"))
    section_1_fields["manufacturer_phone"] = _coalesce(supplier_resolution["manufacturer"].get("phone"))
    section_1_fields["manufacturer_email"] = _coalesce(supplier_resolution["manufacturer"].get("email"))
    section_1_fields["emergency_phone"] = _coalesce(
        section_1_fields.get("emergency_phone"),
        _extract_labeled_value(section_1_blocks.get("subsection_1_4", ""), "Notrufnummer"),
        "145 (Schweiz) / +41 44 251 51 51",
    )
    section_1["content"] = build_sdb_section_content("section_1", section_1, section_1_blocks=section_1_blocks)

    section_2 = sections["section_2"]
    section_2["content"] = dedupe_paragraphs(str(section_2["content"] or ""))
    section_2["content"] = _ensure_section_2_signal_word(section_2["content"])

    section_8 = sections["section_8"]
    if section_8["fields"].get("suva_matches"):
        section_8["fields"]["swiss_exposure_limits"] = ""
        section_8["content"] = _remove_contradictory_swiss_limit_sentence(str(section_8["content"] or ""))
    else:
        section_8["fields"]["swiss_exposure_limits"] = _coalesce(
            section_8["fields"].get("swiss_exposure_limits"),
            "Für die geprüften CAS-Nummern wurden in der hinterlegten SUVA-Referenz keine relevanten MAK-/BAT-Werte gefunden.",
        )
    section_8["content"] = build_sdb_section_content("section_8", section_8)

    section_9 = sections["section_9"]
    section_9["fields"] = _prepare_section_9_fields(section_9["fields"], str(section_9["content"] or ""), product_context)
    suppress_placeholder_when_real_value_exists(section_9["fields"])
    section_9["content"] = build_sdb_section_content("section_9", section_9)

    section_13 = sections["section_13"]
    section_13["fields"]["swiss_disposal_notes"] = _coalesce(
        section_13["fields"].get("swiss_disposal_notes"),
        _default_ch_disposal_notes(),
    )
    section_13["fields"]["waste_code"] = _coalesce_meaningful(
        section_13["fields"].get("waste_code"),
        "fachlich prüfen (beim Hersteller nicht bekannt / Quelle nicht vorhanden)",
    )
    section_13["content"] = build_sdb_section_content("section_13", section_13)

    section_14 = sections["section_14"]
    fields_14 = section_14["fields"]
    no_dangerous_goods = _has_non_dangerous_goods_statement(str(section_14["content"] or ""))
    missing_transport = "Nicht anwendbar" if no_dangerous_goods else "fachlich prüfen (Quelle/Herstellerangabe fehlt)"
    fields_14["un_number_14_1"] = _coalesce_meaningful(fields_14.get("un_number_14_1"), product_context.get("un_number"), missing_transport)
    fields_14["shipping_name_14_2"] = _coalesce(
        fields_14.get("shipping_name_14_2"),
        _extract_labeled_value(str(section_14["content"] or ""), "Versandbezeichnung"),
        missing_transport,
    )
    fields_14["transport_class_14_3"] = _coalesce_meaningful(fields_14.get("transport_class_14_3"), product_context.get("hazard_class"), missing_transport)
    fields_14["packing_group_14_4"] = _coalesce_meaningful(fields_14.get("packing_group_14_4"), product_context.get("packing_group"), missing_transport)
    transport_resolution = resolve_transport_consistency(
        fields_14,
        str(section_14["content"] or ""),
        product_context=product_context,
    )
    fields_14["environmental_hazards_14_5"] = transport_resolution["environmental_hazards_14_5"]
    fields_14["special_precautions_for_user"] = _coalesce(
        fields_14.get("special_precautions_for_user"),
        product_context.get("hazard_shipping_note"),
        "Siehe Abschnitt 7 und 8." if no_dangerous_goods else "Schutzmassnahmen gemäss Abschnitt 7 und 8 beachten.",
    )
    fields_14["bulk_transport_marpol_ibor_imo_or_equivalent_14_7"] = _coalesce(
        fields_14.get("bulk_transport_marpol_ibor_imo_or_equivalent_14_7"),
        "Nicht anwendbar.",
    )
    section_14["content"] = build_sdb_section_content("section_14", section_14)

    section_15 = sections["section_15"]
    section_15["fields"]["regulations_ch"] = _coalesce(
        section_15["fields"].get("regulations_ch"),
        _default_ch_regulations_text(section_15["fields"]),
    )
    section_15["fields"]["chemical_safety_assessment"] = _coalesce(
        section_15["fields"].get("chemical_safety_assessment"),
        "Eine gesonderte Stoffsicherheitsbeurteilung ist aus diesem Datensatz nicht verfügbar.",
    )
    section_15["content"] = build_sdb_section_content("section_15", section_15)

    section_16 = sections["section_16"]
    section_16["content"] = _remove_outdated_legal_references(str(section_16["content"] or ""))
    section_16["fields"]["revision_notes"] = _coalesce(section_16["fields"].get("revision_notes"), "Keine weiteren Angaben.")
    section_16["fields"]["abbreviations_and_acronyms"] = _coalesce(
        section_16["fields"].get("abbreviations_and_acronyms"),
        "ADR, IMDG, IATA, GHS, CAS, EG",
    )
    section_16["fields"]["h_statement_wording_if_needed"] = _coalesce(
        section_16["fields"].get("h_statement_wording_if_needed"),
        _collect_h_statement_wording(section_2["content"]),
        "Wortlaut der relevanten H-Sätze siehe Abschnitt 2.",
    )
    section_16["fields"]["modern_normative_references"] = _coalesce(
        section_16["fields"].get("modern_normative_references"),
        _default_modern_normative_references(),
    )
    section_16["content"] = build_sdb_section_content("section_16", section_16)

    for key in [f"section_{idx}" for idx in range(3, 8)] + [f"section_{idx}" for idx in range(10, 13)]:
        sections[key]["content"] = dedupe_paragraphs(str(sections[key]["content"] or ""))

    return sections


def sync_sdb_fields_from_content(
    sections_json: dict | None,
    *,
    issuer_name: str | None = None,
    issuer_address_line1: str | None = None,
    issuer_address_line2: str | None = None,
    issuer_postal_code: str | None = None,
    issuer_city: str | None = None,
    issuer_country_code: str | None = None,
    issuer_phone: str | None = None,
    issuer_email: str | None = None,
    product_context: dict[str, object] | None = None,
) -> dict[str, dict[str, object]]:
    sections = merge_sections(sections_json)
    product_context = product_context or {}

    section_1 = sections["section_1"]
    section_1_fields = section_1["fields"]
    section_1_blocks = parse_section_1_content(str(section_1["content"] or ""))
    supplier_resolution = resolve_supplier_vs_manufacturer(
        section_1_blocks,
        supplier={
            "name": issuer_name or section_1_fields.get("supplier_name") or "VOXSTER GmbH",
            "address_line1": issuer_address_line1 or _extract_address_line(section_1_fields.get("supplier_address"), 0),
            "address_line2": issuer_address_line2 or _extract_address_line(section_1_fields.get("supplier_address"), 1),
            "postal_code": issuer_postal_code or "",
            "city": issuer_city or "",
            "country_code": issuer_country_code or "",
            "phone": issuer_phone or section_1_fields.get("supplier_phone") or "",
            "email": issuer_email or section_1_fields.get("supplier_email") or "",
        },
    )
    section_1_fields["identified_uses"] = _coalesce(
        _clean_section_1_2_value(_product_group_identified_use(product_context)),
        _clean_section_1_2_value(section_1_fields.get("identified_uses")),
        _clean_section_1_2_value(_extract_labeled_value(section_1_blocks.get("subsection_1_2", ""), "Relevante identifizierte Verwendungen")),
        _extract_clean_section_1_2_bucket(section_1_blocks.get("subsection_1_2", ""), bucket="identified"),
        "Nicht verfügbar.",
    )
    section_1_fields["uses_advised_against"] = _coalesce(
        _clean_section_1_2_value(_product_group_uses_advised_against(product_context)),
        _clean_section_1_2_value(section_1_fields.get("uses_advised_against")),
        _clean_section_1_2_value(_extract_labeled_value(section_1_blocks.get("subsection_1_2", ""), "Verwendungen, von denen abgeraten wird")),
        _extract_clean_section_1_2_bucket(section_1_blocks.get("subsection_1_2", ""), bucket="advised"),
        "Keine Daten verfügbar.",
    )
    address_lines = [
        supplier_resolution["supplier"]["address_line1"],
        supplier_resolution["supplier"]["address_line2"],
        " ".join(
            part
            for part in [
                supplier_resolution["supplier"]["postal_code"],
                supplier_resolution["supplier"]["city"],
            ]
            if part
        ).strip(),
        supplier_resolution["supplier"]["country_code"],
    ]
    section_1_fields["supplier_name"] = supplier_resolution["supplier"]["name"]
    section_1_fields["supplier_address"] = "\n".join(line for line in address_lines if line)
    section_1_fields["supplier_phone"] = _coalesce(
        _extract_labeled_value(section_1_blocks.get("subsection_1_3", ""), "Telefon"),
        supplier_resolution["supplier"]["phone"],
        section_1_fields.get("supplier_phone"),
        "nicht verfügbar",
    )
    section_1_fields["supplier_email"] = _coalesce(
        _extract_labeled_value(section_1_blocks.get("subsection_1_3", ""), "E-Mail der für das SDB verantwortlichen Person"),
        _extract_labeled_value(section_1_blocks.get("subsection_1_3", ""), "E-Mail"),
        supplier_resolution["supplier"]["email"],
        section_1_fields.get("supplier_email"),
        "nicht verfügbar",
    )
    section_1_fields["manufacturer_name"] = _coalesce(supplier_resolution["manufacturer"].get("name"), section_1_fields.get("manufacturer_name"))
    section_1_fields["manufacturer_address"] = _coalesce(supplier_resolution["manufacturer"].get("address"), section_1_fields.get("manufacturer_address"))
    section_1_fields["manufacturer_phone"] = _coalesce(supplier_resolution["manufacturer"].get("phone"), section_1_fields.get("manufacturer_phone"))
    section_1_fields["manufacturer_email"] = _coalesce(supplier_resolution["manufacturer"].get("email"), section_1_fields.get("manufacturer_email"))
    section_1_fields["emergency_phone"] = _coalesce(
        _extract_labeled_value(section_1_blocks.get("subsection_1_4", ""), "Tox Info Suisse (Schweiz)"),
        _extract_labeled_value(section_1_blocks.get("subsection_1_4", ""), "Notrufnummer"),
        section_1_fields.get("emergency_phone"),
        "145 (Schweiz) / +41 44 251 51 51",
    )

    section_8 = sections["section_8"]
    if section_8["fields"].get("suva_matches"):
        section_8["fields"]["swiss_exposure_limits"] = ""
        section_8["content"] = _remove_contradictory_swiss_limit_sentence(str(section_8["content"] or ""))
    else:
        section_8["fields"]["swiss_exposure_limits"] = _coalesce(
            _extract_labeled_value(str(section_8["content"] or ""), "Schweizer Expositionsgrenzwerte / MAK-Bezug"),
            section_8["fields"].get("swiss_exposure_limits"),
            "Für die geprüften CAS-Nummern wurden in der hinterlegten SUVA-Referenz keine relevanten MAK-/BAT-Werte gefunden.",
        )

    section_9 = sections["section_9"]
    section_9["fields"] = _prepare_section_9_fields(section_9["fields"], str(section_9["content"] or ""), product_context)
    suppress_placeholder_when_real_value_exists(section_9["fields"])

    section_13 = sections["section_13"]
    section_13["fields"]["swiss_disposal_notes"] = _coalesce(
        _extract_labeled_value(str(section_13["content"] or ""), "Schweizer Entsorgungshinweise"),
        section_13["fields"].get("swiss_disposal_notes"),
        _default_ch_disposal_notes(),
    )
    section_13["fields"]["waste_code"] = _coalesce_meaningful(
        _extract_labeled_value(str(section_13["content"] or ""), "Abfallcode"),
        _extract_labeled_value(str(section_13["content"] or ""), "Schweizer Abfallcode/LVA-Code"),
        section_13["fields"].get("waste_code"),
        "fachlich prüfen (beim Hersteller nicht bekannt / Quelle nicht vorhanden)",
    )

    section_14 = sections["section_14"]
    section_14_fields = section_14["fields"]
    section_14_content = str(section_14["content"] or "")
    no_dangerous_goods = _has_non_dangerous_goods_statement(section_14_content)
    missing_transport = "Nicht anwendbar" if no_dangerous_goods else "fachlich prüfen (Quelle/Herstellerangabe fehlt)"
    section_14_fields["un_number_14_1"] = _coalesce_meaningful(
        _extract_numbered_field_value(section_14_content, "14.1"),
        section_14_fields.get("un_number_14_1"),
        product_context.get("un_number"),
        missing_transport,
    )
    section_14_fields["shipping_name_14_2"] = _coalesce_meaningful(
        _extract_numbered_field_value(section_14_content, "14.2"),
        section_14_fields.get("shipping_name_14_2"),
        missing_transport,
    )
    section_14_fields["transport_class_14_3"] = _coalesce_meaningful(
        _extract_numbered_field_value(section_14_content, "14.3"),
        section_14_fields.get("transport_class_14_3"),
        product_context.get("hazard_class"),
        missing_transport,
    )
    section_14_fields["packing_group_14_4"] = _coalesce_meaningful(
        _extract_numbered_field_value(section_14_content, "14.4"),
        section_14_fields.get("packing_group_14_4"),
        product_context.get("packing_group"),
        missing_transport,
    )
    transport_resolution = resolve_transport_consistency(section_14_fields, section_14_content, product_context=product_context)
    section_14_fields["environmental_hazards_14_5"] = transport_resolution["environmental_hazards_14_5"]
    section_14_fields["special_precautions_for_user"] = _coalesce(
        _extract_numbered_field_value(section_14_content, "14.6"),
        section_14_fields.get("special_precautions_for_user"),
        product_context.get("hazard_shipping_note"),
        "Siehe Abschnitt 7 und 8." if no_dangerous_goods else "Schutzmassnahmen gemäss Abschnitt 7 und 8 beachten.",
    )
    section_14_fields["bulk_transport_marpol_ibor_imo_or_equivalent_14_7"] = _coalesce(
        _extract_numbered_field_value(section_14_content, "14.7"),
        section_14_fields.get("bulk_transport_marpol_ibor_imo_or_equivalent_14_7"),
        "Nicht anwendbar.",
    )

    section_15 = sections["section_15"]
    section_15["fields"]["regulations_ch"] = _coalesce(
        _extract_labeled_value(str(section_15["content"] or ""), "Schweizer Rechtsvorschriften"),
        section_15["fields"].get("regulations_ch"),
        _default_ch_regulations_text(section_15["fields"]),
    )
    section_15["fields"]["chemical_safety_assessment"] = _coalesce(
        _extract_labeled_value(str(section_15["content"] or ""), "Stoffsicherheitsbeurteilung"),
        section_15["fields"].get("chemical_safety_assessment"),
        "Eine gesonderte Stoffsicherheitsbeurteilung ist aus diesem Datensatz nicht verfügbar.",
    )

    section_16 = sections["section_16"]
    section_16["content"] = _remove_outdated_legal_references(str(section_16["content"] or ""))
    section_16["fields"]["revision_notes"] = _coalesce(
        _extract_labeled_value(str(section_16["content"] or ""), "Änderungshinweise / Revisionsnotizen"),
        section_16["fields"].get("revision_notes"),
        "Keine weiteren Angaben.",
    )
    section_16["fields"]["abbreviations_and_acronyms"] = _coalesce(
        _extract_labeled_value(str(section_16["content"] or ""), "Abkürzungen und Akronyme"),
        section_16["fields"].get("abbreviations_and_acronyms"),
        "ADR, IMDG, IATA, GHS, CAS, EG",
    )
    section_16["fields"]["h_statement_wording_if_needed"] = _coalesce(
        _extract_labeled_value(str(section_16["content"] or ""), "Wortlaut der relevanten H-Sätze"),
        section_16["fields"].get("h_statement_wording_if_needed"),
        _collect_h_statement_wording(sections["section_2"]["content"]),
        "Wortlaut der relevanten H-Sätze siehe Abschnitt 2.",
    )
    section_16["fields"]["modern_normative_references"] = _coalesce(
        section_16["fields"].get("modern_normative_references"),
        _default_modern_normative_references(),
    )
    return sections


def validate_sdb_sections(
    sections_json: dict | None,
    *,
    review_status: str | None = None,
    issuer_name: str | None = None,
    issuer_address_line1: str | None = None,
    issuer_address_line2: str | None = None,
    issuer_postal_code: str | None = None,
    issuer_city: str | None = None,
    issuer_country_code: str | None = None,
    issuer_phone: str | None = None,
    issuer_email: str | None = None,
    product_context: dict[str, object] | None = None,
) -> dict[str, object]:
    raw_sections = merge_sections(sections_json)
    sections = prepare_sdb_sections_for_render(
        sections_json,
        review_status=review_status,
        issuer_name=issuer_name,
        issuer_address_line1=issuer_address_line1,
        issuer_address_line2=issuer_address_line2,
        issuer_postal_code=issuer_postal_code,
        issuer_city=issuer_city,
        issuer_country_code=issuer_country_code,
        issuer_phone=issuer_phone,
        issuer_email=issuer_email,
        product_context=product_context,
    )
    errors: list[str] = []

    for index in range(1, 17):
        key = f"section_{index}"
        section = sections.get(key)
        if not section:
            errors.append(f"{key}: Abschnitt fehlt")
            continue
        if not str(section.get("content") or "").strip():
            errors.append(f"{key}: Abschnitt ist leer")

    if len({key for key in sections.keys() if key.startswith("section_")}) != 16:
        errors.append("no_duplicate_sections: Abschnittsschlüssel inkonsistent")

    section_1 = sections["section_1"]["content"]
    for marker in ("1.2", "1.3", "1.4"):
        if section_1.count(marker) != 1:
            errors.append(f"no_duplicate_subsection_keys: {marker} kommt nicht genau einmal vor")

    section_1_fields = sections["section_1"]["fields"]
    if _is_placeholder_required_value(section_1_fields.get("supplier_phone")):
        errors.append("section_1.3.supplier_phone fehlt")
    if _is_placeholder_required_value(section_1_fields.get("supplier_email")):
        errors.append("section_1.3.supplier_email fehlt")
    if not str(section_1_fields.get("identified_uses") or "").strip():
        errors.append("section_1.2.identified_uses fehlt")
    if str(section_1_fields.get("supplier_name") or "").strip().lower() != "voxster gmbh":
        errors.append("supplier_block_present_for_voxster verletzt")

    section_14_content = str(sections["section_14"]["content"] or "")
    for marker in ("14.1", "14.2", "14.3", "14.4", "14.5", "14.6", "14.7"):
        if section_14_content.count(marker) != 1:
            errors.append(f"section_14: {marker} fehlt oder ist doppelt")
    transport_resolution = resolve_transport_consistency(
        raw_sections["section_14"]["fields"],
        str(raw_sections["section_14"]["content"] or ""),
        product_context=product_context or {},
    )
    for conflict in transport_resolution["conflicts"]:
        errors.append(f"no_conflicting_transport_flags: {conflict}")

    section_9 = sections["section_9"]
    for field_name, label, _fallback in SECTION_9_PROPERTY_DEFAULTS:
        value = str(section_9["fields"].get(field_name) or "").strip()
        if not value:
            errors.append(f"section_9.{field_name} ({label}) fehlt")
        line_count = len(re.findall(rf"(?im)^\s*{re.escape(label)}\s*:", str(section_9['content'])))
        if line_count != 1:
            errors.append(f"no_duplicate_subsection_keys: {label} kommt nicht genau einmal vor")
    placeholder_conflicts = find_placeholder_after_real_value(section_9["fields"])
    for field_name in placeholder_conflicts:
        errors.append(f"no_placeholder_after_real_value: {field_name}")

    section_15_content = str(sections["section_15"]["content"] or "")
    section_16_content = str(sections["section_16"]["content"] or "")
    if not section_15_content:
        errors.append("section_15: Abschnitt ist leer")
    if not section_16_content:
        errors.append("section_16: Abschnitt ist leer")

    if _is_release_build(review_status):
        joined = "\n".join(str(sections[f"section_{index}"]["content"] or "") for index in range(1, 17))
        raw_joined = "\n".join(str(raw_sections[f"section_{index}"]["content"] or "") for index in range(1, 17))
        if _contains_review_marker(joined) or _contains_review_marker(str(review_status or "")):
            errors.append("no_release_with_review_markers verletzt")
        if _contains_review_marker(raw_joined):
            errors.append("no_release_with_review_markers verletzt")

    return {"is_valid": not errors, "errors": errors, "sections": sections}


def validate_sdb_payload(
    sections_json: dict | None,
    *,
    issuer_phone: str | None = None,
    issuer_email: str | None = None,
) -> dict[str, object]:
    sections = merge_sections(sections_json)
    errors: list[str] = []
    for index in range(1, 17):
        key = f"section_{index}"
        section = sections.get(key)
        if not section:
            errors.append(f"{key}: Abschnitt fehlt")
            continue
        has_content = bool(str(section.get("content") or "").strip())
        has_fields = bool([value for value in (section.get("fields") or {}).values() if str(value or "").strip()])
        if not has_content and not has_fields:
            errors.append(f"{key}: Abschnitt ist leer")

    section_1_fields = sections["section_1"]["fields"]
    identified_uses = str(section_1_fields.get("identified_uses") or "").strip()
    if not identified_uses and "Relevante identifizierte Verwendungen" not in str(sections["section_1"]["content"] or ""):
        errors.append("section_1.2.identified_uses fehlt")
    if _is_placeholder_required_value(issuer_phone or section_1_fields.get("supplier_phone")):
        errors.append("section_1.3.supplier_phone fehlt")
    if _is_placeholder_required_value(issuer_email or section_1_fields.get("supplier_email")):
        errors.append("section_1.3.supplier_email fehlt")

    section_14 = sections["section_14"]
    section_14_content = str(section_14["content"] or "")
    if "14.6" not in section_14_content and not str(section_14["fields"].get("special_precautions_for_user") or "").strip():
        errors.append("section_14: 14.6 fehlt oder ist doppelt")
    if "14.7" not in section_14_content and not str(section_14["fields"].get("bulk_transport_marpol_ibor_imo_or_equivalent_14_7") or "").strip():
        errors.append("section_14: 14.7 fehlt oder ist doppelt")

    section_9_fields = sections["section_9"]["fields"]
    for field_name, label, fallback in SECTION_9_PROPERTY_DEFAULTS:
        value = str(section_9_fields.get(field_name) or "").strip()
        if not value and fallback not in str(sections["section_9"]["content"] or ""):
            errors.append(f"section_9.{field_name} ({label}) fehlt")

    return {"is_valid": not errors, "errors": errors}


def build_sdb_section_content(
    section_key: str,
    section: dict[str, object],
    *,
    section_1_blocks: dict[str, str] | None = None,
) -> str:
    content = dedupe_paragraphs(str(section.get("content") or "").strip())
    fields = section.get("fields") if isinstance(section.get("fields"), dict) else {}
    if not isinstance(fields, dict):
        fields = {}

    if section_key == "section_1":
        section_1_blocks = section_1_blocks or parse_section_1_content(content)
        lines = []
        section_1_1 = _normalize_section_1_1_content(
            section_1_blocks.get("subsection_1_1") or _extract_subsection_value(content, "1.1")
        )
        if section_1_1:
            lines.append(f"1.1 Produktidentifikator\n{section_1_1}".strip())
        lines.append(
            "\n".join(
                [
                    "1.2 Relevante identifizierte Verwendungen des Stoffs oder Gemischs und Verwendungen, von denen abgeraten wird",
                    f"Relevante identifizierte Verwendungen: {fields.get('identified_uses') or 'Nicht verfügbar.'}",
                    f"Verwendungen, von denen abgeraten wird: {fields.get('uses_advised_against') or 'Keine Daten verfügbar.'}",
                ]
            )
        )
        supplier_lines = [
            "1.3 Einzelheiten zum Lieferanten, der das Sicherheitsdatenblatt bereitstellt",
            str(fields.get("supplier_name") or "VOXSTER GmbH"),
        ]
        supplier_address = str(fields.get("supplier_address") or "").strip()
        if supplier_address:
            supplier_lines.extend([line for line in supplier_address.splitlines() if line.strip()])
        supplier_lines.extend(
            [
                f"Telefon: {fields.get('supplier_phone') or 'nicht verfügbar'}",
                f"E-Mail der für das SDB verantwortlichen Person: {fields.get('supplier_email') or 'nicht verfügbar'}",
            ]
        )
        if str(fields.get("manufacturer_name") or "").strip():
            supplier_lines.extend(
                [
                    "",
                    "Hersteller (laut Quelle):",
                    str(fields.get("manufacturer_name") or ""),
                ]
            )
            for manufacturer_line in str(fields.get("manufacturer_address") or "").splitlines():
                if manufacturer_line.strip():
                    supplier_lines.append(manufacturer_line.strip())
            if str(fields.get("manufacturer_phone") or "").strip():
                supplier_lines.append(f"Telefon Hersteller: {fields.get('manufacturer_phone')}")
            if str(fields.get("manufacturer_email") or "").strip():
                supplier_lines.append(f"E-Mail Hersteller: {fields.get('manufacturer_email')}")
        lines.append("\n".join(supplier_lines))
        lines.append(f"1.4 Notrufnummer\nTox Info Suisse (Schweiz): {fields.get('emergency_phone') or '145 (Schweiz) / +41 44 251 51 51'}")
        return "\n\n".join(block.strip() for block in lines if str(block).strip())

    if section_key == "section_8":
        if fields.get("suva_matches"):
            return dedupe_paragraphs(_remove_contradictory_swiss_limit_sentence(content))
        supplement = f"Schweizer Expositionsgrenzwerte / MAK-Bezug: {fields.get('swiss_exposure_limits') or 'nicht verfügbar'}"
        return _join_content_blocks(content, supplement)

    if section_key == "section_9":
        lines = ["9.1 Angaben zu den grundlegenden physikalischen und chemischen Eigenschaften"]
        for field_name, label, fallback in SECTION_9_PROPERTY_DEFAULTS:
            lines.append(f"{label}: {fields.get(field_name) or fallback}")
        return "\n".join(lines)

    if section_key == "section_13":
        lines = [
            dedupe_paragraphs(content),
            f"Schweizer Entsorgungshinweise:\n{fields.get('swiss_disposal_notes') or _default_ch_disposal_notes()}",
            f"Schweizer Abfallcode/LVA-Code: {fields.get('waste_code') or 'fachlich prüfen (beim Hersteller nicht bekannt / Quelle nicht vorhanden)'}",
        ]
        return _join_content_blocks(*lines)

    if section_key == "section_14":
        lines = [
            f"14.1 UN-Nummer oder ID-Nummer: {fields.get('un_number_14_1') or 'nicht verfügbar'}",
            f"14.2 Ordnungsgemässe UN-Versandbezeichnung: {fields.get('shipping_name_14_2') or 'nicht verfügbar'}",
            f"14.3 Transportgefahrenklassen: {fields.get('transport_class_14_3') or 'nicht verfügbar'}",
            f"14.4 Verpackungsgruppe: {fields.get('packing_group_14_4') or 'nicht verfügbar'}",
            f"14.5 Umweltgefahren: {fields.get('environmental_hazards_14_5') or 'nicht verfügbar'}",
            f"14.6 Besondere Vorsichtsmassnahmen für den Verwender: {fields.get('special_precautions_for_user') or 'nicht verfügbar'}",
            f"14.7 Massengutbeförderung auf dem Seeweg gemäss IMO-Instrumenten: {fields.get('bulk_transport_marpol_ibor_imo_or_equivalent_14_7') or 'nicht verfügbar'}",
        ]
        return "\n".join(lines)

    if section_key == "section_15":
        lines = [dedupe_paragraphs(content)]
        reg_text = f"Schweizer Rechtsvorschriften: {fields.get('regulations_ch') or 'nicht verfügbar'}"
        csa_text = f"Stoffsicherheitsbeurteilung: {fields.get('chemical_safety_assessment') or 'nicht verfügbar'}"
        if reg_text not in lines:
            lines.append(reg_text)
        if csa_text not in lines:
            lines.append(csa_text)
        return _join_content_blocks(*lines)

    if section_key == "section_16":
        lines = [dedupe_paragraphs(content)]
        for line in [
            f"Wichtigste normative Verweisungen: {fields.get('modern_normative_references') or 'nicht verfügbar'}",
            f"Änderungshinweise / Revisionsnotizen: {fields.get('revision_notes') or 'nicht verfügbar'}",
            f"Abkürzungen und Akronyme: {fields.get('abbreviations_and_acronyms') or 'nicht verfügbar'}",
            f"Wortlaut der relevanten H-Sätze: {fields.get('h_statement_wording_if_needed') or 'nicht verfügbar'}",
        ]:
            if line not in lines:
                lines.append(line)
        return _join_content_blocks(*lines)

    return content


def dedupe_paragraphs(text: str) -> str:
    seen: set[str] = set()
    result: list[str] = []
    for paragraph in re.split(r"\n\s*\n", str(text or "").strip()):
        cleaned_lines: list[str] = []
        for line in paragraph.splitlines():
            stripped = re.sub(r"\s+", " ", line).strip()
            if not stripped:
                continue
            if cleaned_lines and cleaned_lines[-1] == stripped:
                continue
            cleaned_lines.append(stripped)
        cleaned = "\n".join(cleaned_lines).strip()
        if not cleaned:
            continue
        normalized = cleaned.casefold()
        if normalized in seen:
            continue
        seen.add(normalized)
        result.append(cleaned)
    return "\n\n".join(result).strip()


def parse_section_1_content(text: str) -> dict[str, str]:
    result: dict[str, str] = {
        "subsection_1_1": "",
        "subsection_1_2": "",
        "subsection_1_3": "",
        "subsection_1_4": "",
    }
    for marker in ("1.1", "1.2", "1.3", "1.4"):
        pattern = re.compile(
            rf"(?is)(?:^|\n)\s*{re.escape(marker)}\b.*?(?=\n\s*1\.[1-4]\b(?!{re.escape(marker[2])})|\n\s*2\b|\Z)"
        )
        match = pattern.search(text or "")
        if match:
            value = match.group(0).strip()
            value = re.sub(rf"(?is)^{re.escape(marker)}\s*", "", value).strip()
            result[f"subsection_{marker.replace('.', '_')}"] = value
    return result


def resolve_supplier_vs_manufacturer(section_1_blocks: dict[str, str], supplier: dict[str, str]) -> dict[str, dict[str, str]]:
    supplier_name = _coalesce(supplier.get("name"), "VOXSTER GmbH")
    manufacturer = {
        "name": "",
        "address": "",
        "phone": "",
        "email": "",
    }
    source_block = section_1_blocks.get("subsection_1_3", "")
    manufacturer_source = source_block.split("Hersteller (laut Quelle):", 1)[1] if "Hersteller (laut Quelle):" in source_block else source_block
    lines = [_normalize_inline_spacing(line) for line in manufacturer_source.splitlines() if _normalize_inline_spacing(line)]
    manufacturer_name = ""
    manufacturer_lines: list[str] = []
    manufacturer_phone = ""
    manufacturer_email = ""
    for line in lines:
        phone_match = re.match(r"(?i)^telefon(?:\s+hersteller)?\s*:\s*(.+)$", line)
        if phone_match and not manufacturer_phone:
            candidate = _normalize_inline_spacing(phone_match.group(1))
            if _looks_like_phone_number(candidate):
                manufacturer_phone = candidate
                continue
        email_match = re.match(r"(?i)^e-?mail(?:\s+hersteller)?\s*:\s*(.+)$", line)
        if email_match and not manufacturer_email:
            candidate = _normalize_inline_spacing(email_match.group(1))
            if "@" in candidate:
                manufacturer_email = candidate
                continue
        if _is_section_1_3_noise_line(line, supplier):
            continue
        if not manufacturer_email and "@" in line:
            manufacturer_email = line
            continue
        if not manufacturer_phone and _looks_like_phone_number(line):
            manufacturer_phone = line
            continue
        if not manufacturer_name and supplier_name.casefold() not in line.casefold():
            manufacturer_name = line
            continue
        if manufacturer_name:
            manufacturer_lines.append(line)
    if manufacturer_name and manufacturer_name.casefold() != supplier_name.casefold():
        manufacturer = {
            "name": manufacturer_name,
            "address": "\n".join(manufacturer_lines).strip(),
            "phone": manufacturer_phone,
            "email": manufacturer_email,
        }
    return {
        "supplier": {
            "name": supplier_name,
            "address_line1": _coalesce(supplier.get("address_line1")),
            "address_line2": _coalesce(supplier.get("address_line2")),
            "postal_code": _coalesce(supplier.get("postal_code")),
            "city": _coalesce(supplier.get("city")),
            "country_code": _coalesce(supplier.get("country_code")),
            "phone": _coalesce(supplier.get("phone")),
            "email": _coalesce(supplier.get("email")),
        },
        "manufacturer": manufacturer,
    }


def resolve_transport_consistency(
    fields: dict[str, object],
    content: str,
    *,
    product_context: dict[str, object] | None = None,
) -> dict[str, object]:
    product_context = product_context or {}
    raw_value = str(fields.get("environmental_hazards_14_5") or "").strip()
    extracted_flag = _extract_transport_environment_flag(content)
    context_flag = _coalesce(product_context.get("transport_environmental_flag"))
    conflict_flags: list[str] = []
    positive = any("umwelt" in value.casefold() or "marine pollutant" in value.casefold() for value in [raw_value, extracted_flag, context_flag] if value)
    negative = any("nicht" in value.casefold() and ("umwelt" in value.casefold() or "marine pollutant" in value.casefold()) for value in [raw_value, extracted_flag, context_flag] if value)
    placeholder_present = _contains_transport_placeholder(content) or any(
        _contains_transport_placeholder(value) for value in [raw_value, extracted_flag, context_flag] if value
    )
    if positive and placeholder_present:
        conflict_flags.append("Umweltgefahren sind gleichzeitig konkret und als Prüfhinweis markiert")
    if positive and negative:
        conflict_flags.append("Widersprüchliche Umweltgefahren-Angaben")
    resolved = _coalesce(
        next((value for value in [raw_value, extracted_flag, context_flag] if value and not _contains_transport_placeholder(value)), ""),
        raw_value,
        extracted_flag,
        context_flag,
        "nicht verfügbar",
    )
    return {
        "environmental_hazards_14_5": resolved,
        "conflicts": conflict_flags,
    }


def suppress_placeholder_when_real_value_exists(fields: dict[str, object]) -> dict[str, object]:
    for field_name, value in list(fields.items()):
        text = str(value or "").strip()
        if "\n" not in text:
            continue
        parts = [part.strip() for part in text.splitlines() if part.strip()]
        real_parts = [part for part in parts if not _is_placeholder_required_value(part)]
        if real_parts:
            fields[field_name] = real_parts[0]
    return fields


def find_placeholder_after_real_value(fields: dict[str, object]) -> list[str]:
    conflicts: list[str] = []
    for field_name, value in fields.items():
        text = str(value or "").strip()
        if not text:
            continue
        parts = [part.strip() for part in text.splitlines() if part.strip()]
        if len(parts) < 2:
            continue
        if any(not _is_placeholder_required_value(part) for part in parts) and any(_is_placeholder_required_value(part) for part in parts):
            conflicts.append(field_name)
    return conflicts


def _prepare_section_9_fields(fields: dict[str, object], content: str, product_context: dict[str, object]) -> dict[str, str]:
    prepared: dict[str, str] = {}
    normalized = str(content or "")
    context_map = {
        "color": str(product_context.get("color") or "").strip(),
        "odor": str(product_context.get("odor") or "").strip(),
        "ph_value": str(product_context.get("ph_value") or "").strip(),
        "flash_point": str(product_context.get("flash_point") or "").strip(),
        "boiling_point": str(product_context.get("boiling_point") or "").strip(),
        "viscosity": str(product_context.get("viscosity") or "").strip(),
        "solubility": str(product_context.get("solubility") or "").strip(),
        "density": str(product_context.get("density") or "").strip(),
        "appearance": str(product_context.get("appearance") or "").strip(),
        "voc_content_percent": str(product_context.get("voc_content_percent") or "").strip(),
    }
    content_extractors = {
        "appearance": ["Aussehen", "Aggregatzustand/Form", "Aggregatzustand", "Form"],
        "color": ["Farbe"],
        "odor": ["Geruch"],
        "melting_point": ["Schmelzpunkt", "Gefrierpunkt"],
        "boiling_point": ["Siedebeginn und Siedebereich", "Siedepunkt", "Siedebereich"],
        "flammability": ["Entzündlichkeit", "Entzündbarkeit"],
        "lower_explosion_limit": ["Untere Explosionsgrenze", "Untere"],
        "upper_explosion_limit": ["Obere Explosionsgrenze", "Obere"],
        "flash_point": ["Flammpunkt"],
        "auto_ignition_temperature": ["Selbstentzündung", "Zündtemperatur"],
        "decomposition_temperature": ["Zersetzungstemperatur"],
        "ph_value": ["pH-Wert"],
        "viscosity": ["Viskosität"],
        "solubility": ["Wasserlöslichkeit", "Löslichkeit"],
        "partition_coefficient": ["Verteilungskoeffizient", "log Pow"],
        "vapour_pressure": ["Dampfdruck"],
        "density": ["Relative Dichte", "Dichte und/oder relative Dichte", "Dichte"],
        "particle_characteristics": ["Partikeleigenschaften"],
        "voc_content_percent": ["VOC-Gehalt", "VOC"],
    }
    for field_name, _label, fallback in SECTION_9_PROPERTY_DEFAULTS:
        extracted = ""
        for hint in content_extractors.get(field_name, []):
            extracted = _extract_following_property_value(normalized, hint)
            if not extracted:
                extracted = _extract_labeled_value(normalized, hint)
            if extracted and _is_bad_section_9_extracted_value(extracted):
                extracted = ""
            if extracted:
                break
        prepared[field_name] = _normalize_section_9_value(field_name, _coalesce_meaningful(context_map.get(field_name), extracted, fields.get(field_name), fallback))
    return prepared


def _normalize_section_9_value(field_name: str, value: str) -> str:
    text = str(value or "").strip()
    if field_name == "odor" and text.casefold() == "merkmal":
        return "charakteristisch"
    return text


def _coalesce_meaningful(*values: object) -> str:
    for value in values:
        text = str(value or "").strip()
        if text and not _is_placeholder_required_value(text):
            return text
    for value in values:
        text = str(value or "").strip()
        if text:
            return text
    return ""


def _extract_following_property_value(text: str, label: str) -> str:
    lines = [_normalize_inline_spacing(line) for line in str(text or "").splitlines()]
    noise = {
        "",
        "wert",
        "bestimmungsmethode",
        "physikalische und chemische",
        "physikalische und chemische eigenschaften",
        "eigenschaften",
    }
    for index, line in enumerate(lines):
        if not _line_matches_property_label(line, label):
            continue
        for candidate in lines[index + 1 : index + 5]:
            normalized = candidate.casefold().strip(" :")
            if normalized in noise:
                continue
            if re.match(r"(?i)^(geowin|sicherheitsdatenblatt|ausgestellt|gemäss|gemaess|#\\s*\\d+)", candidate):
                continue
            if any(other.casefold() == normalized for _key, other, _fallback in SECTION_9_PROPERTY_DEFAULTS):
                continue
            return candidate
    return ""


def _line_matches_property_label(line: str, label: str) -> bool:
    normalized_line = line.casefold().strip(" :")
    normalized_label = label.casefold().strip(" :")
    if normalized_line == normalized_label:
        return True
    return bool(re.match(rf"^{re.escape(normalized_label)}\s*\([^)]*\)\s*$", normalized_line))


def _is_bad_section_9_extracted_value(value: str) -> bool:
    text = _normalize_inline_spacing(value).casefold().strip(" :")
    if not text:
        return True
    if text in {"(en)", "en", "wert", "bestimmungsmethode", "physikalische und chemische eigenschaften"}:
        return True
    if text.endswith("schwelle"):
        return True
    return False


def _product_group_identified_use(product_context: dict[str, object]) -> str:
    haystack = " ".join(str(product_context.get(key) or "") for key in ("product_name", "title", "sku", "product_title"))
    if re.search(r"flecken|spot|stain|schweiss|schweiß|sudore", haystack, flags=re.I):
        return "Vorbehandlung von Textilien zur Entfernung von Schweiss- und Urinflecken; gewerbliche Anwendung."
    return ""


def _product_group_uses_advised_against(product_context: dict[str, object]) -> str:
    haystack = " ".join(str(product_context.get(key) or "") for key in ("product_name", "title", "sku", "product_title"))
    if re.search(r"flecken|spot|stain|schweiss|schweiß|sudore", haystack, flags=re.I):
        return "Nicht für private Anwendung. Nicht für Lebensmittelkontakt. Nicht zum Versprühen oder Aerosolbilden verwenden, sofern keine geeigneten Schutzmassnahmen vorhanden sind."
    return ""


def _ensure_section_2_signal_word(content: str) -> str:
    text = str(content or "").strip()
    if not text or re.search(r"(?im)^\s*Signalwort\s*:", text):
        return text
    if re.search(r"(?i)\bGefahr\b|\bDanger\b", text):
        return re.sub(r"(?im)^(\s*2\.2[^\n]*\n)", r"\1Signalwort: Gefahr\n", text, count=1)
    return text


def _remove_contradictory_swiss_limit_sentence(content: str) -> str:
    return re.sub(
        r"(?im)^\s*Für die Schweiz sind keine zusätzlichen MAK-/BAT-Grenzwerte.*(?:\n|$)",
        "",
        str(content or ""),
    ).strip()


def _default_ch_regulations_text(fields: dict[str, object] | None = None) -> str:
    checklist = fields.get("ch_legal_checklist") if isinstance(fields, dict) else None
    suva_status = "geprüft/zu berücksichtigen" if isinstance(checklist, dict) and checklist.get("SUVA-Grenzwerte") == "confirmed" else "prüfen"
    return (
        "Schweizer Rechtsvorschriften:\n"
        "- Chemikalienverordnung ChemV, SR 813.11\n"
        "- Chemikalien-Risikoreduktions-Verordnung ChemRRV, SR 814.81, soweit relevant\n"
        f"- SUVA Grenzwerte am Arbeitsplatz: {suva_status}\n"
        "- Arbeitnehmerschutz, Jugendarbeitsschutz und Mutterschutz aufgrund der Einstufung prüfen\n"
        "- VOCV/VOC-Abgabe prüfen, sofern VOC-relevante Bestandteile oder Angaben vorliegen\n"
        "- VVEA, VeVA und LVA für Entsorgung gemäss Abschnitt 13 prüfen\n"
        "- RPC-/UFI-Meldepflicht Schweiz prüfen\n"
        "- Abgabebeschränkungen und Verwenderkreis prüfen"
    )


def _default_ch_disposal_notes() -> str:
    return (
        "- Produktreste und nicht restentleerte Gebinde nicht in Kanalisation, Gewässer oder Erdreich gelangen lassen.\n"
        "- Produktreste und verunreinigte Verpackungen über einen bewilligten Entsorgungsbetrieb entsorgen.\n"
        "- Restentleerte Verpackungen gemäss betrieblichen, kantonalen und eidgenössischen Vorgaben dem geeigneten Recycling-/Entsorgungsweg zuführen.\n"
        "- VVEA, VeVA, LVA sowie kantonale Vorschriften beachten.\n"
        "- Schweizer Abfallcode/LVA-Code fachlich prüfen; beim Hersteller nicht bekannt, falls keine Quelle vorliegt."
    )


def _default_modern_normative_references() -> str:
    return (
        "Verordnung (EG) Nr. 1907/2006 REACH, Anhang II in der Fassung der Verordnung (EU) 2020/878\n"
        "Verordnung (EG) Nr. 1272/2008 CLP\n"
        "Schweizer Chemikalienverordnung ChemV, SR 813.11\n"
        "Schweizer Chemikalien-Risikoreduktions-Verordnung ChemRRV, SR 814.81, soweit relevant\n"
        "SUVA Grenzwerte am Arbeitsplatz, aktuelle MAK-/BAT-Liste"
    )


def _remove_outdated_legal_references(content: str) -> str:
    lines = []
    for line in str(content or "").splitlines():
        if re.search(r"1999/45|2001/60|2010/453|67/548|DSD|DPD", line, flags=re.I):
            continue
        lines.append(line)
    return "\n".join(lines).strip()


def _join_content_blocks(*parts: str) -> str:
    cleaned = [str(part or "").strip() for part in parts if str(part or "").strip()]
    return "\n\n".join(cleaned).strip()


def _coalesce(*values: object) -> str:
    for value in values:
        text = str(value or "").strip()
        if text:
            return text
    return ""


def _extract_labeled_value(text: str, label: str) -> str:
    escaped = re.escape(label)
    pattern = re.compile(rf"(?im)^\s*{escaped}(?:[^\S\r\n]*[:\-][^\S\r\n]*|[^\S\r\n]+)(.+?)\s*$")
    match = pattern.search(text or "")
    return str(match.group(1)).strip() if match else ""


def _extract_subsection_value(text: str, subsection: str) -> str:
    pattern = re.compile(rf"(?is)(^|\n)\s*{re.escape(subsection)}\b.*?(?=\n\s*\d+\.\d+\b|\n\s*[2-9]\b|\n\s*1[0-6]\b|\Z)")
    match = pattern.search(text or "")
    if not match:
        return ""
    block = match.group(0)
    block = re.sub(rf"(?is)^\s*{re.escape(subsection)}\s*", "", block).strip(" :-\n")
    return re.sub(r"\s+\n", "\n", block).strip()


def _extract_numbered_field_value(text: str, marker: str) -> str:
    pattern = re.compile(rf"(?im)^\s*{re.escape(marker)}[^\S\r\n]+.+?:\s*(.+?)\s*$")
    match = pattern.search(text or "")
    return str(match.group(1)).strip() if match else ""


def _extract_address_line(value: object, index: int) -> str:
    lines = [line.strip() for line in str(value or "").splitlines() if line.strip()]
    return lines[index] if index < len(lines) else ""


def _normalize_section_1_1_content(text: str) -> str:
    raw_lines = [_normalize_inline_spacing(line) for line in str(text or "").splitlines()]
    lines = [line for line in raw_lines if line and not re.fullmatch(r"\.?\s*Produktidentifikator", line, flags=re.I)]
    result: list[str] = []
    index = 0
    while index < len(lines):
        line = lines[index]
        if _is_section_1_1_noise_line(line):
            index += 1
            continue
        label = _canonical_section_1_1_label(line)
        if label:
            next_line = lines[index + 1] if index + 1 < len(lines) else ""
            next_label = _canonical_section_1_1_label(next_line) if next_line else ""
            if next_line and not next_label and not _is_section_1_1_noise_line(next_line):
                result.append(f"{label}: {next_line}")
                index += 2
                continue
            index += 1
            continue
        label_match = re.match(r"(?i)^(Artikel-Nr\.?|Stoffnr\.?|EG-Nr\.?|Registrierungsnr\.?|CAS-Nr\.?|UFI)\s*:\s*(.+)$", line)
        if label_match:
            result.append(f"{_canonical_section_1_1_label(label_match.group(1))}: {label_match.group(2).strip()}")
            index += 1
            continue
        if not result or result[-1] != line:
            result.append(line)
        index += 1
    return "\n".join(result).strip()


def _extract_clean_section_1_2_bucket(text: str, *, bucket: str) -> str:
    identified_lines: list[str] = []
    advised_lines: list[str] = []
    current_bucket = ""
    for line in [_normalize_inline_spacing(row) for row in str(text or "").splitlines()]:
        if not line or _is_section_1_2_noise_line(line):
            continue
        if line.casefold().startswith("relevante identifizierte verwendungen:"):
            current_bucket = "identified"
            value = _clean_section_1_2_value(line.split(":", 1)[1])
            if value:
                identified_lines.append(value)
            continue
        if line.casefold() == "relevante identifizierte verwendungen":
            current_bucket = "identified"
            continue
        if line.casefold().startswith("verwendungen, von denen abgeraten wird:"):
            current_bucket = "advised"
            value = _clean_section_1_2_value(line.split(":", 1)[1])
            if value:
                advised_lines.append(value)
            continue
        if line.casefold() == "verwendungen, von denen abgeraten wird":
            current_bucket = "advised"
            continue
        cleaned = _clean_section_1_2_value(line)
        if not cleaned:
            continue
        if current_bucket == "identified":
            identified_lines.append(cleaned)
        elif current_bucket == "advised":
            advised_lines.append(cleaned)
    normalized_identified = _collapse_pc_code_lines(identified_lines)
    normalized_advised = _collapse_pc_code_lines(advised_lines)
    return normalized_identified if bucket == "identified" else normalized_advised


def _clean_section_1_2_value(value: object) -> str:
    text = _normalize_inline_spacing(str(value or ""))
    if not text:
        return ""
    normalized = text.casefold()
    if normalized in {
        "relevante identifizierte verwendungen",
        "verwendungen, von denen abgeraten wird",
        ". relevante identifizierte verwendungen des stoffs oder gemischs und verwendungen, von denen abgeraten wird",
    }:
        return ""
    if normalized.startswith(". relevante identifizierte verwendungen"):
        return ""
    if "relevante identifizierte verwendungen des stoffs" in normalized:
        return ""
    if normalized.startswith("des stoffs oder gemischs und verwendungen, von denen abgeraten wird"):
        return ""
    if normalized.startswith("und verwendungen, von denen abgeraten wird"):
        return ""
    if normalized == "verwendungen, von denen abgeraten wird":
        return ""
    return text


def _collapse_pc_code_lines(lines: list[str]) -> str:
    collapsed: list[str] = []
    index = 0
    while index < len(lines):
        line = lines[index]
        if re.fullmatch(r"PC\d+[A-Z]?", line) and index + 1 < len(lines):
            collapsed.append(f"{line} {lines[index + 1]}")
            index += 2
            continue
        if not collapsed or collapsed[-1] != line:
            collapsed.append(line)
        index += 1
    return "\n".join(collapsed).strip()


def _canonical_section_1_1_label(line: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "", str(line or "").casefold())
    return SECTION_1_1_LABEL_MAP.get(normalized, "")


def _is_section_1_1_noise_line(line: str) -> bool:
    normalized = str(line or "").casefold()
    return normalized in {
        "stoff- / produktidentifikation",
        "stoff / produktidentifikation",
    }


def _is_section_1_2_noise_line(line: str) -> bool:
    normalized = str(line or "").casefold()
    return (
        "relevante identifizierte verwendungen des stoffs oder gemischs und verwendungen, von denen abgeraten wird" in normalized
        or normalized == ". relevante identifizierte verwendungen des stoffs oder gemischs und"
    )


def _is_section_1_3_noise_line(line: str, supplier: dict[str, str]) -> bool:
    normalized = str(line or "").casefold()
    supplier_name = str(supplier.get("name") or "").casefold()
    supplier_phone = _normalize_inline_spacing(str(supplier.get("phone") or "")).casefold()
    supplier_email = str(supplier.get("email") or "").casefold()
    supplier_address_parts = {
        _normalize_inline_spacing(str(supplier.get("address_line1") or "")).casefold(),
        _normalize_inline_spacing(str(supplier.get("address_line2") or "")).casefold(),
        " ".join(
            part for part in [str(supplier.get("postal_code") or "").strip(), str(supplier.get("city") or "").strip()] if part
        ).casefold(),
        str(supplier.get("country_code") or "").strip().casefold(),
    }
    if normalized in supplier_address_parts or normalized in {supplier_name, supplier_phone, supplier_email}:
        return True
    if any(
        marker in normalized
        for marker in (
            "einzelheiten zum lieferanten",
            "adresse/hersteller",
            "verantwortlichen",
            "person für dieses",
            "person fuer dieses",
            "telefon hersteller",
            "e-mail hersteller",
            "adresse der",
            "hersteller (laut quelle)",
        )
    ):
        return True
    return normalized in {"nr.", "sdb", "telefon:", "e-mail:"}


def _looks_like_phone_number(value: str) -> bool:
    digits = re.sub(r"\D", "", str(value or ""))
    if len(digits) < 7:
        return False
    return bool(re.fullmatch(r"[+()0-9][0-9\s+()/.-]+", str(value or "").strip()))


def _normalize_inline_spacing(value: str) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip())


def _extract_transport_environment_flag(text: str) -> str:
    normalized = str(text or "")
    if re.search(r"(?i)marine pollutant|umweltgefährdend|umweltgefaehrdend|fisch und baum", normalized):
        return "umweltgefährdend"
    if re.search(r"(?i)keine umweltgefahren|nicht umweltgefährdend|nicht umweltgefaehrdend", normalized):
        return "nicht umweltgefährdend"
    return ""


def _contains_transport_placeholder(text: str) -> bool:
    normalized = str(text or "").casefold()
    return "prüfen" in normalized or "pruefen" in normalized


def _has_non_dangerous_goods_statement(text: str) -> bool:
    normalized = re.sub(r"\s+", " ", text or "").casefold()
    return (
        "kein gefahrgut im sinne von adr/rid, imdg und iata" in normalized
        or "not classified as dangerous goods according to adr/rid, imdg and iata" in normalized
        or "nicht im anwendungsbereich der vorschriften für den transport gefährlicher güter" in normalized
        or "nicht im anwendungsbereich der vorschriften fuer den transport gefaehrlicher gueter" in normalized
        or "not included in the scope of application regulations concerning the transport of dangerous goods" in normalized
    )


def _collect_h_statement_wording(section_2_content: str) -> str:
    rows: list[str] = []
    for match in re.finditer(r"(?im)^\s*(H\d{3}(?:\+\w+\d{3})?)\s+(.+?)\s*$", section_2_content or ""):
        rows.append(f"{match.group(1)} {match.group(2).strip()}")
    return "; ".join(dict.fromkeys(rows))


def _contains_review_marker(text: str) -> bool:
    normalized = str(text or "").casefold()
    return any(marker in normalized for marker in REVIEW_MARKERS)


def _is_release_build(review_status: str | None) -> bool:
    normalized = str(review_status or "").strip().casefold()
    if not normalized:
        return False
    return normalized in {"released", "release", "freigegeben", "approved", "final", "release_ch", "freigabe_ch"}


def _is_placeholder_required_value(value: object) -> bool:
    text = str(value or "").strip()
    if not text:
        return True
    return any(marker in text.casefold() for marker in PLACEHOLDER_MARKERS)
