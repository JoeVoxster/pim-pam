from __future__ import annotations

from pathlib import Path
import textwrap
import re

import fitz


GHS_IMAGE_DIR = Path(__file__).resolve().parents[1] / "assets" / "ghs"
PUBLIC_CHEM_DIR = Path(__file__).resolve().parents[2] / "public" / "chem"
GHS_IMAGE_MAP = {
    "GHS05": GHS_IMAGE_DIR / "GHS05.png",
    "GHS07": GHS_IMAGE_DIR / "GHS07.png",
    "GHS09": GHS_IMAGE_DIR / "GHS09.png",
}
GHS_SVG_MAP = {
    "GHS05": PUBLIC_CHEM_DIR / "ghs" / "GHS05.svg",
    "GHS07": PUBLIC_CHEM_DIR / "ghs" / "GHS07.svg",
    "GHS09": PUBLIC_CHEM_DIR / "ghs" / "GHS09.svg",
}
ADR_SVG_MAP = {
    "ADR_8": PUBLIC_CHEM_DIR / "adr" / "ADR_8.svg",
    "ADR_pollution": PUBLIC_CHEM_DIR / "adr" / "ADR_pollution.svg",
}
ADR_IMAGE_MAP = {
    "ADR_3": PUBLIC_CHEM_DIR / "adr" / "ADR_3.png",
    "ADR_5.1": PUBLIC_CHEM_DIR / "adr" / "ADR_5.1.png",
    "ADR_LQ": PUBLIC_CHEM_DIR / "adr" / "ADR_LQ.jpg",
}
ADR_LABELS = {
    "ADR_3": "Klasse 3",
    "ADR_5.1": "Klasse 5.1",
    "ADR_8": "Klasse 8",
    "ADR_pollution": "Umwelt",
    "ADR_LQ": "LQ",
}


def _clean_section_title(title: str, index: int) -> str:
    text = (title or "").strip()
    for prefix in (f"ABSCHNITT {index}:", f"Abschnitt {index}:", f"SECTION {index}:", f"{index}."):
        if text.startswith(prefix):
            text = text[len(prefix):].strip()
    return text or f"Abschnitt {index}"


def _display_review_status(value: str | None) -> str:
    text = (value or "").strip()
    if not text:
        return "-"
    if _is_release_build(text):
        return ""
    lower = text.lower()
    if "critical" in lower or "blocked" in lower or "gesperrt" in lower:
        return "Nicht freigegeben – Review-Fehler vorhanden"
    if "review" in lower and "ch" in lower:
        return "Review-Entwurf CH – nicht zur Abgabe an Kunden freigegeben"
    if "review" in lower:
        return "Review-Entwurf"
    return text


def _display_version_label(value: str | None) -> str:
    text = (value or "").strip()
    if not text:
        return "-"
    marker = "Version "
    if marker in text:
        idx = text.find(marker)
        return text[idx:].strip()
    return text


def _is_release_build(value: str | None) -> bool:
    text = (value or "").strip().lower()
    return text in {"released", "release", "approved", "final", "freigegeben", "release_ch", "freigabe_ch"}


def _split_ghs_codes(value: str | None) -> list[str]:
    return [item.strip() for item in re.split(r"[|,;\s]+", value or "") if item.strip().upper().startswith("GHS")]


def _split_adr_codes(value: str | None) -> list[str]:
    output: list[str] = []
    for item in re.split(r"[|,;\s]+", value or ""):
        cleaned = item.strip()
        if cleaned in {*ADR_SVG_MAP, *ADR_IMAGE_MAP} and cleaned not in output:
            output.append(cleaned)
    return output


def _extract_statement_rows(content: str, prefix: str) -> list[tuple[str, str]]:
    rows: list[tuple[str, str]] = []
    pattern = re.compile(rf"(?im)^\s*({prefix}\d{{3}}(?:\+\w+\d{{3}}(?:\+\w+\d{{3}})*)?)\s+(.+?)\s*$")
    for line in (content or "").splitlines():
        match = pattern.match(line.strip())
        if match:
            rows.append((match.group(1).strip(), match.group(2).strip()))
    return rows


def _extract_psa_blocks(content: str) -> list[tuple[str, str]]:
    labels = [
        "Atemschutz",
        "Handschutz",
        "Augenschutz",
        "Koerperschutz",
        "Allgemeine Schutz- und Hygienemassnahmen",
        "Begrenzung und Ueberwachung der Exposition",
    ]
    pattern = re.compile(
        rf"(?ims)^\s*({'|'.join(re.escape(label) for label in labels)})\s*:\s*(.+?)(?=^\s*(?:{'|'.join(re.escape(label) for label in labels)})\s*:|\Z)"
    )
    blocks: list[tuple[str, str]] = []
    for match in pattern.finditer(content or ""):
        blocks.append((match.group(1).strip(), re.sub(r"\s+\n", "\n", match.group(2)).strip()))
    return blocks


def _draw_ghs_symbol(page: fitz.Page, code: str, rect: fitz.Rect) -> None:
    code = (code or "").upper().strip()
    center_x = (rect.x0 + rect.x1) / 2
    center_y = (rect.y0 + rect.y1) / 2
    width = rect.x1 - rect.x0
    height = rect.y1 - rect.y0

    if code == "GHS05":
        page.draw_line(
            fitz.Point(rect.x0 + 4, rect.y1 - 8),
            fitz.Point(rect.x1 - 4, rect.y1 - 8),
            color=(0, 0, 0),
            width=1.2,
        )
        page.draw_line(
            fitz.Point(rect.x0 + 8, rect.y1 - 14),
            fitz.Point(rect.x0 + 18, rect.y1 - 18),
            color=(0, 0, 0),
            width=1.1,
        )
        page.draw_line(
            fitz.Point(rect.x0 + 20, rect.y1 - 18),
            fitz.Point(rect.x0 + 28, rect.y1 - 14),
            color=(0, 0, 0),
            width=1.1,
        )
        page.draw_line(
            fitz.Point(rect.x0 + 16, rect.y0 + 10),
            fitz.Point(rect.x0 + 26, rect.y0 + 16),
            color=(0, 0, 0),
            width=1.2,
        )
        page.draw_line(
            fitz.Point(rect.x0 + 25, rect.y0 + 8),
            fitz.Point(rect.x0 + 35, rect.y0 + 14),
            color=(0, 0, 0),
            width=1.2,
        )
        for dx, dy in ((0, 0), (3, 4), (7, 7)):
            page.draw_circle(fitz.Point(rect.x0 + 24 + dx, rect.y0 + 20 + dy), 1.2, color=(0, 0, 0), fill=(0, 0, 0))
        for dx, dy in ((0, 0), (3, 4), (7, 7)):
            page.draw_circle(fitz.Point(rect.x0 + 34 + dx, rect.y0 + 18 + dy), 1.2, color=(0, 0, 0), fill=(0, 0, 0))
    elif code == "GHS09":
        page.draw_line(
            fitz.Point(rect.x0 + 10, rect.y1 - 6),
            fitz.Point(rect.x1 - 8, rect.y1 - 6),
            color=(0, 0, 0),
            width=1.2,
        )
        page.draw_line(
            fitz.Point(rect.x0 + 14, rect.y1 - 6),
            fitz.Point(rect.x0 + 18, rect.y0 + 8),
            color=(0, 0, 0),
            width=1.2,
        )
        page.draw_line(
            fitz.Point(rect.x0 + 18, rect.y0 + 8),
            fitz.Point(rect.x0 + 24, rect.y0 + 18),
            color=(0, 0, 0),
            width=1.1,
        )
        page.draw_line(
            fitz.Point(rect.x0 + 18, rect.y0 + 8),
            fitz.Point(rect.x0 + 12, rect.y0 + 16),
            color=(0, 0, 0),
            width=1.1,
        )
        page.draw_line(
            fitz.Point(rect.x0 + 18, rect.y0 + 13),
            fitz.Point(rect.x0 + 26, rect.y0 + 13),
            color=(0, 0, 0),
            width=1.1,
        )
        fish = [
            fitz.Point(rect.x0 + 26, rect.y0 + 24),
            fitz.Point(rect.x0 + 34, rect.y0 + 20),
            fitz.Point(rect.x0 + 40, rect.y0 + 24),
            fitz.Point(rect.x0 + 34, rect.y0 + 28),
        ]
        page.draw_polyline(fish + [fish[0]], color=(0, 0, 0), width=1.0)
        page.draw_line(
            fitz.Point(rect.x0 + 40, rect.y0 + 24),
            fitz.Point(rect.x0 + 45, rect.y0 + 20),
            color=(0, 0, 0),
            width=1.0,
        )
        page.draw_line(
            fitz.Point(rect.x0 + 40, rect.y0 + 24),
            fitz.Point(rect.x0 + 45, rect.y0 + 28),
            color=(0, 0, 0),
            width=1.0,
        )
        page.draw_circle(fitz.Point(rect.x0 + 29, rect.y0 + 23), 0.8, color=(0, 0, 0), fill=(0, 0, 0))
    elif code == "GHS07":
        page.insert_textbox(
            fitz.Rect(rect.x0 + width * 0.35, rect.y0 + height * 0.16, rect.x1 - width * 0.35, rect.y0 + height * 0.60),
            "!",
            fontsize=22,
            fontname="hebo",
            align=1,
        )
        page.draw_circle(
            fitz.Point(center_x, rect.y0 + height * 0.70),
            1.5,
            color=(0, 0, 0),
            fill=(0, 0, 0),
        )
    else:
        page.insert_textbox(rect, code, fontsize=7.5, fontname="hebo", align=1)


def _draw_ghs_image(page: fitz.Page, code: str, rect: fitz.Rect) -> bool:
    normalized_code = (code or "").upper().strip()
    image_path = GHS_IMAGE_MAP.get(normalized_code)
    if not image_path or not image_path.exists():
        image_path = GHS_SVG_MAP.get(normalized_code)
    return _draw_image_file(page, image_path, rect)


def _draw_adr_image(page: fitz.Page, code: str, rect: fitz.Rect) -> bool:
    normalized = (code or "").strip()
    image_path = ADR_IMAGE_MAP.get(normalized)
    if not image_path or not image_path.exists():
        image_path = ADR_SVG_MAP.get(normalized)
    return _draw_image_file(page, image_path, rect)


def _draw_adr_symbol(page: fitz.Page, code: str, rect: fitz.Rect) -> None:
    normalized = (code or "").strip()
    if normalized == "ADR_8":
        cx = (rect.x0 + rect.x1) / 2
        cy = (rect.y0 + rect.y1) / 2
        half = min(rect.x1 - rect.x0, rect.y1 - rect.y0) / 2 - 1
        diamond = [
            fitz.Point(cx, cy - half),
            fitz.Point(cx + half, cy),
            fitz.Point(cx, cy + half),
            fitz.Point(cx - half, cy),
        ]
        page.draw_polyline(diamond + [diamond[0]], color=(0, 0, 0), width=1.2)
        page.draw_polyline([fitz.Point(cx - half + 3, cy + 2), fitz.Point(cx + half - 3, cy + 2)], color=(0, 0, 0), width=1.0)
        bottom = [
            fitz.Point(cx - half + 2, cy + 2),
            fitz.Point(cx + half - 2, cy + 2),
            fitz.Point(cx, cy + half - 2),
        ]
        page.draw_polyline(bottom + [bottom[0]], color=(0, 0, 0), fill=(0, 0, 0), width=0.6)
        page.insert_textbox(
            fitz.Rect(rect.x0 + 6, rect.y0 + 7, rect.x1 - 6, rect.y0 + 22),
            "CORROSIVE",
            fontsize=4.8,
            fontname="hebo",
            align=1,
        )
        page.insert_textbox(
            fitz.Rect(rect.x0 + 4, rect.y1 - 15, rect.x1 - 4, rect.y1 - 3),
            "8",
            fontsize=9,
            fontname="hebo",
            color=(1, 1, 1),
            align=1,
        )
        return
    if normalized == "ADR_pollution":
        page.draw_rect(rect, color=(0, 0, 0), width=1.1)
        ground_y = rect.y1 - 8
        page.draw_line(fitz.Point(rect.x0 + 5, ground_y), fitz.Point(rect.x1 - 4, ground_y), color=(0, 0, 0), width=1.1)
        trunk_x = rect.x0 + 16
        page.draw_line(fitz.Point(trunk_x, ground_y), fitz.Point(trunk_x + 5, rect.y0 + 7), color=(0, 0, 0), width=1.2)
        for offset in (0, 5, 10):
            page.draw_line(
                fitz.Point(trunk_x + 4, rect.y0 + 11 + offset),
                fitz.Point(trunk_x - 7, rect.y0 + 5 + offset),
                color=(0, 0, 0),
                width=1.0,
            )
            page.draw_line(
                fitz.Point(trunk_x + 4, rect.y0 + 11 + offset),
                fitz.Point(trunk_x + 14, rect.y0 + 5 + offset),
                color=(0, 0, 0),
                width=1.0,
            )
        fish = [
            fitz.Point(rect.x0 + 23, rect.y0 + 29),
            fitz.Point(rect.x0 + 32, rect.y0 + 24),
            fitz.Point(rect.x0 + 40, rect.y0 + 29),
            fitz.Point(rect.x0 + 32, rect.y0 + 34),
        ]
        page.draw_polyline(fish + [fish[0]], color=(0, 0, 0), width=1.0)
        page.draw_line(fitz.Point(rect.x0 + 40, rect.y0 + 29), fitz.Point(rect.x0 + 46, rect.y0 + 24), color=(0, 0, 0), width=1.0)
        page.draw_line(fitz.Point(rect.x0 + 40, rect.y0 + 29), fitz.Point(rect.x0 + 46, rect.y0 + 34), color=(0, 0, 0), width=1.0)
        page.draw_circle(fitz.Point(rect.x0 + 27, rect.y0 + 28), 0.7, color=(0, 0, 0), fill=(0, 0, 0))
        return
    page.draw_rect(rect, color=(0, 0, 0), width=1.2)
    page.insert_textbox(rect, normalized, fontsize=7.5, fontname="hebo", align=1)


def _draw_image_file(page: fitz.Page, image_path: Path | None, rect: fitz.Rect) -> bool:
    if not image_path or not image_path.exists():
        return False
    try:
        pix = fitz.Pixmap(str(image_path))
    except Exception:
        return False
    image_ratio = pix.width / pix.height if pix.height else 1.0
    box_width = rect.x1 - rect.x0
    box_height = rect.y1 - rect.y0
    box_ratio = box_width / box_height if box_height else image_ratio
    if image_ratio > box_ratio:
        draw_width = box_width
        draw_height = draw_width / image_ratio
    else:
        draw_height = box_height
        draw_width = draw_height * image_ratio
    x0 = rect.x0 + (box_width - draw_width) / 2
    y0 = rect.y0 + (box_height - draw_height) / 2
    page.insert_image(fitz.Rect(x0, y0, x0 + draw_width, y0 + draw_height), filename=str(image_path), keep_proportion=True)
    return True


def render_sdb_pdf(
    *,
    product_title: str,
    brand_name: str | None,
    sku: str,
    cas_number: str | None,
    ec_number: str | None,
    un_number: str | None,
    signal_word: str | None,
    ghs_pictograms: str | None,
    review_status: str | None,
    version_label: str | None,
    effective_date: str | None,
    issuer_name: str | None,
    issuer_address_line1: str | None,
    issuer_address_line2: str | None,
    issuer_postal_code: str | None,
    issuer_city: str | None,
    issuer_country_code: str | None,
    sections: dict[str, dict[str, str]],
    output_path: str | Path,
    document_title: str | None = None,
    adr_pictograms: str | None = None,
) -> Path:
    target = Path(output_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    document = fitz.open()
    page = document.new_page(width=595, height=842)
    margin = 42
    max_width = page.rect.width - (margin * 2)
    y = margin + 58
    display_review_status = _display_review_status(review_status)
    display_version_label = _display_version_label(version_label)
    is_release_build = _is_release_build(review_status)
    display_title = (document_title or product_title or sku or "").strip() or sku

    def ensure_space(required_height: float) -> tuple[fitz.Page, float]:
        nonlocal page, y
        if y + required_height <= page.rect.height - margin - 34:
            return page, y
        page = document.new_page(width=595, height=842)
        y = margin + 58
        return page, y

    def draw_lines(text: str, fontsize: float = 10, line_height: float = 14, bold: bool = False) -> None:
        nonlocal page, y
        wrap_width = max(30, int(max_width / max(fontsize * 0.55, 4.5)))
        wrapped_lines = textwrap.wrap(text or "", width=wrap_width, replace_whitespace=False, drop_whitespace=False) or [""]
        ensure_space(len(wrapped_lines) * line_height)
        for line in wrapped_lines:
            ensure_space(line_height)
            page.insert_text(
                fitz.Point(margin, y),
                line,
                fontsize=fontsize,
                fontname="hebo" if bold else "helv",
            )
            y += line_height

    def draw_block(text: str, *, fontsize: float = 9.5, line_height: float = 13.5, indent: float = 0) -> None:
        nonlocal page, y
        for raw_paragraph in [item.strip() for item in str(text or "").split("\n") if item.strip()] or ["-"]:
            ensure_space(line_height)
            paragraph = raw_paragraph
            paragraph_indent = indent
            bullet_prefix = ""
            if raw_paragraph.startswith("- "):
                bullet_prefix = "• "
                paragraph = raw_paragraph[2:].strip()
                paragraph_indent += 10
            wrap_width = max(28, int((max_width - paragraph_indent) / max(fontsize * 0.55, 4.5)))
            wrapped_lines = textwrap.wrap(paragraph, width=wrap_width, replace_whitespace=False, drop_whitespace=False) or [""]
            ensure_space(len(wrapped_lines) * line_height)
            for idx, line in enumerate(wrapped_lines):
                prefix = bullet_prefix if idx == 0 else ""
                page.insert_text(
                    fitz.Point(margin + paragraph_indent - (10 if bullet_prefix and idx == 0 else 0), y),
                    f"{prefix}{line}",
                    fontsize=fontsize,
                    fontname="helv",
                )
                y += line_height
            y += 2

    def draw_statement_table(title: str, rows: list[tuple[str, str]]) -> None:
        nonlocal page, y
        if not rows:
            return
        draw_lines(title, fontsize=9.6, line_height=14, bold=True)
        code_width = 86
        ensure_space(22)
        header_rect = fitz.Rect(margin, y - 10, margin + max_width, y + 8)
        page.draw_rect(header_rect, color=(0.8, 0.84, 0.9), fill=(0.93, 0.95, 0.98), width=0.6)
        page.insert_text(fitz.Point(margin + 6, y + 3), "Code", fontsize=8.8, fontname="hebo")
        page.insert_text(fitz.Point(margin + code_width + 6, y + 3), "Text", fontsize=8.8, fontname="hebo")
        page.draw_line(fitz.Point(margin + code_width, y - 10), fitz.Point(margin + code_width, y + 8), color=(0.8, 0.84, 0.9), width=0.6)
        y += 18
        for code, text in rows:
            wrap_width = max(24, int((max_width - code_width - 12) / 5.1))
            wrapped_lines = textwrap.wrap(text, width=wrap_width, replace_whitespace=False, drop_whitespace=False) or [""]
            ensure_space(max(1, len(wrapped_lines)) * 13 + 6)
            row_rect = fitz.Rect(margin, y - 10, margin + max_width, y + max(1, len(wrapped_lines)) * 13 + 2)
            page.draw_rect(
                row_rect,
                color=(0.88, 0.9, 0.94),
                fill=(0.985, 0.99, 1.0),
                width=0.4,
            )
            page.draw_line(fitz.Point(margin + code_width, row_rect.y0), fitz.Point(margin + code_width, row_rect.y1), color=(0.9, 0.92, 0.95), width=0.4)
            page.insert_text(fitz.Point(margin + 6, y), code, fontsize=9.1, fontname="hebo")
            text_y = y
            for line in wrapped_lines:
                page.insert_text(fitz.Point(margin + code_width, text_y), line, fontsize=9.1, fontname="helv")
                text_y += 13
            y = text_y + 4
        y += 2

    def draw_ghs_pictograms_block(codes: list[str]) -> None:
        nonlocal page, y
        if not codes:
            return
        draw_lines("Gefahrenpiktogramme", fontsize=9.6, line_height=14, bold=True)
        size = 42
        gap = 16
        label_height = 14
        row_height = size + label_height + 10
        ensure_space(row_height + 8)
        x = margin + 4
        base_y = y + 8
        for code in codes:
            if x + size > margin + max_width:
                y += row_height
                ensure_space(row_height + 8)
                x = margin + 4
                base_y = y + 8
            icon_rect = fitz.Rect(x, base_y, x + size, base_y + size)
            if not _draw_ghs_image(page, code, icon_rect):
                cx = x + size / 2
                cy = base_y + size / 2
                half = size / 2
                diamond = [
                    fitz.Point(cx, cy - half),
                    fitz.Point(cx + half, cy),
                    fitz.Point(cx, cy + half),
                    fitz.Point(cx - half, cy),
                ]
                page.draw_polyline(diamond + [diamond[0]], color=(0.82, 0.0, 0.0), width=2.0)
                page.draw_rect(fitz.Rect(x + 7, base_y + 7, x + size - 7, base_y + size - 7), color=None, fill=(1, 1, 1))
                _draw_ghs_symbol(page, code, icon_rect)
            page.insert_textbox(
                fitz.Rect(x - 8, base_y + size + 3, x + size + 8, base_y + size + label_height),
                code,
                fontsize=7,
                fontname="helv",
                align=1,
            )
            x += size + gap
        y += row_height

    def draw_adr_pictograms_block(codes: list[str]) -> None:
        nonlocal page, y
        if not codes:
            return
        draw_lines("ADR-Kennzeichnung / Transportpiktogramme", fontsize=9.6, line_height=14, bold=True)
        size = 44
        gap = 18
        label_height = 16
        row_height = size + label_height + 10
        ensure_space(row_height + 8)
        x = margin + 4
        base_y = y + 8
        for code in codes:
            if x + size > margin + max_width:
                y += row_height
                ensure_space(row_height + 8)
                x = margin + 4
                base_y = y + 8
            icon_rect = fitz.Rect(x, base_y, x + size, base_y + size)
            if not _draw_adr_image(page, code, icon_rect):
                _draw_adr_symbol(page, code, icon_rect)
            label = ADR_LABELS.get(code, code)
            page.insert_textbox(
                fitz.Rect(x - 8, base_y + size + 3, x + size + 8, base_y + size + label_height),
                label,
                fontsize=7,
                fontname="helv",
                align=1,
            )
            x += size + gap
        y += row_height

    def draw_psa_table(content: str) -> bool:
        nonlocal y
        blocks = _extract_psa_blocks(content)
        if not blocks:
            return False
        draw_lines("8.2 Begrenzung und Ueberwachung der Exposition / persoenliche Schutzausruestung", fontsize=9.8, line_height=14, bold=True)
        for label, value in blocks:
            wrapped = textwrap.wrap(value.replace("\n", " "), width=72, replace_whitespace=False, drop_whitespace=False) or [""]
            ensure_space(max(1, len(wrapped)) * 13 + 10)
            box_height = max(1, len(wrapped)) * 13 + 6
            page.draw_rect(
                fitz.Rect(margin, y - 10, margin + max_width, y - 10 + box_height),
                color=(0.88, 0.9, 0.94),
                fill=(0.985, 0.99, 1.0),
                width=0.4,
            )
            page.insert_text(fitz.Point(margin + 6, y), label, fontsize=9.2, fontname="hebo")
            text_y = y
            for line in wrapped:
                page.insert_text(fitz.Point(margin + 148, text_y), line, fontsize=9.1, fontname="helv")
                text_y += 13
            y = text_y + 4
        return True

    def draw_page_chrome(current_page: fitz.Page, page_number: int, page_total: int) -> None:
        header_top = margin - 10
        footer_y = current_page.rect.height - margin + 18
        issuer_lines = [value for value in [issuer_name, issuer_address_line1, issuer_address_line2, " ".join([part for part in [issuer_postal_code, issuer_city] if part]), issuer_country_code] if value]
        if page_number == 1:
            current_page.insert_text(fitz.Point(margin, header_top), issuer_lines[0] if issuer_lines else "VOXSTER GmbH", fontsize=11, fontname="hebo")
            if len(issuer_lines) > 1:
                current_page.insert_text(fitz.Point(margin, header_top + 12), " | ".join(issuer_lines[1:]), fontsize=8.5, fontname="helv")
            current_page.insert_text(
                fitz.Point(current_page.rect.width - margin - 160, header_top),
                "Sicherheitsdatenblatt",
                fontsize=13,
                fontname="hebo",
            )
            header_meta = f"Version: {display_version_label}"
            if display_review_status:
                header_meta += f" | Status: {display_review_status}"
            current_page.insert_text(
                fitz.Point(current_page.rect.width - margin - 160, header_top + 12),
                header_meta,
                fontsize=8.5,
                fontname="helv",
            )
            line_y = header_top + 20
        else:
            current_page.insert_text(fitz.Point(margin, header_top + 2), display_title, fontsize=9.4, fontname="hebo")
            followup_meta = f"SDB | {display_version_label}"
            if display_review_status:
                followup_meta += f" | {display_review_status}"
            current_page.insert_text(
                fitz.Point(current_page.rect.width - margin - 180, header_top + 2),
                followup_meta,
                fontsize=8.2,
                fontname="helv",
            )
            line_y = header_top + 10
        current_page.draw_line(fitz.Point(margin, line_y), fitz.Point(current_page.rect.width - margin, line_y), color=(0.75, 0.79, 0.86), width=0.8)
        current_page.draw_line(fitz.Point(margin, footer_y - 8), fitz.Point(current_page.rect.width - margin, footer_y - 8), color=(0.75, 0.79, 0.86), width=0.8)
        current_page.insert_text(
            fitz.Point(margin, footer_y),
            f"{display_title} | Stand {effective_date or '-'}",
            fontsize=8,
            fontname="helv",
        )
        current_page.insert_text(
            fitz.Point(current_page.rect.width - margin - 70, footer_y),
            f"Seite {page_number}/{page_total}",
            fontsize=8,
            fontname="helv",
        )

    y += 8
    draw_lines(display_title, fontsize=15.5, line_height=22, bold=True)
    y += 1
    if is_release_build:
        draw_lines(f"Version: {display_version_label} | Gueltig ab: {effective_date or '-'}", fontsize=10, line_height=14)
    else:
        draw_lines(f"Review-Status: {display_review_status} | Version: {display_version_label} | Gueltig ab: {effective_date or '-'}", fontsize=10, line_height=14)
    y += 4
    ghs_codes = _split_ghs_codes(ghs_pictograms)
    adr_codes = _split_adr_codes(adr_pictograms)

    for index in range(1, 17):
        section_key = f"section_{index}"
        section = sections.get(section_key) or {}
        title = _clean_section_title(str(section.get("title") or f"Abschnitt {index}").strip(), index)
        content = str(section.get("content") or "").strip() or "-"
        ensure_space(48)
        page.draw_rect(
            fitz.Rect(margin, y, margin + max_width, y + 18),
            color=(0.82, 0.86, 0.92),
            fill=(0.94, 0.96, 0.99),
            width=0.6,
        )
        page.insert_text(
            fitz.Point(margin + 8, y + 13),
            f"{index}. {title}",
            fontsize=10.5,
            fontname="hebo",
        )
        y += 30
        if index == 2:
            draw_ghs_pictograms_block(ghs_codes)
            h_rows = _extract_statement_rows(content, "H")
            p_rows = _extract_statement_rows(content, "P")
            if h_rows or p_rows:
                pre_h, _, rest = content.partition("Gefahrenhinweise")
                lead = pre_h.strip()
                if lead:
                    draw_block(lead, fontsize=9.3, line_height=13.2, indent=4)
                if h_rows:
                    draw_statement_table("Gefahrenhinweise", h_rows)
                safety_lead = rest
                if "Sicherheitshinweise" in rest:
                    _, _, after_safety = rest.partition("Sicherheitshinweise")
                    safety_lead = after_safety
                if p_rows:
                    draw_statement_table("Sicherheitshinweise", p_rows)
                trailing = re.sub(r"(?im)^\s*[HP]\d{3}(?:\+\w+\d{3}(?:\+\w+\d{3})*)?\s+.+$", "", safety_lead).strip()
                if trailing:
                    draw_block(trailing, fontsize=9.3, line_height=13.2, indent=4)
            else:
                draw_block(content, fontsize=9.3, line_height=13.2, indent=4)
        elif index == 8 and draw_psa_table(content):
            pass
        elif index == 14:
            draw_adr_pictograms_block(adr_codes)
            draw_block(content, fontsize=9.3, line_height=13.2, indent=4)
        else:
            draw_block(content, fontsize=9.3, line_height=13.2, indent=4)
        y += 6

    total_pages = len(document)
    for page_index in range(total_pages):
        draw_page_chrome(document[page_index], page_index + 1, total_pages)

    document.save(target)
    document.close()
    return target
