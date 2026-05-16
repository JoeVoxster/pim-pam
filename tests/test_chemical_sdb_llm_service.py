from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.db.base import Base
from app.db.models import ProductSDBLLMRun
from app.schemas.pim import ProductCreate, ProductSDBUpdate, VariantCreate
from app.services import chemical_sdb_llm_service
from app.services.chemical_sdb_llm_service import get_sdb_llm_config_status, run_product_sdb_llm_normalization
from app.services.pim_service import create_product, get_product_sdb, upsert_product_sdb


def test_sdb_llm_normalization_stores_prompts_without_api_key(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    engine = create_engine(f"sqlite:///{tmp_path / 'pim.db'}", future=True)
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False, future=True)

    with SessionLocal() as session:
        product, _ = create_product(
            session,
            ProductCreate(
                sku="CHEM-LLM-1",
                title="Demo Chemieprodukt",
                brand_name="VOXSTER",
                status="active",
                is_chemical=True,
                cas_number="7681-52-9",
                un_number="1791",
            ),
            VariantCreate(sku="CHEM-LLM-1", variant_title="Default Variant"),
        )
        upsert_product_sdb(
            session,
            product.id,
            ProductSDBUpdate(
                raw_text="1. Bezeichnung des Stoffs bzw. Gemischs und des Unternehmens\nProduktname: Demo\n2. Mögliche Gefahren\nGefahr.",
                sections_json={
                    "section_1": {"title": "Bezeichnung des Stoffs bzw. Gemischs und des Unternehmens", "content": "Produktname: Demo"},
                    "section_2": {"title": "Mögliche Gefahren", "content": "Gefahr."},
                },
            ),
        )
        session.commit()

        result = run_product_sdb_llm_normalization(session, product.id)
        session.commit()

        run = session.scalar(select(ProductSDBLLMRun))
        sdb = get_product_sdb(session, product.id)

    assert result["status"] == "missing_api_key"
    assert run is not None
    assert run.status == "missing_api_key"
    assert "VOXSTER GmbH" in (run.system_prompt or "")
    assert "Produktdaten" in (run.user_prompt or "")
    assert sdb["review_status"] == "review_required"
    assert sdb["issuer_name"] == "VOXSTER GmbH"


def test_sdb_llm_config_exposes_quality_and_reasoning(monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("OPENAI_SDB_MODEL", "gpt-5.5")
    monkeypatch.setenv("OPENAI_SDB_QUALITY_MODE", "xhigh")
    monkeypatch.delenv("OPENAI_SDB_REASONING_EFFORT", raising=False)

    config = get_sdb_llm_config_status()

    assert config["enabled"] is True
    assert config["model"] == "gpt-5.5"
    assert config["quality_mode"] == "xhigh"
    assert config["reasoning_effort"] == "xhigh"
    assert config["focused_pass"] is True


def test_sdb_llm_normalization_syncs_section_fields_from_content(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("OPENAI_SDB_MODEL", "gpt-5-mini")

    def _fake_call_openai_json(**_kwargs):
        return {
            "raw_text": "{}",
            "json": {
                "review_status": "review_required",
                "version_label": "Entwurf 2.0",
                "effective_date": "2026-04-19",
                "issuer": {
                    "issuer_name": "VOXSTER GmbH",
                    "issuer_address_line1": "Obere Ifangstrasse 10",
                    "issuer_postal_code": "8215",
                    "issuer_city": "Hallau",
                    "issuer_country_code": "CH",
                    "issuer_phone": "+41 52 502 67 23",
                    "issuer_email": "info@voxster.ch",
                },
                "sections_json": {
                    "section_14": {
                        "title": "Angaben zum Transport",
                        "content": "\n".join(
                            [
                                "14.1 UN-Nummer oder ID-Nummer: 1791",
                                "14.2 Ordnungsgemässe UN-Versandbezeichnung: HYPOCHLORITLOESUNG",
                                "14.3 Transportgefahrenklassen: 8",
                                "14.4 Verpackungsgruppe: II",
                                "14.5 Umweltgefahren: UMWELTGEFAEHRDEND",
                                "14.6 Besondere Vorsichtsmassnahmen für den Verwender: Schutzmassnahmen gemäss Abschnitt 7 und 8 beachten.",
                                "14.7 Massengutbeförderung auf dem Seeweg gemäss IMO-Instrumenten: Nicht anwendbar bzw. keine Daten verfügbar.",
                            ]
                        ),
                    }
                },
                "warnings": [],
            },
        }

    monkeypatch.setattr(chemical_sdb_llm_service, "_call_openai_json", _fake_call_openai_json)

    engine = create_engine(f"sqlite:///{tmp_path / 'pim.db'}", future=True)
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False, future=True)

    with SessionLocal() as session:
        product, _ = create_product(
            session,
            ProductCreate(
                sku="CHEM-LLM-2",
                title="Demo Chemieprodukt",
                brand_name="VOXSTER",
                status="active",
                is_chemical=True,
                cas_number="7681-52-9",
                un_number="1791",
                hazard_class="8",
                packing_group="II",
            ),
            VariantCreate(sku="CHEM-LLM-2", variant_title="Default Variant"),
        )
        upsert_product_sdb(
            session,
            product.id,
            ProductSDBUpdate(
                raw_text="Transportdaten vorhanden",
                sections_json={"section_14": {"title": "Angaben zum Transport", "content": ""}},
            ),
        )
        session.commit()

        result = run_product_sdb_llm_normalization(session, product.id)
        session.commit()
        sdb = get_product_sdb(session, product.id)

    assert result["status"] == "completed"
    section_14 = sdb["sections_json"]["section_14"]["fields"]
    assert section_14["un_number_14_1"] == "1791"
    assert section_14["shipping_name_14_2"] == "HYPOCHLORITLOESUNG"
    assert section_14["transport_class_14_3"] == "8"
    assert section_14["packing_group_14_4"] == "II"


def test_sdb_llm_thorough_mode_runs_focused_sections_pass(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("OPENAI_SDB_MODEL", "gpt-5.2")
    calls: list[str] = []

    def _fake_call_openai_json(**kwargs):
        calls.append(kwargs["user_prompt"])
        if len(calls) == 1:
            return {
                "raw_text": "{}",
                "json": {
                    "review_status": "review_required",
                    "version_label": "Entwurf CH",
                    "effective_date": "2026-05-15",
                    "issuer": {},
                    "sections_json": {
                        "section_13": {"title": "Hinweise zur Entsorgung", "content": "Alte Entsorgung."},
                        "section_14": {"title": "Angaben zum Transport", "content": "14.1 UN-Nummer oder ID-Nummer: nicht verfügbar"},
                    },
                    "warnings": [],
                },
            }
        return {
            "raw_text": "{}",
            "json": {
                "sections_json": {
                    "section_13": {
                        "title": "Hinweise zur Entsorgung",
                        "content": "Produktreste über einen bewilligten Entsorgungsbetrieb entsorgen. Schweizer Abfallcode/LVA-Code fachlich prüfen.",
                    },
                    "section_14": {
                        "title": "Angaben zum Transport",
                        "content": "14.1 UN-Nummer oder ID-Nummer: Nicht anwendbar\n14.2 Ordnungsgemässe UN-Versandbezeichnung: Nicht anwendbar\n14.3 Transportgefahrenklassen: Nicht anwendbar",
                    },
                },
                "warnings": ["Abschnitt 13/14 fokussiert verbessert."],
            },
        }

    monkeypatch.setattr(chemical_sdb_llm_service, "_call_openai_json", _fake_call_openai_json)
    engine = create_engine(f"sqlite:///{tmp_path / 'pim.db'}", future=True)
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False, future=True)

    with SessionLocal() as session:
        product, _ = create_product(
            session,
            ProductCreate(sku="CHEM-LLM-FOCUS", title="D1 Schweiss Fleckenentferner", brand_name="Tintolav", status="active", is_chemical=True),
            VariantCreate(sku="CHEM-LLM-FOCUS", variant_title="Default Variant"),
        )
        upsert_product_sdb(session, product.id, ProductSDBUpdate(raw_text="Quelle Abschnitt 13 und 14"))
        session.commit()

        result = run_product_sdb_llm_normalization(session, product.id, quality_mode="thorough")
        session.commit()
        sdb = get_product_sdb(session, product.id)

    assert len(calls) == 2
    assert result["focused_sections_applied"] is True
    assert result["reasoning_effort"] == "high"
    assert "bewilligten Entsorgungsbetrieb" in sdb["sections_json"]["section_13"]["content"]
    assert "Nicht anwendbar" in sdb["sections_json"]["section_14"]["content"]


def test_sdb_llm_normalization_replaces_foreign_emergency_numbers_with_ch_tox_info(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("OPENAI_SDB_MODEL", "gpt-5-mini")

    def _fake_call_openai_json(**_kwargs):
        return {
            "raw_text": "{}",
            "json": {
                "review_status": "review_required",
                "version_label": "Entwurf CH",
                "effective_date": "2026-05-14",
                "issuer": {
                    "issuer_name": "VOXSTER GmbH",
                    "issuer_address_line1": "Obere Ifangstrasse 10",
                    "issuer_postal_code": "8215",
                    "issuer_city": "Hallau",
                    "issuer_country_code": "CH",
                    "issuer_phone": "+41 52 502 67 23",
                    "issuer_email": "info@voxster.ch",
                },
                "sections_json": {
                    "section_1": {
                        "title": "Bezeichnung des Stoffs bzw. Gemischs und des Unternehmens",
                        "content": "\n".join(
                            [
                                "1.1 Produktidentifikator",
                                "Produktname: D1 - Sudore",
                                "1.3 Einzelheiten zum Lieferanten, der das Sicherheitsdatenblatt bereitstellt",
                                "Tintolav s.r.l.",
                                "1.4 Notrufnummer",
                                "- UK National Poisons Emergency number: +44 (0)870 600 6266",
                                "- London (Emergency 24h): +44 (0) 207188 0100",
                                "- National contact (Malta): Emergency Ambulance 112",
                            ]
                        ),
                    }
                },
                "warnings": [],
            },
        }

    monkeypatch.setattr(chemical_sdb_llm_service, "_call_openai_json", _fake_call_openai_json)

    engine = create_engine(f"sqlite:///{tmp_path / 'pim.db'}", future=True)
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False, future=True)

    with SessionLocal() as session:
        product, _ = create_product(
            session,
            ProductCreate(sku="CHEM-LLM-CH", title="D1 Schweiss Fleckenentferner", brand_name="Tintolav", status="active", is_chemical=True),
            VariantCreate(sku="CHEM-LLM-CH", variant_title="Default Variant"),
        )
        upsert_product_sdb(
            session,
            product.id,
            ProductSDBUpdate(raw_text="Quelle mit UK Emergency Number", sections_json={"section_1": {"title": "Bezeichnung", "content": ""}}),
        )
        session.commit()

        result = run_product_sdb_llm_normalization(session, product.id)
        session.commit()
        sdb = get_product_sdb(session, product.id)

    section_1 = sdb["sections_json"]["section_1"]["content"]
    assert result["status"] == "completed"
    assert "Tox Info Suisse" in section_1
    assert "Notfallnummer: 145" in section_1
    assert "+41 44 251 51 51" in section_1
    assert "UK National Poisons" not in section_1
    assert "London" not in section_1
    assert "Malta" not in section_1
