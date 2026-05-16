from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.db.session import session_scope
from app.services.sdb_translation_service import backfill_sdb_documents_from_assets


def main() -> None:
    parser = argparse.ArgumentParser(description="Backfill chemical SDB document registry rows from existing SDB/SDS assets.")
    parser.add_argument("--product-id", type=int, default=None, help="Optional product id to limit the backfill.")
    parser.add_argument("--commit", action="store_true", help="Persist changes. Without this flag the run is a dry-run.")
    args = parser.parse_args()

    with session_scope() as session:
        result = backfill_sdb_documents_from_assets(session, args.product_id, commit=args.commit)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
