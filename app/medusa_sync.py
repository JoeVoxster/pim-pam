from __future__ import annotations

import argparse
import json
from typing import Any

from app.db.session import session_scope
from app.services.medusa.config_service import get_or_create_medusa_connection, save_medusa_connection
from app.services.medusa.sync_service import MedusaSyncService
from app.utils.pim_config import get_pim_settings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="PIM/PAM Medusa Admin API Sync")
    sub = parser.add_subparsers(dest="command", required=True)

    test = sub.add_parser("test-connection")
    test.add_argument("--connection", default="default")

    dry = sub.add_parser("dry-run")
    dry.add_argument("--connection", default="default")
    dry.add_argument("--product-id", type=int, required=True)
    dry.add_argument("--force", action="store_true")

    export = sub.add_parser("export")
    export.add_argument("--connection", default="default")
    export.add_argument("--product-id", type=int, required=True)
    export.add_argument("--force", action="store_true")

    export_all = sub.add_parser("export-all")
    export_all.add_argument("--connection", default="default")
    export_all.add_argument("--limit", type=int, default=20)
    export_all.add_argument("--force", action="store_true")

    repair = sub.add_parser("repair-mapping")
    repair.add_argument("--connection", default="default")

    sync_prices = sub.add_parser("sync-prices")
    sync_prices.add_argument("--connection", default="default")
    sync_prices.add_argument("--product-id", type=int, required=True)

    sync_translations = sub.add_parser("sync-translations")
    sync_translations.add_argument("--connection", default="default")
    sync_translations.add_argument("--product-id", type=int, required=True)

    show = sub.add_parser("show-run")
    show.add_argument("run_id", type=int)

    args = parser.parse_args(argv)
    with session_scope(get_pim_settings().database_url) as session:
        service = MedusaSyncService(session)
        if args.command == "test-connection":
            result = service.test_connection(args.connection)
        elif args.command == "dry-run":
            result = service.dry_run_product(args.product_id, args.connection, force=args.force)
        elif args.command == "export":
            result = service.export_product(args.product_id, args.connection, dry_run=False, force=args.force)
        elif args.command == "export-all":
            from sqlalchemy import select
            from app.db.models import Product

            product_ids = list(session.scalars(select(Product.id).where(Product.status != "archived").order_by(Product.id.asc()).limit(args.limit)))
            result = {"products": [service.export_product(product_id, args.connection, dry_run=False, force=args.force) for product_id in product_ids]}
        elif args.command == "repair-mapping":
            result = service.repair_mapping(args.connection)
        elif args.command in {"sync-prices", "sync-translations"}:
            # Prices/translations are separate run items inside product export; keep explicit commands for ops clarity.
            result = service.export_product(args.product_id, args.connection, dry_run=False, force=True)
        elif args.command == "show-run":
            result = _show_run(session, args.run_id)
        else:
            parser.error("Unbekannter Command")
            return 2
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    return 0


def _show_run(session, run_id: int) -> dict[str, Any]:
    from sqlalchemy import select
    from app.db.models import MedusaSyncRun, MedusaSyncRunItem

    run = session.get(MedusaSyncRun, run_id)
    if run is None:
        return {"status": "not_found", "run_id": run_id}
    items = session.scalars(select(MedusaSyncRunItem).where(MedusaSyncRunItem.run_id == run_id).order_by(MedusaSyncRunItem.id.asc())).all()
    return {
        "id": run.id,
        "mode": run.mode,
        "status": run.status,
        "summary": run.summary,
        "items": [
            {
                "entity_type": item.entity_type,
                "local_entity_id": item.local_entity_id,
                "medusa_id": item.medusa_id,
                "locale_code": item.locale_code,
                "action": item.action,
                "status": item.status,
                "error_message": item.error_message,
            }
            for item in items
        ],
    }


if __name__ == "__main__":
    raise SystemExit(main())
