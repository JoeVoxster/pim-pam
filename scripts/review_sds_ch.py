#!/usr/bin/env python
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from sqlalchemy import select

from app.db.models import ChemicalDocument, Product
from app.db.session import session_scope
from app.services.sds_swiss_review_service import apply_safe_auto_fixes, review_sds_document


def main() -> int:
    parser = argparse.ArgumentParser(description="CH-SDB-Review fuer bestehende SDB-Versionen ausfuehren.")
    parser.add_argument("--dry-run", action="store_true", default=True)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--product-id", type=int)
    parser.add_argument("--sku")
    parser.add_argument("--language")
    parser.add_argument("--fix-legal-references", action="store_true")
    parser.add_argument("--validate-transport", action="store_true")
    parser.add_argument("--validate-section-9", action="store_true")
    parser.add_argument("--validate-swiss-mak", action="store_true")
    parser.add_argument("--validate-section-15", action="store_true")
    parser.add_argument("--output", default="/opt/output/sds_ch_review_report.json")
    args = parser.parse_args()

    with session_scope() as session:
        product_stmt = select(Product)
        if args.product_id:
            product_stmt = product_stmt.where(Product.id == args.product_id)
        if args.sku:
            product_stmt = product_stmt.where(Product.sku == args.sku)
        products = list(session.scalars(product_stmt))
        product_ids = [row.id for row in products]
        doc_stmt = select(ChemicalDocument).where(ChemicalDocument.document_type == "sds")
        if product_ids:
            doc_stmt = doc_stmt.where(ChemicalDocument.product_id.in_(product_ids))
        if args.language:
            doc_stmt = doc_stmt.where(ChemicalDocument.locale == args.language)
        documents = list(session.scalars(doc_stmt.order_by(ChemicalDocument.product_id.asc(), ChemicalDocument.id.asc())))

        reports = []
        for document in documents:
            report = review_sds_document(session, document.id)
            if args.apply:
                auto = apply_safe_auto_fixes(session, document.id)
                report = review_sds_document(session, document.id)
                report["applied_auto_fixes"] = auto.get("applied") or []
            reports.append(report.get("json_report") or report)

        if not args.apply:
            session.rollback()

    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps({"count": len(reports), "reports": reports}, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"CH-SDB-Review abgeschlossen: {len(reports)} Dokument(e). Report: {output}")
    print("Dry-Run: keine Datenbankänderung." if not args.apply else "Apply: Auto-Fixes wurden gespeichert.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
