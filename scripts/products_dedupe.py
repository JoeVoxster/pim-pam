from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.db.session import session_scope
from app.services.product_dedupe_service import merge_product_duplicates


def main() -> int:
    parser = argparse.ArgumentParser(description="Analyse and safely merge duplicate PIM products.")
    parser.add_argument("--confidence", choices=["HIGH", "MEDIUM", "LOW"], default="HIGH")
    parser.add_argument("--supplier", default=None, help="Optional supplier/source filter, e.g. TintoLove.")
    parser.add_argument("--product-id", type=int, default=None, help="Limit analysis around one product id.")
    parser.add_argument("--dry-run", action="store_true", default=False, help="Preview only. This is the default unless --apply is set.")
    parser.add_argument("--apply", action="store_true", help="Apply merge for HIGH/MEDIUM groups.")
    parser.add_argument("--yes", action="store_true", help="Required with --apply to avoid accidental merges.")
    parser.add_argument("--output-dir", default="/opt/output/product_dedupe")
    args = parser.parse_args()

    if args.apply and not args.yes:
        print("Apply abgebrochen: bitte zuerst Backup erstellen und mit --yes explizit bestätigen.", file=sys.stderr)
        return 2
    if args.apply and args.confidence == "LOW":
        print("LOW-Confidence-Gruppen werden nicht automatisch gemerged.", file=sys.stderr)
        return 2

    with session_scope() as session:
        result = merge_product_duplicates(
            session,
            confidence=args.confidence,
            supplier=args.supplier,
            product_id=args.product_id,
            apply=bool(args.apply),
            yes=bool(args.yes),
            output_dir=args.output_dir,
        )
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
