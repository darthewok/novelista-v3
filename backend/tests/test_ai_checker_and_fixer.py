from __future__ import annotations

from app.enums import Audience, BookFormat, WritingStyle
from app.pipeline import ai_checker, slop_fix
from app.store import create_book
from tests._llm_mock import install_mock_llm


def test_silent_e_inflections_caught():
    text = (
        "Harlan spent the morning utilizing every copper wire in the shed, synergizing the old relays "
        "and catalyzing a fresh spark before anyone noticed."
    )
    rep = ai_checker.scan(text)
    hits = [f for f in rep["findings"] if f["rule"] == "tier1_slop"]
    matched = {f["quote"].lower() for f in hits}
    assert {"utilizing", "synergizing", "catalyzing"} <= matched


def test_physical_weight_and_fantasy_realm_exempt():
    book = create_book(title="Oath of Ash", writing_style=WritingStyle.high_epic, book_format=BookFormat.novel)
    text = (
        "The weight of the brass ring pulled at Bram's pocket while he studied his sense of direction "
        "along the border of the northern realm. Thick rain rattled against the slate roof and soaked his cloak "
        "before the ferryman swung the iron gate shut. Every lantern along the pier sputtered in the gust, "
        "and Bram counted three coins onto the wet planks without asking the ferryman's name. Down below the "
        "pilings, dark water slapped against weathered timber and carried old kelp out toward the channel."
    )
    # Pad past min_unit_words=500? Or scan without book min word error:
    rep = ai_checker.scan(text, book=book)
    t1_quotes = [f["quote"].lower() for f in rep["findings"] if f["rule"] == "tier1_slop"]
    assert not t1_quotes


def test_dialogue_exemption_for_filters_and_pov():
    book = create_book(title="Test", book_format=BookFormat.short_story)
    narration = " ".join(["Marta stacked clean saucers behind the counter while steam drifted from the kettle."] * 35)
    dialogue = '"Honestly, I felt terrified and Dov knew everything," Marta said, tapping the rim.'
    rep = ai_checker.scan(f"{narration}\n\n{dialogue}", book=book)
    rules = {f["rule"] for f in rep["findings"]}
    assert "emotion_naming" not in rules
    assert "pov_break" not in rules


def test_token_meltdown_script_and_meta_leaks():
    long_tok = "schreiberiikriechbaueriirennertsiereichsteinschlottfriedhiller"
    text = f"Marta opened the door.\n\n*Plant 3.*\n\nThe冰冷的 handle clicked.\n\n{long_tok}\n\nWait—I wrote myself into confusion."
    rep = ai_checker.scan(text)
    rules = {f["rule"] for f in rep["findings"]}
    assert "token_meltdown" in rules
    assert "script_leak" in rules
    assert "meta_leak" in rules
    assert rep["cleanliness_grade"] == "F"


def test_negation_contrast_and_staccato_fragments():
    text = (
        "Silence returned. Heavy. Wet. Cold. "
        "He did not run from the courtyard. He stood near the wall. "
        "Not out of courage. Out of spite. "
        "It wasn't anger; it was relief."
    )
    rep = ai_checker.scan(text)
    rules = {f["rule"] for f in rep["findings"]}
    assert "negation_contrast" in rules
    assert "staccato_fragments" in rules


def test_slop_fix_long_sentence_no_truncation_corruption(monkeypatch):
    # Sentence intentionally > 220 chars so v2's 160-char slice bug would have left dangling tail garbage!
    long_bad = (
        "Harlan paused beside the rusted boiler at the rear of the damp greenhouse where water dripped down "
        "the glass panes in uneven silver tracks, and he couldn't help but delve into the tangled archive "
        "of forged receipts that seemed to whisper old secrets across every dusty shelf in the room."
    )
    assert len(long_bad) > 220
    full_text = f"Morning light crossed the gravel yard.\n\n{long_bad}\n\nOutside, a bicycle bell rang twice."
    rep = ai_checker.scan(full_text)
    assert any(f["rule"] == "tier1_slop" for f in rep["findings"])

    replacement = (
        "Harlan paused beside the rusted boiler in the greenhouse and spread the forged receipts across an empty seed tray."
    )
    install_mock_llm(monkeypatch, lambda task, sys, usr: replacement)

    fixed, fix_rep = slop_fix.fix(full_text, rep) if False else __import__("asyncio").run(slop_fix.fix(full_text, rep))
    assert fix_rep["rewritten_spans"] == 1
    assert "tangled archive" not in fixed
    assert "dusty shelf in the room" not in fixed
    assert replacement in fixed
    assert "Morning light crossed the gravel yard." in fixed
    assert "Outside, a bicycle bell rang twice." in fixed


def test_slop_fix_one_surgical(monkeypatch):
    text = "The lantern guttered. Clara chose to delve into the ledger before dawn. Rain pattered outside."
    rep = ai_checker.scan(text)
    hit = next(f for f in rep["findings"] if f["rule"] == "tier1_slop")
    install_mock_llm(monkeypatch, lambda task, sys, usr: "Clara turned open the ledger before dawn.")
    out, changed = __import__("asyncio").run(
        slop_fix.fix_one(
            text,
            start_char=hit["start_char"],
            end_char=hit["end_char"],
            message=hit["message"],
            rule=hit["rule"],
            quote=hit["quote"],
        )
    )
    assert changed is True
    assert "delve" not in out
    assert out == "The lantern guttered. Clara turned open the ledger before dawn. Rain pattered outside."


def test_readability_mismatch_children():
    pb = create_book(title="Tiny Fox", book_format=BookFormat.picture_book, audience=Audience.picture_book_3_7)
    academic = (
        "Notwithstanding the overwhelming meteorological unpredictability characterizing the subterranean ecosystem, "
        "Barnaby systematically contemplated epistemological ramifications regarding botanical photosynthesis "
        "throughout extraordinarily complicated nocturnal expeditions."
    ) * 4
    rep = ai_checker.scan(academic, book=pb)
    assert any(f["rule"] == "readability_mismatch" for f in rep["findings"])


def test_normalize_prose_typography_preserves_headings_and_rules():
    raw = (
        "## Chapter 1 - Harbor Fog\n\n"
        "“We’re late,” Arthur said – checking his brass watch - while rain pelted ‘Boston’ pier.\n\n"
        "---\n\n"
        "“Not yet,” Clara replied."
    )
    out = slop_fix.normalize_prose_typography(raw)
    assert out.startswith("## Chapter 1 - Harbor Fog\n")
    assert '"We\'re late," Arthur said - checking his brass watch - while rain pelted \'Boston\' pier.' in out
    assert "—" not in out and "–" not in out
    assert "\n---\n" in out
    assert "“" not in out and "”" not in out and "’" not in out


def test_ai_checker_post_book1_rules_and_dialogue_pct():
    from app.storage import atomic_write_text
    from app.store import book_dir

    book = create_book(
        title="The Silversmith",
        idea="Arthur Hale investigates forged manifests with Clara in 1924 Boston.",
        book_format=BookFormat.novella,
    )
    bdir = book_dir(book.id)
    shared_sent = (
        "Steam hammered through the cast-iron pipes beneath Dock Street before dawn shook the shutters."
    )
    atomic_write_text(bdir / "outline.md", f"### Ch 1: Opening\n- {shared_sent}\n")

    prose = (
        f"## Chapter 1\n\n"
        f"{shared_sent}\n\n"
        "\"We are opening the vault early,\" he said briskly. "
        "\"Wait here,\" she whispered softly. "
        "\"I never met her,\" Arthur said Emily nodding nearby. "
        "It wasn't rust; it was dried paint. "
        "Something deeper hummed behind the floorboards, something unspoken settled between them.\n\n"
        "Silas Thorne stood outside the warehouse while “mixed” curly quotes appeared.\n\n"
        "“Bring the ledger down to the wharf before the tide turns and the harbormaster locks the gate,” Arthur urged."
    )
    rep = ai_checker.scan(prose, book=book, bdir=bdir, chapter_num=1)
    rules = {f["rule"] for f in rep["findings"]}
    assert "mixed_quotes" in rules
    assert "adverb_dialogue_tags" in rules
    assert "vague_something" in rules
    assert "cliche_ai_name" in rules
    assert "negation_contrast" in rules
    assert "exemplar_copy" in rules
    assert rep["metrics"]["dialogue_pct"] > 20.0
    adv_quotes = [f["quote"] for f in rep["findings"] if f["rule"] == "adverb_dialogue_tags"]
    assert not any("Emily" in q for q in adv_quotes)

    # Seed-requested name should NOT trigger cliche_ai_name
    seeded_book = create_book(title="Thorne's Watch", idea="Detective Silas leads the case.")
    s_rep = ai_checker.scan("Silas Thorne checked the lock on the wharf.", book=seeded_book)
    assert "cliche_ai_name" not in {f["rule"] for f in s_rep["findings"]}


def test_normalize_typography_wordcount_and_spaced_dashes():
    raw = (
        "## Chapter 1 - Boston Wharf\n\n"
        "Above the door hung a sign—\"North Star Pawn\" – painted in gold leaf.\n"
        "I checked the casing -- a heavy brass rim.\n\n"
        "---\n\n"
        "(Word count: 1624)\n"
    )
    out = slop_fix.normalize_prose_typography(raw)
    assert out.startswith("## Chapter 1 - Boston Wharf\n")
    assert 'sign - "North Star Pawn" - painted in gold leaf.' in out
    assert "casing - a heavy brass rim." in out
    assert "—" not in out and "–" not in out and "--" not in out.replace("---", "")
    assert "\n---\n" in out
    assert "(Word count:" not in out


def test_slop_fix_rejects_context_leak_and_ballooning(monkeypatch):
    import asyncio

    before_ctx = "Harbor fog drifted over the granite pier as ferry bells tolled across East Boston."
    target_sent = "Arthur chose to delve into the ledger inside the dry warehouse."
    after_ctx = "Officer Davies stepped beside the iron winch and folded his oilskin coat tightly."
    full_text = f"{before_ctx}\n\n{target_sent} {after_ctx}"
    rep = ai_checker.scan(full_text)
    assert any(f["rule"] == "tier1_slop" for f in rep["findings"])

    # Mock LLM echoing `after_ctx` verbatim + truncating with ellipsis (like real Book 2 corruption bug!)
    leaky_reply = "Arthur opened the ledger inside the warehouse. Officer Davies stepped beside the iron winch and folded..."
    install_mock_llm(monkeypatch, lambda t, s, u: leaky_reply)
    fixed_leak, frep1 = asyncio.run(slop_fix.fix(full_text, rep))
    assert frep1["rewritten_spans"] == 0
    assert frep1["skipped_spans"] == 1
    assert fixed_leak == full_text

    # Mock LLM ballooning > 1.9x without overlap -> also rejected cleanly
    balloon_reply = " ".join(["Arthur turned open the calfskin ledger and examined every smudge of coal soot along the margin."] * 4)
    install_mock_llm(monkeypatch, lambda t, s, u: balloon_reply)
    fixed_bal, frep2 = asyncio.run(slop_fix.fix(full_text, rep))
    assert frep2["rewritten_spans"] == 0
    assert fixed_bal == full_text


def test_ai_checker_ignores_brief_matches_and_flags_meta_and_dashes():
    from app.storage import atomic_write_text
    from app.store import book_dir, brief_path

    book = create_book(title="Watchmaker", book_format=BookFormat.novella)
    bdir = book_dir(book.id)
    brief_line = "Arthur Hale examined the chipped porcelain dial with a jeweler's loupe before sunrise."
    atomic_write_text(brief_path(bdir, 1), f"## WHAT TO CHANGE\n- With: '{brief_line}'\n")

    dash_sents = " ".join(
        [
            f"Entry number {i} listed an old ship—a timber brigantine bound for port with salt cod and oak staves piled high on deck."
            for i in range(1, 9)
        ]
    )
    prose = (
        f"## Chapter 1\n\n{brief_line}\n\n"
        f"{dash_sents}\n\n"
        "Outside the shop, delivery wagons rattled down the damp cobblestones.\n\n"
        "(Word count: 1042)"
    )
    rep = ai_checker.scan(prose, book=book, bdir=bdir, chapter_num=1)
    rules = {f["rule"] for f in rep["findings"]}
    assert "exemplar_copy" not in rules
    assert "meta_leak" in rules
    assert "em_dash_density" in rules
    assert "em_dash_overuse" in rules
    em_spans = [f for f in rep["findings"] if f["rule"] == "em_dash_overuse"]
    assert all(f["end_char"] > f["start_char"] for f in em_spans)


def test_iter3_typography_quote_guard_and_extended_checker(monkeypatch):
    import asyncio

    raw_messy = (
        "## Chapter 2 - The Pier\n\n"
        "\"Move along,\" he said–not looking back--toward the shed..\n\n"
        "(932 words)\n\n"
        "\"Wait…\" she called.\n\n"
        "(1837 Words)"
    )
    clean_typ = slop_fix.normalize_prose_typography(raw_messy)
    assert "(932 words)" not in clean_typ and "(1837 Words)" not in clean_typ
    assert "said - not looking back - toward the shed." in clean_typ
    assert "—" not in clean_typ and "–" not in clean_typ
    assert '"Wait..." she called.' in clean_typ
    assert ".." not in clean_typ.replace("...", "")

    assert "exemplar_copy" in slop_fix.AUTO_FIX_RULES

    # Reject rewrite that drops quotes on dialogue span
    q_text = 'He took the brass key. "I chose to delve into the harbor vault last night," Salty admitted. Rain hit the shutters.'
    q_rep = ai_checker.scan(q_text)
    assert any(f["rule"] == "tier1_slop" for f in q_rep["findings"])
    install_mock_llm(monkeypatch, lambda t, s, u: "I checked the harbor vault last night, Salty admitted.")
    fixed_q, q_meta = asyncio.run(slop_fix.fix(q_text, q_rep))
    assert q_meta["rewritten_spans"] == 0
    assert fixed_q == q_text

    # Multiple filter_words spans + (932 words) meta_leak + 1924 anachronism + American English UK-term + unbalanced_quotes
    period_book = create_book(
        title="Boston Ledger",
        idea="Boston, October 1924 (American English throughout). Watchmaker Arthur Hale hunts a ledger.",
        book_format=BookFormat.novella,
    )
    lines = [
        "Arthur walked down Hanover Street before breakfast cleanly.",
        "I felt the cold fog damp against my collar while horses pulled wagons past Dock Square.",
        "I seemed to trace the mud prints along the timber wharf beside the berth.",
        "I realized that nobody had locked the cargo shed overnight at pier seven.",
        "I watched as three men carried tea boxes under the fluorescent lights near the breakroom.",
        "I heard the sound of waves slapping the pilings beneath the grey constable boat.",
        "I noticed a torn ledger page lying beside the rusted barrel hoops.",
        "(932 words)",
        "\"Wait at the gate before the tide rises, Arthur shouted across the alley.",
    ]
    # Expand past 400 words safely without duplication
    filler = " ".join([f"Line number {k} recorded brass gears, oilcans, and timber crates at berth {k}." for k in range(1, 30)])
    sample = "\n\n".join(lines) + "\n\n" + filler
    rep2 = ai_checker.scan(sample, book=period_book)
    rules2 = {f["rule"] for f in rep2["findings"]}
    assert "meta_leak" in rules2
    assert "anachronism" in rules2
    assert "regional_spelling" in rules2
    assert "unbalanced_quotes" in rules2
    uq_finding = next(f for f in rep2["findings"] if f["rule"] == "unbalanced_quotes")
    assert uq_finding["end_char"] > uq_finding["start_char"]
    filter_findings = [f for f in rep2["findings"] if f["rule"] == "filter_words"]
    assert len(filter_findings) >= 4

    assert {"unbalanced_quotes", "regional_spelling"} <= slop_fix.OPTIONAL_STYLE_RULES

    ast_raw = (
        "I recalled trusting instincts.*\n\n"
        "* * *\n\n"
        "She checked the *Harbinger* manifest carefully."
    )
    ast_clean = slop_fix.normalize_prose_typography(ast_raw)
    assert ast_clean == (
        "I recalled trusting instincts.\n\n"
        "* * *\n\n"
        "She checked the *Harbinger* manifest carefully."
    )


def test_iter4_typography_fence_uk_and_fixer_priority(monkeypatch):
    import asyncio
    from app.pipeline._common import strip_llm_chatter
    from app.pipeline import scene_planner
    from app.storage import atomic_write_text
    from app.store import book_dir

    assert "copied_text" in slop_fix.AUTO_FIX_RULES

    messy = (
        "## Chapter 2: Pier Five\n\n"
        "He walked  towards the grey shed amongst the pilings before the trip was cancelled.\n\n"
        "(Word count: 1,498)\n\n"
        "(1,023 words)\n\n"
        "## LATER CHAPTERS — OFF-LIMITS SCOPE FENCE (DO NOT SPOIL OR RESOLVE)\n"
        "- Ch 3: Secret spoiler line"
    )
    stripped = strip_llm_chatter(messy)
    assert "LATER CHAPTERS" not in stripped
    assert "(Word count: 1,498)" not in stripped and "(1,023 words)" not in stripped

    norm_us = slop_fix.normalize_prose_typography(messy, american_english=True)
    assert "He walked toward the gray shed among the pilings before the trip was canceled." in norm_us
    assert "LATER CHAPTERS" not in norm_us

    # Heading line exempted & outline 7w ok vs voice 7w / outline 10w caught
    b = create_book(title="Boothbay", idea="Boothbay, Maine (American English throughout).", book_format=BookFormat.novella)
    bdir = book_dir(b.id)
    atomic_write_text(bdir / "voice.md", "### Specimen\nRain pelted the green copper weathervane loudly overhead.")
    atomic_write_text(bdir / "outline.md", "### Ch 1: Opening Harbor\n- Ruth walked down the weathered wooden wharf planks slowly before dawn.")
    scene_planner.save_scenes(bdir, 1, {"chapter": 1, "scenes": [{"title": "Opening Harbor", "goal": "Check boatyard hull", "word_budget": 600}]})

    # 7 words matching outline only (not 10) -> allowed; exact heading matching outline -> allowed
    ok_text = "## Ch 1: Opening Harbor\n\nRuth walked down the weathered wooden wharf near the shed."
    rep_ok = ai_checker.scan(ok_text, book=b, bdir=bdir, chapter_num=1)
    assert "exemplar_copy" not in {f["rule"] for f in rep_ok["findings"]}

    # 7 words matching voice specimen OR 10 words matching outline -> caught
    bad_text = (
        "Rain pelted the green copper weathervane loudly above the loft.\n\n"
        "Ruth walked down the weathered wooden wharf planks slowly before breakfast."
    )
    rep_bad = ai_checker.scan(bad_text, book=b, bdir=bdir, chapter_num=1)
    assert sum(1 for f in rep_bad["findings"] if f["rule"] == "exemplar_copy") == 2

    # Unbalanced quote repair + error priority over earlier style warnings (max_rewrites=2)
    verbs = ["seemed to", "felt", "noticed that", "realized that", "watched as", "heard the sound of", "saw that"]
    nouns = [
        "anchor", "buoy", "capstan", "dory", "engine", "fender", "gaff", "halyard", "keel", "oar",
        "pilings", "rudder", "scupper", "tiller", "winch", "cleat", "bowline", "spool", "thwart", "gunwale", "transom", "chock",
    ]
    filler_filters = "\n\n".join(
        [
            f"Captain Sam {nouns[i]} stood on pier {i + 10}. He {verbs[i % len(verbs)]} touch the damp {nouns[i]} beside dockhouse {i + 10} before noon."
            for i in range(22)
        ]
    )
    prio_text = (
        f"{filler_filters}\n\n"
        '"Stay back from the crane, Gallagher shouted into the wind.\n\n'
        "Victor chose to delve into the ledger inside the office."
    )
    rep_prio = ai_checker.scan(prio_text, book=b)
    prio_rules = {f["rule"] for f in rep_prio["findings"]}
    assert "filter_words" in prio_rules
    assert "unbalanced_quotes" in prio_rules
    assert "tier1_slop" in prio_rules

    def _mock_fix(t: str, s: str, u: str) -> str:
        if "[tier1_slop]" in u:
            return "Victor checked the ledger inside the office."
        if "[unbalanced_quotes]" in u:
            return '"Stay back from the crane," Gallagher shouted into the wind.'
        return "Harlan checked the rope near the quiet dock."

    install_mock_llm(monkeypatch, _mock_fix)
    fixed, frep = asyncio.run(slop_fix.fix(prio_text, rep_prio, book=b, bdir=bdir, include_warnings=True, max_rewrites=2))
    assert frep["rewritten_spans"] == 2
    assert "delve" not in fixed
    assert '"Stay back from the crane," Gallagher shouted into the wind.' in fixed


def test_iter5_copied_text_step_skip_near_dup_offsets_and_a_sense_of():
    # Single 14-word duplicated sentence must emit ONE copied_text error (not 4+ overlapping),
    # and near-duplicate paragraph_loop must expose real character offsets for highlighting/repair.
    sent = "Brody ignored their bickering and signaled both waiting uniformed deputies forward onto the wet wooden dock."
    p1 = (
        f"Morning mist rolled across Cape Ann while seagulls wheeled over the breakwater. {sent} "
        "Cold spray clung to every iron cleat beside the salt shed."
    )
    p2 = (
        f"Rain pelted the gravel yard behind the office trailer. {sent} "
        "A sense of unease settled across the crew, though Helen kept a good sense of direction along the pier."
    )
    # Near-duplicate of p1 (differing by 1 word) -> >= 0.72 Jaccard overlap + real offsets
    p1_near = (
        f"Morning mist rolled across Cape Ann while gulls wheeled over the breakwater. {sent} "
        "Cold spray clung to every iron cleat beside the salt shed."
    )
    raw_doc = f"{p1}\n\n{p2}\n\n{p1_near}"
    rep = ai_checker.scan(raw_doc)
    copied = [f for f in rep["findings"] if f["rule"] == "copied_text"]
    assert len([f for f in copied if f["line"] == 3]) == 1  # ONE match for 14w repeated sentence on L3, not 4+ sliding windows!
    assert len(copied) >= 2
    ploop = [f for f in rep["findings"] if f["rule"] == "paragraph_loop"]
    assert ploop and ploop[0]["end_char"] > ploop[0]["start_char"]
    t1_quotes = [f["quote"].lower() for f in rep["findings"] if f["rule"] == "tier1_slop"]
    assert "a sense of" in t1_quotes
    assert not any("direction" in f.get("full_sentence", "").lower() and "unease" not in f.get("full_sentence", "").lower() for f in rep["findings"] if f["rule"] == "tier1_slop")

    # dedupe_paragraphs_mechanically strips >=68% near-duplicate paragraph & normalize fixes stray `., `
    cleaned_doc, rem = slop_fix.dedupe_paragraphs_mechanically(raw_doc)
    assert rem == 1
    assert "while gulls wheeled" not in cleaned_doc
    assert "., " not in slop_fix.normalize_prose_typography("This indicated hidden details., debts owed and favors exchanged.")


def test_iter6_instruction_leak_dash_proper_noun_pov_and_adjacent_echo():
    from app.enums import POV
    from app.pipeline._common import is_american_english, strip_llm_chatter
    from app.storage import atomic_write_text
    from app.store import book_dir

    # 1. Instruction leak caught by ai_checker and stripped by strip_llm_chatter & normalize_prose_typography
    leaked = (
        "I locked the dockhouse door behind me.\n\n"
        "Output ONLY the revised prose for Scene 1, keeping final word count within 92%—115% of 501 words—preserve all dialogue exchanges."
    )
    rep_leak = ai_checker.scan(leaked)
    assert "meta_leak" in {f["rule"] for f in rep_leak["findings"]}
    assert "Output ONLY" not in strip_llm_chatter(leaked)
    assert "Output ONLY" not in slop_fix.normalize_prose_typography(leaked)

    # 2. Question-mark em-dash cleanup + US backyard locale detection
    pb_book = create_book(title="Pip and the Puddle Boat", idea="A rainy backyard porch story.", book_format=BookFormat.picture_book)
    assert is_american_english(pb_book, book_dir(pb_book.id)) is True
    uk_book = create_book(title="Cornwall Fog", idea="A cozy mystery in Cornwall, England.", book_format=BookFormat.novella)
    assert is_american_english(uk_book, book_dir(uk_book.id)) is False
    fixed_dash = slop_fix.normalize_prose_typography("Discomfort?—flashed across his jaw. Wait?—He paused. Grey bumps rippled.", american_english=True)
    assert "Discomfort - flashed" in fixed_dash
    assert "—" not in fixed_dash
    assert "Wait? He paused." in fixed_dash
    assert "Gray bumps rippled." in fixed_dash

    # 3. Adjacent paraphrase sentence detection + removal
    para_dup = (
        "The scent preceded me, a sterile chill mixed with ammonia. "
        "The smell arrived before the doors—a sterile chill mixed with ammonia. "
        "I signed the harbor log at the front desk."
    )
    rep_adj = ai_checker.scan(para_dup)
    assert "adjacent_echo" in {f["rule"] for f in rep_adj["findings"]}
    clean_adj, adj_rem = slop_fix.dedupe_adjacent_sentences(para_dup)
    assert adj_rem == 1
    assert "smell arrived before the doors" not in clean_adj
    assert "I signed the harbor log at the front desk." in clean_adj

    # 4. tier2_repeat skips capitalized proper surnames while catching lowercase verb reuse
    b = create_book(title="The Saltworks Key", idea="Gloucester, Massachusetts (American English).", book_format=BookFormat.novella, pov=POV.first_person)
    bdir = book_dir(b.id)
    atomic_write_text(bdir / "characters.md", "- Raymond Foster — Superintendent at Foster Saltworks\n- Helen Novak — Claims auditor")
    atomic_write_text(bdir / "outline.md", "### Ch 1: Inventory\n- POV: Helen Novak\n- Setting: Gloucester")
    proper_txt = (
        "Raymond Foster signed the log at Foster Saltworks before noon. Foster's brass keyring hung beside the scale.\n\n"
        "A sharp glint caught Helen's eye as she pulled the oiled canvas roll open. Helen stepped onto the catwalk."
    )
    rep_prop = ai_checker.scan(proper_txt, book=b, bdir=bdir, chapter_num=1)
    rules_prop = [f["rule"] for f in rep_prop["findings"]]
    assert "tier2_repeat" not in rules_prop
    pov_errs = [f for f in rep_prop["findings"] if f["rule"] == "pov_break" and f["severity"] == "error"]
    assert len(pov_errs) >= 2
    assert "pov_break" in slop_fix.AUTO_FIX_RULES

    lower_txt = "Management tried to foster unity across crews. Good logs foster clearer audits."
    rep_lower = ai_checker.scan(lower_txt, book=b, bdir=bdir, chapter_num=1)
    assert "tier2_repeat" in {f["rule"] for f in rep_lower["findings"]}


def test_iter7_ellipsis_quote_split_sequel_bait_possessive_pov_and_typography(monkeypatch):
    import asyncio
    from app.enums import POV
    from app.storage import atomic_write_text
    from app.store import book_dir

    b = create_book(
        title="The Breakwater Manifest",
        idea="Present-day Astoria, Oregon mystery. Standalone story.",
        book_format=BookFormat.novella,
        pov=POV.first_person,
        chapter_count=6,
    )
    bdir = book_dir(b.id)
    atomic_write_text(bdir / "outline.md", "### Ch 1: Arrival\n- POV: Nora Delgado\n\n### Ch 6: Closing\n- POV: Nora Delgado")

    # 1. Ellipsis-ending dialogue quote (`..." Her tone...`) splits cleanly so slop_fix fixes `endeavor`
    split_txt = (
        'She said, "And their relevance to your inquiry..." Her tone trailed off, implying my entire endeavor was frivolous at best.'
    )
    spans = ai_checker.split_sentences_with_spans(split_txt)
    assert len(spans) == 2
    assert spans[1].in_dialogue is False
    assert spans[1].text.count('"') == 0

    rep_end = ai_checker.scan(split_txt, book=b, bdir=bdir, chapter_num=2)
    install_mock_llm(monkeypatch, lambda t, s, u: "Her tone trailed off, implying my whole inquiry was pointless.")
    fixed_end, frep = asyncio.run(slop_fix.fix(split_txt, rep_end, book=b, bdir=bdir, chapter_num=2))
    assert frep["rewritten_spans"] == 1
    assert "endeavor" not in fixed_end

    # 2. Final standalone chapter catches `sequel_bait` and possessive opener `pov_break`
    ch6_bad = (
        "Salt stung her lips, promising rain. I adjusted my jacket against the chill at Pier Eleven.\n\n"
        "The relentless rhythm of the river seemed quieter somehow—a pause, not an end."
    )
    rep6 = ai_checker.scan(ch6_bad, book=b, bdir=bdir, chapter_num=6)
    ch6_rules = {(f["rule"], f["severity"]) for f in rep6["findings"]}
    assert ("pov_break", "error") in ch6_rules
    assert ("sequel_bait", "error") in ch6_rules
    assert "sequel_bait" in slop_fix.AUTO_FIX_RULES

    # 3. Short adjacent echo removal + `?..."` / `marks...some` / `R.My` typography cleanup
    short_dup = "A chair scraped across the floor. A chair scraped on the floor. Benji waited near the door."
    cleaned_short, rem_s = slop_fix.dedupe_adjacent_sentences(short_dup)
    assert rem_s == 1
    assert cleaned_short.count("chair scraped") == 1

    dirty_typo = 'Was he there?... Check the marks...some initials read R.My note said wait.'
    norm_typo = slop_fix.normalize_prose_typography(dirty_typo, american_english=True)
    assert "there?" in norm_typo and "?..." not in norm_typo
    assert "marks... some" in norm_typo
    assert "R. My" in norm_typo


def test_iter8_context_free_span_fix_guards_and_sequel_bait_patterns(monkeypatch):
    import asyncio
    from app.enums import POV
    from app.storage import atomic_write_text
    from app.store import book_dir

    # 1. Context-free _rewrite_span does NOT pass CONTEXT BEFORE/AFTER, and _valid_span_rewrite rejects lowercase fragments & extra sentences
    full = (
        "Harbor lights blurred across the wet apron as sirens faded.\n\n"
        "The cold drizzle made his effort to endeavor look staged. "
        "I tucked the manifest beside my calipers."
    )
    rep = ai_checker.scan(full)
    seen_users: list[str] = []

    def _mock_span_bad(task: str, sys: str, usr: str) -> str:
        seen_users.append(usr)
        if len(seen_users) == 1:
            return "made his effort look staged."  # invalid lowercase start when orig starts uppercase
        return "The drizzle made his work look staged. Another extra sentence appeared."  # 2 sentences vs 1 orig

    install_mock_llm(monkeypatch, _mock_span_bad)
    fixed_bad, meta_bad = asyncio.run(slop_fix.fix(full, rep))
    assert seen_users and "CONTEXT BEFORE" not in seen_users[0] and "CONTEXT AFTER" not in seen_users[0]
    assert meta_bad["skipped_spans"] == 1
    assert fixed_bad == full

    install_mock_llm(monkeypatch, lambda t, s, u: "The cold rain made his effort look staged.")
    fixed_ok, meta_ok = asyncio.run(slop_fix.fix(full, rep))
    assert meta_ok["rewritten_spans"] == 1
    assert "The cold rain made his effort look staged." in fixed_ok

    # 2. Spaced `? — before`, `framework — *drip*`, and expanded US normalization
    messy2 = "Resentment? — before he looked down at the metal framework — *drip*. We catalogued his acknowledgement and walked downwards."
    clean2 = slop_fix.normalize_prose_typography(messy2, american_english=True)
    assert "Resentment - before" in clean2
    assert "framework - *drip*" in clean2
    assert "—" not in clean2
    assert "cataloged" in clean2 and "acknowledgment" in clean2 and "downward" in clean2

    # 3. Expanded sequel_bait catches `work here wasn't quite finished`, `unanswered questions`, `unnamed individual`
    b = create_book(
        title="The Point Hudson Ledger",
        idea="Present-day Port Townsend, WA. Standalone story.",
        book_format=BookFormat.novella,
        pov=POV.first_person,
        chapter_count=6,
    )
    bdir = book_dir(b.id)
    atomic_write_text(bdir / "outline.md", "### Ch 1\n- POV: Tess Navarro\n\n### Ch 6\n- POV: Tess Navarro")
    bait = "My work here wasn't quite finished yet; there were still unanswered questions about the unnamed individual."
    rep_bait = ai_checker.scan(bait, book=b, bdir=bdir, chapter_num=6)
    assert any(f["rule"] == "sequel_bait" and f["severity"] == "error" for f in rep_bait["findings"])


def test_iter9_gender_mismatch_latex_currency_weight_sequel_and_span_guards(monkeypatch):
    import asyncio
    from app.enums import POV
    from app.storage import atomic_write_text
    from app.store import book_dir

    # 1. Currency numbers must NEVER trip LaTeX math detection, while real math still triggers
    cur_txt = "Dale owed $1,782 on the mortgage. Child support came to $450 biweekly. Late fees hit $300."
    rep_cur = ai_checker.scan(cur_txt)
    assert "artefact" not in {f["rule"] for f in rep_cur["findings"]}
    rep_math = ai_checker.scan("He sketched the formula $x^2 + y^2 = z^2$ onto the pad.")
    assert "artefact" in {f["rule"] for f in rep_math["findings"]}

    # 2. gender_mismatch catches 'Mr. Navarro' for female cast member and deterministically fixes to 'Ms. Navarro'
    b = create_book(
        title="The Astoria Slipway Ledger",
        idea="Present-day Astoria, Oregon. Standalone novella.",
        book_format=BookFormat.novella,
        pov=POV.first_person,
        chapter_count=6,
    )
    bdir = book_dir(b.id)
    atomic_write_text(
        bdir / "seed.txt",
        "Strict single first-person POV: Maren Vasquez (she/her, age 38; addressed strictly as Ms. Vasquez—never Mr.) and Tess Navarro (she/her).",
    )
    atomic_write_text(bdir / "characters.md", "| Maren Vasquez | 38 | she/her |\n| Tess Navarro | 37 | she/her |\n| Victor Halvorsen | 57 | he/him |")
    atomic_write_text(bdir / "outline.md", "### Ch 1\n- POV: Maren Vasquez\n\n### Ch 6\n- POV: Maren Vasquez")

    bad_hon = '"Good morning, Mr. Vasquez," Ruth said. "Also tell Mr. Navarro to wait."\n\nMoney went to the Caymen Islands.\n\n---'
    rep_hon = ai_checker.scan(bad_hon, book=b, bdir=bdir, chapter_num=4)
    gm_hits = [f for f in rep_hon["findings"] if f["rule"] == "gender_mismatch"]
    assert len(gm_hits) == 2

    llm_called = 0

    def _no_llm(t, s, u):
        nonlocal llm_called
        llm_called += 1
        return u

    install_mock_llm(monkeypatch, _no_llm)
    fixed_hon, frep = asyncio.run(slop_fix.fix(bad_hon, rep_hon, book=b, bdir=bdir, chapter_num=4))
    assert llm_called == 0
    assert "Ms. Vasquez" in fixed_hon and "Ms. Navarro" in fixed_hon
    assert "Mr. Vasquez" not in fixed_hon and "Mr. Navarro" not in fixed_hon
    assert "Cayman Islands" in fixed_hon
    assert not fixed_hon.rstrip().endswith("---")

    # 3. Span rewrite guards: reject opposite-gender flip, vague 'something', unchanged exemplar_copy, and skip <6w warning fragment
    assert slop_fix._valid_span_rewrite(
        "It wasn't hostility; she simply hated delays.",
        "He seemed uninterested in helping anyone.",
        "",
        "",
    ) is False
    assert slop_fix._valid_span_rewrite(
        "It wasn't a question.",
        "He stated something.",
        "",
        "",
    ) is False
    assert slop_fix._valid_span_rewrite(
        "Everything alright, Ms. Navarro? he asked with a fake smile.",
        "Everything alright, Ms. Navarro? he asked with a sharp nod.",
        "",
        "",
        rules={"exemplar_copy"},
    ) is False

    short_warn = "It wasn't a question. She stared across the desk patiently."
    rep_sw = ai_checker.scan(short_warn)
    fixed_sw, meta_sw = asyncio.run(slop_fix.fix(short_warn, rep_sw, include_warnings=True))
    assert fixed_sw == short_warn and meta_sw["rewritten_spans"] == 0

    # 4. Sequel bait + physical vs abstract 'the weight of'
    ch6_bad = (
        "I lifted the weight of the steel crate onto the pallet.\n\n"
        "Looking at his mug, I felt the weight of unanswered questions.\n\n"
        "I sensed this arrest would only be a chapter—not the end. Another layer peeled back."
    )
    rep6 = ai_checker.scan(ch6_bad, book=b, bdir=bdir, chapter_num=6)
    bait_hits = [f for f in rep6["findings"] if f["rule"] == "sequel_bait"]
    assert len(bait_hits) >= 2
    cliche_quotes = [f["quote"].lower() for f in rep6["findings"] if f["rule"] == "modern_fiction_cliche"]
    assert any("weight of" in q for q in cliche_quotes)
    assert not any("steel crate" in q for q in cliche_quotes)


def test_iter10_deceased_guard_pov_quotes_future_clue_and_typography():
    from app.enums import POV
    from app.storage import atomic_write_text
    from app.store import book_dir

    b = create_book(
        title="The Newport Slipway Ledger",
        idea="Present-day Newport, Oregon. Maren Vasquez investigates Kenji Sato (he/him, deceased).",
        book_format=BookFormat.novella,
        pov=POV.first_person,
        chapter_count=6,
    )
    bdir = book_dir(b.id)
    atomic_write_text(
        bdir / "seed.txt",
        "Strict first-person POV: Maren Vasquez (she/her, age 38; Ms. Vasquez). Victim: Kenji Sato (he/him, age 42, deceased) drowned in Slip 2.",
    )
    atomic_write_text(
        bdir / "characters.md",
        "| Maren Vasquez | 38 | she/her |\n| Devon Brooks | 44 | he/him |\n| Kenji Sato (Deceased) | 42 | he/him |",
    )
    atomic_write_text(
        bdir / "outline.md",
        "### Ch 1\n- POV: Maren Vasquez\n\n### Ch 6\n- POV: Maren Vasquez\n\n"
        "## Foreshadowing Ledger\n"
        "| Item / Clue | Planted (Ch) | Reinforced (Ch) | Paid Off (Ch) |\n"
        "|---|---|---|---|\n"
        "| $5,000 Cashier's Check | 4 | — | 6 |\n"
        "| Brass Feeler Gauge | 5 | — | 6 |\n",
    )

    # 1. extract_deceased_surnames finds Sato/Kenji; _extract_cast_pronouns omits deceased unless in span
    dead = ai_checker.extract_deceased_surnames(bdir)
    assert "sato" in dead and "kenji" in dead
    assert "Sato" not in slop_fix._extract_cast_pronouns(bdir, "A shadow fell across the doorway.")
    known = slop_fix._extract_known_names(bdir)

    # 2. _valid_span_rewrite blocks introducing deceased/cast name, italic * breakage, extra quotes, or glued period
    assert not slop_fix._valid_span_rewrite("A shadow fell across the doorway.", "Mr. Sato stood in the doorway.", "", "", known_names=known)
    assert not slop_fix._valid_span_rewrite("*Brooks waited quietly.*", "*Brooks waited quietly.", "", "", known_names=known)
    assert not slop_fix._valid_span_rewrite('"Smell what? Rain," he replied.', '"Smell what? "Rain," he said.', "", "", known_names=known)
    assert not slop_fix._valid_span_rewrite("No info available.", "Missing information.My notebook dropped.", "", "", known_names=known)

    # 3. normalize_prose_typography repairs glued sentence boundary and stray '"?" He asked'
    messy = 'Possible withholding information.My hands stayed steady. "Another clue?" He asked tightly.'
    cleaned = slop_fix.normalize_prose_typography(messy, american_english=True)
    assert "information. My hands" in cleaned
    assert '?" he asked tightly.' in cleaned

    # 4. _poss_pov_re does NOT flag 'his boots' for female Maren, while drift_re catches 'avoiding Maren and Devon'
    ok_boots = "Devon pulled the winch lever. Dust coated his boots. I watched him step back."
    rep_boots = ai_checker.scan(ok_boots, book=b, bdir=bdir, chapter_num=1)
    assert "pov_break" not in {f["rule"] for f in rep_boots["findings"]}

    bad_ch6 = (
        'Ray muttered, avoiding Maren and Devon\'s eyes on the wet concrete.\n\n'
        'Devon hesitated, sniffing cautiously. "Smell what? "Rain," he said, too quickly.\n\n'
        "But somewhere out there in the shadows, other secrets waited to be unearthed."
    )
    rep6 = ai_checker.scan(bad_ch6, book=b, bdir=bdir, chapter_num=6)
    r6 = {(f["rule"], f["severity"]) for f in rep6["findings"]}
    assert ("pov_break", "error") in r6
    assert ("unbalanced_quotes", "error") in r6
    assert ("sequel_bait", "error") in r6

    # 5. future_clue_leak flags premature Ch 4 check / Ch 5 feeler gauge inside Ch 1
    early_leak = "I spotted a frayed lanyard beside the brass feeler gauge and asked about a cashier's check."
    rep_fc = ai_checker.scan(early_leak, book=b, bdir=bdir, chapter_num=1)
    fc_hits = [f for f in rep_fc["findings"] if f["rule"] == "future_clue_leak"]
    assert len(fc_hits) == 2
    assert all(f["severity"] == "error" for f in fc_hits)


def test_iter11_gender_meta_leak_and_error_future_clue():
    import asyncio
    from app.enums import POV
    from app.pipeline._common import strip_llm_chatter
    from app.storage import atomic_write_text
    from app.store import book_dir, save_book

    assert "future_clue_leak" in slop_fix.AUTO_FIX_RULES

    b = create_book(
        title="Harbor Ledger",
        book_format=BookFormat.novella,
        pov=POV.first_person,
        chapter_count=6,
        idea="Ms. Hannah Morales (38, she/her) checks Deputy Carlos Vega (44, he/him).",
    )
    save_book(b)
    bdir = book_dir(b.id)
    atomic_write_text(
        bdir / "seed.txt",
        "Ms. Hannah Morales (38, she/her) arrives in town.\nDeputy Carlos Vega (44, he/him) greets her at the gate.\n",
    )
    atomic_write_text(
        bdir / "characters.md",
        "## Cast at a Glance\n- **Hannah Morales** (38, she/her): Adjuster\n- **Carlos Vega** (44, he/him): Deputy\n",
    )

    leaked = (
        'I locked the latch behind me.\n\n'
        'Instead of describing it as "A testament to bureaucracy," I watched the rain.\n\n'
        'Expand and deepen the scenes in this draft while keeping dialogue sharp.\n\n'
        '## Chapter End Note\nRemoved the dashed lines per notes.'
    )
    stripped = strip_llm_chatter(leaked)
    assert "Instead of describing" not in stripped
    assert "Expand and deepen" not in stripped
    assert "Chapter End Note" not in stripped
    assert "I locked the latch behind me." in stripped

    rep_leak = ai_checker.scan(leaked, book=b, bdir=bdir, chapter_num=6)
    leak_rules = {f["rule"] for f in rep_leak["findings"]}
    assert "meta_leak" in leak_rules

    rep_seq = ai_checker.scan("I closed the ledger, knowing this wasn't truly over yet.", book=b, bdir=bdir, chapter_num=6)
    assert any(f["rule"] == "sequel_bait" and f["severity"] == "error" for f in rep_seq["findings"])

    bad_gender = (
        "Mrs. Morales checked her watch on the ramp.\n\n"
        "Deputy Carlos Vega leaned out her cruiser window.\n\n"
        "Deputy Carlos Vega walked onto the dock. She held a notebook."
    )
    rep_g = ai_checker.scan(bad_gender, book=b, bdir=bdir, chapter_num=1)
    g_msgs = [f["message"] for f in rep_g["findings"] if f["rule"] == "gender_mismatch"]
    assert any("Ms. Morales" in m for m in g_msgs)
    assert any("her cruiser" in m for m in g_msgs)
    assert any("'She' follows male" in m for m in g_msgs)

    # Deterministic Ms. Morales repair via slop_fix.fix on clean-Spanish/single-hon span
    fixed_hon, _ = asyncio.run(
        slop_fix.fix("Mrs. Morales checked her watch.", ai_checker.scan("Mrs. Morales checked her watch.", book=b, bdir=bdir, chapter_num=1), book=b, bdir=bdir, chapter_num=1)
    )
    assert "Ms. Morales" in fixed_hon and "Mrs. Morales" not in fixed_hon

    # Mixed-gender paragraph MUST NOT trigger false positive on 'She waited by the door.'
    safe_mixed = "Hannah Morales handed Carlos Vega the envelope. She waited by the door."
    rep_safe = ai_checker.scan(safe_mixed, book=b, bdir=bdir, chapter_num=1)
    assert not any(f["rule"] == "gender_mismatch" for f in rep_safe["findings"])


def test_iter12_family_honorific_split_quote_world_clue_and_slop_fix(monkeypatch):
    import asyncio
    from app.enums import POV
    from app.pipeline._common import strip_llm_chatter
    from app.storage import atomic_write_text
    from app.store import book_dir, save_book

    b = create_book(
        title="The Cedar Hull Accord",
        book_format=BookFormat.novella,
        pov=POV.first_person,
        genre="Literary Coastal Drama",
        chapter_count=6,
        idea=(
            "Present-day Depoe Bay, Oregon. Ms. Clara Mercer (she/her, age 34) and Eli Mercer (he/him, age 29). "
            "Nolan Whitaker (he/him, age 62). Tessa Kwan (she/her, age 49). Zero crime, murder, or police subplots."
        ),
    )
    save_book(b)
    bdir = book_dir(b.id)
    atomic_write_text(bdir / "seed.txt", b.idea)
    atomic_write_text(bdir / "world.md", "## Material Culture\nLocal timber: Port Orford cedar boards and bronze bolts.\n")
    atomic_write_text(
        bdir / "characters.md",
        "## Cast at a Glance\n- **Clara Mercer** (34, she/her): Shipwright\n- **Eli Mercer** (29, he/him): Carpenter\n"
        "- **Nolan Whitaker** (62, he/him): Owner\n- **Tessa Kwan** (49, she/her): Surveyor\n",
    )
    atomic_write_text(
        bdir / "outline.md",
        "### Ch 1: Arrival\n- POV: Clara Mercer\n"
        "### Ch 5: Fastening\n- POV: Clara Mercer\n\n"
        "## Foreshadowing Ledger\n"
        "| Item / Clue | Planted (Ch) | Reinforced (Ch) | Paid Off (Ch) |\n"
        "|---|---|---|---|\n"
        "| Port Orford Cedar Quality | 2 | 3 | 6 |\n"
        "| Tessa Kwan's Fastening Standards | 4 | 5 | 6 |\n"
        "| Brass Feeler Gauge | 5 | — | 6 |\n",
    )

    # 1. Family sibling surname ('Clara' she/her + 'Eli' he/him) MUST NOT rewrite 'Mr. Mercer' -> 'Ms. Mercer'
    hon_txt = '"Regulations don\'t negotiate, Mr. Mercer," she stated cleanly.'
    rep_hon = ai_checker.scan(hon_txt, book=b, bdir=bdir, chapter_num=4)
    assert not any(f["rule"] == "gender_mismatch" for f in rep_hon["findings"])
    fixed_hon, _ = asyncio.run(slop_fix.fix(hon_txt, rep_hon, book=b, bdir=bdir, chapter_num=4))
    assert "Mr. Mercer" in fixed_hon and "Ms. Mercer" not in fixed_hon

    # 2. Cross-paragraph dialogue attribution ('directing the question at Tessa') & multi-sentence quote with ', Clara.'
    safe_prose = (
        'Nolan arrived just then, squinting across the cradle.\n\n'
        '"Well?" he prompted, directing the question at Tessa without preamble.\n\n'
        'He paused, then continued, "I\'m going to take the job offer, Clara. It starts Monday."'
    )
    rep_safe = ai_checker.scan(safe_prose, book=b, bdir=bdir, chapter_num=4)
    assert not any(f["rule"] == "gender_mismatch" for f in rep_safe["findings"])

    # 3. Day-1 world timber ('Port Orford cedar') & cast name ('Tessa Kwan') do NOT trigger future_clue_leak;
    #    late concrete prop ('brass feeler gauge') DOES trigger error & slop_fix replaces it cleanly
    ch1_mixed = (
        "Sunshine warmed the Port Orford cedar boards while I thought about Tessa Kwan arriving Friday. "
        "Eli tossed a brass feeler gauge onto the bench beside his plane."
    )
    rep_fc = ai_checker.scan(ch1_mixed, book=b, bdir=bdir, chapter_num=1)
    fc_quotes = [f["quote"].lower() for f in rep_fc["findings"] if f["rule"] == "future_clue_leak"]
    assert fc_quotes == ["brass feeler gauge"]

    def _mock_fc_fix(task: str, sys: str, usr: str) -> str:
        assert "future_clue_leak" in usr
        assert "Replace" in sys or "delete" in sys.lower()
        return "Eli tossed a folded chalk line onto the bench beside his plane."

    install_mock_llm(monkeypatch, _mock_fc_fix)
    fixed_fc, _ = asyncio.run(slop_fix.fix(ch1_mixed, rep_fc, book=b, bdir=bdir, chapter_num=1))
    assert "feeler gauge" not in fixed_fc.lower()
    assert not any(f["rule"] == "future_clue_leak" for f in ai_checker.scan(fixed_fc, book=b, bdir=bdir, chapter_num=1)["findings"])

    # 4. strip_llm_chatter cleans '---\\nI aimed for distinct registers; please let me know...'
    trials_raw = "### Trial 1 — Clean\nCold salt mist drifted across the cradle.\n---\nI aimed for distinct registers as requested; please let me know if any adjustments are needed!"
    assert "please let me know" not in strip_llm_chatter(trials_raw).lower()


def test_em_dash_guardrail_normalization_checker_and_kdp(monkeypatch):
    import asyncio
    from app.pipeline import kdp_kit
    from app.store import book_dir

    # 1. Deterministic replacement covers headings, prose, dialogue interruptions, ranges, and preserves compound hyphens & rules
    sample = (
        "## Chapter 1 — The Slipway\n\n"
        "<!-- keep html comment -->\n\n"
        "\"Wait—\" Mara said—holding a well-worn wrench from 1920–1924 (cast-iron grip)—\"don't cut!\"\n\n"
        "\"Wait—!\" he yelled.\n\n"
        "\"—and then the boom swung,\" he trailed off—\n\n"
        "---\n\n"
        "* * *\n\n"
        "Above the door hung a sign—\"North Star Pawn\" – painted in gold leaf.\n"
    )
    cleaned = slop_fix.replace_em_dashes_with_hyphens(sample)
    assert "## Chapter 1 - The Slipway" in cleaned
    assert "<!-- keep html comment -->" in cleaned
    assert '"Wait-" Mara said - holding a well-worn wrench from 1920-1924 (cast-iron grip) - "don\'t cut!"' in cleaned
    assert '"Wait-!" he yelled.' in cleaned
    assert '"-and then the boom swung," he trailed off-' in cleaned
    assert "\n---\n" in cleaned and "\n* * *\n" in cleaned
    assert 'sign - "North Star Pawn" - painted in gold leaf.' in cleaned
    assert "—" not in cleaned and "–" not in cleaned and "--" not in cleaned.replace("---", "").replace("<!--", "").replace("-->", "")

    # 2. ai_checker flags literal em-dash with em_dash_forbidden (including heading-only) AND still catches spaced dash patterns
    head_only = "## Chapter 1 — Slipway\n\nPlain clean prose without any punctuation issues here."
    rep_head = ai_checker.scan(head_only)
    assert any(f["rule"] == "em_dash_forbidden" for f in rep_head["findings"])

    dense_spaced = " ".join(
        [f"Entry number {i} listed an old ship - a timber brigantine bound for port with salt cod and oak staves piled high on deck." for i in range(1, 9)]
        + ["It wasn't just a leak - it was sabotage. It wasn't anger - it was dread."]
    )
    rep_spaced = ai_checker.scan(dense_spaced)
    spaced_rules = {f["rule"] for f in rep_spaced["findings"]}
    assert "em_dash_forbidden" not in spaced_rules
    assert {"not_just_but", "negation_contrast", "em_dash_density", "em_dash_overuse"} <= spaced_rules

    # 3. slop_fix.fix & fix_one clean em_dash_forbidden deterministically with 0 LLM calls
    def _fail_llm(task: str, sys: str, usr: str) -> str:
        raise AssertionError(f"Unexpected LLM call during deterministic em-dash fix: {task}")

    install_mock_llm(monkeypatch, _fail_llm)
    prose_with_em = "## Chapter 1 — Harbor\n\nI picked up the wrench—cold iron—and waited.\n\n\"Stop—\" he whispered."
    rep_em = ai_checker.scan(prose_with_em)
    assert "em_dash_forbidden" in {f["rule"] for f in rep_em["findings"]}

    fixed_all, fix_meta = asyncio.run(slop_fix.fix(prose_with_em, rep_em))
    assert "—" not in fixed_all
    assert "## Chapter 1 - Harbor" in fixed_all
    assert "wrench - cold iron - and waited." in fixed_all
    assert '"Stop-" he whispered.' in fixed_all
    assert fix_meta["rewritten_spans"] == 0 and fix_meta["deduped_paragraphs"] >= 1
    assert not any(f["rule"] == "em_dash_forbidden" for f in ai_checker.scan(fixed_all)["findings"])

    span_f = next(f for f in rep_em["findings"] if f["rule"] == "em_dash_forbidden" and f["end_char"] > f["start_char"])
    fixed_one, ok_one = asyncio.run(
        slop_fix.fix_one(
            prose_with_em,
            start_char=span_f["start_char"],
            end_char=span_f["end_char"],
            message=span_f["message"],
            rule="em_dash_forbidden",
            quote=span_f["quote"],
        )
    )
    assert ok_one and "wrench - cold iron - and waited." in fixed_one

    # 4. kdp_kit normalizes em-dashes in blurb_html & kdp_kit.md
    b = create_book(title="Harbor Blurb Test", book_format=BookFormat.novella)
    bdir = book_dir(b.id)
    saved_kit = kdp_kit.save_kdp_kit(
        bdir,
        {
            "subtitle": "A Coastal Mystery",
            "blurb_html": "<p>She thought the ledger was lost—until Tuesday’s tide brought it back.</p>",
            "keywords": ["coastal harbor mystery book"],
            "categories": ["FICTION / Sea Stories"],
            "comp_titles": ["Plainsong by Kent Haruf"],
            "age_range": "Adult",
            "cover_art_brief": "Cedar pilings in mist.",
        },
        title=b.title,
        is_children=False,
    )
    assert "—" not in saved_kit["blurb_html"]
    assert "lost - until" in saved_kit["blurb_html"]




