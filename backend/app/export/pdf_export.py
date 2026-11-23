from __future__ import annotations

import os
from pathlib import Path

from fpdf import FPDF

from app.export.helpers import (
    collect_chapters,
    is_scene_break,
    parse_inline_runs,
    unit_heading_text,
)
from app.store import Book

# Trim sizes in inches (W, H) matching KDP print presets
TRIM_PRESETS: dict[str, tuple[float, float]] = {
    "5x8": (5.0, 8.0),
    "5.5x8.5": (5.5, 8.5),
    "6x9": (6.0, 9.0),
    "8.5x8.5": (8.5, 8.5),
}

_FONT_CANDIDATES: list[tuple[str, str, str]] = [
    # (regular, bold, italic)
    (r"C:\Windows\Fonts\georgia.ttf", r"C:\Windows\Fonts\georgiab.ttf", r"C:\Windows\Fonts\georgiai.ttf"),
    (r"C:\Windows\Fonts\times.ttf", r"C:\Windows\Fonts\timesbd.ttf", r"C:\Windows\Fonts\timesi.ttf"),
    ("/usr/share/fonts/truetype/liberation/LiberationSerif-Regular.ttf", "/usr/share/fonts/truetype/liberation/LiberationSerif-Bold.ttf", "/usr/share/fonts/truetype/liberation/LiberationSerif-Italic.ttf"),
    ("/usr/share/fonts/truetype/dejavu/DejaVuSerif.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSerif-Bold.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSerif-Italic.ttf"),
]


def _latin_safe(s: str) -> str:
    return (
        s.replace("—", " - ")
        .replace("–", "-")
        .replace("“", '"')
        .replace("”", '"')
        .replace("‘", "'")
        .replace("’", "'")
        .replace("…", "...")
        .encode("latin-1", "replace")
        .decode("latin-1")
    )


class _BookPDF(FPDF):
    def __init__(self, *, width_in: float, height_in: float, book_title: str, author: str, unicode_font: bool):
        super().__init__(unit="in", format=(width_in, height_in))
        self.book_title = book_title
        self.author = author
        self.unicode_font = unicode_font
        self.font_name = "BookSerif" if unicode_font else "Times"
        self.show_running_header = False

    def safe_str(self, text: str) -> str:
        return text if self.unicode_font else _latin_safe(text)

    def header(self) -> None:
        if not self.show_running_header or self.page_no() <= 2:
            return
        self.set_y(0.35)
        self.set_font(self.font_name, "I", 8.5)
        self.set_text_color(110, 105, 100)
        label = self.author if self.page_no() % 2 == 0 else self.book_title
        self.cell(0, 0.2, self.safe_str(label), align="C")
        self.set_text_color(28, 25, 23)

    def footer(self) -> None:
        if not self.show_running_header:
            return
        self.set_y(-0.45)
        self.set_font(self.font_name, "", 9)
        self.set_text_color(100, 95, 90)
        self.cell(0, 0.2, str(self.page_no()), align="C")
        self.set_text_color(28, 25, 23)


def _setup_fonts(pdf: _BookPDF) -> bool:
    for reg, bld, ita in _FONT_CANDIDATES:
        if os.path.exists(reg) and os.path.exists(bld) and os.path.exists(ita):
            pdf.add_font("BookSerif", "", reg)
            pdf.add_font("BookSerif", "B", bld)
            pdf.add_font("BookSerif", "I", ita)
            pdf.add_font("BookSerif", "BI", bld)
            return True
    return False


def build_pdf_bytes(book: Book, bdir: Path, *, trim: str = "6x9", accepted_only: bool = False) -> bytes:
    w_in, h_in = TRIM_PRESETS.get(trim, TRIM_PRESETS["6x9"])
    fm = book.front_matter
    author = fm.author_name.strip() or "Author"

    # Probe font availability first
    probe = FPDF()
    has_unicode = any(os.path.exists(r) and os.path.exists(b) and os.path.exists(i) for r, b, i in _FONT_CANDIDATES)
    del probe

    pdf = _BookPDF(width_in=w_in, height_in=h_in, book_title=book.title, author=author, unicode_font=has_unicode)
    if has_unicode:
        _setup_fonts(pdf)
    pdf.set_auto_page_break(auto=True, margin=0.68)
    pdf.set_margins(left=0.72, top=0.72, right=0.58)

    # Title Page
    pdf.add_page()
    pdf.ln(h_in * 0.28)
    pdf.set_font(pdf.font_name, "B", 20)
    pdf.multi_cell(0, 0.36, pdf.safe_str(book.title), align="C")
    if fm.subtitle.strip():
        pdf.ln(0.15)
        pdf.set_font(pdf.font_name, "I", 12.5)
        pdf.multi_cell(0, 0.25, pdf.safe_str(fm.subtitle.strip()), align="C")
    pdf.ln(0.8)
    pdf.set_font(pdf.font_name, "", 12)
    pdf.cell(0, 0.25, pdf.safe_str(author), align="C", new_x="LMARGIN", new_y="NEXT")

    # Copyright Page
    pdf.add_page()
    pdf.ln(h_in * 0.55)
    pdf.set_font(pdf.font_name, "", 9.5)
    year = fm.copyright_year.strip() or "2026"
    copy_lines = [
        book.title,
        f"Copyright (c) {year} {author}",
        f"Published by {fm.imprint.strip()}" if fm.imprint.strip() else "",
        "All rights reserved.",
    ]
    for cl in copy_lines:
        if cl:
            pdf.cell(0, 0.2, pdf.safe_str(cl), new_x="LMARGIN", new_y="NEXT")

    if fm.dedication.strip():
        pdf.add_page()
        pdf.ln(h_in * 0.3)
        pdf.set_font(pdf.font_name, "I", 11)
        pdf.multi_cell(0, 0.24, pdf.safe_str(fm.dedication.strip()), align="C")

    # Chapters / Spreads
    chapters = collect_chapters(book, bdir, accepted_only=accepted_only)
    pdf.show_running_header = True
    body_size = 13.0 if trim == "8.5x8.5" else 10.8
    line_h = 0.26 if trim == "8.5x8.5" else 0.215

    for ch in chapters:
        pdf.add_page()
        pdf.ln(0.45)
        pdf.set_font(pdf.font_name, "B", 14.5)
        pdf.multi_cell(0, 0.28, pdf.safe_str(unit_heading_text(book, ch)), align="C")
        pdf.ln(0.28)

        if ch.illustration_path and ch.illustration_path.exists():
            max_img_w = w_in - 1.4
            pdf.image(str(ch.illustration_path), x=0.72, w=max_img_w)
            pdf.ln(0.2)

        pdf.set_font(pdf.font_name, "", body_size)
        first = True
        for para in ch.paragraphs:
            if para.startswith("#"):
                pdf.ln(0.12)
                pdf.set_font(pdf.font_name, "B", body_size + 1)
                pdf.multi_cell(0, line_h, pdf.safe_str(para.lstrip("# ").strip()))
                pdf.set_font(pdf.font_name, "", body_size)
                pdf.ln(0.06)
                first = True
            elif is_scene_break(para):
                pdf.ln(0.1)
                pdf.cell(0, line_h, "*   *   *", align="C", new_x="LMARGIN", new_y="NEXT")
                pdf.ln(0.1)
                first = True
            else:
                runs = parse_inline_runs(para)
                prefix = "" if first else "    "
                # Use write() cleanly across inline bold/italic runs
                for r_idx, (seg, is_b, is_i) in enumerate(runs):
                    style = ("B" if is_b else "") + ("I" if is_i else "")
                    pdf.set_font(pdf.font_name, style, body_size)
                    chunk = (prefix + seg) if r_idx == 0 else seg
                    pdf.write(line_h, pdf.safe_str(chunk))
                pdf.ln(line_h * 1.15)
                first = False

    if fm.author_bio.strip() or fm.acknowledgments.strip():
        pdf.add_page()
        pdf.ln(0.4)
        pdf.set_font(pdf.font_name, "B", 13)
        if fm.acknowledgments.strip():
            pdf.cell(0, 0.26, "Acknowledgments", align="C", new_x="LMARGIN", new_y="NEXT")
            pdf.ln(0.15)
            pdf.set_font(pdf.font_name, "", body_size)
            pdf.multi_cell(0, line_h, pdf.safe_str(fm.acknowledgments.strip()))
            pdf.ln(0.3)
        if fm.author_bio.strip():
            pdf.set_font(pdf.font_name, "B", 13)
            pdf.cell(0, 0.26, "About the Author", align="C", new_x="LMARGIN", new_y="NEXT")
            pdf.ln(0.15)
            pdf.set_font(pdf.font_name, "", body_size)
            pdf.multi_cell(0, line_h, pdf.safe_str(fm.author_bio.strip()))

    raw_bytes = pdf.output()
    return bytes(raw_bytes)
