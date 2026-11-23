from __future__ import annotations

import re
from pathlib import Path

from app import llm_client
from app.enums import POV
from app.formats import get_profile
from app.pipeline._common import (
    WarnCB,
    extract_plot_spine,
    is_mystery_genre,
    outline_block,
    parse_llm_json,
    parse_outline_units,
    pov_name,
    strip_llm_chatter,
    warn_if_cut,
)
from app.pipeline.characters_gen import scrub_banned_names
from app.pipeline.directives import build_directives, per_unit_word_target
from app.storage import read_text
from app.store import Book


_FP_NARR_VERBS_RE = re.compile(
    r"\bI\s+(?:stepped|walked|looked|said|asked|felt|saw|sat|stood|picked|opened|pulled|turned|called|found|watched|stared)\b"
)


def _outline_has_prose_leak(outline_md: str) -> bool:
    """Detect outlines that accidentally dumped narrative story prose or quoted dialogue (even inside bullets)."""
    units = parse_outline_units(outline_md or "")
    for num, _ in units:
        blk = outline_block(outline_md, num)
        if not blk:
            continue
        prose_hits = 0
        bullet_count = 0
        non_bullet_words = 0
        max_bullet_words = 0
        for ln in blk.splitlines()[1:]:
            s = ln.strip()
            if not s or s.startswith("#") or s.startswith("|"):
                continue
            is_bullet = s.startswith(("- ", "* ", "• "))
            body = re.sub(r"^[-*•]\s+", "", s).strip() if is_bullet else s
            if re.match(r"^(?:\*\*)?(?:POV|Setting(?:\s*&\s*Time)?|Opening(?:\s+Hook(?:\s+Word)?)?|Chapter Ending(?:\s+State)?|Ending Beat)\s*:", body, re.IGNORECASE):
                continue
            bw = len(body.split())
            has_quote_or_fp = ('"' in body or "“" in body or "”" in body or bool(_FP_NARR_VERBS_RE.search(body)))
            if is_bullet:
                bullet_count += 1
                max_bullet_words = max(max_bullet_words, bw)
                if bw >= 65 or (bw >= 38 and has_quote_or_fp):
                    return True
            else:
                non_bullet_words += bw
                if len(body) >= 120 and has_quote_or_fp:
                    prose_hits += 1
        if prose_hits >= 1 or (bullet_count < 3 and non_bullet_words >= 120) or (bullet_count == 1 and max_bullet_words >= 75):
            return True
    return False


async def generate(
    book: Book,
    bdir: Path,
    *,
    ref_text: str = "",
    stream_cb: llm_client.StreamCB | None = None,
    on_warning: WarnCB = None,
) -> str:
    prof = get_profile(book.book_format)
    directives = build_directives(book)

    seed = read_text(bdir / "seed.txt").strip() or book.idea
    world = read_text(bdir / "world.md").strip()
    chars = read_text(bdir / "characters.md").strip()
    mystery = read_text(bdir / "mystery.md").strip()

    count = book.chapter_count or prof.default_units
    per_unit = per_unit_word_target(book)
    u_label = prof.unit_label

    if prof.key.value == "picture_book":
        structure_spec = (
            f"Design a {count}-spread Children's Picture Book outline (~{per_unit or 45} words per spread).\n"
            f"Use exact headings `### Spread 1: <Short Title>` through `### Spread {count}: <Short Title>`.\n"
            "FORMAT LOCK: Structured outline bullets ONLY — NEVER write final story paragraphs.\n"
            "Under each Spread include:\n"
            "- **Visual Concept:** what the illustration shows\n"
            "- **Story Beat & Page-Turn Hook:** 1–2 concise action bullets ending on curiosity/motion\n"
            "- **Read-Aloud Sound Note:** cadence, refrain, or sound word if fitting\n"
            "STORY PAYOFF RULE: Ensure any central problem or lost object introduced in middle spreads is visibly rescued, fixed, or returned ON-PAGE before the cozy journey home in the closing spreads.\n"
            "OPENER DIVERSITY RULE: Vary the first word and opening action of every spread — NEVER start more than 2 spreads with the main character's name.\n"
            "Keep tone joyful, warm, curious, or gently adventurous — never grimdark."
        )
        max_tok = 3500
    elif prof.craft_profile == "short":
        structure_spec = (
            f"Design a tight {count}-part Short Story outline (~{per_unit or 1500} words per part).\n"
            f"Use exact headings `### Part 1: <Title>` through `### Part {count}: <Title>`.\n"
            "FORMAT LOCK: Structured bullets ONLY — NEVER write multi-sentence story prose or quoted dialogue inside the outline.\n"
            "Under each Part include:\n"
            "- POV: <character name>\n"
            "- 3–5 concrete scene bullets (Goal -> Friction -> Turn; no filler travel or backstory dumps)\n"
            "- **Ending Beat:** the exact image/line where this part stops\n\n"
            "End with a brief `## Foreshadowing Ledger` table (`| Item | Planted | Reinforced | Paid Off |`)."
        )
        max_tok = 3500
    else:
        mystery_mode = is_mystery_genre(book, seed)
        fp_rule = (
            "- **First-Person POV Lock:** Every single chapter MUST set `- POV: <Protagonist Full Name>` to the SAME single first-person narrator — NEVER assign another character as POV.\n"
            if book.pov == POV.first_person
            else ""
        )
        spine_lock_2 = (
            "2. **Exact Crime, Physical Mechanism & Timeline Math:** concrete physical mechanism, exact times, exact cause of death, and exact calendar day-gaps (e.g. Oct 8 -> Oct 11 is 3 days)."
            if mystery_mode
            else "2. **Core Premise/Dilemma, Physical Stakes & Exact Calendar Math:** exact calendar dates, remaining day-counts between every chapter (e.g., Tue Oct 8 -> Fri Oct 11 is 3 days; Thu Oct 10 -> Fri Oct 11 is 1 day), and exact physical/financial stakes from Seed (NO invented crimes or deaths)."
        )
        spine_lock_3 = (
            "3. **Causal Clue Chain:** exact proof connecting every planted clue cleanly to the culprit (every red herring removed or paid off)."
            if mystery_mode
            else "3. **Causal Escalation Chain:** concrete cause-and-effect steps (each technical/relational obstacle logically forcing the next choice) grounded strictly in Seed."
        )
        spine_lock_4 = (
            "4. **Named Culprit(s), Motive & On-Page Finale Fate:** who did it, why, and joint on-page arrest/exposure of ALL culprits together in the final chapter."
            if mystery_mode
            else "4. **Climax & On-Page Resolution:** exact final outcome promised by Seed with ZERO invented fraud charges, police, arrests, or cliffhangers."
        )
        closure_spec = (
            "**NO SEQUEL CLIFFHANGERS OR OFF-PAGE ARRESTS IN FINAL CHAPTER:** Unless the seed explicitly asks for a cliffhanger or multi-book series, the final chapter MUST explicitly plot the complete on-page resolution in sequence: (a) protagonist/investigator presents gathered evidence to authorities/key parties, (b) culprit exposure/confession on-page, (c) complete official resolution/arrest on-page (NEVER off-page), (d) explicit exoneration of any innocent ally, and (e) exact active giver->recipient closing beat from Seed with ZERO lingering suspicions, unanswered questions, or sequel teasers.\n"
            if mystery_mode
            else "**SEED-FAITHFUL FINAL CHAPTER RESOLUTION (NO INVENTED CRIMES OR CLIFFHANGERS):** Unless the seed explicitly asks for a crime or cliffhanger, the final chapter MUST stage ONLY the exact climactic payoff and warm/honest interpersonal resolution promised by Seed (NEVER invent crimes, legal charges, or unrequested subplots!), preserving exact giver->recipient handoffs and ZERO sequel teasers.\n"
        )
        bullet_guidance = (
            "- 3–5 concrete cause-and-effect bullets (`but` / `therefore` turns, written in unmistakable active voice; assign EVERY key Seed fact—exact cause & time, culprits on-page BEFORE the finale, every physical clue/turn, and institutional payoff—to distinct chapters without repeating the same visit across two chapters or inventing detours)"
            if mystery_mode
            else "- 3–5 concrete cause-and-effect bullets (`but` / `therefore` turns, written in unmistakable active voice; assign EVERY key Seed beat, exact remaining-day countdown, and exact material/item quantity cleanly across chapters without contradictions, late deliveries after use, or invented crimes)"
        )
        structure_spec = (
            f"Design a {count}-{u_label.lower()} outline (~{per_unit or 2500} words per {u_label.lower()}).\n"
            "**FORMAT LOCK — TERSE NEUTRAL BULLETS ONLY, NEVER STORY PROSE:** Under every `### Ch N: <Title>`, write ONLY `- POV:`, `- Setting & Time:`, `- Opening Hook Word: <single fresh word>`, 3–5 terse neutral plot bullets (`- ...`, strictly 10–30 words each), and `**Chapter Ending State:** ...`. NEVER write multi-sentence narrative paragraphs, first-person sentences (`\"I stepped...\"`, `'I inhaled...'`), or quoted spoken dialogue inside any bullet!\n"
            "**STRICT SEED, QUANTITY & WORKPLACE LOCK:** Keep every character name, ownership role (NEVER flip owner vs. buyer), EXACT numerical age, exact item quantities (e.g., two boards vs three), living vs retired parents vs deceased relatives, explicit gender/pronoun & honorific (`she/her` / `Ms.` vs `he/him` / `Mr.`), exact calendar math (e.g., Tue Oct 8 -> Fri Oct 11 is **3 days**, Thu Oct 10 -> Fri Oct 11 is **1 day**), cause-before-effect order, and fixed workplaces from Seed & Cast Reference 100% verbatim. NEVER invent unrequested deceased grandparents, personal heirlooms, offshore bribes, or unrequested crimes.\n\n"
            "First, BEFORE `### Ch 1:`, output a brief `## Plot Logic & Resolution Spine` section establishing 5 falsifiable story locks:\n"
            "1. **Protagonist, Cast Ages, Pronouns & 1-Workplace Locks:** exact names, ages, he/she pronouns, and single fixed workplaces.\n"
            f"{spine_lock_2}\n"
            f"{spine_lock_3}\n"
            f"{spine_lock_4}\n"
            "5. **Strict Cast & Prop Lock:** ONLY characters/heirlooms defined in Cast Reference and exact quantities/owners from Seed.\n\n"
            f"Then use exact headings `### Ch 1: <Title>` through `### Ch {count}: <Title>`.\n"
            "Under EVERY chapter include:\n"
            "- POV: <single character name for close POV, or Omniscient>\n"
            f"{fp_rule}"
            "- Setting & Time:\n"
            "- Opening Hook Word: <single fresh word — NEVER repeat any earlier chapter's opening word>\n"
            f"{bullet_guidance}\n"
            "- **Chapter Ending State:** exact concrete point where the chapter stops (hard stop boundary)\n\n"
            "Preserve dramatic reveal order: do NOT resolve mid/late turns in Ch 1.\n"
            f"{closure_spec}"
            "**Opening Variety Rule:** Specify a distinct opening hook word and beat for EVERY chapter (dialogue, physical tool action, knocked mug, invoice) — NEVER start two chapters with the same opening word (`The`, etc.) or consecutive chapters with weather/atmosphere.\n"
            "Include varied pacing (quiet character/relational shifts between pressure spikes). Output ONLY the Markdown outline (no conversational intro).\n\n"
            "End with `## Foreshadowing Ledger`:\n"
            "| Item / Clue | Planted (Ch) | Reinforced (Ch) | Paid Off (Ch) |\n"
            "|---|---|---|---|\n"
            "(List ONLY concrete late-chapter turning points or late-revealed objects that must stay out of earlier chapters — NEVER list bare character names or standard Day-1 workshop materials. Ensure every planted thread pays off by the final chapter.)"
        )
        max_tok = 8000

    system = "\n\n---\n\n".join(
        [
            directives,
            f"You are a master developmental editor outlining a **{prof.label}** using structured outline notation only (zero story prose).",
            structure_spec,
        ]
    )

    user_parts = [f"Title: {book.title}", f"Seed:\n{seed}"]
    if world:
        user_parts.append(f"World:\n{world[:6000]}")
    if chars:
        user_parts.append(f"Characters:\n{chars[:6000]}")
    if mystery:
        user_parts.append(f"Author-only secrets / true motives to weave invisibly (do not spoil early):\n{mystery[:3000]}")
    if ref_text.strip():
        user_parts.append(f"Reference material:\n{ref_text.strip()[:10000]}")

    res = await llm_client.complete(
        system=system,
        user="\n\n".join(user_parts),
        task="outline",
        max_tokens=max_tok,
        creativity="medium",
        stream_cb=stream_cb,
    )
    warn_if_cut(res.stop_reason, on_warning, "Outline")
    allowed_src = f"{book.title}\n{seed}\n{ref_text}"
    out = scrub_banned_names(strip_llm_chatter(res.text), allowed_src)

    needs_spine = prof.craft_profile == "novel" and "## plot logic" not in out.lower()
    if _outline_has_prose_leak(out) or needs_spine:
        if on_warning:
            on_warning("Outline contained narrative prose paragraphs or omitted Plot Logic Spine; converting strictly to bulleted beat structure.")
        rep_res = await llm_client.complete(
            system=(
                f"Convert this {prof.label} outline into strictly structured bulleted beats.\n"
                "NEVER output narrative story paragraphs, first-person storytelling sentences ('I inhaled...'), or quoted dialogue lines anywhere (even inside `- ` bullets).\n"
                "Preserve EVERY exact character name, ownership role, and date-gap math from Seed and Cast Reference verbatim.\n"
                + ("Include `## Plot Logic & Resolution Spine` before `### Ch 1:` and `## Foreshadowing Ledger` at the end.\n" if prof.craft_profile == "novel" else "")
                + "Every unit block must have `- POV:`, `- Setting & Time:`, `- Opening Hook Word:`, 3–5 terse neutral plot bullets (`- ...`, strictly 10–30 words each), and `**Chapter Ending State:**`."
            ),
            user=f"SEED:\n{seed}\n\nCAST:\n{chars[:3000]}\n\nRAW OUTLINE TO CONVERT:\n{out}",
            task="outline",
            max_tokens=max_tok,
            creativity="low",
        )
        repaired = scrub_banned_names(strip_llm_chatter(rep_res.text), allowed_src)
        if repaired and not _outline_has_prose_leak(repaired):
            out = repaired

    # Seed-fidelity & continuity audit pass for rich novel/novella outlines
    if prof.craft_profile == "novel" and len((seed or "").strip()) >= 160:
        ver_rule_2 = (
            "2. Preserve EVERY Seed subplot cleanly on-page, exact cause-of-death time/manner, clue chain, and ZERO fabricated bribes framing innocent allies.\n"
            if is_mystery_genre(book, seed)
            else "2. Preserve EVERY Seed subplot, living vs retired parent facts (ZERO invented dead grandparents/heirlooms), exact material quantities, and cause-before-effect delivery order cleanly on-page with ZERO fabricated crimes or bribery.\n"
        )
        ver_rule_4 = (
            "4. Verify the final chapter explicitly stages on-page resolution matching Seed (confession/exposure + joint arrest if mystery) with ZERO lingering doubts, sequel hooks, or prose paragraphs under bullets.\n"
            if is_mystery_genre(book, seed)
            else "4. Verify the final chapter stages ONLY Seed's promised climax & warm resolution on-page (NEVER invent crimes, legal charges, or unrequested subplots!) with ZERO lingering doubts, sequel hooks, or prose paragraphs under bullets.\n"
        )
        ver_res = await llm_client.complete(
            system=(
                "Audit and tighten this structural outline against Premise Seed and Cast Reference using terse neutral plot bullets ONLY (10–30 words per bullet, ZERO first-person sentences or quoted dialogue).\n"
                "Fix ONLY concrete plot/continuity defects if present:\n"
                "1. Verify exact calendar subtraction math (e.g., Tue->Fri is 3 days; Thu->Fri is 1 day) and cause-before-effect order (materials procured BEFORE they are installed; buyout/offer precedes refusal).\n"
                f"{ver_rule_2}"
                "3. Eliminate duplicate visits or contradictory delivery schedules across chapters, keep characters in their Seed workplaces, and make active voice unambiguous.\n"
                f"{ver_rule_4}"
                "Preserve `## Plot Logic & Resolution Spine`, `### Ch 1:` through last chapter headings, and `## Foreshadowing Ledger`. Return ONLY the clean Markdown outline."
            ),
            user=f"PREMISE SEED:\n{seed[:6000]}\n\nCAST:\n{chars[:3000]}\n\nOUTLINE TO AUDIT:\n{out}",
            task="outline",
            max_tokens=max_tok,
            creativity="low",
        )
        audited = scrub_banned_names(strip_llm_chatter(ver_res.text), allowed_src)
        if (
            audited
            and not _outline_has_prose_leak(audited)
            and len(parse_outline_units(audited)) >= max(2, len(parse_outline_units(out)))
        ):
            out = audited

    return out
