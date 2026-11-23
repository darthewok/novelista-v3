from __future__ import annotations

import time
import httpx
import pytest

from app.main import app
from tests._llm_mock import install_mock_llm


def _wait_job(client, jid: str, timeout: float = 5.0) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        r = client.get(f"/api/jobs/{jid}")
        assert r.status_code == 200
        d = r.json()
        if d["status"] in {"done", "error", "cancelled"}:
            return d
        time.sleep(0.03)
    raise AssertionError(f"Job {jid} did not finish within {timeout}s")


def test_end_to_end_router_jobs(client, monkeypatch):
    def _mock(task: str, sys: str, usr: str) -> str:
        if task == "seed":
            return "1. **The Cider House** — Two cousins inherit a rigged orchard.\n2. **Fog Bell** — A lighthouse keeper forgets a tide."
        if task == "foundation":
            return "## Core Premise\nA rainy apple valley with strict guild rules."
        if task == "voice":
            if "Trial 1" in sys:
                return "### Trial 1 — Brisk Dry\nThe latch stuck again.\n\n### Trial 2 — Warm\nRain smelled of crushed apples.\n\n### Trial 3 — Close\nNobody oiled the hinge."
            return "## Part 2 — Project Voice Calibration\nChosen register: Brisk Dry\n\n### Vocabulary Wells\n- pomace, slate, cooperage"
        if task == "canon":
            return "- Cider mill opens in October."
        if task == "outline":
            return "### Ch 1: First Pressing\n- POV: Hannah\n- Hannah unlocks the shed.\n\n### Ch 2: Sour Mash\n- POV: Hannah\n- Barn roof leaks.\n"
        if task == "scenes":
            return '{"chapter": 1, "scenes": [{"title": "Shed", "word_budget": 400, "location": "Orchard", "time": "Dawn", "pov": "Hannah", "cast": ["Hannah"], "start_state": "Wet", "goal": "Open press", "turn": "Broken bolt", "outcome": "Needs blacksmith", "hard_stop": "Gate shuts", "illustration_prompt": ""}]}'
        if task == "editor":
            return '{"verdict": "Solid start.", "weakest_sentences": [], "ai_patterns": [], "continuity_violations": [], "missed_beats": [], "over_explained_subtext": [], "flat_scenes": [], "what_to_keep": ["Cold iron press"], "concrete_revisions": ["Sharpen opening line"]}'
        if task == "summary":
            return '{"summary": "Hannah unlocked the press and found a snapped bolt.", "state": "alive=Hannah; location=Orchard"}'
        if task == "revise":
            if "ISSUES TO REMOVE" in usr:
                return "Hannah pushed open the heavy oak doors before sunrise."
            return " ".join(["Hannah wiped rain from the iron screw press and inspected the split oak beam beside the trough."] * 22)
        return " ".join(["Hannah unlocked the timber shed and chose to delve into the cold press logbook before dawn."] * 22)

    install_mock_llm(monkeypatch, _mock)

    # 1. Create book
    b = client.post("/api/books", json={"title": "Cider Ledger", "book_format": "short_story", "target_words": 1000, "chapter_count": 2}).json()
    bid = b["id"]

    # 2. Seed candidates + choose
    j = client.post(f"/api/books/{bid}/seed/candidates").json()
    assert _wait_job(client, j["id"])["status"] == "done"
    cands = client.get(f"/api/books/{bid}/seed/candidates").json()
    assert len(cands["candidates"]) == 2
    ch = client.post(f"/api/books/{bid}/seed/choose", json={"index": 1})
    assert ch.status_code == 200
    assert "rigged orchard" in ch.json()["text"]

    # 3. World -> Characters -> Voice Trials -> Voice -> Canon -> Outline
    for ep in ("world/generate", "characters/generate", "voice/trials"):
        rj = client.post(f"/api/books/{bid}/{ep}").json()
        assert _wait_job(client, rj["id"])["status"] == "done"
    rj = client.post(f"/api/books/{bid}/voice/generate", json={"choice": 1}).json()
    assert _wait_job(client, rj["id"])["status"] == "done"
    for ep in ("canon/generate", "outline/generate"):
        rj = client.post(f"/api/books/{bid}/{ep}").json()
        assert _wait_job(client, rj["id"])["status"] == "done"

    # 4. Draft ch 1 with polish=True -> catches `delve` if any, updates summary & canon
    dj = client.post(f"/api/books/{bid}/chapters/1/draft", json={"polish": True, "scene_cards": True}).json()
    assert _wait_job(client, dj["id"])["status"] == "done"

    ch1 = client.get(f"/api/books/{bid}/chapters/1").json()
    assert ch1["word_count"] > 100
    assert ch1["status"] in {"drafted", "polished"}

    # 5. Selection co-writer
    rw = client.post(
        f"/api/books/{bid}/chapters/1/rewrite-selection",
        json={"start_char": 0, "end_char": min(60, len(ch1["text"])), "action": "tighten"},
    )
    assert rw.status_code == 200
    assert len(rw.json()["replacement"]) > 10

    # 6. Draft-all remaining pending chapter (Ch 2)
    da = client.post(f"/api/books/{bid}/draft-all", json={"polish": False, "scene_cards": False}).json()
    assert da["chapters"] == [2]
    assert _wait_job(client, da["job_ids"][0])["status"] == "done"
    assert client.get(f"/api/books/{bid}/chapters/2").json()["status"] == "drafted"


def test_audit_fix_unit_parser_selective_and_fix_all(client, monkeypatch):
    from app.enums import ChapterStatus
    from app.pipeline import manuscript_audit
    from app.routers.audit import _extract_unit_numbers_from_issue
    from app.storage import atomic_write_json, read_text
    from app.store import book_dir, chapter_path, get_book, save_book

    # 1. Unit parser tests
    valid = {1, 2, 3, 4}
    assert _extract_unit_numbers_from_issue("Parts 2 and 3 lack descriptive density found in Part 1.", valid) == {1, 2, 3}
    assert _extract_unit_numbers_from_issue("Part 1, Part 3 pacing dips.", valid) == {1, 3}
    assert _extract_unit_numbers_from_issue("Chapters 2-4 repeat similes.", valid) == {2, 3, 4}
    assert _extract_unit_numbers_from_issue("Word count is 12% below minimum threshold.", valid) == set()

    # 2. Setup 3-part short story with saved audit containing multiple issues
    b_res = client.post(
        "/api/books",
        json={
            "title": "Orbital Audit Test",
            "book_format": "short_story",
            "target_words": 4500,
            "chapter_count": 3,
            "idea": "Engineers audit a generation ship hull in orbit.",
        },
    ).json()
    bid = b_res["id"]
    client.put(
        f"/api/books/{bid}/outline",
        json={"text": "### Part 1: Telemetry\n- Beat\n\n### Part 2: Archive\n- Beat\n\n### Part 3: Suture\n- Beat\n"},
    )
    b = get_book(bid)
    assert b is not None
    bdir = book_dir(bid)
    b_p1 = " ".join(["Part one telemetry showed nominal pressure across Sector Gamma."] * 25)
    b_p2 = " ".join(["Part two archive notes skimmed quickly over Kofi's shift report."] * 25)
    b_p3 = " ".join(["Part three inspection remained quiet until Miriam arrived."] * 25)
    for num, body_txt in ((1, b_p1), (2, b_p2), (3, b_p3)):
        atomic_write_json_p = chapter_path(bdir, num)
        atomic_write_json_p.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_json_p.write_text(f"## Part {num}\n\n{body_txt}\n", encoding="utf-8")
        ch = b.get_chapter(num)
        assert ch is not None
        ch.word_count = len(body_txt.split())
        ch.status = ChapterStatus.drafted
    save_book(b)

    atomic_write_json(
        bdir / "reports" / "manuscript_audit.json",
        {
            "deterministic": manuscript_audit.compute_deterministic_audit(b, bdir),
            "editorial": {
                "publishability_verdict": "needs_revision",
                "executive_summary": "Needs deeper Part 2 expansion and cleanup.",
                "strengths": ["Clear hard sci-fi voice."],
                "structural_issues": [
                    "Part 2 lacks descriptive density.",
                    "Word count is below the minimum threshold.",
                ],
                "continuity_risks": [],
                "ai_style_patterns": ["Heavy reliance on 'simmering resentment'."],
                "top_fixes": [
                    {"chapter": 1, "action": "Sharpen opening gauge check."},
                    {"chapter": 2, "action": "Add spoken dialogue between Kofi and Beatrice."},
                    {"chapter": 3, "action": "Replace passive transitions with physical beats."},
                ],
            },
        },
    )

    revised_chapters: list[str] = []

    def _mock_audit_fix(task: str, sys: str, usr: str) -> str:
        if task == "revise":
            m_ch = None
            for line in usr.splitlines():
                if line.startswith("Fix these editorial audit issues for Part "):
                    m_ch = line
                    break
            revised_chapters.append(m_ch or usr[:80])
            if "Part 2:" in usr or "Part 2" in (m_ch or ""):
                return " ".join(["Kofi gripped the copper wrench and argued with Beatrice across the bulkhead."] * 35)
            return " ".join(["Concrete physical gauges clicked cleanly across the orbital maintenance deck."] * 30)
        if task == "summary":
            return '{"summary": "Updated summary after audit fix.", "state": "alive=Miriam,Kofi"}'
        if task == "canon":
            return "NONE"
        if task == "audit":
            return '{"publishability_verdict": "ready", "executive_summary": "Re-audited clean.", "strengths": ["Tighter scenes."], "structural_issues": [], "continuity_risks": [], "ai_style_patterns": [], "top_fixes": []}'
        return "Clean."

    install_mock_llm(monkeypatch, _mock_audit_fix)

    # Empty selection -> 400
    bad_r = client.post(
        f"/api/books/{bid}/audit/fix",
        json={"global_issues": [], "priority_fixes": [], "fix_all": False, "rerun_audit": False},
    )
    assert bad_r.status_code == 400

    # 3. Selective fix on Part 2 ONLY (no re-audit) -> leaves Part 1 and Part 3 untouched, creates v1 snapshot
    sel_j = client.post(
        f"/api/books/{bid}/audit/fix",
        json={
            "global_issues": ["Part 2 lacks descriptive density."],
            "priority_fixes": [{"chapter": 2, "action": "Add spoken dialogue between Kofi and Beatrice."}],
            "fix_all": False,
            "rerun_audit": False,
        },
    )
    assert sel_j.status_code == 200
    assert _wait_job(client, sel_j.json()["id"])["status"] == "done"
    assert "Kofi gripped the copper wrench" in read_text(chapter_path(bdir, 2))
    assert "Part one telemetry" in read_text(chapter_path(bdir, 1))
    assert "Part three inspection" in read_text(chapter_path(bdir, 3))
    v_info = client.get(f"/api/books/{bid}/chapters/2/versions").json()
    assert v_info["current_version"] >= 1 and len(v_info["versions"]) >= 1

    # 4. Accept Part 1 so it is locked, then run Fix All + rerun_audit=True -> fixes Parts 2 & 3, skips Part 1, refreshes audit
    acc = client.post(f"/api/books/{bid}/chapters/1/accept")
    assert acc.status_code == 200
    revised_chapters.clear()
    all_j = client.post(
        f"/api/books/{bid}/audit/fix",
        json={"fix_all": True, "rerun_audit": True},
    )
    assert all_j.status_code == 200
    assert _wait_job(client, all_j.json()["id"])["status"] == "done"

    assert any("Part 2" in x for x in revised_chapters)
    assert any("Part 3" in x for x in revised_chapters)
    assert not any("Part 1" in x for x in revised_chapters)
    assert "Part one telemetry" in read_text(chapter_path(bdir, 1))
    assert client.get(f"/api/books/{bid}/audit").json()["editorial"]["publishability_verdict"] == "ready"


def test_api_unknown_routes_return_404_not_405(client):
    assert client.post("/api/does-not-exist").status_code == 404
    assert client.get("/api/does-not-exist").status_code == 404
    assert client.delete("/api/health").status_code == 405
    assert client.get("/books/test-id/audit").status_code == 200


def test_polish_strips_all_em_dashes(client, monkeypatch):
    b_res = client.post(
        "/api/books",
        json={
            "title": "Hyphen Guardrail Test",
            "book_format": "short_story",
            "target_words": 800,
            "chapter_count": 1,
            "idea": "An inspector checks an old timber slipway.",
        },
    ).json()
    bid = b_res["id"]
    client.put(f"/api/books/{bid}/outline", json={"text": "### Part 1: Timber Pier\n- Beat\n"})

    dirty = (
        "## Part 1 — Timber Pier\n\n"
        + " ".join(["I checked the cast-iron cleat—cold under my thumb—before tide rose."] * 8)
        + '\n\n"Wait—" Mara called—before the ferry horn cut her off.\n'
    )
    put_r = client.put(f"/api/books/{bid}/chapters/1", json={"text": dirty})
    assert put_r.status_code == 200
    assert any(f["rule"] == "em_dash_forbidden" for f in put_r.json()["report"]["findings"])

    def _mock_polish(task: str, sys: str, usr: str) -> str:
        if task == "editor":
            return (
                '{"verdict": "Tighten prose.", "weakest_sentences": [], "ai_patterns": [], '
                '"continuity_violations": [], "missed_beats": [], "over_explained_subtext": [], '
                '"flat_scenes": [], "what_to_keep": ["Cast-iron cleat"], "concrete_revisions": ["Remove em-dashes"]}'
            )
        if task == "revise":
            # Mock LLM still sneaks in em-dashes so normalize_prose_typography + Polish must clean them!
            return (
                "## Part 1 — Timber Pier\n\n"
                + " ".join(["I gripped the cast-iron cleat—slick with mist—and watched the hull."] * 8)
                + '\n\n"Wait—" Mara called—just as the harbor bell rang once.\n'
            )
        if task == "summary":
            return '{"summary": "Checked the cast-iron cleat.", "state": "location=Timber Pier"}'
        if task == "canon":
            return "NONE"
        return "Clean."

    install_mock_llm(monkeypatch, _mock_polish)
    pj = client.post(f"/api/books/{bid}/chapters/1/polish")
    assert pj.status_code == 200
    assert _wait_job(client, pj.json()["id"])["status"] == "done"

    ch_out = client.get(f"/api/books/{bid}/chapters/1").json()
    rep_out = client.get(f"/api/books/{bid}/chapters/1/report").json()
    assert "—" not in ch_out["text"] and "–" not in ch_out["text"]
    assert "## Part 1 - Timber Pier" in ch_out["text"]
    assert "cleat - slick with mist - and watched" in ch_out["text"]
    assert '"Wait-" Mara called - just' in ch_out["text"]
    assert "cast-iron" in ch_out["text"]
    assert not any(f["rule"] == "em_dash_forbidden" for f in rep_out["findings"])



