from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

import pandas as pd


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Sanitize Medusa import CSV")
    parser.add_argument("--file", required=True, help="Path to products_medusa_import.csv")
    parser.add_argument("--in-place", action="store_true", help="Rewrite the input file in place")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    path = Path(args.file)
    frame = pd.read_csv(path, dtype=str).fillna("")

    frame = frame.apply(_sanitize_row, axis=1)
    frame = _dedupe_rows(frame)
    frame = _normalize_variant_options(frame)

    target = path if args.in_place else path.with_name(f"{path.stem}.sanitized{path.suffix}")
    frame.to_csv(target, index=False)
    print(target)
    print(f"rows={len(frame)}")
    return 0


def _sanitize_row(row: pd.Series) -> pd.Series:
    sku = row.get("Variant Sku", "")
    if _looks_like_path(sku):
        row["Variant Sku"] = _extract_sku(sku) or _extract_sku_from_metadata(row.get("Variant Metadata", "")) or sku
    if not row.get("Variant Option 1 Name", "").strip() and row.get("Variant Option 1 Value", "").strip():
        row["Variant Option 1 Name"] = "Pack Size"
    if not row.get("Variant Option 1 Value", "").strip():
        inferred = _extract_packaging(" ".join([row.get("Variant Title", ""), row.get("Product Title", ""), row.get("Variant Metadata", "")]))
        if inferred:
            row["Variant Option 1 Name"] = row.get("Variant Option 1 Name", "") or "Pack Size"
            row["Variant Option 1 Value"] = inferred
    if not row.get("Variant Option 1 Name", "").strip() and not row.get("Variant Option 1 Value", "").strip():
        row["Variant Option 1 Name"] = "Default"
        row["Variant Option 1 Value"] = "Default"
    return row


def _dedupe_rows(frame: pd.DataFrame) -> pd.DataFrame:
    rows: list[pd.Series] = []
    seen: set[tuple[str, str, str]] = set()
    for _, row in frame.iterrows():
        key = (
            row.get("Product Handle", "").strip().lower(),
            (row.get("Variant Option 1 Value", "") or row.get("Variant Sku", "") or row.get("Variant Title", "")).strip().lower(),
            row.get("Variant Sku", "").strip().lower(),
        )
        if key in seen:
            continue
        seen.add(key)
        rows.append(row)
    return pd.DataFrame(rows, columns=frame.columns)


def _normalize_variant_options(frame: pd.DataFrame) -> pd.DataFrame:
    normalized_groups: list[pd.DataFrame] = []
    for _, group in frame.groupby("Product Handle", dropna=False, sort=False):
        group = group.copy()
        if len(group) > 1:
            option_values = [str(value).strip() for value in group["Variant Option 1 Value"].tolist()]
            if all(option_values):
                group["Variant Option 1 Name"] = "Pack Size"
            else:
                unique_variant_keys = {
                    (str(row["Variant Sku"]).strip(), str(row["Variant Title"]).strip())
                    for _, row in group.iterrows()
                }
                if len(unique_variant_keys) == 1:
                    group = group.iloc[[0]].copy()
        normalized_groups.append(group)
    return pd.concat(normalized_groups, ignore_index=True)


def _extract_sku_from_metadata(metadata: str) -> str | None:
    if not metadata:
        return None
    try:
        data = json.loads(metadata)
    except Exception:
        data = {}
    candidates = [str(data.get("variant_sku") or ""), str(data.get("supplier_sku") or "")]
    for candidate in candidates:
        extracted = _extract_sku(candidate)
        if extracted:
            return extracted
    return None


def _looks_like_path(value: str) -> bool:
    return "/" in (value or "") or value.lower().startswith("x350f")


def _extract_sku(value: str) -> str | None:
    patterns = [
        r"([a-z]\d{2}-\d{3}[a-z]\d{0,2})(?:[^a-z0-9]|[a-z]{3,}|$)",
        r"([a-z]\d{2}-\d{3}[a-z0-9]{1,4})",
        r"([a-z]{1,4}-\d{2,4}[a-z0-9]{1,6})",
        r"([a-z]{1,4}\d{2,4}[a-z0-9]{1,6})",
    ]
    for pattern in patterns:
        match = re.search(pattern, value, re.IGNORECASE)
        if match:
            return match.group(1)
    return None


def _extract_packaging(value: str) -> str | None:
    match = re.search(r"(\d+(?:[.,]\d+)?)\s*(ml|cl|l|lt|liter|litre|kg|g)\b", value, re.IGNORECASE)
    if not match:
        return None
    amount = match.group(1).replace(",", ".")
    unit = match.group(2).lower()
    unit_map = {"l": "liter", "lt": "liter", "litre": "liter", "liter": "liter"}
    amount = amount.rstrip("0").rstrip(".") if "." in amount else amount
    return f"{amount} {unit_map.get(unit, unit)}"


if __name__ == "__main__":
    raise SystemExit(main())
