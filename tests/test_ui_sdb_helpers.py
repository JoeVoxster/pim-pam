from app.services.sdb_support import default_sdb_sections
from app.ui.dash_app import _merge_sdb_ui_sections


def test_merge_sdb_ui_sections_preserves_fields_and_overwrites_content() -> None:
    stored = default_sdb_sections()
    stored["section_14"]["fields"]["un_number_14_1"] = "1791"
    stored["section_14"]["fields"]["transport_class_14_3"] = "8"
    stored["section_14"]["content"] = "old content"

    merged = _merge_sdb_ui_sections(stored, [""] * 13 + ["new transport content"] + ["", ""])

    assert merged["section_14"]["content"] == "new transport content"
    assert merged["section_14"]["fields"]["un_number_14_1"] == "1791"
    assert merged["section_14"]["fields"]["transport_class_14_3"] == "8"


def test_merge_sdb_ui_sections_syncs_structured_fields_from_manual_content() -> None:
    stored = default_sdb_sections()
    merged = _merge_sdb_ui_sections(
        stored,
        [""] * 13
        + [
            "\n".join(
                [
                    "14.1 UN-Nummer oder ID-Nummer: 1791",
                    "14.2 Ordnungsgemässe UN-Versandbezeichnung: HYPOCHLORITLOESUNG",
                    "14.3 Transportgefahrenklassen: 8",
                    "14.4 Verpackungsgruppe: II",
                    "14.5 Umweltgefahren: UMWELTGEFAEHRDEND",
                    "14.6 Besondere Vorsichtsmassnahmen für den Verwender: Schutzmassnahmen gemäss Abschnitt 7 und 8 beachten.",
                    "14.7 Massengutbeförderung auf dem Seeweg gemäss IMO-Instrumenten: Nicht anwendbar bzw. keine Daten verfügbar.",
                ]
            )
        ]
        + ["", ""],
    )

    assert merged["section_14"]["fields"]["un_number_14_1"] == "1791"
    assert merged["section_14"]["fields"]["shipping_name_14_2"] == "HYPOCHLORITLOESUNG"
    assert merged["section_14"]["fields"]["transport_class_14_3"] == "8"
    assert merged["section_14"]["fields"]["packing_group_14_4"] == "II"
