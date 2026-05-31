from __future__ import annotations

from io import BytesIO

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.base import Base
from app.services.r2_config_service import effective_r2_settings, save_r2_config, serialize_r2_config
from app.services.asset_service import build_r2_object_key, create_asset_record, detect_asset_language, update_asset_metadata, upload_r2_asset_from_bytes, upload_selected_assets_to_r2
from app.services.r2_storage_service import BunnyStorage, R2Settings


class FakeR2Storage:
    settings = R2Settings(
        endpoint="https://example.r2.cloudflarestorage.com",
        bucket="voxster-media",
        region="auto",
        access_key_id="test",
        secret_access_key="secret",
        public_base_url="https://media.example.test",
    )

    def __init__(self) -> None:
        self.uploaded: list[dict[str, object]] = []

    def upload_fileobj(self, fileobj, object_key: str, *, content_type: str, metadata: dict[str, str] | None = None) -> None:
        self.uploaded.append(
            {
                "object_key": object_key,
                "content_type": content_type,
                "metadata": metadata or {},
                "payload": fileobj.read(),
            }
        )

    def public_url(self, object_key: str) -> str:
        return f"{self.settings.public_base_url}/{object_key}"


def test_r2_object_key_uses_product_sdb_language_and_safe_filename() -> None:
    key = build_r2_object_key(
        "Sicherheitsdatenblatt NäOH 25 kg.pdf",
        asset_type="safety_data_sheet",
        product_id=1420,
        language_code="de-CH",
    )

    assert key.startswith("prod/assets/products/1420/sdb/de-ch/")
    assert key.endswith("-sicherheitsdatenblatt-naoh-25-kg.pdf")
    assert " " not in key


def test_upload_r2_asset_from_bytes_saves_metadata(tmp_path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'pim.db'}", future=True)
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False, future=True)
    fake_storage = FakeR2Storage()

    with SessionLocal() as session:
        asset, log = upload_r2_asset_from_bytes(
            session,
            b"col1,col2\n1,2\n",
            "Import Datei.csv",
            asset_type="import_file",
            title="Import",
            storage=fake_storage,
        )
        session.commit()

    assert asset.storage_provider == "cloudflare_r2"
    assert asset.bucket == "voxster-media"
    assert asset.object_key is not None
    assert asset.public_url == f"https://media.example.test/{asset.object_key}"
    assert asset.mime_type == "text/csv"
    assert asset.file_extension == ".csv"
    assert asset.status == "uploaded"
    assert fake_storage.uploaded[0]["object_key"] == asset.object_key
    assert any("Upload zu cloudflare_r2 erfolgreich" in row["message"] for row in log)


def test_local_asset_record_stores_and_updates_language_code(tmp_path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'pim.db'}", future=True)
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False, future=True)
    source = tmp_path / "sdb.pdf"
    source.write_bytes(b"%PDF-1.4\n%%EOF\n")

    with SessionLocal() as session:
        asset = create_asset_record(session, source, language_code="de-CH")
        update_asset_metadata(
            session,
            asset.id,
            title="Sicherheitsdatenblatt",
            asset_type="safety_data_sheet",
            language_code="fr",
            status="active",
        )
        session.commit()

    assert asset.language_code == "fr"
    assert asset.title == "Sicherheitsdatenblatt"
    assert asset.asset_type == "safety_data_sheet"


def test_detect_asset_language_prefers_pdf_text_over_misleading_filename() -> None:
    assert (
        detect_asset_language(
            filename="tintolav-d2-sds-it.pdf",
            mime_type="application/pdf",
            extracted_text="SAFETY DATA SHEET\nSECTION 1. Identification\nDetails of the supplier\nUses advised against",
        )
        == "en"
    )


def test_upload_r2_asset_rejects_executable(tmp_path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'pim.db'}", future=True)
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False, future=True)

    with SessionLocal() as session:
        with pytest.raises(ValueError, match="nicht erlaubt"):
            upload_r2_asset_from_bytes(session, b"echo bad", "bad.sh", storage=FakeR2Storage())


def test_r2_config_masks_secret_and_preserves_blank_secret(tmp_path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'pim.db'}", future=True)
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False, future=True)

    with SessionLocal() as session:
        config = save_r2_config(
            session,
            {
                "enabled": True,
                "endpoint": "https://example.r2.cloudflarestorage.com",
                "bucket": "voxster-media",
                "region": "auto",
                "access_key_id": "ACCESS123456",
                "secret_access_key": "SECRET123456",
                "max_upload_size_mb": 25,
            },
        )
        save_r2_config(
            session,
            {
                "enabled": True,
                "endpoint": "https://example.r2.cloudflarestorage.com",
                "bucket": "voxster-media",
                "region": "auto",
                "access_key_id": "",
                "secret_access_key": "",
                "max_upload_size_mb": 25,
            },
        )
        session.commit()
        data = serialize_r2_config(config)

    assert data["secret_configured"] is True
    assert "SECRET123456" not in str(data)
    assert data["access_key_id_masked"] == "ACCE***3456"
    assert config.secret_access_key == "SECRET123456"


def test_effective_r2_settings_prefers_enabled_db_config(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("R2_ACCESS_KEY_ID", "ENV_ACCESS")
    monkeypatch.setenv("R2_SECRET_ACCESS_KEY", "ENV_SECRET")
    engine = create_engine(f"sqlite:///{tmp_path / 'pim.db'}", future=True)
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False, future=True)

    with SessionLocal() as session:
        save_r2_config(
            session,
            {
                "enabled": True,
                "endpoint": "https://db.r2.cloudflarestorage.com",
                "bucket": "db-bucket",
                "region": "auto",
                "access_key_id": "DB_ACCESS",
                "secret_access_key": "DB_SECRET",
                "public_base_url": "https://media.example.test",
                "max_upload_size_mb": 50,
            },
        )
        settings = effective_r2_settings(session)

    assert settings.endpoint == "https://db.r2.cloudflarestorage.com"
    assert settings.bucket == "db-bucket"
    assert settings.access_key_id == "DB_ACCESS"
    assert settings.secret_access_key == "DB_SECRET"
    assert settings.public_base_url == "https://media.example.test"


def test_r2_object_key_respects_custom_path_prefix() -> None:
    key = build_r2_object_key(
        "Manual.pdf",
        asset_type="manual",
        product_id=12,
        path_prefix="stage/media",
    )

    assert key.startswith("stage/media/products/12/manuals/")


def test_upload_selected_assets_to_r2_uploads_local_asset_without_duplicate(tmp_path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'pim.db'}", future=True)
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False, future=True)
    source = tmp_path / "photo.jpg"
    source.write_bytes(b"not-a-real-image")
    fake_storage = FakeR2Storage()

    with SessionLocal() as session:
        local_asset = create_asset_record(session, source)
        local_asset.asset_type = "product_image"
        result = upload_selected_assets_to_r2(session, [local_asset.id], storage=fake_storage)
        second = upload_selected_assets_to_r2(session, [local_asset.id], storage=fake_storage)
        session.commit()

    assert result["uploaded_count"] == 1
    assert result["items"][0]["status"] == "uploaded"
    assert second["uploaded_count"] == 0
    assert second["skipped_count"] == 1
    assert "Dublette" in second["items"][0]["message"]


def test_upload_selected_assets_supports_bunny_storage(tmp_path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'pim.db'}", future=True)
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False, future=True)
    source = tmp_path / "photo.jpg"
    source.write_bytes(b"not-a-real-image")
    settings = R2Settings(
        endpoint="https://storage.bunnycdn.com/voxster-media",
        bucket="voxster-media",
        region="auto",
        access_key_id="unused",
        secret_access_key="zone-password",
        public_base_url="https://media.example.test",
    )

    class FakeBunnyStorage(BunnyStorage):
        def __init__(self) -> None:
            super().__init__(settings)
            self.uploaded: list[dict[str, object]] = []

        def upload_fileobj(self, fileobj, object_key: str, *, content_type: str, metadata: dict[str, str] | None = None) -> None:
            self.uploaded.append({"object_key": object_key, "content_type": content_type, "payload": fileobj.read()})

    fake_storage = FakeBunnyStorage()

    with SessionLocal() as session:
        local_asset = create_asset_record(session, source)
        local_asset.asset_type = "product_image"
        result = upload_selected_assets_to_r2(session, [local_asset.id], storage=fake_storage)
        session.commit()

    assert result["uploaded_count"] == 1
    assert result["items"][0]["status"] == "uploaded"
    assert result["items"][0]["object_key"].startswith("prod/assets/general/product_image/")
    assert fake_storage.uploaded
    with SessionLocal() as session:
        uploaded = session.get(type(local_asset), result["items"][0]["target_asset_id"])
        assert uploaded.storage_provider == "bunny_storage"
        assert uploaded.storage_path.startswith("bunny://voxster-media/")


def test_upload_selected_assets_migrates_public_remote_asset_to_bunny(tmp_path, monkeypatch) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'pim.db'}", future=True)
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False, future=True)
    payload = b"remote-image"
    settings = R2Settings(
        endpoint="https://storage.bunnycdn.com/voxster-media",
        bucket="voxster-media",
        region="auto",
        access_key_id="unused",
        secret_access_key="zone-password",
        public_base_url="https://media.example.test",
    )

    class FakeResponse:
        content = payload

        def raise_for_status(self) -> None:
            return None

    class FakeBunnyStorage(BunnyStorage):
        def __init__(self) -> None:
            super().__init__(settings)
            self.uploaded: list[dict[str, object]] = []

        def upload_fileobj(self, fileobj, object_key: str, *, content_type: str, metadata: dict[str, str] | None = None) -> None:
            self.uploaded.append({"object_key": object_key, "content_type": content_type, "payload": fileobj.read()})

    monkeypatch.setattr("app.services.asset_service.requests.get", lambda *_args, **_kwargs: FakeResponse())
    fake_storage = FakeBunnyStorage()

    with SessionLocal() as session:
        remote_source = tmp_path / "remote.jpg"
        remote_source.write_bytes(b"placeholder")
        remote_asset = create_asset_record(session, remote_source)
        remote_asset.storage_provider = "cloudflare_r2"
        remote_asset.storage_path = "r2://voxster-media/prod/assets/products/1/images/remote.jpg"
        remote_asset.object_key = "prod/assets/products/1/images/remote.jpg"
        remote_asset.public_url = "https://old-r2.example.test/prod/assets/products/1/images/remote.jpg"
        remote_asset.asset_type = "product_image"
        result = upload_selected_assets_to_r2(session, [remote_asset.id], storage=fake_storage)
        session.commit()

    assert result["uploaded_count"] == 1
    assert fake_storage.uploaded[0]["payload"] == payload
    with SessionLocal() as session:
        uploaded = session.get(type(remote_asset), result["items"][0]["target_asset_id"])
        assert uploaded.storage_provider == "bunny_storage"


def test_upload_selected_assets_to_r2_reports_invalid_asset(tmp_path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'pim.db'}", future=True)
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False, future=True)

    with SessionLocal() as session:
        result = upload_selected_assets_to_r2(session, [999], storage=FakeR2Storage())

    assert result["error_count"] == 1
    assert result["items"][0]["message"] == "Asset existiert nicht."


def test_upload_selected_assets_to_r2_skips_archived_and_tiny_images(tmp_path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'pim.db'}", future=True)
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False, future=True)
    source = tmp_path / "tiny.jpg"
    source.write_bytes(b"not-a-real-image")
    fake_storage = FakeR2Storage()

    with SessionLocal() as session:
        archived = create_asset_record(session, source)
        archived.asset_type = "product_image"
        archived.status = "archived"
        tiny = create_asset_record(session, source)
        tiny.asset_type = "product_image"
        tiny.width = 65
        tiny.height = 65
        result = upload_selected_assets_to_r2(session, [archived.id, tiny.id], storage=fake_storage)
        session.commit()

    assert result["uploaded_count"] == 0
    assert result["skipped_count"] == 2
    assert fake_storage.uploaded == []
    messages = [str(item["message"]) for item in result["items"]]
    assert any("Archiviertes Asset" in message for message in messages)
    assert any("Bild zu klein" in message for message in messages)
