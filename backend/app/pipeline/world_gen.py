from __future__ import annotations

from pathlib import Path

from app import llm_client
from app.formats import get_profile
from app.pipeline._common import WarnCB, craft_prompt_for_book, strip_llm_chatter, voice_or_default, warn_if_cut
from app.pipeline.characters_gen import scrub_banned_names
from app.pipeline.directives import build_directives
from app.storage import read_text
from app.store import Book


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
    craft = craft_prompt_for_book(book)
    voice = voice_or_default(bdir)
    seed = read_text(bdir / "seed.txt").strip() or book.idea

    if prof.book_format.value == "picture_book" if hasattr(prof, "book_format") else prof.key.value == "picture_book":
        sections = (
            "Keep it focused and visual (350–650 words total) with Markdown headings:\n"
            "## Setting & Visual Atmosphere\n## Seasonal / Sensory Palette (Colors, Sounds, Textures)\n"
            "## Recurring Props & Spatial Landmarks\n## Rules of This Storybook World"
        )
        max_tok = 2000
    elif prof.craft_profile == "short":
        sections = (
            "Keep it tight and directly usable by scenes (500–900 words total) with Markdown headings:\n"
            "## Immediate Setting & Physical Layout\n## Social Norms & Everyday Pressures\n"
            "## Key Locations & Props\n## Sensory Palette"
        )
        max_tok = 2500
    else:
        sections = (
            "Write a concrete, lived-in world bible (1,100–2,000 words) with Markdown headings:\n"
            "## Core Premise & Everyday Reality\n## Geography & Key Locations\n"
            "## Institutions, Factions & Daily Economy\n## Unwritten Rules, Costs & Constraints\n"
            "## Recent Friction & Catalyst\n## Sensory & Material Culture Palette"
        )
        max_tok = 6500

    system = "\n\n---\n\n".join(
        [
            directives,
            "You are designing a grounded, original setting bible. Every world rule must create concrete daily consequences, costs, or interpersonal friction — avoid encyclopedic lore dumps and generic fantasy/sci-fi clichés.\n"
            "STRICT SEED FIDELITY: Preserve every ownership relationship (NEVER flip who owns a business vs. who is trying to buy it out), character job/workplace, calendar date, cause of death, and core conflict from Seed premise 100% verbatim — NEVER invent contradictory lore.",
            sections,
            craft,
            f"## VOICE GUIDE\n{voice}",
        ]
    )

    user_parts = [f"Project title: {book.title}", f"Seed premise:\n{seed}"]
    if ref_text.strip():
        user_parts.append(f"Reference material:\n{ref_text.strip()[:18000]}")

    res = await llm_client.complete(
        system=system,
        user="\n\n".join(user_parts),
        task="foundation",
        max_tokens=max_tok,
        creativity="medium",
        stream_cb=stream_cb,
    )
    warn_if_cut(res.stop_reason, on_warning, "World bible")
    out = strip_llm_chatter(res.text)
    return scrub_banned_names(out, f"{book.title}\n{seed}\n{ref_text}")
