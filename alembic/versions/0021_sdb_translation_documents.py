"""SDB translation documents and prompts.

Revision ID: 0021_sdb_translation_documents
Revises: 0020_product_translation_ai
Create Date: 2026-04-30 00:00:00.000000
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0021_sdb_translation_documents"
down_revision = "0020_product_translation_ai"
branch_labels = None
depends_on = None


DEFAULT_SYSTEM_PROMPT = (
    "Du bist ein professioneller Fachübersetzer für chemische Sicherheitsdatenblätter. "
    "Das Ergebnis ist immer nur ein KI-Entwurf und muss fachlich/rechtlich geprüft werden."
)

DEFAULT_USER_PROMPT = """Übersetze das vorhandene Sicherheitsdatenblatt von {{source_locale}} nach {{target_locale}} für die Zielregion {{target_region}}.

Wichtig:
- Erfinde keine Daten.
- Behalte CAS-Nummern, H-Sätze, P-Sätze, Einstufungen, Signalwörter, Grenzwerte, Transportangaben, UFI, REACH/CLP-Angaben und Abschnittsnummern unverändert, sofern keine geprüften regionenspezifischen Daten vorhanden sind.
- Wenn regionale Pflichtangaben fehlen, markiere sie mit [PRÜFEN] und erkläre kurz, was geprüft werden muss.
- Gib das Ergebnis strukturiert nach den 16 Abschnitten eines Sicherheitsdatenblatts aus.
- Das Ergebnis ist ein Entwurf und darf nicht automatisch veröffentlicht werden.

Produkt: {{product_name}}
SKU: {{product_sku}}
Gefahrstoffdaten: {{hazard_classification}}
CAS-Nummern: {{cas_numbers}}
Regulatorische Hinweise: {{regulatory_notes}}

Ausgangs-SDB:
{{source_sdb_text}}
"""


def upgrade() -> None:
    op.create_table(
        "sdb_translation_prompts",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("document_type", sa.String(length=64), server_default="sds", nullable=False),
        sa.Column("source_locale", sa.String(length=16), nullable=True),
        sa.Column("target_locale", sa.String(length=16), nullable=True),
        sa.Column("target_region", sa.String(length=16), nullable=True),
        sa.Column("system_prompt", sa.Text(), nullable=True),
        sa.Column("user_prompt_template", sa.Text(), nullable=False),
        sa.Column("active", sa.Boolean(), server_default="1", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_sdb_translation_prompts_scope",
        "sdb_translation_prompts",
        ["document_type", "source_locale", "target_locale", "target_region"],
    )

    op.create_table(
        "chemical_documents",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("product_id", sa.Integer(), nullable=False),
        sa.Column("document_type", sa.String(length=64), server_default="sds", nullable=False),
        sa.Column("source_document_id", sa.Integer(), nullable=True),
        sa.Column("locale", sa.String(length=16), nullable=True),
        sa.Column("language_code", sa.String(length=16), nullable=True),
        sa.Column("region_code", sa.String(length=16), nullable=True),
        sa.Column("title", sa.String(length=500), nullable=True),
        sa.Column("file_url", sa.String(length=1000), nullable=True),
        sa.Column("asset_id", sa.Integer(), nullable=True),
        sa.Column("extracted_text", sa.Text(), nullable=True),
        sa.Column("generated_text", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=64), server_default="draft", nullable=False),
        sa.Column("version", sa.String(length=64), nullable=True),
        sa.Column("valid_from", sa.String(length=32), nullable=True),
        sa.Column("created_by_ai", sa.Boolean(), server_default="0", nullable=False),
        sa.Column("ai_provider", sa.String(length=64), nullable=True),
        sa.Column("ai_model", sa.String(length=128), nullable=True),
        sa.Column("ai_prompt_id", sa.Integer(), nullable=True),
        sa.Column("review_note", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["ai_prompt_id"], ["sdb_translation_prompts.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["asset_id"], ["assets.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["product_id"], ["products.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["source_document_id"], ["chemical_documents.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_chemical_documents_product_id", "chemical_documents", ["product_id"])
    op.create_index("ix_chemical_documents_source_document_id", "chemical_documents", ["source_document_id"])
    op.create_index("ix_chemical_documents_status", "chemical_documents", ["status"])
    op.create_index("ix_chemical_documents_locale_region", "chemical_documents", ["locale", "region_code"])

    op.bulk_insert(
        sa.table(
            "sdb_translation_prompts",
            sa.column("name", sa.String()),
            sa.column("document_type", sa.String()),
            sa.column("source_locale", sa.String()),
            sa.column("target_locale", sa.String()),
            sa.column("target_region", sa.String()),
            sa.column("system_prompt", sa.Text()),
            sa.column("user_prompt_template", sa.Text()),
            sa.column("active", sa.Boolean()),
        ),
        [
            {
                "name": "Standard SDB-Entwurf",
                "document_type": "sds",
                "source_locale": None,
                "target_locale": None,
                "target_region": None,
                "system_prompt": DEFAULT_SYSTEM_PROMPT,
                "user_prompt_template": DEFAULT_USER_PROMPT,
                "active": True,
            }
        ],
    )


def downgrade() -> None:
    op.drop_index("ix_chemical_documents_locale_region", table_name="chemical_documents")
    op.drop_index("ix_chemical_documents_status", table_name="chemical_documents")
    op.drop_index("ix_chemical_documents_source_document_id", table_name="chemical_documents")
    op.drop_index("ix_chemical_documents_product_id", table_name="chemical_documents")
    op.drop_table("chemical_documents")
    op.drop_index("ix_sdb_translation_prompts_scope", table_name="sdb_translation_prompts")
    op.drop_table("sdb_translation_prompts")
