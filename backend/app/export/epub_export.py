from __future__ import annotations

import html
import io
import mimetypes
import uuid
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from app.export.helpers import (
    collect_chapters,
    find_cover_image,
    is_scene_break,
    md_inline_to_html,
    unit_heading_text,
)
from app.store import Book

_EPUB_CSS = """@namespace epub "http://www.idpf.org/2007/ops";
body {
  font-family: Georgia, "Palatino Linotype", serif;
  line-height: 1.6;
  margin: 5% 6%;
  color: #1c1917;
}
h1, h2, h3 {
  font-weight: normal;
  text-align: center;
  margin: 1.6em 0 1em 0;
  line-height: 1.25;
}
p {
  margin: 0;
  text-indent: 1.35em;
  text-align: justify;
}
p.first, h1 + p, h2 + p, div.scene-break + p {
  text-indent: 0;
}
div.scene-break {
  text-align: center;
  margin: 1.4em 0;
  letter-spacing: 0.4em;
  color: #78716c;
}
div.title-page {
  text-align: center;
  margin-top: 22%;
}
div.title-page h1 {
  font-size: 2.1em;
  margin-bottom: 0.35em;
}
div.title-page p.subtitle {
  text-indent: 0;
  text-align: center;
  font-style: italic;
  font-size: 1.15em;
  margin-bottom: 2em;
}
div.title-page p.author {
  text-indent: 0;
  text-align: center;
  font-size: 1.1em;
  margin-top: 2.5em;
}
div.copyright-page p, div.dedication-page p, div.backmatter p {
  text-indent: 0;
  text-align: left;
  margin-bottom: 0.8em;
}
div.dedication-page {
  text-align: center;
  margin-top: 25%;
  font-style: italic;
}
div.dedication-page p { text-align: center; }
figure.illus {
  text-align: center;
  margin: 1em 0 1.4em 0;
}
figure.illus img {
  max-width: 100%;
  height: auto;
}
"""


def _xhtml_doc(title: str, body_html: str) -> str:
    return (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<!DOCTYPE html>\n'
        '<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops" lang="en">\n'
        "<head>\n"
        '  <meta charset="utf-8" />\n'
        f"  <title>{html.escape(title)}</title>\n"
        '  <link rel="stylesheet" type="text/css" href="styles.css" />\n'
        "</head>\n"
        f"<body>\n{body_html}\n</body>\n</html>\n"
    )


def build_epub_bytes(book: Book, bdir: Path, *, accepted_only: bool = False) -> bytes:
    chapters = collect_chapters(book, bdir, accepted_only=accepted_only)
    fm = book.front_matter
    author = fm.author_name.strip() or "Author"
    year = fm.copyright_year.strip() or str(datetime.now(timezone.utc).year)
    book_uuid = f"urn:uuid:{uuid.uuid5(uuid.NAMESPACE_DNS, f'novelista-v3-{book.id}')}"
    modified = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    manifest_items: list[str] = [
        '<item id="css" href="styles.css" media-type="text/css"/>',
        '<item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>',
        '<item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>',
    ]
    spine_ids: list[str] = []
    nav_entries: list[tuple[str, str]] = []
    files: dict[str, bytes] = {}

    # Optional cover image
    cover_img = find_cover_image(bdir)
    if cover_img:
        mime = mimetypes.guess_type(cover_img.name)[0] or "image/jpeg"
        ext = cover_img.suffix.lower()
        manifest_items.append(f'<item id="cover-image" href="images/cover{ext}" media-type="{mime}" properties="cover-image"/>')
        files[f"OEBPS/images/cover{ext}"] = cover_img.read_bytes()
        cover_html = _xhtml_doc(
            "Cover",
            f'<div style="text-align:center;"><img src="images/cover{ext}" alt="Cover" style="max-width:100%;max-height:100%;"/></div>',
        )
        files["OEBPS/cover.xhtml"] = cover_html.encode("utf-8")
        manifest_items.append('<item id="cover" href="cover.xhtml" media-type="application/xhtml+xml"/>')
        spine_ids.append("cover")

    # Title page
    sub_html = f'<p class="subtitle">{html.escape(fm.subtitle)}</p>' if fm.subtitle.strip() else ""
    series_html = (
        f'<p class="subtitle">{html.escape(fm.series_name)}'
        + (f" · Book {html.escape(fm.series_number)}" if fm.series_number.strip() else "")
        + "</p>"
        if fm.series_name.strip()
        else ""
    )
    title_body = (
        '<div class="title-page">'
        f"<h1>{html.escape(book.title)}</h1>"
        f"{sub_html}{series_html}"
        f'<p class="author">{html.escape(author)}</p>'
        "</div>"
    )
    files["OEBPS/title.xhtml"] = _xhtml_doc(book.title, title_body).encode("utf-8")
    manifest_items.append('<item id="titlepage" href="title.xhtml" media-type="application/xhtml+xml"/>')
    spine_ids.append("titlepage")
    nav_entries.append(("Title Page", "title.xhtml"))

    # Copyright page
    imprint = f"<p>Published by {html.escape(fm.imprint)}</p>" if fm.imprint.strip() else ""
    copy_body = (
        '<div class="copyright-page">'
        f"<p><strong>{html.escape(book.title)}</strong></p>"
        f"<p>Copyright © {html.escape(year)} {html.escape(author)}</p>"
        f"{imprint}"
        "<p>All rights reserved. No part of this publication may be reproduced or transmitted without permission.</p>"
        "</div>"
    )
    files["OEBPS/copyright.xhtml"] = _xhtml_doc("Copyright", copy_body).encode("utf-8")
    manifest_items.append('<item id="copyright" href="copyright.xhtml" media-type="application/xhtml+xml"/>')
    spine_ids.append("copyright")

    if fm.dedication.strip():
        ded_body = f'<div class="dedication-page"><p>{md_inline_to_html(fm.dedication.strip())}</p></div>'
        files["OEBPS/dedication.xhtml"] = _xhtml_doc("Dedication", ded_body).encode("utf-8")
        manifest_items.append('<item id="dedication" href="dedication.xhtml" media-type="application/xhtml+xml"/>')
        spine_ids.append("dedication")

    if fm.epigraph.strip():
        epi_body = f'<div class="dedication-page"><p>{md_inline_to_html(fm.epigraph.strip())}</p></div>'
        files["OEBPS/epigraph.xhtml"] = _xhtml_doc("Epigraph", epi_body).encode("utf-8")
        manifest_items.append('<item id="epigraph" href="epigraph.xhtml" media-type="application/xhtml+xml"/>')
        spine_ids.append("epigraph")

    # Chapters / Spreads
    for idx, ch in enumerate(chapters, 1):
        heading = unit_heading_text(book, ch)
        parts: list[str] = [f"<h2>{html.escape(heading)}</h2>"]
        if ch.illustration_path and ch.illustration_path.exists():
            ext = ch.illustration_path.suffix.lower()
            mime = mimetypes.guess_type(ch.illustration_path.name)[0] or "image/jpeg"
            img_href = f"images/illus_{ch.number:03d}{ext}"
            files[f"OEBPS/{img_href}"] = ch.illustration_path.read_bytes()
            manifest_items.append(f'<item id="img_{ch.number:03d}" href="{img_href}" media-type="{mime}"/>')
            parts.append(f'<figure class="illus"><img src="{img_href}" alt="{html.escape(heading)}"/></figure>')

        first_para = True
        for para in ch.paragraphs:
            if para.startswith("#"):
                h_txt = para.lstrip("# ").strip()
                parts.append(f"<h3>{html.escape(h_txt)}</h3>")
                first_para = True
            elif is_scene_break(para):
                parts.append('<div class="scene-break">* * *</div>')
                first_para = True
            else:
                cls = ' class="first"' if first_para else ""
                parts.append(f"<p{cls}>{md_inline_to_html(para)}</p>")
                first_para = False

        fname = f"ch_{ch.number:03d}.xhtml"
        cid = f"ch_{ch.number:03d}"
        files[f"OEBPS/{fname}"] = _xhtml_doc(heading, "\n".join(parts)).encode("utf-8")
        manifest_items.append(f'<item id="{cid}" href="{fname}" media-type="application/xhtml+xml"/>')
        spine_ids.append(cid)
        nav_entries.append((heading, fname))

    # Back Matter
    if any(x.strip() for x in (fm.acknowledgments, fm.author_bio, fm.also_by)):
        bm_parts: list[str] = ['<div class="backmatter">']
        if fm.acknowledgments.strip():
            bm_parts.append(f"<h2>Acknowledgments</h2><p>{md_inline_to_html(fm.acknowledgments.strip())}</p>")
        if fm.author_bio.strip():
            bm_parts.append(f"<h2>About the Author</h2><p>{md_inline_to_html(fm.author_bio.strip())}</p>")
        if fm.also_by.strip():
            bm_parts.append(f"<h2>Also By {html.escape(author)}</h2><p>{md_inline_to_html(fm.also_by.strip())}</p>")
        bm_parts.append("</div>")
        files["OEBPS/backmatter.xhtml"] = _xhtml_doc("About & Acknowledgments", "\n".join(bm_parts)).encode("utf-8")
        manifest_items.append('<item id="backmatter" href="backmatter.xhtml" media-type="application/xhtml+xml"/>')
        spine_ids.append("backmatter")
        nav_entries.append(("About the Author", "backmatter.xhtml"))

    # Navigation XHTML (EPUB 3) + toc.ncx (EPUB 2 fallback for older readers)
    nav_li = "\n".join(f'      <li><a href="{html.escape(href)}">{html.escape(label)}</a></li>' for label, href in nav_entries)
    nav_xhtml = (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<!DOCTYPE html>\n'
        '<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops" lang="en">\n'
        "<head><meta charset=\"utf-8\"/><title>Table of Contents</title><link rel=\"stylesheet\" type=\"text/css\" href=\"styles.css\"/></head>\n"
        "<body>\n"
        '  <nav epub:type="toc" id="toc">\n    <h1>Contents</h1>\n    <ol>\n'
        f"{nav_li}\n"
        "    </ol>\n  </nav>\n</body>\n</html>\n"
    )
    files["OEBPS/nav.xhtml"] = nav_xhtml.encode("utf-8")

    nav_points = "\n".join(
        f'    <navPoint id="np{i}" playOrder="{i}"><navLabel><text>{html.escape(label)}</text></navLabel><content src="{html.escape(href)}"/></navPoint>'
        for i, (label, href) in enumerate(nav_entries, 1)
    )
    toc_ncx = (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1">\n'
        "  <head>\n"
        f'    <meta name="dtb:uid" content="{book_uuid}"/>\n'
        '    <meta name="dtb:depth" content="1"/>\n'
        '    <meta name="dtb:totalPageCount" content="0"/>\n'
        '    <meta name="dtb:maxPageNumber" content="0"/>\n'
        "  </head>\n"
        f"  <docTitle><text>{html.escape(book.title)}</text></docTitle>\n"
        f"  <navMap>\n{nav_points}\n  </navMap>\n</ncx>\n"
    )
    files["OEBPS/toc.ncx"] = toc_ncx.encode("utf-8")
    files["OEBPS/styles.css"] = _EPUB_CSS.encode("utf-8")

    manifest_xml = "\n    ".join(manifest_items)
    spine_xml = "\n    ".join(f'<itemref idref="{sid}"/>' for sid in spine_ids)
    meta_cover = '<meta name="cover" content="cover-image"/>' if cover_img else ""
    content_opf = (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="pub-id">\n'
        '  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">\n'
        f'    <dc:identifier id="pub-id">{book_uuid}</dc:identifier>\n'
        f"    <dc:title>{html.escape(book.title)}</dc:title>\n"
        f"    <dc:creator>{html.escape(author)}</dc:creator>\n"
        "    <dc:language>en</dc:language>\n"
        f'    <meta property="dcterms:modified">{modified}</meta>\n'
        f"    {meta_cover}\n"
        "  </metadata>\n"
        f"  <manifest>\n    {manifest_xml}\n  </manifest>\n"
        f'  <spine toc="ncx">\n    {spine_xml}\n  </spine>\n'
        "</package>\n"
    )
    files["OEBPS/content.opf"] = content_opf.encode("utf-8")
    files["META-INF/container.xml"] = (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">\n'
        "  <rootfiles>\n"
        '    <rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/>\n'
        "  </rootfiles>\n"
        "</container>\n"
    ).encode("utf-8")

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        # EPUB spec: mimetype MUST be first entry and stored uncompressed (ZIP_STORED)
        zf.writestr("mimetype", b"application/epub+zip", compress_type=zipfile.ZIP_STORED)
        for path, data in files.items():
            zf.writestr(path, data, compress_type=zipfile.ZIP_DEFLATED)
    return buf.getvalue()
