from pathlib import Path

import fitz

from app.pdf.parser import extract_pdf_text


def test_extract_pdf_text_reads_text(tmp_path: Path) -> None:
    pdf_path = tmp_path / "sample.pdf"
    document = fitz.open()
    page = document.new_page()
    page.insert_text((72, 72), "Technische Daten")
    document.save(pdf_path)
    document.close()

    assert "Technische Daten" in extract_pdf_text(pdf_path)
