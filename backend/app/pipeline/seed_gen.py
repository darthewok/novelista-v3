from __future__ import annotations

from app import llm_client
from app.formats import get_profile
from app.pipeline._common import WarnCB, strip_llm_chatter, warn_if_cut
from app.pipeline.directives import build_directives
from app.store import Book


async def generate_candidates(
    book: Book,
    *,
    count: int = 10,
    ref_text: str = "",
    stream_cb: llm_client.StreamCB | None = None,
    on_warning: WarnCB = None,
) -> str:
    prof = get_profile(book.book_format)
    directives = build_directives(book)

    if prof.craft_profile == "children":
        craft_lens = (
            "Each concept needs a vivid child-relatable hero or creature, an irresistible everyday curiosity or small trouble, "
            "a clear sensory motif, warm emotional stakes, and a satisfying concrete resolution (no dark grimdark tropes)."
        )
    elif prof.craft_profile == "short":
        craft_lens = (
            "Each concept needs a razor-tight central predicament, an immediate dramatic situation (no sweeping multi-kingdom backstory), "
            "an awkward human cost, and a turn that reframes the opening."
        )
    else:
        craft_lens = (
            "Each concept needs four load-bearing elements: (1) a concrete distinguishing premise or social friction, "
            "(2) a personal dilemma with conflicting loyalties, (3) an irreversible cost or constraint, and (4) an unmistakable sensory world detail. "
            "Avoid generic chosen-one, ancient prophecy, or faceless evil empire pitches."
        )

    system = "\n\n".join(
        [
            directives,
            "You are a senior acquiring story editor brainstorming original, commercially compelling book concepts.",
            craft_lens,
            "Output format: strictly numbered `1.` through `10.` entries. For each entry write:\n"
            "N. **Title** — 2–4 crisp sentences covering protagonist, hook, concrete conflict, and ending promise (without spoiling every detail).",
        ]
    )

    user_parts = [
        f"Generate {count} genuinely distinct story concepts for a **{prof.label}** ({book.audience.value}).",
    ]
    if book.idea.strip():
        user_parts.append(f"Author's initial spark / direction (honor or riff creatively on this):\n{book.idea.strip()}")
    if book.title and book.title.strip().lower() != "untitled":
        user_parts.append(f"Working title: {book.title.strip()}")
    if ref_text.strip():
        user_parts.append(f"Uploaded reference notes:\n{ref_text.strip()[:18000]}")

    res = await llm_client.complete(
        system=system,
        user="\n\n".join(user_parts),
        task="seed",
        max_tokens=4000,
        creativity="high",
        stream_cb=stream_cb,
    )
    warn_if_cut(res.stop_reason, on_warning, "Seed candidates")
    return strip_llm_chatter(res.text)
