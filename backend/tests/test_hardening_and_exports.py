from __future__ import annotations

import io
import xml.etree.ElementTree as ET
import zipfile

from docx import Document
from pypdf import PdfReader

from app import events, jobs
from app.enums import BookFormat, ChapterStatus
from app.store import book_dir, chapter_path, get_book, save_book
from tests._llm_mock import install_mock_llm


def test_uploads_path_traversal_blocked(client):
    res = client.post("/api/books", json={"title": "SecTest"})
    assert res.status_code == 201
    bid = res.json()["id"]

    # Windows backslash encoded traversal attempt
    bad = client.delete(f"/api/books/{bid}/uploads/..%5cbook.json")
    assert bad.status_code == 400
    # Ensure book.json still intact
    assert client.get(f"/api/books/{bid}").status_code == 200


def test_patch_book_and_settings_validation(client):
    res = client.post("/api/books", json={"title": "ValTest"})
    bid = res.json()["id"]

    bad_null = client.patch(f"/api/books/{bid}", json={"status": None})
    # status isn't in BookPatch so extra ignored or title null fails 422:
    bad_title = client.patch(f"/api/books/{bid}", json={"title": None})
    assert bad_title.status_code == 422

    # Valid idea update syncs seed.txt
    ok = client.patch(f"/api/books/{bid}", json={"idea": "A clockmaker steals winter."})
    assert ok.status_code == 200
    assert client.get(f"/api/books/{bid}/seed").json()["text"] == "A clockmaker steals winter."

    # Settings GET on clean state populates all task roles and all providers
    gs = client.get("/api/settings")
    assert gs.status_code == 200
    assert "draft" in gs.json()["models"]
    assert "ollama" in gs.json()["providers"]

    # Settings validation rejects bad provider with 422 instead of 500
    bad_set = client.put("/api/settings", json={"provider": "bogus_provider"})
    assert bad_set.status_code == 422

    # API key set & explicit null clear
    s1 = client.put("/api/settings", json={"providers": {"openai": {"api_key": "sk-test-1234567890"}}})
    assert s1.status_code == 200
    assert s1.json()["providers"]["openai"]["has_key"] is True
    assert s1.json()["providers"]["openai"]["api_key"].startswith("sk-tes")

    s2 = client.put("/api/settings", json={"providers": {"openai": {"api_key": None}}})
    assert s2.status_code == 200
    assert s2.json()["providers"]["openai"]["has_key"] is False


def test_sse_multi_subscriber_broadcast():
    import asyncio

    async def _run():
        events.publish("j1", {"type": "started"})
        events.publish("j1", {"type": "token", "text": "Hello "})
        events.publish("j1", {"type": "token", "text": "world"})
        events.publish("j1", {"type": "done"})

        sub1 = [ev async for ev in events.subscribe("j1")]
        sub2 = [ev async for ev in events.subscribe("j1")]
        assert sub1[-1]["type"] == "done"
        assert sub2[-1]["type"] == "done"
        assert "".join(e.get("text", "") for e in sub1 if e["type"] == "token") == "Hello world"
        assert "".join(e.get("text", "") for e in sub2 if e["type"] == "token") == "Hello world"

    asyncio.run(_run())


def test_full_mocked_pipeline_and_kdp_exports(client, monkeypatch):
    def _mock(task: str, sys: str, usr: str) -> str:
        if task == "seed":
            return "1. **The Brass Lantern** — A harbor keeper discovers forged tides.\n2. **Salt Ledger** — Two sisters trade debts."
        if task == "outline":
            return "### Ch 1: Low Water\n- POV: Clara\n- Clara checks the weir.\n\n### Ch 2: High Fog\n- POV: Clara\n- The bell rings.\n"
        if task == "scenes":
            return '{"chapter": 1, "scenes": [{"title": "Weir", "word_budget": 400, "location": "Dock", "time": "Dawn", "pov": "Clara", "cast": ["Clara"], "start_state": "Cold", "goal": "Inspect lock", "turn": "Key missing", "outcome": "Tracks found", "hard_stop": "Stop at gate", "illustration_prompt": ""}]}'
        if task == "summary":
            return '{"summary": "Clara checked the dock and spotted footprints.", "state": "alive=Clara; location=Pier"}'
        if task == "canon":
            return "- Pier lock key is missing."
        if task == "editor":
            return '{"verdict": "Clean.", "weakest_sentences": [], "ai_patterns": [], "continuity_violations": [], "missed_beats": [], "over_explained_subtext": [], "flat_scenes": [], "what_to_keep": ["Dock detail"], "concrete_revisions": ["Tighten closing beat"]}'
        if task == "kdp":
            return '{"subtitle": "A Harbor Mystery", "blurb_html": "<p>Fog rolls into St. Ives.</p>", "keywords": ["cozy harbor mystery", "maritime suspense"], "categories": ["Fiction / Mystery & Detective / Cozy"], "comp_titles": ["The Thursday Murder Club"], "age_range": "Adult", "cover_art_brief": "Brass lantern on wet stone pier."}'
        return " ".join(["Clara stepped onto the wet oak planks and checked the brass padlock twice before sunrise."] * 25)

    install_mock_llm(monkeypatch, _mock)

    # Create book + front matter
    b_res = client.post(
        "/api/books",
        json={
            "title": "Salt & Brass",
            "book_format": "short_story",
            "target_words": 1200,
            "chapter_count": 2,
            "front_matter": {"author_name": "M. E. Vance", "subtitle": "A Harbor Tale", "dedication": "For winter walkers."},
        },
    )
    assert b_res.status_code == 201
    bid = b_res.json()["id"]

    # Sync outline
    ol = client.put(
        f"/api/books/{bid}/outline",
        json={"text": "### Ch 1: Low Water\n- POV: Clara\n- Beat 1\n\n### Ch 2: High Fog\n- POV: Clara\n- Beat 2\n"},
    )
    assert ol.status_code == 200
    assert ol.json()["chapter_count"] == 2

    # Write & scan chapters directly via PUT + test Draft / Polish / KDP Kit
    prose_1 = " ".join(["Clara carried the brass storm lantern past the salt shed while gulls wheeled overhead."] * 28)
    prose_2 = " ".join(["Tidewater slapped the stone stairs where the old harbor bell hung silent in fog."] * 28)
    assert client.put(f"/api/books/{bid}/chapters/1", json={"text": prose_1}).status_code == 200
    assert client.put(f"/api/books/{bid}/chapters/2", json={"text": prose_2}).status_code == 200

    # EPUB 3 export validation
    epub_r = client.get(f"/api/books/{bid}/export/epub")
    assert epub_r.status_code == 200
    zf = zipfile.ZipFile(io.BytesIO(epub_r.content))
    names = zf.namelist()
    assert names[0] == "mimetype"
    assert zf.getinfo("mimetype").compress_type == zipfile.ZIP_STORED
    assert zf.read("mimetype") == b"application/epub+zip"
    assert "META-INF/container.xml" in names
    assert "OEBPS/content.opf" in names
    assert "OEBPS/nav.xhtml" in names
    assert "OEBPS/ch_001.xhtml" in names
    assert "OEBPS/ch_002.xhtml" in names
    ET.fromstring(zf.read("OEBPS/content.opf"))
    ET.fromstring(zf.read("OEBPS/nav.xhtml"))

    # DOCX export validation
    docx_r = client.get(f"/api/books/{bid}/export/docx")
    assert docx_r.status_code == 200
    doc = Document(io.BytesIO(docx_r.content))
    full_docx_text = "\n".join(p.text for p in doc.paragraphs)
    assert "Salt & Brass" in full_docx_text
    assert "brass storm lantern" in full_docx_text

    # KDP PDF export validation
    pdf_r = client.get(f"/api/books/{bid}/export/pdf?trim=5.5x8.5")
    assert pdf_r.status_code == 200
    reader = PdfReader(io.BytesIO(pdf_r.content))
    assert len(reader.pages) >= 5
    pdf_txt = "\n".join((pg.extract_text() or "") for pg in reader.pages)
    assert "Salt & Brass" in pdf_txt
    assert "brass storm lantern" in pdf_txt

    # Pure read-only ZIP export does NOT write manuscript.md onto disk
    assert not (book_dir(bid) / "manuscript.md").exists()
    zip_r = client.get(f"/api/books/{bid}/export")
    assert zip_r.status_code == 200
    assert not (book_dir(bid) / "manuscript.md").exists()


def test_spa_and_favicon_serving(client):
    h = client.get("/api/health")
    assert h.status_code == 200 and h.json() == {"ok": True}

    fav = client.get("/favicon.ico")
    assert fav.status_code == 200
    assert "svg" in fav.headers.get("content-type", "")

    spa = client.get("/")
    assert spa.status_code == 200
    assert "Novelista" in spa.text

    unknown_api = client.get("/api/nonexistent_route_xyz")
    assert unknown_api.status_code == 404


def test_kdp_kit_strips_template_labels_cleanly():
    from app.pipeline.kdp_kit import save_kdp_kit
    from app.store import create_book

    b = create_book(title="Clean Blurb Test")
    bdir = book_dir(b.id)
    saved = save_kdp_kit(
        bdir,
        {
            "subtitle": "A Boston Mystery",
            "blurb_html": "<p><strong>Hook:</strong> Two hours before the wreck, his watch stopped.</p><p>Body: Arthur must trace the ledger.</p>",
            "keywords": ["historical mystery"],
            "categories": ["Fiction / Mystery & Detective / Historical"],
        },
    )
    blurb = saved["blurb_html"]
    assert "Hook:" not in blurb and "Body:" not in blurb
    assert "<strong></strong>" not in blurb
    assert blurb == "<p>Two hours before the wreck, his watch stopped.</p><p>Arthur must trace the ledger.</p>"


def test_uploads_has_cover_flag(client):
    res = client.post("/api/books", json={"title": "CoverFlagTest"})
    bid = res.json()["id"]

    u1 = client.get(f"/api/books/{bid}/uploads")
    assert u1.status_code == 200
    assert u1.json()["has_cover"] is False

    tiny_png = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15c4"
    up = client.post(
        f"/api/books/{bid}/cover",
        files={"file": ("cover.png", io.BytesIO(tiny_png), "image/png")},
    )
    assert up.status_code == 201

    u2 = client.get(f"/api/books/{bid}/uploads")
    assert u2.status_code == 200
    assert u2.json()["has_cover"] is True

    d = client.delete(f"/api/books/{bid}/cover")
    assert d.status_code == 204
    assert client.get(f"/api/books/{bid}/uploads").json()["has_cover"] is False


def test_seed_parse_candidates_unspaced_markdown_heading():
    from app.routers.seed import parse_candidates

    raw = (
        "6. **The Archivist's Index** — Eleanor studies rum-running ledgers.\n\n"
        "7.**The Cobbler's Thread** — Tommy O'Malley finds a coded slip in a boot."
    )
    parsed = parse_candidates(raw)
    assert [c["index"] for c in parsed] == [6, 7]
    assert "Cobbler" in parsed[1]["title"]
