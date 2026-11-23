from __future__ import annotations

import io
from pathlib import Path

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.shared import Inches, Pt

from app.export.helpers import (
    collect_chapters,
    is_scene_break,
    parse_inline_runs,
    unit_heading_text,
)
from app.store import Book


def build_docx_bytes(book: Book, bdir: Path, *, accepted_only: bool = False) -> bytes:
    doc = Document()
    fm = book.front_matter
    author = fm.author_name.strip() or "Author"

    sec = doc.sections[0]
    sec.top_margin = Inches(1.0)
    sec.bottom_margin = Inches(1.0)
    sec.left_margin = Inches(1.0)
    sec.right_margin = Inches(1.0)

    normal = doc.styles["Normal"]
    normal.font.name = "Georgia"
    normal.font.size = Pt(11.5)
    normal.paragraph_format.line_spacing = 1.45

    # Title Page
    tp = doc.add_paragraph()
    tp.alignment = WD_ALIGN_PARAGRAPH.CENTER
    tp.paragraph_format.space_before = Inches(2.2)
    r_title = tp.add_run(book.title)
    r_title.bold = True
    r_title.font.size = Pt(24)

    if fm.subtitle.strip():
        sp = doc.add_paragraph()
        sp.alignment = WD_ALIGN_PARAGRAPH.CENTER
        rs = sp.add_run(fm.subtitle.strip())
        rs.italic = True
        rs.font.size = Pt(14)

    ap = doc.add_paragraph()
    ap.alignment = WD_ALIGN_PARAGRAPH.CENTER
    ap.paragraph_format.space_before = Inches(0.8)
    ra = ap.add_run(f"by {author}")
    ra.font.size = Pt(13)

    if fm.dedication.strip():
        doc.add_page_break()
        dp = doc.add_paragraph()
        dp.alignment = WD_ALIGN_PARAGRAPH.CENTER
        dp.paragraph_format.space_before = Inches(2.0)
        rd = dp.add_run(fm.dedication.strip())
        rd.italic = True

    chapters = collect_chapters(book, bdir, accepted_only=accepted_only)
    for ch in chapters:
        doc.add_page_break()
        h = doc.add_heading(unit_heading_text(book, ch), level=1)
        h.alignment = WD_ALIGN_PARAGRAPH.CENTER
        if ch.illustration_path and ch.illustration_path.exists():
            ip = doc.add_paragraph()
            ip.alignment = WD_ALIGN_PARAGRAPH.CENTER
            ip.add_run().add_picture(str(ch.illustration_path), width=Inches(4.5))

        first = True
        for para in ch.paragraphs:
            if para.startswith("#"):
                doc.add_heading(para.lstrip("# ").strip(), level=2)
                first = True
            elif is_scene_break(para):
                sb = doc.add_paragraph("* * *")
                sb.alignment = WD_ALIGN_PARAGRAPH.CENTER
                sb.paragraph_format.space_before = Pt(12)
                sb.paragraph_format.space_after = Pt(12)
                first = True
            else:
                p = doc.add_paragraph()
                p.paragraph_format.first_line_indent = Inches(0 if first else 0.3)
                p.paragraph_format.space_after = Pt(4)
                for seg, is_b, is_i in parse_inline_runs(para):
                    run = p.add_run(seg)
                    run.bold = is_b
                    run.italic = is_i
                first = False

    if fm.author_bio.strip() or fm.acknowledgments.strip():
        doc.add_page_break()
        if fm.acknowledgments.strip():
            doc.add_heading("Acknowledgments", level=1)
            doc.add_paragraph(fm.acknowledgments.strip())
        if fm.author_bio.strip():
            doc.add_heading("About the Author", level=1)
            doc.add_paragraph(fm.author_bio.strip())

    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()
