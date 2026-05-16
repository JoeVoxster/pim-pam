from __future__ import annotations

from pathlib import Path

import fitz


def extract_pdf_text(path: str | Path) -> str:
    file_path = Path(path)
    if not file_path.exists():
        return ""
    texts: list[str] = []
    with fitz.open(file_path) as document:
        for page in document:
            text = page.get_text("text").strip()
            if text:
                texts.append(text)
    return "\n".join(texts).strip()
