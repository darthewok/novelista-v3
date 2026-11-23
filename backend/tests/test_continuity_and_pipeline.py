from __future__ import annotations

import asyncio

from app.enums import BookFormat, POV
from app.pipeline import (
    adversarial_cut,
    canon_gen,
    chapter_revise,
    context_builder,
    manuscript_audit,
)
from app.pipeline._common import foreshadowing_for_chapter, parse_outline_units, pov_name
from app.storage import atomic_write_text, read_text
from app.store import book_dir, chapter_path, create_book, save_book
from app.routers.outline import sync_chapters
from tests._llm_mock import install_mock_llm


def test_foreshadowing_parses_all_three_columns():
    outline = """### Ch 1: Arrival
- Beat

### Ch 4: Storm
- Beat

### Ch 7: Tide
- Beat

## Foreshadowing Ledger
| Item | Planted | Reinforced | Paid Off |
|---|---|---|---|
| Cracked brass key | Ch 1, 2 | Ch 4 | Ch 7 |
| Salt ledger | 2 | 5, 6 | Ch 7 |
"""
    f1 = foreshadowing_for_chapter(outline, 1)
    f4 = foreshadowing_for_chapter(outline, 4)
    f7 = foreshadowing_for_chapter(outline, 7)
    assert any("PLANT" in x and "brass key" in x for x in f1)
    assert any("REINFORCE" in x and "brass key" in x for x in f4)
    assert any("PAY OFF" in x and "brass key" in x for x in f7)
    assert any("PAY OFF" in x and "Salt ledger" in x for x in f7)


def test_pov_name_formats_and_omniscient():
    assert pov_name("- POV Character: Mara Vance") == "Mara Vance"
    assert pov_name("- POV: Third-person limited (Tomasz)") == "Tomasz"
    assert pov_name("- POV: First-person") is None
    assert pov_name("- POV: Mara Vance", book_pov=POV.third_omniscient) is None


def test_context_builder_filters_future_chapters():
    b = create_book(title="Ledger", book_format=BookFormat.novella)
    bdir = book_dir(b.id)
    outline = "### Ch 1: One\n- POV: Mara\n- Beat 1\n### Ch 2: Two\n- POV: Mara\n- Beat 2\n### Ch 3: Three\n- POV: Mara\n- Secret ending\n"
    atomic_write_text(bdir / "outline.md", outline)
    sync_chapters(b, outline)
    save_book(b)

    atomic_write_text(
        bdir / "summary.md",
        "## Ch 1 — One\nMara opens the shop.\n- State: alive=Mara\n\n"
        "## Ch 2 — Two\nMara finds the ledger.\n- State: ledger=found\n\n"
        "## Ch 3 — Three\nSECRET FUTURE BETRAYAL SPOILER.\n- State: killer=Jonas\n",
    )
    atomic_write_text(
        bdir / "canon.md",
        "## World Rules\n- Docks flood at spring tide.\n\n"
        "## Ch 1\n- Shop opens at six.\n\n"
        "## Ch 3\n- FUTURE SECRET KILLER IS JONAS.\n",
    )
    ctx, _ = context_builder.build_chapter_context(b, bdir, 2)
    assert "Mara opens the shop." in ctx
    assert "SECRET FUTURE BETRAYAL SPOILER" not in ctx
    assert "FUTURE SECRET KILLER IS JONAS" not in ctx


def test_canon_append_preserves_facts_on_revision(monkeypatch):
    b = create_book(title="Tide")
    bdir = book_dir(b.id)
    atomic_write_text(
        bdir / "canon.md",
        "## World Rules\n- Harbor bell rings at dusk.\n\n## Ch 1\n- Mara lost her iron ring.\n- Pier Three is closed.\n",
    )
    # Mock canon extractor re-returning BOTH old Ch 1 fact AND one new fact when Ch 1 is revised
    install_mock_llm(
        monkeypatch,
        lambda t, s, u: "- Mara lost her iron ring.\n- Pier Three is closed.\n- Ferryman owes Mara four shillings.",
    )
    updated = asyncio.run(canon_gen.append_from_chapter(b, bdir, 1, "Revised chapter text."))
    assert "Mara lost her iron ring." in updated
    assert "Pier Three is closed." in updated
    assert "Ferryman owes Mara four shillings." in updated


def test_noop_revision_guard(monkeypatch):
    b = create_book(title="Test")
    bdir = book_dir(b.id)
    orig = "Mara unlocked the green door and set her wet boots beside the stove."
    install_mock_llm(monkeypatch, lambda t, s, u: orig)
    rev, no_op = asyncio.run(chapter_revise.generate(b, bdir, 1, current_text=orig, notes="Make it punchier"))
    assert no_op is True
    assert rev == orig


def test_adversarial_cut_recapitalizes():
    txt = "Naturally, Mara walked toward the pier before sunrise."
    out, rep = adversarial_cut.apply_cuts(
        txt,
        [{"quote": "Naturally, ", "type": "FAT", "reason": "filler"}],
        max_percent=30.0,
    )
    assert rep["applied_cuts"] == 1
    assert out == "Mara walked toward the pier before sunrise."


def test_outline_parses_spreads_parts_and_chapters():
    md = "### Spread 1: Under the Leaf\n- Beat\n### Spread 2: Puddle Jump\n- Beat\n"
    units = parse_outline_units(md)
    assert units == [(1, "Under the Leaf"), (2, "Puddle Jump")]


def test_manuscript_audit_full_flow(monkeypatch):
    b = create_book(title="Harbor", book_format=BookFormat.short_story)
    bdir = book_dir(b.id)
    atomic_write_text(bdir / "outline.md", "### Part 1: Dock\n- Beat\n### Part 2: Bell\n- Beat\n")
    sync_chapters(b, read_text(bdir / "outline.md"))
    save_book(b)
    atomic_write_text(chapter_path(bdir, 1), " ".join(["Rain tapped the slate roof as Mara sorted nets."] * 35))
    atomic_write_text(chapter_path(bdir, 2), " ".join(["Salt crusted the brass bell beside the lock."] * 35))

    install_mock_llm(
        monkeypatch,
        lambda t, s, u: '{"publishability_verdict": "ready", "executive_summary": "Sharp and cohesive.", "strengths": ["Grounded setting"], "structural_issues": [], "continuity_risks": [], "ai_style_patterns": [], "top_fixes": []}',
    )
    audit = asyncio.run(manuscript_audit.run_full_audit(b, bdir))
    assert audit["deterministic"]["total_words"] > 400
    assert audit["editorial"]["publishability_verdict"] == "ready"


def test_strip_llm_chatter_preserves_story_openers():
    from app.pipeline._common import strip_llm_chatter

    chatter = (
        "Okay, excellent! Here's the revised Chapter 2:\n\n"
        "Rain hammered the copper awnings along Commercial Wharf.\n\n"
        "Let me know if you'd like me to adjust the pacing."
    )
    assert strip_llm_chatter(chatter) == "Rain hammered the copper awnings along Commercial Wharf."

    chatter2 = "Here is the cast sheet:\n\n## Cast at a Glance\n- Arthur Hale"
    assert strip_llm_chatter(chatter2).startswith("## Cast at a Glance")

    story1 = "Great gulls wheeled above the harbor.\n\nHe stepped onto the wet timber."
    assert strip_llm_chatter(story1) == story1

    story2 = "Alright, I thought, checking my watch.\n\nThe whistle blew twice."
    assert strip_llm_chatter(story2) == story2


def test_characters_banned_ai_names_detector():
    from app.pipeline.characters_gen import _find_banned_names

    cast_bad = "## Cast\n- Elara Vance: harbor clerk\n- Silas Thorne: inspector"
    hits = _find_banned_names(cast_bad, "Boston 1924 watchmaker mystery")
    assert {"Elara", "Vance", "Silas", "Thorne"} <= set(hits)

    # Allowed when explicitly named in seed
    hits_seeded = _find_banned_names(cast_bad, "Detective Elara Vance hunts Silas Thorne in Boston.")
    assert hits_seeded == []


def test_chapter_revise_length_collapse_guard(monkeypatch):
    b = create_book(title="Clockwork", book_format=BookFormat.novella)
    bdir = book_dir(b.id)
    orig = " ".join(["Arthur adjusted the brass escapement under the green bench lamp."] * 60)  # 600 words
    shrunk = " ".join(["Arthur worked at his bench."] * 35)  # 175 words (< 75%)
    install_mock_llm(monkeypatch, lambda t, s, u: shrunk)
    out, no_op = asyncio.run(chapter_revise.generate(b, bdir, 1, current_text=orig, notes="Sharpen dialogue"))
    assert no_op is True
    assert out == orig


def test_context_builder_include_scene_cards_flag():
    from app.pipeline import scene_planner

    b = create_book(title="Harbor Watch", book_format=BookFormat.novella, target_words=10000, chapter_count=4)
    bdir = book_dir(b.id)
    atomic_write_text(bdir / "outline.md", "### Ch 1: Wharf\n- POV: Arthur\n- Beat 1\n")
    sync_chapters(b, read_text(bdir / "outline.md"))
    save_book(b)
    scene_planner.save_scenes(
        bdir,
        1,
        {"chapter": 1, "scenes": [{"title": "Shop Opening", "word_budget": 900}, {"title": "Pawnshop", "word_budget": 900}]},
    )

    ctx_with, _ = context_builder.build_chapter_context(b, bdir, 1, include_scene_cards=True)
    ctx_without, _ = context_builder.build_chapter_context(b, bdir, 1, include_scene_cards=False)
    assert "SCENE PLAN" in ctx_with
    assert "Pawnshop" in ctx_with
    assert "SCENE PLAN" not in ctx_without
    assert "Pawnshop" not in ctx_without


def test_strip_llm_chatter_long_preamble_and_multi_sentence_signoff():
    from app.pipeline._common import strip_llm_chatter

    long_doc = (
        "Okay, this is excellent groundwork. The worldbuilding is rich and evocative, providing plenty of texture "
        "for Arthur's narration. Remember to keep the 1924 period details sharp throughout every chapter. "
        "Here is the complete breakdown as requested for the story cast below:\n\n"
        "## Cast at a Glance\n- Arthur Hale: Boston watchmaker\n\n"
        "(Word count: 1042)\n\n"
        "This framework should provide ample material for developing compelling character interactions. "
        "Let me know if you would like me to elaborate on any aspect of these characters!"
    )
    cleaned = strip_llm_chatter(long_doc)
    assert cleaned == "## Cast at a Glance\n- Arthur Hale: Boston watchmaker"

    story1 = "Great gulls wheeled above Boston Harbor.\n\nHe locked the brass casing."
    assert strip_llm_chatter(story1) == story1


def test_characters_scrub_banned_names():
    from app.pipeline.characters_gen import scrub_banned_names

    raw = "- SILAS Croft and Silas stood beside Elara and THORNE."
    scrubbed = scrub_banned_names(raw, "Boston harbor mystery about Arthur and Clara.")
    assert scrubbed == "- CALEB Croft and Caleb stood beside Hannah and MERCER."

    # Respects names requested in seed
    keep_silas = scrub_banned_names(raw, "Silas investigates Elara.")
    assert "SILAS" in keep_silas and "Elara" in keep_silas
    assert "THORNE" not in keep_silas


def test_chapter_revise_scene_by_scene_preserves_length(monkeypatch):
    b = create_book(title="Watchmaker", book_format=BookFormat.novella, target_words=16200, chapter_count=6)
    bdir = book_dir(b.id)
    sc1 = " ".join(["Arthur oiled the mainspring inside the quiet Tremont Street watch shop while rain pattered outside."] * 85)
    sc2 = " ".join(["Rossi slid the faded pawn ticket across the scarred walnut counter under a single warm bulb."] * 85)
    orig = f"{sc1}\n\n* * *\n\n{sc2}"
    assert len(orig.split()) >= 2400

    prompts_seen: list[str] = []

    def _mock_rev(task: str, sys: str, usr: str) -> str:
        prompts_seen.append(usr)
        if "SCENE 1 OF 2" in usr:
            return " ".join(["Arthur adjusted the brass mainspring inside the quiet Tremont shop as rain tapped the glass."] * 82)
        return " ".join(["Rossi slid the faded yellow pawn ticket across the walnut counter beneath one bare bulb."] * 82)

    install_mock_llm(monkeypatch, _mock_rev)
    revised, no_op = asyncio.run(
        chapter_revise.generate(b, bdir, 1, current_text=orig, notes="Sharpen verb choices cleanly")
    )
    assert len(prompts_seen) == 2
    assert no_op is False
    assert "* * *" in revised
    assert len(revised.split()) >= int(len(orig.split()) * 0.85)


def test_ollama_repeat_limit_retry_drops_json_mode(monkeypatch):
    from app import llm_client
    from app.settings import ProviderConfig, ProviderName, Settings

    attempts: list[dict] = []

    async def _fake_ollama(*, json_mode: bool, overrides, **kwargs):
        attempts.append({"json_mode": json_mode, "overrides": dict(overrides or {})})
        if len(attempts) == 1:
            raise llm_client.LLMError("Ollama error: prediction aborted, token repeat limit reached", retryable=True)
        return llm_client.LLMResult(text='{"verdict": "ok"}', stop_reason="stop", model="gemma3:12b")

    monkeypatch.setattr(llm_client, "_complete_ollama", _fake_ollama)
    s = Settings(
        provider=ProviderName.ollama,
        providers={"ollama": ProviderConfig(repeat_penalty=1.08)},
        models={"editor": "gemma3:12b"},
    )
    res = asyncio.run(
        llm_client.complete(
            system="sys",
            user="usr",
            task="editor",
            json_mode=True,
            settings_override=s,
        )
    )
    assert res.text == '{"verdict": "ok"}'
    assert len(attempts) == 2
    assert attempts[0]["json_mode"] is True
    assert attempts[1]["json_mode"] is False
    assert attempts[1]["overrides"]["repeat_penalty"] >= 1.18


def test_first_person_pov_lock_opener_guard_and_spine_injection():
    b = create_book(
        title="Glassblower Receipt",
        book_format=BookFormat.novella,
        pov=POV.first_person,
        chapter_count=6,
        target_words=10200,
    )
    bdir = book_dir(b.id)
    outline = (
        "## Plot Logic & Resolution Spine\n"
        "- **Protagonist:** Nora Lind (sister of deceased Owen Lind).\n"
        "- **Culprit & Proof:** Walter Briggs paid Martin Reid to close the kiln damper at 10:30 p.m.\n\n"
        "### Ch 1: Ashes at Wharf Four\n"
        "- POV: Nora Lind\n- Beat 1\n"
        "### Ch 3: Ledger Audit\n"
        "- POV: Martin Reid\n- Intentional outline drift test\n"
        "### Ch 6: Harbor Arrest\n"
        "- POV: Nora Lind\n- Finale resolution\n"
    )
    atomic_write_text(bdir / "outline.md", outline)
    sync_chapters(b, outline)
    save_book(b)
    atomic_write_text(chapter_path(bdir, 1), "## Chapter 1\n\nThe brass box rattled when I lifted it.")
    atomic_write_text(chapter_path(bdir, 2), "## Chapter 2\n\nSalt crusted my boots along the granite seawall.")

    ctx3, meta3 = context_builder.build_chapter_context(b, bdir, 3)
    assert meta3["pov_character"] == "Nora Lind"
    assert "You are writing as **Nora Lind** in strict first-person ('I')" in ctx3
    assert "PREVIOUS CHAPTER OPENING WORDS (DO NOT REUSE)" in ctx3
    assert "Chapter 1: 'The'" in ctx3
    assert "Chapter 2: 'Salt'" in ctx3
    assert "HARD OPENING RULE: Do NOT start sentence 1 with:" in ctx3

    ctx6, _ = context_builder.build_chapter_context(b, bdir, 6)
    assert "PLOT LOGIC & RESOLUTION SPINE (MUST PAY OFF ON-PAGE)" in ctx6
    assert "10:30 p.m." in ctx6


def test_scrub_banned_names_avoids_existing_collision_and_mid_tags():
    from app.pipeline._common import strip_llm_chatter
    from app.pipeline.characters_gen import scrub_banned_names

    raw = "- Caleb Mercer met Silas Finch beside Thorne."
    scrubbed = scrub_banned_names(raw, "Coastal Maine mystery about Nora Lind.")
    assert "Silas" not in scrubbed and "Thorne" not in scrubbed
    # Since Caleb already existed, Silas must NOT also become Caleb!
    assert "Frank Finch" in scrubbed

    with_tags = "Line one.\n\n(932 words)\n\nLine two.\n\n(1837 Words)"
    assert strip_llm_chatter(with_tags) == "Line one.\n\nLine two."


def test_ollama_stream_interrupted_resets_client_and_retries(monkeypatch):
    from app import llm_client
    from app.settings import ProviderConfig, ProviderName, Settings

    attempts = 0
    closed_calls = 0

    async def _fake_close():
        nonlocal closed_calls
        closed_calls += 1

    async def _fake_ollama(**kwargs):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise llm_client.LLMError("Ollama stream interrupted: incomplete chunked read", retryable=True)
        return llm_client.LLMResult(text="Clean prose output.", stop_reason="stop", model="gemma3:12b")

    monkeypatch.setattr(llm_client, "close_client", _fake_close)
    monkeypatch.setattr(llm_client, "_complete_ollama", _fake_ollama)

    s = Settings(
        provider=ProviderName.ollama,
        providers={"ollama": ProviderConfig()},
        models={"draft": "gemma3:12b"},
    )
    res = asyncio.run(
        llm_client.complete(system="sys", user="usr", task="draft", settings_override=s)
    )
    assert res.text == "Clean prose output."
    assert attempts == 2
    assert closed_calls == 1


def test_iter4_characters_and_outline_prose_guards_and_seed_lock(monkeypatch):
    from app.pipeline import characters_gen, outline_gen

    seed_txt = (
        "Present-day Rockport, Maine. Nora Lind investigates brother Owen Lind's death "
        "against Harbor Commissioner Walter Briggs."
    )
    b = create_book(
        title="The Harbor Ledger",
        book_format=BookFormat.novella,
        pov=POV.first_person,
        idea=seed_txt,
        chapter_count=3,
        target_words=4500,
    )
    bdir = book_dir(b.id)
    atomic_write_text(bdir / "seed.txt", seed_txt)

    char_calls: list[str] = []

    def _mock_chars(task: str, sys: str, usr: str) -> str:
        char_calls.append(sys)
        if "repairing a character cast sheet" in sys:
            return "## Cast at a Glance\n| Name |\n|---|\n| **Nora Lind** |\n| **Owen Lind** |\n| **Walter Briggs** |\n"
        return (
            "## Cast at a Glance\n| Name |\n|---|\n| **Nora Lind** |\n| **Eamon Lind** |\n| **Walter Briggs** |\n\n"
            "## Chapter 1: Inventory\n\nI stepped into the burned workshop.\n"
        )

    install_mock_llm(monkeypatch, _mock_chars)
    chars_out = asyncio.run(characters_gen.generate(b, bdir))
    assert len(char_calls) == 2
    assert "Owen Lind" in chars_out
    assert "I stepped into" not in chars_out
    assert "## Chapter 1" not in chars_out

    outline_calls: list[str] = []

    def _mock_outline(task: str, sys: str, usr: str) -> str:
        outline_calls.append(sys)
        if "Convert this" in sys:
            return (
                "## Plot Logic & Resolution Spine\n- **Protagonist:** Nora Lind\n\n"
                "### Ch 1: Ashes\n- POV: Nora Lind\n- Beat A\n- Beat B\n**Chapter Ending State:** Ends cleanly.\n"
                "### Ch 2: Ledger\n- POV: Nora Lind\n- Beat C\n- Beat D\n**Chapter Ending State:** Stops.\n"
                "### Ch 3: Arrest\n- POV: Nora Lind\n- Beat E\n- Beat F\n**Chapter Ending State:** Case solved.\n"
            )
        return (
            "### Ch 1: Ashes\n"
            '"Wait here," I said, gripping the soot-darkened knob beside the workbench as rain poured across every windowpane in Rockport.\n\n'
            'I walked to the back bench and stared at the cold brass receipt box for ten minutes before calling the sheriff.\n'
        )

    install_mock_llm(monkeypatch, _mock_outline)
    out_md = asyncio.run(outline_gen.generate(b, bdir))
    assert len(outline_calls) == 2
    assert "## Plot Logic & Resolution Spine" in out_md
    assert "Beat A" in out_md


def test_iter4_draft_scene_isolation_opener_guard_and_revise_floor(monkeypatch):
    from app.pipeline import chapter_draft, scene_planner

    b = create_book(
        title="The Boatbuilder's Notch",
        book_format=BookFormat.novella,
        pov=POV.first_person,
        idea="Present-day Boothbay, Maine (American English throughout). Ruth Halvorsen solves the boat sinking.",
        chapter_count=2,
        target_words=5200,
    )
    bdir = book_dir(b.id)
    atomic_write_text(
        bdir / "outline.md",
        "### Ch 1: Hull Inspection\n- POV: Ruth Halvorsen\n- Beat 1\n### Ch 2: Final Proof\n- POV: Ruth Halvorsen\n- Beat 2\n",
    )
    sync_chapters(b, read_text(bdir / "outline.md"))
    save_book(b)
    atomic_write_text(chapter_path(bdir, 1), "## Chapter 1: Hull Inspection\n\nThe brass bell rang at dawn.")
    scene_planner.save_scenes(
        bdir,
        2,
        {
            "chapter": 2,
            "scenes": [
                {"title": "Tally Box", "word_budget": 800, "hard_stop": "Pause at shed door."},
                {"title": "Harbor Arrest", "word_budget": 900, "hard_stop": "Crane cuffed on pier."},
            ],
        },
    )

    ctx2, _ = context_builder.build_chapter_context(b, bdir, 2)
    assert "FINAL CHAPTER CLOSURE LOCK" in ctx2

    long_sc1 = " ".join(["Water lapped against the cedar dory beside Boothbay wharf while Ruth examined each bronze rivet closely."] * 35)
    long_sc2 = " ".join(["Samira carried Arthur's oak half-hull model onto the sunny pier as Sheriff Brooks cuffed Victor Crane."] * 35)

    sc1_bad_count = 0

    def _mock_draft(task: str, sys: str, usr: str) -> str:
        nonlocal sc1_bad_count
        if "FORBIDDEN FIRST WORDS" in usr:
            return "Cold fog rolled across Boothbay harbor before Ruth knocked on the shed door."
        if "SCENE 1 OF 2" in usr:
            sc1_bad_count += 1
            if sc1_bad_count == 1:
                return f"The wind shifted.\n\n*Plant 1.*\n\n{long_sc1}"
            return f"The wind cleared.\n\n{long_sc1}"
        if "SCENE 2 OF 2" in usr:
            return long_sc2
        return long_sc1

    install_mock_llm(monkeypatch, _mock_draft)
    drafted, rep = asyncio.run(chapter_draft.generate(b, bdir, 2))
    assert "* * *" in drafted
    assert "Samira carried" in drafted
    assert "*Plant 1.*" not in drafted
    assert context_builder.extract_opening_word(drafted) == "Cold"
    assert sc1_bad_count >= 2

    # chapter_revise 0.86 shrink floor rejects 80% compression
    orig_rev = " ".join(["Ruth checked the garboard seam along the cedar planks while rain dripped steadily."] * 40)
    shrunk_80 = " ".join(["Ruth checked the garboard seam along the cedar planks quickly."] * 32)
    install_mock_llm(monkeypatch, lambda t, s, u: shrunk_80)
    rev_out, rev_noop = asyncio.run(chapter_revise.generate(b, bdir, 1, current_text=orig_rev, notes="Sharpen dialogue"))
    assert rev_noop is True
    assert rev_out == orig_rev


def test_iter4_summary_anchor_editor_json_repair_and_kdp_antislop(monkeypatch):
    from app.pipeline import editor_pass, kdp_kit, summary_gen
    from app.storage import read_json

    b = create_book(
        title="The Boatbuilder's Notch",
        book_format=BookFormat.novella,
        pov=POV.first_person,
        idea="Ruth Halvorsen investigates Arthur Pendelton's sinking.",
        chapter_count=2,
        target_words=3400,
    )
    bdir = book_dir(b.id)
    atomic_write_text(bdir / "outline.md", "### Ch 1: Claim\n- POV: Ruth Halvorsen\n### Ch 2: Arrest\n- POV: Ruth Halvorsen\n")
    sync_chapters(b, read_text(bdir / "outline.md"))
    save_book(b)
    atomic_write_text(bdir / "characters.md", "## Cast at a Glance\n- **Ruth Halvorsen**: Adjuster\n- **Arthur Pendelton**: Boatbuilder\n")

    sum_prompts: list[str] = []

    def _mock_summary(task: str, sys: str, usr: str) -> str:
        sum_prompts.append(usr)
        return '{"summary": "Elara inspected Arthur\'s skiff at dawn.", "state": "alive=Elara; dead=Arthur"}'

    install_mock_llm(monkeypatch, _mock_summary)
    sum_md = asyncio.run(summary_gen.update(b, bdir, 1, "I checked the flooded hull."))
    assert "CAST REFERENCE" in sum_prompts[0] and "Ruth Halvorsen" in sum_prompts[0]
    assert "Elara" not in sum_md
    cont = read_json(bdir / "continuity.json")
    assert "Elara" not in cont["1"]["summary"]

    # editor_pass 1-shot JSON repair recovery
    ed_calls = 0

    def _mock_editor(task: str, sys: str, usr: str) -> str:
        nonlocal ed_calls
        ed_calls += 1
        if ed_calls == 1:
            return '{"verdict": "Close ending", "weakest_sentences": ["Bad "quote" line",]}'
        return '{"verdict": "Close ending clean", "weakest_sentences": ["Weak line"], "ai_patterns": [], "continuity_violations": [], "missed_beats": [], "over_explained_subtext": [], "flat_scenes": [], "what_to_keep": [], "concrete_revisions": []}'

    install_mock_llm(monkeypatch, _mock_editor)
    parsed_ed, _ = asyncio.run(editor_pass.run(b, bdir, 2, "Samira handed Ruth the key."))
    assert ed_calls == 2
    assert parsed_ed["parse_failed"] is False
    assert parsed_ed["verdict"] == "Close ending clean"

    # kdp_kit blurb anti-slop & keyword overlap cleanup
    kdp_calls = 0

    def _mock_kdp(task: str, sys: str, usr: str) -> str:
        nonlocal kdp_calls
        kdp_calls += 1
        if kdp_calls == 1:
            return (
                '{"subtitle": "A Boothbay Harbor Mystery", '
                '"blurb_html": "<p>As Ruth delves into Arthur\'s sinking, truths surface.</p>", '
                '"keywords": ["boatbuilders mystery maine", "coastal wooden boat suspense", "harbor fraud investigation", "marine adjuster detective story", "boothbay fiction series", "maine shipyard thriller novel", "maritime sabotage whodunit"], '
                '"categories": ["FICTION / Mystery & Suspense / Amateur Sleuth"], '
                '"comp_titles": ["Dead Wake by Erik Larson"], '
                '"age_range": "Adult", "cover_art_brief": "Cedar dory beside pier."}'
            )
        return (
            '{"subtitle": "A Boothbay Harbor Mystery", '
            '"blurb_html": "<p>Ruth traces a forged hull receipt back to Boothbay Harbor\'s darkest scheme.</p>", '
            '"keywords": ["coastal wooden boat suspense", "harbor fraud investigation", "marine adjuster detective story", "maine shipyard thriller novel", "maritime sabotage whodunit", "new england amateur sleuth book", "wooden dory murder puzzle"], '
            '"categories": ["FICTION / Mystery & Suspense / Amateur Sleuth"], '
            '"comp_titles": ["Dead Wake by Erik Larson"], '
            '"age_range": "Adult", "cover_art_brief": "Cedar dory beside pier."}'
        )

    install_mock_llm(monkeypatch, _mock_kdp)
    kit = asyncio.run(kdp_kit.generate(b, bdir))
    assert kdp_calls == 2
    assert "delves" not in kit["blurb_html"].lower()
    assert not any("boatbuilders" in kw.lower() for kw in kit["keywords"])


def test_iter5_scene_isolation_replay_stripping_and_kdp_present_day_guard(monkeypatch):
    from app.pipeline import chapter_draft, kdp_kit

    seed_txt = "Present-day Gloucester, Massachusetts (American English throughout). Helen Novak investigates Raymond Foster."
    b = create_book(
        title="The Saltworks Key",
        book_format=BookFormat.novella,
        pov=POV.first_person,
        idea=seed_txt,
        chapter_count=2,
        target_words=3400,
    )
    bdir = book_dir(b.id)
    atomic_write_text(bdir / "seed.txt", seed_txt)
    atomic_write_text(
        bdir / "outline.md",
        "### Ch 1: Salt Wharf\n- POV: Helen Novak\n- Secret Beat A\n### Ch 2: Key\n- POV: Helen Novak\n- Final Beat B\n",
    )
    sync_chapters(b, read_text(bdir / "outline.md"))
    save_book(b)

    ctx_scene, _ = context_builder.build_chapter_context(b, bdir, 1, include_scene_cards=False, scene_mode=True)
    assert "THIS CHAPTER'S OUTLINE" not in ctx_scene
    assert "1,445" not in ctx_scene and "scene-by-scene" in ctx_scene

    prev_scene = (
        'Gallagher shuffled his boots near the pilings and stared toward the breakwater.\n\n'
        '"Where did you find that brass key?" she demanded sharply across the quiet shed.'
    )
    replayed_next = (
        '"Where did you find that brass key?" she demanded sharply across the quiet shed while rain dripped.\n\n'
        'Inside the scale cabinet, Helen lifted a waxed canvas tool roll smelling of linseed oil.'
    )
    cleaned = chapter_draft.strip_scene_boundary_replay(prev_scene, replayed_next)
    assert "Where did you find that brass key" not in cleaned
    assert "waxed canvas tool roll" in cleaned

    # save_kdp_kit drops multi-word subtitle subsets like "Cape Ann"
    saved = kdp_kit.save_kdp_kit(
        bdir,
        {
            "subtitle": "A Cape Ann Harbor Mystery",
            "blurb_html": "<p>Test</p>",
            "keywords": ["Cape Ann", "working wharf sabotage mystery"],
            "categories": ["FICTION / Mystery & Suspense / Amateur Sleuth"],
        },
        title="The Saltworks Key",
    )
    assert saved["keywords"] == ["working wharf sabotage mystery"]

    # kdp_kit.generate flags Historical Fiction on Present-day seed + short keyword
    calls: list[str] = []

    def _mock_kdp2(task: str, sys: str, usr: str) -> str:
        calls.append(usr)
        if len(calls) == 1:
            return (
                '{"subtitle": "A Maritime Harbor Mystery", '
                '"blurb_html": "<p>Helen Novak investigates Raymond Foster\'s fatal gantry collapse.</p>", '
                '"keywords": ["gloucester working waterfront whodunit", "massachusetts fishing port suspense novel", "commercial dockyard fraud investigator", "coastal new england timber sabotage", "marine claims adjuster detective story", "harborfront development corruption thriller", "working boatyard cooperative mystery book"], '
                '"categories": ["FICTION / Historical Fiction / 20th Century", "FICTION / Mystery & Suspense / Amateur Sleuth"], '
                '"comp_titles": ["Dead Wake by Erik Larson"], "age_range": "Adult", "cover_art_brief": "Gantry key."}'
            )
        return (
            '{"subtitle": "A Maritime Harbor Mystery", '
            '"blurb_html": "<p>Helen Novak investigates Raymond Foster\'s fatal gantry collapse.</p>", '
            '"keywords": ["gloucester working waterfront mystery story", "maritime insurance fraud amateur sleuth", "new england harbor sabotage thriller", "commercial dockyard murder investigation", "coastal whodunit strong female investigator", "massachusetts fishing port conspiracy book", "working wharf timber sabotage thriller"], '
            '"categories": ["FICTION / Mystery & Suspense / Amateur Sleuth", "FICTION / Contemporary Fiction"], '
            '"comp_titles": ["Dead Wake by Erik Larson"], "age_range": "Adult", "cover_art_brief": "Gantry key."}'
        )

    install_mock_llm(monkeypatch, _mock_kdp2)
    kit2 = asyncio.run(kdp_kit.generate(b, bdir))
    assert len(calls) == 2
    assert "Historical" in calls[1]
    assert all("historical" not in c.lower() for c in kit2["categories"])


def test_iter6_scene_planner_spine_and_bisac_normalization(monkeypatch):
    from app.pipeline import kdp_kit, scene_planner

    b = create_book(
        title="The Breakwater Manifest",
        idea="Present-day Astoria, Oregon mystery. Standalone story.",
        book_format=BookFormat.novella,
        pov=POV.first_person,
        chapter_count=6,
    )
    bdir = book_dir(b.id)
    atomic_write_text(
        bdir / "outline.md",
        "## Plot Logic & Resolution Spine\n"
        "- Diane Osei is ONLY the Clatsop County Port Clerk.\n"
        "- Nora hands Leo's calipers to Benji at closing.\n\n"
        "### Ch 6: High Slack\n- POV: Nora Delgado\n- Arrest at Pier 11 and handoff.\n",
    )
    atomic_write_text(bdir / "canon.md", "## Ch 1\n- Single fatality: Leo Hannigan drowned Oct 13.")
    atomic_write_text(bdir / "characters.md", "- Nora Delgado — surveyor\n- Diane Osei — Clatsop County Port Clerk")

    seen_prompts: list[tuple[str, str]] = []

    def _mock_sc(task: str, sys: str, usr: str) -> str:
        seen_prompts.append((sys, usr))
        return '{"chapter": 6, "scenes": [{"title": "Confrontation", "word_budget": 1700, "location": "Pier 11", "time": "Oct 16", "pov": "Nora Delgado", "cast": ["Nora Delgado"], "start_state": "Arrive at pier", "goal": "Present wire receipt", "turn": "Deputy arrives", "outcome": "Arrest completed", "hard_stop": "Hand calipers to Benji", "illustration_prompt": ""}]}'

    install_mock_llm(monkeypatch, _mock_sc)
    asyncio.run(scene_planner.generate_scenes(b, bdir, 6))
    assert len(seen_prompts) == 1
    sys_msg, usr_msg = seen_prompts[0]
    assert "Plot Logic & Resolution Spine" in usr_msg
    assert "Diane Osei is ONLY the Clatsop County Port Clerk" in usr_msg
    assert "Canon Facts (< Chapter 6):" in usr_msg
    assert "FIRST-PERSON NOTE STYLE" in sys_msg
    assert "FINAL STANDALONE CHAPTER STAGING" in sys_msg

    # BISAC normalization snaps invented / NONFICTION paths into curated BISG paths
    adult_saved = kdp_kit.save_kdp_kit(
        bdir,
        {
            "subtitle": "An Astoria Harbor Mystery",
            "blurb_html": "<p>Test</p>",
            "keywords": ["columbia river bar maritime mystery story"],
            "categories": ["FICTION / Crime Thrillers / Amateur Sleuth", "FICTION / Contemporary Fiction"],
        },
        title="The Breakwater Manifest",
        is_children=False,
        is_present_day=True,
    )
    assert adult_saved["categories"] == [
        "FICTION / Mystery & Detective / Amateur Sleuth",
        "FICTION / Small Town & Rural",
    ]

    pb_saved = kdp_kit.save_kdp_kit(
        bdir,
        {
            "subtitle": "A Rainy-Day Picture Book",
            "blurb_html": "<p>Test</p>",
            "keywords": ["preschool animal teamwork picture book story"],
            "categories": ["JUVENILE FICTION / Animals", "JUVENILE NONFICTION / Activities / Crafts & Hobbies"],
        },
        title="Pip",
        is_children=True,
    )
    assert "JUVENILE FICTION / Animals / General" in pb_saved["categories"]
    assert all("NONFICTION" not in c.upper() for c in pb_saved["categories"])


def test_iter7_canon_seed_ground_truth_scene_cap_and_kdp_blurb_guards(monkeypatch):
    from app.pipeline import canon_gen, chapter_draft, kdp_kit, scene_planner

    b = create_book(
        title="The Cedar Cove Ledger",
        idea="Present-day Anacortes, Washington (American English). Standalone novella.",
        book_format=BookFormat.novel,
        pov=POV.first_person,
        chapter_count=6,
        target_words=16200,
    )
    bdir = book_dir(b.id)
    seed_body = "Paul Brennan (39) drowned Nov 3 at 10:30 p.m.; Clara Ortiz (36) inspects Nov 4 morning. Marge Halverson stays at Port Office."
    atomic_write_text(bdir / "seed.txt", seed_body)
    atomic_write_text(bdir / "characters.md", "- Clara Ortiz (36, she/her) — adjuster\n- Marge Halverson (54, she/her) — Port Office clerk")
    atomic_write_text(bdir / "outline.md", "### Ch 1: Drydock\n- POV: Clara Ortiz\n- Inspect Slip 2.\n")

    seen_canon_usr: list[str] = []

    def _mock_iter7(task: str, sys: str, usr: str) -> str:
        if task == "canon":
            seen_canon_usr.append(usr)
            return "## World Rules\n- Skagit Boatyard\n## Characters & Possessions\n- Clara Ortiz (36, she/her)\n## Timeline & Ages\n- Nov 3 -> Nov 4\n## Hard Constraints\n- 1 fatality"
        if task == "scenes":
            if "condense" in sys.lower() or "10–25 word plot bullets" in sys:
                return '{"chapter": 1, "scenes": [{"title": "Slip Two", "word_budget": 850, "location": "Slip 2", "time": "Nov 4 AM", "pov": "Clara Ortiz", "cast": ["Clara Ortiz"], "start_state": "Arrive at drydock.", "goal": "Inspect sheared pin.", "turn": "Grooves reveal carbide cut.", "outcome": "Bag pin cleanly.", "hard_stop": "Walk to office.", "illustration_prompt": ""}, {"title": "Port Log", "word_budget": 850, "location": "Port Office", "time": "Nov 4 PM", "pov": "Clara Ortiz", "cast": ["Clara Ortiz", "Marge Halverson"], "start_state": "Enter Port Office.", "goal": "Request Nov 2 slip.", "turn": "Work order falsified.", "outcome": "Obtain copy.", "hard_stop": "Depart clerk desk.", "illustration_prompt": ""}]}'
            # Emit 3 scenes + quoted dialogue -> planner trims to 2 and condenses quotes away!
            return (
                '{"chapter": 1, "scenes": ['
                '{"title": "Slip Two", "word_budget": 600, "location": "Slip 2", "time": "Nov 4", "pov": "Clara Ortiz", "cast": ["Clara Ortiz"], "start_state": "Start.", "goal": "Check.", "turn": "\\"You poke around too much, Miss Ortiz,\\" Owen snapped across the dockyard.", "outcome": "End.", "hard_stop": "Leave.", "illustration_prompt": ""},'
                '{"title": "Port Log", "word_budget": 600, "location": "Port Office", "time": "Nov 4", "pov": "Clara Ortiz", "cast": ["Marge Halverson"], "start_state": "Start.", "goal": "Check.", "turn": "Find slip.", "outcome": "End.", "hard_stop": "Leave.", "illustration_prompt": ""},'
                '{"title": "Extra Replay", "word_budget": 500, "location": "Port Office", "time": "Nov 4", "pov": "Clara Ortiz", "cast": ["Marge Halverson"], "start_state": "Replay.", "goal": "Replay.", "turn": "Replay.", "outcome": "Stop.", "hard_stop": "Stop.", "illustration_prompt": ""}]}'
            )
        if task == "kdp":
            if "FIXES REQUIRED" in usr:
                return '{"subtitle": "An Anacortes Drydock Mystery", "blurb_html": "<p><strong>A sheared steel pin points to foul play.</strong></p><p>Clara Ortiz audits drydock records after Paul Brennan dies in Slip 2—uncovering bribed inspections.</p>", "keywords": ["anacortes drydock sabotage thriller", "pacific northwest marine surveyor story", "coastal boatyard murder investigation", "harbor worker bribery mystery book", "puget sound crime amateur sleuth", "working waterfront suspense novella", "small town dockside whodunit mystery"], "categories": ["FICTION / Mystery & Detective / Women Sleuths"], "comp_titles": ["The Dry by Jane Harper"], "age_range": "Adult", "cover_art_brief": "Brass feeler gauge."}'
            return '{"subtitle": "An Anacortes Drydock Mystery", "blurb_html": "<p><strong>Secrets—are better left undisturbed.</strong></p><p>Clara uncovers a web of bribery across the waterfront – one clue at a time.</p>", "keywords": ["small town setting", "anacortes drydock sabotage thriller", "pacific northwest marine surveyor story", "coastal boatyard murder investigation", "harbor worker bribery mystery book", "puget sound crime amateur sleuth", "working waterfront suspense"], "categories": ["FICTION / Mystery & Detective / Women Sleuths"], "comp_titles": ["The Dry by Jane Harper"], "age_range": "Adult", "cover_art_brief": "Brass feeler gauge."}'
        return "Salt stung cold steel along the breakwater."

    install_mock_llm(monkeypatch, _mock_iter7)
    asyncio.run(canon_gen.generate(b, bdir))
    assert seen_canon_usr and "Paul Brennan (39) drowned Nov 3" in seen_canon_usr[0]

    sc_res = asyncio.run(scene_planner.generate_scenes(b, bdir, 1))
    assert len(sc_res["scenes"]) == 2
    assert "You poke around too much" not in str(sc_res["scenes"])

    kdp_res = asyncio.run(kdp_kit.generate(b, bdir))
    assert "better left undisturbed" not in kdp_res["blurb_html"].lower()
    assert "a web of" not in kdp_res["blurb_html"].lower()
    assert " – " not in kdp_res["blurb_html"]

    # _ensure_fresh_opener includes strict first-person POV lock when requested
    seen_op_sys: list[str] = []

    def _mock_op(task: str, sys: str, usr: str) -> str:
        seen_op_sys.append(sys)
        return "Frost coated the steel cleats as I stepped onto the pier."

    install_mock_llm(monkeypatch, _mock_op)
    op_out = asyncio.run(chapter_draft._ensure_fresh_opener("Salt stung my lips as I walked out.", {"salt"}, is_first_person=True))
    assert seen_op_sys and "FIRST-PERSON POV" in seen_op_sys[0]
    assert op_out.startswith("Frost coated")


def test_iter8_single_scene_novella_outline_audit_and_kdp_comps(monkeypatch):
    from app.pipeline import canon_gen, kdp_kit, outline_gen, scene_planner

    seed_txt = (
        "Present-day Port Townsend, Washington (Oct 14–16, 2024; American English). Standalone novella. "
        "Tess Navarro (37, she/her) investigates Martin Cho (40, he/him) drowning in Slip 3 at 10:15 p.m. Oct 13. "
        "Ruth Lind (56, she/her) stays strictly in Jefferson County Harbor Office. Deputy Frank Alvarez arrests Grant Mercer and Dale Kowalski."
    )
    b = create_book(
        title="The Point Hudson Ledger",
        idea=seed_txt,
        book_format=BookFormat.novella,
        pov=POV.first_person,
        chapter_count=6,
        target_words=9600,  # ~1,600w/chapter -> single unified scene card!
    )
    bdir = book_dir(b.id)
    atomic_write_text(bdir / "seed.txt", seed_txt)
    atomic_write_text(bdir / "characters.md", "- Tess Navarro (37, she/her)\n- Ruth Lind (56, she/her) — Harbor Office only")

    # 1. outline_gen runs 1-shot Seed-Fidelity verification pass on rich novel/novella seeds (>= 160 chars)
    out_calls: list[str] = []

    def _mock_iter8(task: str, sys: str, usr: str) -> str:
        if task == "outline":
            out_calls.append(sys)
            if "Audit and tighten" in sys:
                return (
                    "## Plot Logic & Resolution Spine\n- Ruth Lind stays at Harbor Office.\n\n"
                    "### Ch 1: Pin\n- POV: Tess Navarro\n- Oct 13 drowning noted cleanly.\n"
                    "### Ch 2: Slip\n- POV: Tess Navarro\n- Harbor Office check.\n"
                )
            return (
                "## Plot Logic & Resolution Spine\n- Draft spine\n\n"
                "### Ch 1: Pin\n- POV: Tess Navarro\n- Initial beat.\n"
                "### Ch 2: Slip\n- POV: Tess Navarro\n- Harbor Office check.\n"
            )
        if task == "scenes":
            return (
                '{"chapter": 1, "scenes": ['
                '{"title": "Part A", "word_budget": 800, "location": "Slip 3", "time": "Oct 14 AM", "pov": "Tess Navarro", "cast": ["Tess Navarro"], "start_state": "Arrive.", "goal": "Inspect pin.", "turn": "Grooves spotted.", "outcome": "Bag pin.", "hard_stop": "Exit apron.", "illustration_prompt": ""},'
                '{"title": "Part B", "word_budget": 800, "location": "Harbor Office", "time": "Oct 14 PM", "pov": "Tess Navarro", "cast": ["Tess Navarro"], "start_state": "Arrive.", "goal": "Check log.", "turn": "Mismatch.", "outcome": "Leave.", "hard_stop": "Step out.", "illustration_prompt": ""}]}'
            )
        if task == "canon":
            assert "GROUND TRUTH SEED" not in usr
            return "- Tess bagged the steel keel-block pin in Ch 1."
        if task == "kdp":
            if "24–48 characters EACH" in sys:
                return '{"keywords": ["pacific northwest boatyard murder mystery", "working harbor marine surveyor suspense story", "small town Washington amateur sleuth mystery", "bribed drydock inspection crime novella story", "female claims adjuster coastal investigation", "puget sound tugboat sabotage whodunit book", "standalone small town waterfront crime fiction"]}'
            return '{"subtitle": "A Port Townsend Mystery", "blurb_html": "<p>Test copy.</p>", "keywords": ["coastal crime", "marine claims", "drydock incident"], "categories": ["FICTION / Mystery & Detective / Amateur Sleuth"], "comp_titles": ["The Guest List by Lucy Fokley", "Before We Were Yours by Lisa Wingate"], "age_range": "Adult", "cover_art_brief": "Feeler gauge."}'
        return "Martin chose not to sign."

    install_mock_llm(monkeypatch, _mock_iter8)
    out_md = asyncio.run(outline_gen.generate(b, bdir))
    assert len(out_calls) == 2
    assert "Oct 13 drowning noted cleanly" in out_md

    # 2. scene_planner caps novella (~1,600w/ch) to EXACTLY 1 unified scene card & canon_gen append skips SEED
    sc = asyncio.run(scene_planner.generate_scenes(b, bdir, 1))
    assert len(sc["scenes"]) == 1
    asyncio.run(canon_gen.append_from_chapter(b, bdir, 1, "I bagged the steel keel-block pin from Slip 3."))

    # 3. kdp_kit expands <20 char keywords AND strips misspelled/off-genre comps ('Lucy Fokley', 'Before We Were Yours')
    kit = asyncio.run(kdp_kit.generate(b, bdir))
    assert all(len(kw) >= 20 for kw in kit["keywords"])
    assert not any("fokley" in c.lower() or "before we were yours" in c.lower() for c in kit["comp_titles"])
    assert "The Dry by Jane Harper" in kit["comp_titles"]


def test_iter9_outline_prose_leak_canon_isolation_editor_salvage_and_kdp_keywords(monkeypatch):
    from app.pipeline import canon_gen, editor_pass, kdp_kit, outline_gen

    # 1. _outline_has_prose_leak catches long bulleted narrative paragraphs inside outline
    prose_outline = (
        "## Plot Logic & Resolution Spine\n- Lock 1.\n\n"
        "### Ch 1: Slip\n- POV: Maren Vasquez\n"
        "- I inhaled the cold saltwater mist as I walked along the gravel apron toward the boatyard gate where the rain stopped sometime before dawn and gulls circled overhead above the timber cribs while the dispatcher called with early news about the machinist drowning.\n"
    )
    assert outline_gen._outline_has_prose_leak(prose_outline) is True

    crisp_outline = (
        "## Plot Logic & Resolution Spine\n- Lock 1.\n\n"
        "### Ch 1: Slip\n- POV: Maren Vasquez\n"
        "- Maren interviews boatyard foreman Devon Brooks beside Slip 2.\n"
        "- She examines bright carbide cutoff grooves inside the broken pin.\n"
        "- Maren seals the snapped shoring pin inside a numbered evidence bag.\n"
    )
    assert outline_gen._outline_has_prose_leak(crisp_outline) is False

    # 2. editor_pass._salvage_editor_json recovers broken unescaped-quote JSON cleanly
    broken_raw = (
        '{"verdict": "Add spoken friction in Ch 2.", '
        '"weakest_sentences": ["Line with "inner quote" here"], '
        '"ai_patterns": ["rain intensified twice"], '
        '"continuity_violations": ["Ruth called Tess Mr. Navarro"], '
        '"missed_beats": [], "over_explained_subtext": [], "flat_scenes": [], '
        '"what_to_keep": ["Strong tool crib detail"], '
        '"concrete_revisions": ["Replace summary with dialogue."]}'
    )
    salv = editor_pass._salvage_editor_json(broken_raw)
    assert salv is not None and salv["parse_failed"] is False
    assert "Ch 2" in salv["verdict"]
    assert "Ruth called Tess" in salv["continuity_violations"][0]

    # 3. canon_gen.append_from_chapter isolates prompt from EXISTING CANON & deduplicates near-identical bullets
    b = create_book(
        title="The Astoria Slipway Ledger",
        idea="Present-day Astoria, Oregon.",
        book_format=BookFormat.novella,
        pov=POV.first_person,
        chapter_count=6,
    )
    bdir = book_dir(b.id)
    atomic_write_text(bdir / "seed.txt", "Present-day Astoria, Oregon. Maren Vasquez investigates.")
    atomic_write_text(bdir / "canon.md", "## Hard Constraints\n- Ray Kowalczyk signed a false work order for Slip 2.\n")
    canon_users: list[str] = []

    def _mock_iter9(task: str, sys: str, usr: str) -> str:
        if task == "canon":
            canon_users.append(usr)
            return "- Ray Kowalczyk signed the false work order claiming Slip 2 pins were replaced.\n- Maren sealed the snapped pin into an evidence bag."
        if task == "kdp":
            if "24–48 characters EACH" in sys:
                return '{"keywords": ["pacific northwest boatyard murder mystery", "working waterfront marine claims investigation", "oregon coast crane mechanic bribery whodunit", "standalone coastal detective novella book"]}'
            return '{"subtitle": "An Astoria Boatyard Mystery", "blurb_html": "<p>Maren Vasquez checks Slip 2.  Two days remain.</p>", "keywords": ["maritime thriller", "boatyard suspense", "oregon coastal murder investigation", "marine insurance adjuster detective"], "categories": ["FICTION / Mystery & Detective / Private Investigators"], "comp_titles": ["Raven Black by Ann Cleeves"], "age_range": "Adult", "cover_art_brief": "Snapped steel pin on wet concrete."}'
        return "Clean."

    install_mock_llm(monkeypatch, _mock_iter9)
    updated_canon = asyncio.run(canon_gen.append_from_chapter(b, bdir, 1, "Maren bagged the pin at Slip 2."))
    assert canon_users and "EXISTING CANON" not in canon_users[0]
    assert "Maren sealed the snapped pin" in updated_canon
    assert "claiming Slip 2 pins were replaced" not in updated_canon

    # 4. kdp_kit expands <20-char keywords via object JSON while preserving valid >=20-char ones & strips double spaces
    kit = asyncio.run(kdp_kit.generate(b, bdir))
    assert "  " not in kit["blurb_html"]
    assert len(kit["keywords"]) >= 5
    assert all(len(kw) >= 20 for kw in kit["keywords"])
    assert "oregon coastal murder investigation" in kit["keywords"]


def test_iter10_future_spine_fence_summary_restore_editor_strip_and_kdp_guards(monkeypatch):
    from app.pipeline import editor_pass, kdp_kit, summary_gen
    from app.storage import read_text

    seed_txt = "Present-day Newport, Oregon. Hannah Morales (she/her) investigates Kenji Sato (he/him, deceased)."
    b = create_book(
        title="The Newport Slipway Ledger",
        book_format=BookFormat.novella,
        genre="Coastal Mystery",
        pov=POV.first_person,
        idea=seed_txt,
        chapter_count=6,
        target_words=8400,
    )
    b.front_matter.author_name = "D. L. Brooks"
    save_book(b)
    bdir = book_dir(b.id)
    atomic_write_text(bdir / "seed.txt", seed_txt)
    atomic_write_text(
        bdir / "characters.md",
        "- Hannah Morales (38, she/her)\n- Owen Callahan (45, he/him)\n- Kenji Sato (Deceased, 42, he/him)",
    )
    atomic_write_text(
        bdir / "outline.md",
        "## Plot Logic & Resolution Spine\n"
        "1. **Protagonist & Cast:** Hannah Morales (38), Owen Callahan (45).\n"
        "2. **Exact Crime:** Kenji Sato drowned at 10:15 p.m.\n"
        "3. **Causal Clue Chain:** Cashier's check ties Halvorsen; feeler gauge sits in locker.\n"
        "4. **Named Culprits:** Halvorsen and Novak arrested together.\n\n"
        "### Ch 1: Monday Morning\n- POV: Hannah Morales\n- Inspect pin.\n"
        "### Ch 6: Slipway Reckoning\n- POV: Hannah Morales\n- Final arrests.\n\n"
        "## Foreshadowing Ledger\n"
        "| Item / Clue | Planted (Ch) | Reinforced (Ch) | Paid Off (Ch) |\n"
        "|---|---|---|---|\n"
        "| Cashier's Check | 4 | — | 6 |\n"
        "| Brass Feeler Gauge | 5 | — | 6 |\n",
    )
    sync_chapters(b, read_text(bdir / "outline.md"))
    save_book(b)

    # 1. Early context injects FUTURE CLUES fence and strips Causal Clue Chain / Culprit bullets from Spine
    ctx1, _ = context_builder.build_chapter_context(b, bdir, 1)
    assert "FUTURE CLUES — STRICTLY FORBIDDEN IN CHAPTER 1" in ctx1
    assert "**Cashier's Check**" in ctx1 and "**Brass Feeler Gauge**" in ctx1
    assert "Halvorsen and Novak arrested together" not in ctx1

    # 2. _strip_voice_specimens removes unrelated bicycle/bean dummy paragraphs before editor_pass
    voice_raw = (
        "## Part 2 — Project Voice Calibration\n### Rhythm & Syntax Rules\n- Crisp concrete prose.\n\n"
        "### Unrelated Style Specimen\n[UNRELATED STYLE SPECIMEN — RHYTHM DEMO ONLY]\nBeans spilled into a plastic colander.\n\n"
        "### AI-Tic Anti-Examples (Do -> Instead)\n- Bad tire sentence.\n\n"
        "### Character Speech Contrasts\n- Owen speaks plainly.\n"
    )
    stripped_v = editor_pass._strip_voice_specimens(voice_raw)
    assert "plastic colander" not in stripped_v and "Bad tire" not in stripped_v
    assert "Owen speaks plainly." in stripped_v

    # 3. summary_gen restores truncated/macronized 'Kenji Sat' / 'Kenji Satō' -> 'Kenji Sato'
    def _mock_iter10(task: str, sys: str, usr: str) -> str:
        if task == "summary":
            return '{"summary": "Hannah Morales inspected Slip 2 after Kenji Sat drowned; Owen checked Kenji Satō\'s locker.", "state": "dead=Kenji Sat"}'
        if task == "kdp":
            if "FIXES REQUIRED" in usr:
                return '{"subtitle": "A Newport Boatyard Mystery", "blurb_html": "<p>Plain concrete blurb without clichés.</p>", "keywords": ["oregon coastal whodunit mystery story", "marine claims adjuster investigation book", "pacific northwest boatyard sabotage crime", "small town working waterfront detective", "shoring pin maritime fraud whodunit", "standalone coastal mystery novella series", "independent investigator harborfront murder"], "categories": ["FICTION / Mystery & Detective / Private Investigators", "FICTION / Sea Stories", "FICTION / Small Town & Rural"], "comp_titles": ["Raven Black by Ann Cleeves"], "age_range": "Adult", "cover_art_brief": "Notched steel pin on wet concrete, author name D. L. Brooks."}'
            if "24–48 characters EACH" in sys:
                return '{"keywords": ["oregon coastal whodunit mystery story", "marine claims adjuster investigation book", "pacific northwest boatyard sabotage crime", "small town working waterfront detective", "shoring pin maritime fraud whodunit", "standalone coastal mystery novella series", "independent investigator harborfront murder"]}'
            return '{"subtitle": "A Newport Boatyard Mystery", "blurb_html": "<p>Kenji’s death isn\'t just an accident—it\'s a scheme with roots reaching far beyond.</p>", "keywords": ["bayou crime thriller", "waterfront disappearance", "pacific northwest fiction", "pacific northwest suspense", "maritime investigation", "northwest coastal crimes", "shipyard accident investigation"], "categories": ["FICTION / Sea Stories"], "comp_titles": ["An Expert in Murder by Josephine Tey", "Maisie Dobbs by Jacqueline Winspear"], "age_range": "Adult", "cover_art_brief": "Pin on concrete. Top title The Newport Slipway Ledger, author name \'Hannah Morales\'."}'
        return "Clean."

    install_mock_llm(monkeypatch, _mock_iter10)
    sum_md = asyncio.run(summary_gen.update(b, bdir, 1, "I examined the snapped pin."))
    assert "Kenji Sato drowned" in sum_md and "Kenji Sato's locker" in sum_md
    assert "Kenji Sat " not in sum_md and "Satō" not in sum_md

    # 4. kdp_kit enforces D. L. Brooks cover author, strips HTML from kdp_kit.md, cleans hallucinated bayou/thriller/Josephine Tey comps
    kit = asyncio.run(kdp_kit.generate(b, bdir))
    assert "D. L. Brooks" in kit["cover_art_brief"] and "Hannah Morales" not in kit["cover_art_brief"]
    assert not any("bayou" in kw.lower() or "disappearance" in kw.lower() or "thriller" in kw.lower() for kw in kit["keywords"])
    assert not any("josephine tey" in c.lower() or "maisie dobbs" in c.lower() for c in kit["comp_titles"])
    assert "Raven Black by Ann Cleeves" in kit["comp_titles"]
    md_out = read_text(bdir / "kdp_kit.md")
    assert "<p>" not in md_out and "<strong>" not in md_out


def test_iter11_canon_no_outline_and_kdp_7kw_and_author(monkeypatch):
    from app.pipeline import kdp_kit
    from app.store import FrontMatter

    b = create_book(
        title="The Newport Slipway Ledger",
        book_format=BookFormat.novella,
        pov=POV.first_person,
        genre="Cozy Maritime Mystery",
        idea="Present-day Newport, Oregon. Ms. Hannah Morales investigates Kenji Sato's drowning.",
        chapter_count=6,
        target_words=8400,
        front_matter=FrontMatter(author_name="D. L. Brooks"),
    )
    save_book(b)
    bdir = book_dir(b.id)
    atomic_write_text(bdir / "seed.txt", b.idea)
    atomic_write_text(bdir / "world.md", "### Slipway Dock\nWorking yard in Oregon.")
    atomic_write_text(bdir / "characters.md", "- **Hannah Morales** (she/her): Adjuster")
    atomic_write_text(bdir / "outline.md", "## Plot Logic\nSPOILER_CLUE_FROM_OUTLINE: $5,000 check.\n### Ch 1: Monday\n- Beat\n")

    canon_prompts: list[str] = []
    kdp_calls: list[str] = []

    def _mock_iter11(task: str, sys: str, usr: str) -> str:
        if task == "canon":
            canon_prompts.append(usr)
            return "## World Rules\n- Working dock\n## Characters & Possessions\n- Hannah Morales\n## Timeline & Ages\n- Monday morning\n## Hard Constraints\n- Day-1 only"
        if task == "kdp":
            kdp_calls.append(sys + "\n" + usr)
            if "FIXES REQUIRED" in usr:
                return (
                    '{"subtitle": "A Pacific Coast Harbor Mystery", '
                    '"blurb_html": "<p>Hannah checks the snapped pin while rain hammers the concrete apron.</p>", '
                    '"keywords": ["newport waterfront maritime investigation", "oregon coastal whodunit story", "pacific northwest marine adjuster"], '
                    '"categories": ["FICTION / Mystery & Detective / Amateur Sleuth", "FICTION / Sea Stories"], '
                    '"comp_titles": ["Long Bright River by Liz Moore", "Raven Black by Ann Cleeves"], '
                    '"age_range": "Adult", "cover_art_brief": "Steel shoring pin on concrete, author D. L. Brooks."}'
                )
            if "2 NEW" in sys:
                return (
                    '{"keywords": ['
                    '"working marina fraud investigation", '
                    '"rainy northwest harborfront sleuth"'
                    ']}'
                )
            if "ALREADY ACCEPTED KEYWORDS" in usr:
                return (
                    '{"keywords": ['
                    '"small town working boatyard detective", '
                    '"pacific northwest claims fraud mystery", '
                    '"independent investigator harborfront crime", '
                    '"commercial fishing port amateur sleuth", '
                    '"coastal oregon timber dockyard whodunit"'
                    ']}'
                )
            return (
                '{"subtitle": "A Pacific Coast Harbor Mystery", '
                '"blurb_html": "<p>A quiet inquiry unfolds into harbor corruption before another tragedy strikes.</p>", '
                '"keywords": ["newport harbor mystery novel", "waterfront thriller", "oregon mystery"], '
                '"categories": ["FICTION / Mystery & Detective / Amateur Sleuth"], '
                '"comp_titles": ["Long Bright River by Liz Moore"], '
                '"age_range": "Adult", "cover_art_brief": "Pin on concrete."}'
            )
        return "Ok."

    install_mock_llm(monkeypatch, _mock_iter11)
    asyncio.run(canon_gen.generate(b, bdir))
    assert len(canon_prompts) == 1
    assert "SPOILER_CLUE_FROM_OUTLINE" not in canon_prompts[0]

    kit = asyncio.run(kdp_kit.generate(b, bdir))
    assert len(kdp_calls) == 4
    assert "unfolds" in kdp_calls[1].lower()
    assert len(kit["keywords"]) == 7
    assert all(20 <= len(kw) <= 50 and "newport" not in kw.lower() and "novel" not in kw.lower() for kw in kit["keywords"])
    assert not any("long bright river" in c.lower() for c in kit["comp_titles"])
    md_out = read_text(bdir / "kdp_kit.md")
    assert "**Author:** D. L. Brooks" in md_out


def test_iter12_non_mystery_genre_voice_outline_revise_audit_and_kdp(monkeypatch):
    from app.pipeline import kdp_kit, outline_gen, scene_planner, voice_gen
    from app.pipeline._common import is_mystery_genre
    from app.store import FrontMatter

    # 1. is_mystery_genre classifies non-mystery drama vs mystery accurately
    b_drama = create_book(
        title="The Cedar Hull Accord",
        book_format=BookFormat.novella,
        pov=POV.first_person,
        genre="Literary Coastal Drama",
        idea=(
            "Present-day Depoe Bay, Oregon. Ms. Clara Mercer (she/her, age 34) rebuilds Tillicum with brother Eli Mercer (he/him, age 29). "
            "Zero crime, murder, or police subplots. "
            + ("Tactile wooden boat repair details. " * 105)
            + "END_OF_SEED_MARKER_3800: Nolan clears the check and Eli signs the 50/50 partnership ledger."
        ),
        chapter_count=6,
        target_words=8400,
        front_matter=FrontMatter(author_name="D. L. Brooks"),
    )
    save_book(b_drama)
    bdir = book_dir(b_drama.id)
    atomic_write_text(bdir / "seed.txt", b_drama.idea)
    assert not is_mystery_genre(b_drama, b_drama.idea)

    b_myst = create_book(
        title="The Newport Slipway Ledger",
        book_format=BookFormat.novella,
        pov=POV.first_person,
        genre="Coastal Mystery",
        idea="Hannah Morales investigates David Park's drowning.",
    )
    assert is_mystery_genre(b_myst, b_myst.idea)

    atomic_write_text(bdir / "world.md", "Depoe Bay wooden boatworks yard.")
    atomic_write_text(bdir / "characters.md", "- **Clara Mercer** (34, she/her): Shipwright\n- **Eli Mercer** (29, he/him): Carpenter")
    atomic_write_text(bdir / "canon.md", "## World Rules\nDepoe Bay\n## Timeline & Ages\nMonday Oct 7.")
    long_ch = "Steam rose off the cedar plank.\n\n" + ("Eli planed the scarf joint carefully by lamplight. " * 140) + "\n\nFINAL_CLOSING_BEAT"
    atomic_write_text(bdir / "chapters" / "ch_006.md", long_ch)

    calls: list[tuple[str, str, str]] = []

    def _mock_iter12(task: str, sys: str, usr: str) -> str:
        calls.append((task, sys, usr))
        if task == "outline":
            return (
                "## Plot Logic & Resolution Spine\n1. Cast: Clara, Eli\n2. Dilemma: dry rot\n3. Chain: repair\n4. Resolution: partnership ledger\n5. Lock: Tillicum\n\n"
                "### Ch 1: Cradle\n- POV: Clara Mercer\n- Setting & Time: Mon\n- Opening Hook Word: Oak\n- Inspect hull.\n**Chapter Ending State:** Stripped strake.\n"
                "### Ch 6: Launch\n- POV: Clara Mercer\n- Setting & Time: Fri\n- Opening Hook Word: Dawn\n- Launch boat and co-sign ledger.\n**Chapter Ending State:** Open shop doors.\n"
            )
        if task == "scenes":
            return '{"chapter": 6, "scenes": [{"title": "Launch", "word_budget": 1400, "location": "Ramp", "time": "Fri AM", "pov": "Clara Mercer", "cast": ["Clara", "Eli"], "start_state": "Dawn", "goal": "Launch", "turn": "Check", "outcome": "Ledger", "hard_stop": "Doors open", "illustration_prompt": ""}]}'
        if task == "voice":
            if "3 genuinely distinct" in sys:
                return "### Trial 1 — Grounded Realism\nI knelt beside the plank.\n### Trial 2 — Spare\nSawdust stuck to my sleeve.\n### Trial 3 — Warm\nEli tossed me the rag."
            return (
                "## Part 2 — Project Voice Calibration\nChosen register: Grounded Realism\n\n"
                "### Rhythm & Syntax Rules\n- concrete sentence-length, punctuation, and sensory priorities\n"
                "- Utilize active verbs and keep contractions natural.\n\n"
                "### Unrelated Style Specimen\n[UNRELATED STYLE SPECIMEN — RHYTHM DEMO ONLY]\nBeans rattled in a bowl.\n\n"
                "### AI-Tic Anti-Examples (Do -> Instead)\n- Old -> New.\n\n"
                "### Character Speech Contrasts\n- how each major character in this cast differs in cadence, diction, and evasion\n"
                "- Clara speaks crisply.\n\n"
                "### Vocabulary Wells\n- 20–35 concrete sensory, trade, weather, or regional words drawn STRICTLY from Seed and World Bible\n"
                "1. Strake\n2. Oak"
            )
        if task == "revise":
            return "Clara bent over the transom frame, checking every bronze bolt before breakfast."
        if task == "audit":
            return '{"publishability_verdict": "ready", "executive_summary": "Clean.", "strengths": [], "structural_issues": [], "continuity_risks": [], "ai_style_patterns": [], "top_fixes": []}'
        if task == "kdp":
            if "FIXES REQUIRED" in usr:
                return (
                    '{"subtitle": "A Depoe Bay Boatyard Story", '
                    '"blurb_html": "<p>Clara Mercer fights to launch a cedar double-ender before Friday tide.</p>", '
                    '"keywords": ["oregon working waterfront drama", "wooden boatbuilding siblings tale", "pacific northwest harbor fiction", "commercial shipwright family debt", "brother sister reconciliation book", "contemporary coastal sea fiction", "timber boat restoration novella"], '
                    '"categories": ["FICTION / Mystery & Detective / Traditional", "FICTION / Literary", "FICTION / Sea Stories"], '
                    '"comp_titles": ["The Dry by Jane Harper", "Plainsong by Kent Haruf"], '
                    '"age_range": "Adult", "cover_art_brief": "Cedar plank by D. L. Brooks."}'
                )
            return (
                '{"subtitle": "A Depoe Bay Boatyard Story", '
                '"blurb_html": "<p>She uncovers more than just dry rot—everything she holds dear hangs precariously in the balance.</p>", '
                '"keywords": ["oregon working waterfront drama", "wooden boatbuilding siblings tale", "pacific northwest harbor fiction", "commercial shipwright family debt", "brother sister reconciliation book", "contemporary coastal sea fiction", "timber boat restoration novella"], '
                '"categories": ["FICTION / Mystery & Detective / Traditional"], '
                '"comp_titles": ["Raven Black by Ann Cleeves"], '
                '"age_range": "Adult", "cover_art_brief": "Cedar plank by D. L. Brooks."}'
            )
        return "Ok."

    install_mock_llm(monkeypatch, _mock_iter12)

    out_md = asyncio.run(outline_gen.generate(b_drama, bdir))
    assert "### Ch 1" in out_md
    atomic_write_text(bdir / "outline.md", out_md)
    sync_chapters(b_drama, out_md)
    save_book(b_drama)
    outline_calls = [c for c in calls if c[0] == "outline"]
    assert "Miranda rights" not in outline_calls[0][1]
    assert "END_OF_SEED_MARKER_3800" in outline_calls[-1][2]

    asyncio.run(scene_planner.generate_scenes(b_drama, bdir, 6))
    sp_calls = [c for c in calls if c[0] == "scenes"]
    assert "joint arrest" not in sp_calls[-1][1]
    assert "END_OF_SEED_MARKER_3800" in sp_calls[-1][2]

    trials_md = asyncio.run(voice_gen.trials(b_drama, bdir))
    assert "### Trial 1" in trials_md
    v_trials_sys = [c[1] for c in calls if c[0] == "voice"][0]
    assert "Surveyor's Report" in v_trials_sys and "Fahrenheit" in v_trials_sys
    voice_md = asyncio.run(voice_gen.generate(b_drama, bdir, choice=1))
    assert "- concrete sentence-length" not in voice_md
    assert "how each major character" not in voice_md
    assert "Use active verbs" in voice_md and "Utilize" not in voice_md

    draft_tics = "I walked—slowly—and felt uneasy, then felt cold again—too cold—at dusk."
    asyncio.run(chapter_revise.generate(b_drama, bdir, 6, current_text=draft_tics, notes="Clean up."))
    rev_calls = [c for c in calls if c[0] == "revise"]
    assert "DETECTED AI-TICS & MECHANICS TO CLEAN IN THIS PASS" in rev_calls[-1][2]

    asyncio.run(manuscript_audit.run_full_audit(b_drama, bdir))
    aud_calls = [c for c in calls if c[0] == "audit"]
    assert "END_OF_SEED_MARKER_3800" in aud_calls[-1][2] and "MID:" in aud_calls[-1][2]

    kit = asyncio.run(kdp_kit.generate(b_drama, bdir))
    kdp_calls = [c for c in calls if c[0] == "kdp"]
    assert len(kdp_calls) == 2
    assert "more than just" in kdp_calls[1][2].lower()
    assert not any("mystery" in c.lower() for c in kit["categories"])
    assert not any("jane harper" in c.lower() or "ann cleeves" in c.lower() for c in kit["comp_titles"])
    assert "The Shipping News by Annie Proulx" in kit["comp_titles"]



