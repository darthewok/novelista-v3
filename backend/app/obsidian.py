from __future__ import annotations

import hashlib
import json
import logging
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app import llm_client
from app.enums import BookFormat, ChapterStatus, POV, Tense, WritingStyle
from app.export.helpers import collect_chapters, unit_heading_text
from app.export.markdown_export import build_markdown
from app.formats import get_profile
from app.pipeline import ai_checker, canon_gen
from app.pipeline._common import (
    UNIT_HEADING_RE,
    WarnCB,
    allowed_name_sources,
    load_prompt,
    parse_llm_json,
    parse_outline_units,
    strip_llm_chatter,
    warn_if_cut,
)
from app.pipeline.characters_gen import scrub_banned_names
from app.pipeline.directives import build_directives, per_unit_word_target
from app.storage import atomic_write_json, atomic_write_text, backup_prev, read_json, read_text, safe_slug
from app.store import (
    Book,
    ChapterRec,
    book_dir,
    book_lock,
    chapter_path,
    get_book,
    report_path,
    save_book,
    snapshot_chapter,
)

log = logging.getLogger("novelista.obsidian")

OBSIDIAN_ROLES: tuple[str, ...] = (
    "ignore",
    "seed",
    "world",
    "characters",
    "voice",
    "canon",
    "mystery",
    "outline",
    "chapter",
    "reference",
)
VALID_ROLE_SET = set(OBSIDIAN_ROLES)

SKIP_DIRS = {".obsidian", ".trash", ".git", "node_modules", "__pycache__", ".venv", ".idea", ".vscode"}
MAX_NOTES = 2000
MAX_NOTE_BYTES = 1_000_000
MAX_REF_CHARS = 120_000

_MEDIA_EXTS = {
    ".png", ".jpg", ".jpeg", ".webp", ".gif", ".svg", ".bmp",
    ".pdf", ".mp3", ".wav", ".ogg", ".m4a", ".mp4", ".mov", ".webm", ".canvas",
}

_FM_RE = re.compile(r"^---\n([\s\S]*?)\n(?:---|\.\.\.)(?:\n|$)")
_COMMENT_RE = re.compile(r"%%[\s\S]*?%%")
_DATAVIEW_BLOCK_RE = re.compile(r"```(?:dataview|dataviewjs|tasks|obsidian-[a-z0-9_-]*)\b[\s\S]*?```", re.IGNORECASE)
_CODE_FENCE_RE = re.compile(r"```[\s\S]*?```|`[^`\n]+`")
_INLINE_TAG_RE = re.compile(r"(?:^|(?<=\s))#([A-Za-z][A-Za-z0-9_/-]*)")
_EMBED_RE = re.compile(r"!\[\[([^\]]+)\]\]")
_WIKILINK_RE = re.compile(r"(?<!\!)\[\[([^\]\n]+)\]\]")
_CALLOUT_RE = re.compile(r"^>\s*\[!([a-zA-Z0-9_-]+)\][+-]?\s*(.*)$", re.MULTILINE)
_LEADING_NUM_RE = re.compile(r"(?:ch(?:apter)?|spread|part|scene)?[\s._-]*(\d+)\b", re.IGNORECASE)


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _clean_path_input(raw: str) -> str:
    s = (raw or "").strip()
    if len(s) >= 2 and ((s[0] == '"' and s[-1] == '"') or (s[0] == "'" and s[-1] == "'")):
        s = s[1:-1].strip()
    return s


def resolve_vault_root(vault_path: str) -> Path:
    """Validate and resolve a local Obsidian vault directory path."""
    cleaned = _clean_path_input(vault_path)
    if not cleaned:
        raise ValueError("Vault path cannot be empty.")
    p = Path(cleaned).expanduser()
    if not p.exists():
        raise ValueError(f"Vault path does not exist: {cleaned}")
    if not p.is_dir():
        raise ValueError(f"Vault path is not a directory: {cleaned}")
    return p.resolve()


def resolve_write_back_dir(vault_root: Path, write_back_dir: str) -> Path:
    """Resolve `write_back_dir` strictly inside `vault_root`, rejecting traversal or absolute paths."""
    cleaned = _clean_path_input(write_back_dir).replace("\\", "/")
    if not cleaned or "\x00" in cleaned or ":" in cleaned or cleaned.startswith("/"):
        raise ValueError("Write-back subfolder must be a relative path inside the vault.")
    raw = cleaned.strip("/")
    if not raw:
        raise ValueError("Write-back subfolder cannot be empty.")
    parts = [pt for pt in raw.split("/") if pt]
    if not parts or any(pt in {".", ".."} or pt.startswith(".") for pt in parts):
        raise ValueError("Write-back subfolder cannot contain '.', '..', or hidden directories.")
    root = vault_root.resolve()
    cand = (root / Path(*parts)).resolve()
    if cand == root or not cand.is_relative_to(root):
        raise ValueError("Write-back subfolder must stay strictly inside the vault.")
    return cand


def browse_directories(path: str = "") -> dict[str, Any]:
    """List local directories so users can pick an arbitrary Obsidian vault or book folder."""
    home = Path.home().resolve()
    quick: list[dict[str, str]] = [{"label": "Home", "path": str(home)}]
    for name in ("Documents", "Desktop", "Obsidian"):
        cand = home / name
        if cand.exists() and cand.is_dir():
            quick.append({"label": name, "path": str(cand.resolve())})
    if os.name == "nt":
        for code in range(ord("C"), ord("Z") + 1):
            drv = Path(f"{chr(code)}:\\")
            try:
                if drv.exists():
                    quick.append({"label": f"{chr(code)}:", "path": str(drv)})
            except OSError:
                continue

    cleaned = _clean_path_input(path)
    target = Path(cleaned).expanduser().resolve() if cleaned else home
    if not target.exists() or not target.is_dir():
        raise ValueError(f"Folder not found: {cleaned or target}")

    dirs: list[dict[str, Any]] = []
    try:
        entries = sorted(target.iterdir(), key=lambda p: p.name.lower())
    except OSError as exc:
        raise ValueError(f"Cannot read folder '{target}': {exc}") from exc

    for entry in entries:
        try:
            if not entry.is_dir():
                continue
            nm = entry.name
            if nm.startswith(".") or nm in {"$RECYCLE.BIN", "System Volume Information"}:
                continue
            is_vault = (entry / ".obsidian").is_dir()
            dirs.append({"name": nm, "path": str(entry.resolve()), "is_obsidian_vault": is_vault})
        except OSError:
            continue

    parent = str(target.parent) if target.parent != target else None
    return {
        "current": str(target),
        "parent": parent,
        "is_obsidian_vault": (target / ".obsidian").is_dir(),
        "quick_roots": quick,
        "directories": dirs[:300],
    }


def _fallback_yaml_parse(yaml_text: str) -> dict[str, Any]:
    """Minimal pure-Python YAML key/value & list parser used if PyYAML is unavailable."""
    out: dict[str, Any] = {}
    cur_list_key: str | None = None
    for raw_ln in yaml_text.splitlines():
        if not raw_ln.strip() or raw_ln.lstrip().startswith("#"):
            continue
        m_item = re.match(r"^\s+-\s+(.*)$", raw_ln)
        if m_item and cur_list_key:
            val = m_item.group(1).strip().strip("\"'")
            out.setdefault(cur_list_key, [])
            if isinstance(out[cur_list_key], list):
                out[cur_list_key].append(val)
            continue
        cur_list_key = None
        m_kv = re.match(r"^([A-Za-z0-9_.-]+)\s*:\s*(.*)$", raw_ln)
        if not m_kv:
            continue
        k, raw_v = m_kv.group(1).strip(), m_kv.group(2).strip()
        if not raw_v:
            out[k] = []
            cur_list_key = k
            continue
        if raw_v.startswith("[") and raw_v.endswith("]"):
            inner = raw_v[1:-1].strip()
            out[k] = [p.strip().strip("\"'") for p in inner.split(",") if p.strip()] if inner else []
            continue
        low = raw_v.lower()
        if low in {"true", "yes"}:
            out[k] = True
        elif low in {"false", "no"}:
            out[k] = False
        elif re.fullmatch(r"-?\d+", raw_v):
            out[k] = int(raw_v)
        else:
            out[k] = raw_v.strip("\"'")
    return out


def parse_frontmatter(raw: str) -> tuple[dict[str, Any], str]:
    """Extract leading YAML frontmatter dictionary and return `(metadata, body)`."""
    text = (raw or "").lstrip("\ufeff").replace("\r\n", "\n").replace("\r", "\n")
    m = _FM_RE.match(text)
    if not m:
        return {}, text
    yaml_src = m.group(1)
    body = text[m.end() :]
    parsed: Any = None
    try:
        import yaml  # type: ignore[import-untyped]

        parsed = yaml.safe_load(yaml_src)
    except Exception:
        parsed = _fallback_yaml_parse(yaml_src)
    if not isinstance(parsed, dict):
        parsed = {}
    return parsed, body


def extract_tags(fm: dict[str, Any], body: str) -> list[str]:
    """Collect normalized lowercase tags from YAML frontmatter and inline `#tags`."""
    seen: set[str] = set()
    tags: list[str] = []

    def _add(raw_tag: str) -> None:
        t = str(raw_tag or "").strip().lstrip("#").strip().lower()
        if t and t not in seen:
            seen.add(t)
            tags.append(t)

    for key in ("tags", "tag"):
        val = fm.get(key)
        if isinstance(val, list):
            for item in val:
                _add(str(item))
        elif isinstance(val, str):
            for part in re.split(r"[\s,]+", val):
                _add(part)

    stripped_body = _CODE_FENCE_RE.sub(" ", body or "")
    for m in _INLINE_TAG_RE.finditer(stripped_body):
        _add(m.group(1))
    return tags


def extract_wikilinks(body: str) -> list[str]:
    """Extract deduplicated target note names from `[[Target|Alias]]` / `[[Target#Heading]]`."""
    seen: set[str] = set()
    links: list[str] = []
    for m in _WIKILINK_RE.finditer(body or ""):
        inner = m.group(1).strip()
        target = inner.split("|", 1)[0].split("#", 1)[0].strip()
        if target and target not in seen:
            seen.add(target)
            links.append(target)
    return links


def _extract_heading_title(body: str, fallback_stem: str) -> str:
    for ln in (body or "").splitlines():
        s = ln.strip()
        if s.startswith("# "):
            return s[2:].strip()[:120] or fallback_stem
    return fallback_stem


def _extract_markdown_section(body: str, heading: str) -> str:
    target = heading.strip().lower()
    lines = (body or "").splitlines()
    start_idx: int | None = None
    start_lvl = 1
    for i, ln in enumerate(lines):
        m = re.match(r"^(#{1,6})\s+(.+?)\s*$", ln.strip())
        if m and m.group(2).strip().rstrip("#").strip().lower() == target:
            start_idx = i
            start_lvl = len(m.group(1))
            break
    if start_idx is None:
        return body
    out: list[str] = []
    for ln in lines[start_idx + 1 :]:
        m = re.match(r"^(#{1,6})\s+", ln.strip())
        if m and len(m.group(1)) <= start_lvl:
            break
        out.append(ln)
    return "\n".join(out).strip() or body


def clean_obsidian_markdown(
    body: str,
    *,
    note_index: dict[str, tuple[str, str]],
    depth: int = 0,
    seen: set[str] | None = None,
) -> str:
    """Convert Obsidian syntax (embeds, wikilinks, comments, dataview blocks, callouts) to clean Markdown."""
    visited = set(seen or set())
    txt = _COMMENT_RE.sub("", (body or "").replace("\r\n", "\n"))
    txt = _DATAVIEW_BLOCK_RE.sub("", txt)

    def _replace_embed(m: re.Match[str]) -> str:
        raw_inner = m.group(1).strip()
        target_part = raw_inner.split("|", 1)[0].strip()
        if "#" in target_part:
            note_ref, sec_ref = target_part.split("#", 1)
        else:
            note_ref, sec_ref = target_part, ""
        note_clean = note_ref.strip()
        ext = Path(note_clean).suffix.lower()
        if ext in _MEDIA_EXTS:
            return ""
        lookup = note_clean.removesuffix(".md").strip().lower()
        stem_lookup = Path(lookup).name
        hit = note_index.get(lookup) or note_index.get(stem_lookup)
        if not hit or depth >= 2:
            return ""
        canonical_key, embedded_raw = hit
        if canonical_key in visited:
            return ""
        _, emb_body = parse_frontmatter(embedded_raw)
        if sec_ref.strip():
            emb_body = _extract_markdown_section(emb_body, sec_ref)
        return clean_obsidian_markdown(
            emb_body,
            note_index=note_index,
            depth=depth + 1,
            seen=visited | {canonical_key},
        )

    txt = _EMBED_RE.sub(_replace_embed, txt)

    def _replace_wikilink(m: re.Match[str]) -> str:
        inner = m.group(1).strip()
        target_part, *alias_parts = inner.split("|", 1)
        if alias_parts and alias_parts[0].strip():
            return alias_parts[0].strip()
        if "#" in target_part:
            note_part, head_part = target_part.split("#", 1)
            note_name = note_part.strip().split("/")[-1].removesuffix(".md")
            return f"{note_name} — {head_part.strip()}" if note_name else head_part.strip()
        return target_part.strip().split("/")[-1].removesuffix(".md")

    txt = _WIKILINK_RE.sub(_replace_wikilink, txt)
    txt = _CALLOUT_RE.sub(
        lambda m: f"> **{(m.group(2).strip() or m.group(1).strip().title())}**",
        txt,
    )
    txt = re.sub(r"\n{3,}", "\n\n", txt).strip()
    return txt


_ROLE_ALIASES: dict[str, str] = {
    "seed": "seed",
    "premise": "seed",
    "pitch": "seed",
    "logline": "seed",
    "idea": "seed",
    "synopsis": "seed",
    "overview": "seed",
    "blurb": "seed",
    "concept": "seed",
    "story": "seed",
    "world": "world",
    "worldbuilding": "world",
    "setting": "world",
    "settings": "world",
    "location": "world",
    "locations": "world",
    "place": "world",
    "places": "world",
    "lore": "world",
    "magic": "world",
    "faction": "world",
    "factions": "world",
    "culture": "world",
    "geography": "world",
    "character": "characters",
    "characters": "characters",
    "cast": "characters",
    "npc": "characters",
    "npcs": "characters",
    "protagonist": "characters",
    "antagonist": "characters",
    "person": "characters",
    "people": "characters",
    "voice": "voice",
    "style": "voice",
    "tone": "voice",
    "diction": "voice",
    "canon": "canon",
    "continuity": "canon",
    "rule": "canon",
    "rules": "canon",
    "timeline": "canon",
    "bible": "canon",
    "facts": "canon",
    "constraint": "canon",
    "constraints": "canon",
    "mystery": "mystery",
    "secret": "mystery",
    "secrets": "mystery",
    "twist": "mystery",
    "twists": "mystery",
    "spoiler": "mystery",
    "spoilers": "mystery",
    "culprit": "mystery",
    "outline": "outline",
    "plot": "outline",
    "beat": "outline",
    "beats": "outline",
    "beat_sheet": "outline",
    "beatsheet": "outline",
    "storyboard": "outline",
    "arc": "outline",
    "arcs": "outline",
    "structure": "outline",
    "plan": "outline",
    "chapter": "chapter",
    "chapters": "chapter",
    "draft": "chapter",
    "drafts": "chapter",
    "manuscript": "chapter",
    "prose": "chapter",
    "spread": "chapter",
    "spreads": "chapter",
    "reference": "reference",
    "research": "reference",
    "inspiration": "reference",
    "clipping": "reference",
    "clippings": "reference",
    "ignore": "ignore",
    "archive": "ignore",
    "archived": "ignore",
    "template": "ignore",
    "templates": "ignore",
    "trash": "ignore",
    "meta": "ignore",
    "scratch": "ignore",
    "daily": "ignore",
    "journal": "ignore",
}


def _match_alias_tokens(text: str) -> str | None:
    for tok in re.findall(r"[a-z]+", (text or "").lower()):
        if tok in _ROLE_ALIASES:
            return _ROLE_ALIASES[tok]
    return None


def _heuristic_role(
    rel_path: str,
    folder: str,
    title: str,
    fm: dict[str, Any],
    tags: list[str],
    body: str,
) -> tuple[str, str]:
    # 1. Explicit frontmatter property
    for key in ("novelista_role", "role", "type", "category", "kind"):
        val = fm.get(key)
        if isinstance(val, str) and val.strip():
            mapped = _match_alias_tokens(val)
            if mapped:
                return mapped, f"frontmatter:{key}={val.strip()}"

    # 2. Tags
    for t in tags:
        for seg in t.split("/"):
            mapped = _match_alias_tokens(seg)
            if mapped:
                return mapped, f"tag:#{t}"

    # 3. Folder hierarchy
    if folder:
        for seg in reversed(folder.split("/")):
            mapped = _match_alias_tokens(seg)
            if mapped:
                return mapped, f"folder:{seg}"

    # 4. Filename stem & top heading
    stem = Path(rel_path).stem
    for label, cand in (("filename", stem), ("heading", title)):
        if re.match(r"^(?:ch(?:apter)?|spread|part)[\s._-]*\d+\b", cand.strip(), re.IGNORECASE):
            bullet_lines = sum(1 for ln in body.splitlines() if ln.strip().startswith(("- ", "* ", "• ")))
            words = len(body.split())
            if words >= 300 and bullet_lines < max(3, words // 70):
                return "chapter", f"{label}:chapter-prose"
            return "outline", f"{label}:chapter-note"
        mapped = _match_alias_tokens(cand)
        if mapped:
            return mapped, f"{label}:{cand}"

    return "ignore", "unclassified"


def _summarize_frontmatter(fm: dict[str, Any]) -> dict[str, Any]:
    keep = ("title", "name", "type", "role", "novelista_role", "genre", "pov", "tense", "pronouns", "gender", "age", "chapter", "order", "format", "status")
    out: dict[str, Any] = {}
    for k in keep:
        if k in fm and fm[k] not in (None, "", []):
            out[k] = fm[k]
    return out


def _collect_vault_notes(vault_root: Path, write_back_dir: str = "Novelista Export") -> dict[str, Any]:
    try:
        wb_resolved: Path | None = resolve_write_back_dir(vault_root, write_back_dir)
    except ValueError:
        wb_resolved = None

    raw_records: list[dict[str, Any]] = []
    note_index: dict[str, tuple[str, str]] = {}
    truncated = False

    for cur_root, dirs, files in os.walk(vault_root):
        cur_path = Path(cur_root)
        # Prune skipped, hidden, write-back, or symlink-escaping directories
        kept_dirs: list[str] = []
        for d in sorted(dirs, key=str.lower):
            if d.startswith(".") or d in SKIP_DIRS:
                continue
            cand = cur_path / d
            try:
                res = cand.resolve()
            except OSError:
                continue
            if not res.is_relative_to(vault_root):
                continue
            if wb_resolved is not None and (res == wb_resolved or res.is_relative_to(wb_resolved)):
                continue
            kept_dirs.append(d)
        dirs[:] = kept_dirs

        for fname in sorted(files, key=str.lower):
            if not fname.lower().endswith(".md"):
                continue
            fpath = cur_path / fname
            try:
                res = fpath.resolve()
                if not res.is_relative_to(vault_root) or not res.is_file():
                    continue
                stat = res.stat()
                if stat.st_size > MAX_NOTE_BYTES:
                    continue
                raw = res.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue

            rel_path = res.relative_to(vault_root).as_posix()
            folder = Path(rel_path).parent.as_posix()
            if folder == ".":
                folder = ""
            content_hash = hashlib.sha256(raw.encode("utf-8", errors="replace")).hexdigest()[:16]
            mtime_iso = datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            key_full = rel_path.removesuffix(".md").lower()
            key_stem = Path(rel_path).stem.lower()
            note_index[key_full] = (key_full, raw)
            note_index.setdefault(key_stem, (key_full, raw))

            raw_records.append(
                {
                    "rel_path": rel_path,
                    "name": Path(rel_path).stem,
                    "folder": folder,
                    "hash": content_hash,
                    "mtime": mtime_iso,
                    "size_bytes": stat.st_size,
                    "raw": raw,
                }
            )
            if len(raw_records) >= MAX_NOTES:
                truncated = True
                break
        if truncated:
            break

    notes: list[dict[str, Any]] = []
    for rec in raw_records:
        fm, body = parse_frontmatter(rec["raw"])
        tags = extract_tags(fm, body)
        wikilinks = extract_wikilinks(body)
        title = str(fm.get("title") or fm.get("name") or _extract_heading_title(body, rec["name"])).strip()
        cleaned = clean_obsidian_markdown(body, note_index=note_index)
        words = len(cleaned.split())
        excerpt = re.sub(r"\s+", " ", cleaned).strip()[:180]
        suggested_role, reason = _heuristic_role(rec["rel_path"], rec["folder"], title, fm, tags, cleaned)
        notes.append(
            {
                "rel_path": rec["rel_path"],
                "name": rec["name"],
                "title": title or rec["name"],
                "folder": rec["folder"],
                "hash": rec["hash"],
                "mtime": rec["mtime"],
                "word_count": words,
                "char_count": len(cleaned),
                "tags": tags,
                "wikilinks": wikilinks,
                "frontmatter": _summarize_frontmatter(fm),
                "_full_fm": fm,
                "_cleaned": cleaned,
                "excerpt": excerpt,
                "suggested_role": suggested_role,
                "suggestion_reason": reason,
            }
        )

    return {
        "vault_root": vault_root,
        "notes": notes,
        "note_index": note_index,
        "truncated": truncated,
    }


def _infer_book_defaults(vault_root: Path, notes: list[dict[str, Any]], folder_rules: dict[str, str], file_overrides: dict[str, str]) -> dict[str, Any]:
    title = vault_root.name.replace("_", " ").strip() or "Untitled"
    genre = ""
    pov = POV.third_limited.value
    tense = Tense.past.value
    book_format = BookFormat.novel.value
    language = "English"
    chapter_count: int | None = None
    target_words: int | None = None

    def _eff(n: dict[str, Any]) -> str:
        return resolve_note_role(n, {"folder_rules": folder_rules, "file_overrides": file_overrides, "tag_rules": {}})

    # Prefer frontmatter from seed / outline / chapter notes
    priority = [n for n in notes if _eff(n) in {"seed", "outline"}] + [n for n in notes if _eff(n) != "ignore"]
    for n in priority:
        fm = n.get("_full_fm") or {}
        if isinstance(fm.get("book_title"), str) and fm["book_title"].strip():
            title = fm["book_title"].strip()
        if isinstance(fm.get("genre"), str) and fm["genre"].strip() and not genre:
            genre = fm["genre"].strip()
        if isinstance(fm.get("language"), str) and fm["language"].strip():
            language = fm["language"].strip()
        raw_pov = str(fm.get("pov") or "").lower()
        if "first" in raw_pov:
            pov = POV.first_person.value
        elif "omni" in raw_pov:
            pov = POV.third_omniscient.value
        elif "second" in raw_pov:
            pov = POV.second_person.value
        raw_tense = str(fm.get("tense") or "").lower()
        if "present" in raw_tense:
            tense = Tense.present.value
        raw_fmt = str(fm.get("book_format") or fm.get("format") or "").lower().replace(" ", "_")
        if raw_fmt in {f.value for f in BookFormat}:
            book_format = raw_fmt
        if isinstance(fm.get("chapter_count"), int) and fm["chapter_count"] > 0:
            chapter_count = fm["chapter_count"]
        if isinstance(fm.get("target_words"), int) and fm["target_words"] >= 150:
            target_words = fm["target_words"]

    # Estimate chapter count from mapped outline or chapter notes if still unset
    outline_notes = [n for n in notes if _eff(n) == "outline"]
    if chapter_count is None and outline_notes:
        parsed_total = 0
        for on in outline_notes:
            parsed_total += len(parse_outline_units(on.get("_cleaned") or ""))
        if parsed_total >= 1:
            chapter_count = parsed_total
        elif len(outline_notes) >= 2:
            chapter_count = len(outline_notes)

    if chapter_count is not None and target_words is None:
        if chapter_count <= 5:
            book_format = BookFormat.short_story.value
            target_words = chapter_count * 1500
        elif chapter_count <= 12:
            book_format = BookFormat.novella.value
            target_words = chapter_count * 2500
        else:
            book_format = BookFormat.novel.value
            target_words = chapter_count * 3200

    prof = get_profile(BookFormat(book_format))
    return {
        "title": title[:200],
        "genre": genre[:120],
        "pov": pov,
        "tense": tense,
        "book_format": book_format,
        "audience": prof.default_audience.value,
        "language": language[:60],
        "chapter_count": chapter_count or prof.default_units,
        "target_words": target_words or prof.default_words,
    }


def _build_folder_and_file_suggestions(notes: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, str], dict[str, str]]:
    by_folder: dict[str, list[dict[str, Any]]] = {}
    for n in notes:
        by_folder.setdefault(n["folder"], []).append(n)

    folder_summaries: list[dict[str, Any]] = []
    folder_rules: dict[str, str] = {}
    file_overrides: dict[str, str] = {}

    for folder in sorted(by_folder.keys(), key=lambda x: (x != "", x.lower())):
        items = by_folder[folder]
        counts: dict[str, int] = {}
        for it in items:
            r = it["suggested_role"]
            if r != "ignore":
                counts[r] = counts.get(r, 0) + 1
        top_role = "ignore"
        if counts:
            best_role, best_cnt = max(counts.items(), key=lambda kv: kv[1])
            if folder and best_cnt >= max(1, int(len(items) * 0.6)):
                top_role = best_role
                folder_rules[folder] = best_role
        folder_summaries.append(
            {
                "path": folder,
                "label": folder or "(Vault Root)",
                "note_count": len(items),
                "total_words": sum(i["word_count"] for i in items),
                "suggested_role": top_role,
            }
        )

    for n in notes:
        r = n["suggested_role"]
        if r == "ignore":
            continue
        folder_rule = folder_rules.get(n["folder"])
        if folder_rule != r:
            file_overrides[n["rel_path"]] = r

    return folder_summaries, folder_rules, file_overrides


def _public_note_view(n: dict[str, Any], *, effective_role: str | None = None, change: str | None = None) -> dict[str, Any]:
    out = {
        "rel_path": n["rel_path"],
        "name": n["name"],
        "title": n["title"],
        "folder": n["folder"],
        "hash": n["hash"],
        "mtime": n["mtime"],
        "word_count": n["word_count"],
        "char_count": n["char_count"],
        "tags": n["tags"],
        "wikilinks": n["wikilinks"],
        "frontmatter": n["frontmatter"],
        "excerpt": n["excerpt"],
        "suggested_role": n["suggested_role"],
        "suggestion_reason": n["suggestion_reason"],
    }
    if effective_role is not None:
        out["effective_role"] = effective_role
    if change is not None:
        out["change"] = change
    return out


def scan_vault(vault_path: str, *, write_back_dir: str = "Novelista Export") -> dict[str, Any]:
    """Scan an arbitrary Obsidian vault directory and suggest folder/file mappings."""
    vault_root = resolve_vault_root(vault_path)
    collected = _collect_vault_notes(vault_root, write_back_dir=write_back_dir)
    notes = collected["notes"]
    folders, folder_rules, file_overrides = _build_folder_and_file_suggestions(notes)
    suggested_book = _infer_book_defaults(vault_root, notes, folder_rules, file_overrides)

    role_counts: dict[str, int] = {r: 0 for r in OBSIDIAN_ROLES}
    for n in notes:
        eff = resolve_note_role(n, {"folder_rules": folder_rules, "file_overrides": file_overrides, "tag_rules": {}})
        role_counts[eff] = role_counts.get(eff, 0) + 1

    return {
        "vault_path": str(vault_root),
        "is_obsidian_vault": (vault_root / ".obsidian").is_dir(),
        "total_notes": len(notes),
        "truncated": collected["truncated"],
        "folders": folders,
        "notes": [_public_note_view(n) for n in notes],
        "suggested_folder_rules": folder_rules,
        "suggested_file_overrides": file_overrides,
        "suggested_book": suggested_book,
        "role_counts": role_counts,
    }


def resolve_note_role(note: dict[str, Any], config: dict[str, Any]) -> str:
    """Resolve effective Novelista role: file_overrides > longest folder_rule > tag_rule > frontmatter > ignore."""
    file_overrides = config.get("file_overrides") or {}
    rel_path = note["rel_path"]
    if rel_path in file_overrides:
        cand = str(file_overrides[rel_path]).strip().lower()
        if cand in VALID_ROLE_SET:
            return cand

    folder_rules = config.get("folder_rules") or {}
    note_folder = (note.get("folder") or "").strip("/")
    best_match: tuple[int, str] | None = None
    for raw_folder, raw_role in folder_rules.items():
        role = str(raw_role or "").strip().lower()
        if role not in VALID_ROLE_SET or role == "ignore":
            continue
        rf = str(raw_folder or "").strip("/")
        if rf == "":
            if note_folder == "":
                cand_len = 0
                if best_match is None or cand_len > best_match[0]:
                    best_match = (cand_len, role)
        elif note_folder == rf or note_folder.startswith(rf + "/"):
            cand_len = len(rf) + 1
            if best_match is None or cand_len > best_match[0]:
                best_match = (cand_len, role)
    if best_match is not None:
        return best_match[1]

    tag_rules = {str(k).lstrip("#").strip().lower(): str(v).strip().lower() for k, v in (config.get("tag_rules") or {}).items()}
    for t in note.get("tags") or []:
        t_low = str(t).lstrip("#").strip().lower()
        if t_low in tag_rules and tag_rules[t_low] in VALID_ROLE_SET:
            return tag_rules[t_low]

    fm = note.get("frontmatter") or {}
    fm_role = str(fm.get("novelista_role") or "").strip().lower()
    if fm_role in VALID_ROLE_SET:
        return fm_role

    return "ignore"


def obsidian_config_path(bdir: Path) -> Path:
    return bdir / "obsidian.json"


def load_obsidian_config(bdir: Path) -> dict[str, Any] | None:
    data = read_json(obsidian_config_path(bdir))
    return data if isinstance(data, dict) else None


def save_obsidian_config(bdir: Path, cfg: dict[str, Any]) -> None:
    atomic_write_json(obsidian_config_path(bdir), cfg)


def normalize_mapping_config(raw: dict[str, Any]) -> dict[str, Any]:
    vault_root = resolve_vault_root(str(raw.get("vault_path") or ""))
    wb = str(raw.get("write_back_dir") or "Novelista Export").strip() or "Novelista Export"
    # Validate write_back_dir immediately so invalid paths are rejected up-front
    resolve_write_back_dir(vault_root, wb)

    def _clean_map(d: Any, *, strip_slashes: bool = False) -> dict[str, str]:
        if not isinstance(d, dict):
            return {}
        out: dict[str, str] = {}
        for k, v in d.items():
            role = str(v or "").strip().lower()
            if role not in VALID_ROLE_SET:
                continue
            key = str(k).replace("\\", "/").strip()
            if strip_slashes:
                key = key.strip("/")
            out[key] = role
        return out

    return {
        "vault_path": str(vault_root),
        "folder_rules": _clean_map(raw.get("folder_rules"), strip_slashes=True),
        "file_overrides": _clean_map(raw.get("file_overrides"), strip_slashes=False),
        "tag_rules": {str(k).lstrip("#").strip().lower(): r for k, r in _clean_map(raw.get("tag_rules")).items()},
        "write_back_dir": wb.replace("\\", "/").strip("/"),
        "auto_sync_before_draft": bool(raw.get("auto_sync_before_draft", True)),
        "import_chapters": bool(raw.get("import_chapters", False)),
    }


def _strip_unit_prefix(title: str) -> str:
    return re.sub(
        r"^(?:(?:ch(?:apter)?|spread|part)\.?\s*\d+\s*[:\-—–.]*|\d+\s*[:\-—–.]+)\s*",
        "",
        (title or "").strip(),
        flags=re.IGNORECASE,
    ).strip()


def _note_sort_key(note: dict[str, Any]) -> tuple[int, str]:
    fm = note.get("_full_fm") or note.get("frontmatter") or {}
    for key in ("chapter", "order", "number", "unit", "spread"):
        val = fm.get(key)
        if isinstance(val, int) and val > 0:
            return (val, note["rel_path"].lower())
        if isinstance(val, str) and val.strip().isdigit():
            return (int(val.strip()), note["rel_path"].lower())
    m = _LEADING_NUM_RE.search(Path(note["rel_path"]).stem)
    if m:
        return (int(m.group(1)), note["rel_path"].lower())
    return (999999, note["rel_path"].lower())


def _format_character_entry(note: dict[str, Any]) -> str:
    fm = note.get("_full_fm") or {}
    display_name = str(fm.get("name") or fm.get("title") or note["title"] or note["name"]).strip()
    meta_bits: list[str] = []
    pronouns = fm.get("pronouns") or fm.get("gender")
    if pronouns:
        pr = str(pronouns).strip()
        if pr.lower() in {"female", "f", "woman", "she"}:
            pr = "she/her"
        elif pr.lower() in {"male", "m", "man", "he"}:
            pr = "he/him"
        meta_bits.append(pr)
    age = fm.get("age")
    if age not in (None, ""):
        meta_bits.append(f"age {age}")
    for key in ("occupation", "workplace", "role_in_story", "affiliation"):
        if fm.get(key):
            meta_bits.append(str(fm[key]).strip())
    header = f"## {display_name} ({', '.join(meta_bits)})" if meta_bits else f"## {display_name}"
    body = (note.get("_cleaned") or "").strip()
    # Avoid repeating identical H1 name immediately below
    lines = body.splitlines()
    if lines and lines[0].strip().lstrip("# ").strip().lower() == display_name.lower():
        body = "\n".join(lines[1:]).strip()
    return f"{header}\n\n{body}".strip() if body else header


def _join_sections(notes: list[dict[str, Any]]) -> str:
    blocks: list[str] = []
    for n in sorted(notes, key=lambda x: (x["folder"].lower(), x["rel_path"].lower())):
        body = (n.get("_cleaned") or "").strip()
        if not body:
            continue
        lines = body.splitlines()
        title = n["title"].strip()
        if lines and lines[0].strip().startswith("# "):
            body_clean = body
            if lines[0].strip()[2:].strip().lower() == title.lower():
                body_clean = "\n".join(lines[1:]).strip()
            blocks.append(f"## {title}\n\n{body_clean}".strip())
        else:
            blocks.append(f"## {title}\n\n{body}".strip())
    return "\n\n---\n\n".join(blocks).strip()


def _compile_outline_notes(book: Book, notes: list[dict[str, Any]]) -> tuple[str, bool]:
    prof = get_profile(book.book_format)
    ordered = sorted(notes, key=_note_sort_key)
    combined_raw = "\n\n".join((n.get("_cleaned") or "").strip() for n in ordered if (n.get("_cleaned") or "").strip()).strip()
    if not combined_raw:
        return "", False

    # Case 1: Already structured outline headings (`### Ch N:` etc.)
    if len(ordered) == 1:
        units = parse_outline_units(combined_raw)
        if units:
            return combined_raw + "\n", False
        return combined_raw + "\n", True

    # Case 2: Multiple chapter-level outline notes
    has_unit_headings = sum(1 for n in ordered if parse_outline_units(n.get("_cleaned") or ""))
    if has_unit_headings == len(ordered):
        return "\n\n".join((n.get("_cleaned") or "").strip() for n in ordered) + "\n", False

    sections: list[str] = []
    used_nums: set[int] = set()
    next_seq = 1
    for n in ordered:
        body = (n.get("_cleaned") or "").strip()
        if not body:
            continue
        if parse_outline_units(body):
            sections.append(body)
            for num, _ in parse_outline_units(body):
                used_nums.add(num)
            continue
        num_guess, _ = _note_sort_key(n)
        if num_guess >= 999999 or num_guess in used_nums:
            while next_seq in used_nums:
                next_seq += 1
            num = next_seq
        else:
            num = num_guess
        used_nums.add(num)
        next_seq = max(next_seq, num + 1)

        raw_title = _strip_unit_prefix(n["title"])
        clean_title = raw_title or n["title"] or f"{prof.unit_label} {num}"
        fm = n.get("_full_fm") or {}
        pov_line = f"- POV: {fm['pov']}\n" if fm.get("pov") else ""
        # Strip duplicate H1 matching title
        lines = body.splitlines()
        if lines and lines[0].strip().startswith("# "):
            body = "\n".join(lines[1:]).strip()
        sections.append(f"### {prof.unit_label} {num}: {clean_title}\n{pov_line}\n{body}".strip())

    compiled = "\n\n".join(sections).strip() + "\n"
    units = parse_outline_units(compiled)
    return compiled, len(units) == 0


def _artifact_stats(book: Book, bdir: Path) -> dict[str, Any]:
    files = {
        "seed": bdir / "seed.txt",
        "world": bdir / "world.md",
        "characters": bdir / "characters.md",
        "voice": bdir / "voice.md",
        "canon": bdir / "canon.md",
        "mystery": bdir / "mystery.md",
        "outline": bdir / "outline.md",
        "summary": bdir / "summary.md",
        "reference": bdir / "uploads" / "_vault_ref.md",
    }
    stats: dict[str, Any] = {}
    for k, p in files.items():
        txt = read_text(p)
        stats[k] = {"chars": len(txt), "words": len(txt.split()) if txt.strip() else 0, "exists": bool(txt.strip())}
    stats["chapters"] = {"count": len(book.chapters), "drafted": sum(1 for c in book.chapters if c.status != ChapterStatus.pending)}
    return stats


def _outline_source_signature(outline_notes: list[dict[str, Any]]) -> str:
    if not outline_notes:
        return ""
    parts = sorted(f"{n['rel_path']}:{n['hash']}" for n in outline_notes)
    return hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest()[:16]


def outline_source_signature_for_config(cfg: dict[str, Any]) -> str:
    """Compute signature of currently saved outline source notes from `obsidian.json`."""
    hashes = cfg.get("note_hashes") or {}
    roles = cfg.get("note_roles") or {}
    parts = sorted(
        f"{rp}:{hashes[rp]}"
        for rp, role in roles.items()
        if role == "outline" and rp in hashes
    )
    if not parts:
        return ""
    return hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest()[:16]


def compile_vault_to_book(
    book: Book,
    bdir: Path,
    config: dict[str, Any],
    *,
    roles: list[str] | None = None,
    import_chapters: bool | None = None,
    backup: bool = True,
) -> dict[str, Any]:
    """Compile mapped Obsidian notes into Novelista's local book artifacts and update `obsidian.json`."""
    from app.routers.outline import sync_chapters

    prev_cfg = load_obsidian_config(bdir) or {}
    norm_cfg = normalize_mapping_config(config)
    vault_root = Path(norm_cfg["vault_path"])
    collected = _collect_vault_notes(vault_root, write_back_dir=norm_cfg["write_back_dir"])
    notes = collected["notes"]

    role_filter = {r.strip().lower() for r in roles if r.strip()} if roles else None
    do_import_chapters = norm_cfg["import_chapters"] if import_chapters is None else bool(import_chapters)
    if role_filter and "chapter" in role_filter and import_chapters is None:
        do_import_chapters = True

    by_role: dict[str, list[dict[str, Any]]] = {r: [] for r in OBSIDIAN_ROLES}
    new_hashes: dict[str, str] = {}
    new_roles_map: dict[str, str] = {}

    for n in notes:
        eff = resolve_note_role(n, norm_cfg)
        by_role[eff].append(n)
        if eff != "ignore":
            new_hashes[n["rel_path"]] = n["hash"]
            new_roles_map[n["rel_path"]] = eff

    warnings: list[str] = []
    synced_roles: list[str] = []
    needs_outline_structuring = False
    preserved_structured_sig = ""

    def _should_compile(role_name: str) -> bool:
        if role_filter is not None and role_name not in role_filter:
            return False
        return len(by_role[role_name]) > 0

    def _write_artifact(path: Path, content: str) -> None:
        if backup and path.exists():
            backup_prev(path)
        atomic_write_text(path, content)

    # 1. Seed
    if _should_compile("seed"):
        seed_notes = sorted(by_role["seed"], key=lambda x: x["rel_path"].lower())
        if len(seed_notes) == 1:
            seed_txt = (seed_notes[0].get("_cleaned") or "").strip()
        else:
            seed_txt = _join_sections(seed_notes)
        _write_artifact(bdir / "seed.txt", seed_txt)
        book.idea = seed_txt[:20_000]
        synced_roles.append("seed")

    # 2. World
    if _should_compile("world"):
        world_txt = _join_sections(by_role["world"])
        _write_artifact(bdir / "world.md", world_txt + ("\n" if world_txt else ""))
        if len(world_txt) > 5000:
            warnings.append(f"World notes compiled to {len(world_txt):,} chars; drafting prompts cap World Reference at 5,000 chars.")
        synced_roles.append("world")

    # 3. Characters
    if _should_compile("characters"):
        char_blocks = [_format_character_entry(n) for n in sorted(by_role["characters"], key=lambda x: x["rel_path"].lower())]
        chars_txt = "\n\n---\n\n".join(b for b in char_blocks if b).strip()
        _write_artifact(bdir / "characters.md", chars_txt + ("\n" if chars_txt else ""))
        if len(chars_txt) > 6000:
            warnings.append(f"Character notes compiled to {len(chars_txt):,} chars; drafting prompts cap Cast Reference at 6,000 chars.")
        synced_roles.append("characters")

    # 4. Voice
    if _should_compile("voice"):
        voice_notes = sorted(by_role["voice"], key=lambda x: x["rel_path"].lower())
        raw_voice = (voice_notes[0].get("_cleaned") or "").strip() if len(voice_notes) == 1 else _join_sections(voice_notes)
        if "Part 1 — Core Prose Discipline" not in raw_voice and "Part 1 - Core Prose Discipline" not in raw_voice:
            part1 = load_prompt("voice_default.md")
            voice_txt = f"{part1}\n\n---\n\n## Part 2 — Vault Voice Calibration\n\n{raw_voice}\n"
        else:
            voice_txt = raw_voice + "\n"
        if re.search(r'["“][^"”]{25,}["”]', raw_voice):
            warnings.append(
                "Voice note contains quoted dialogue/prose. Remember AI Checker blocks 7+ word verbatim matches with voice.md ('exemplar_copy')."
            )
        _write_artifact(bdir / "voice.md", voice_txt)
        synced_roles.append("voice")

    # 5. Canon (preserve existing `## Ch N` chapter-memory sections unless vault explicitly defines them!)
    if _should_compile("canon"):
        canon_notes = sorted(by_role["canon"], key=lambda x: x["rel_path"].lower())
        new_base = (canon_notes[0].get("_cleaned") or "").strip() if len(canon_notes) == 1 else _join_sections(canon_notes)
        existing_canon = read_text(bdir / "canon.md")
        _, existing_ch_sections = canon_gen._split_base_and_chapters(existing_canon)
        _, vault_has_ch_sections = canon_gen._split_base_and_chapters(new_base)
        if existing_ch_sections and not vault_has_ch_sections:
            canon_txt = f"{new_base}\n\n{existing_ch_sections}\n"
        else:
            canon_txt = new_base + "\n"
        _write_artifact(bdir / "canon.md", canon_txt)
        synced_roles.append("canon")

    # 6. Mystery / Author-only secrets
    if _should_compile("mystery"):
        myst_txt = _join_sections(by_role["mystery"])
        _write_artifact(bdir / "mystery.md", myst_txt + ("\n" if myst_txt else ""))
        synced_roles.append("mystery")

    # 7. Outline
    if _should_compile("outline"):
        prev_sig = str(prev_cfg.get("structured_outline_source_sig") or "")
        cur_sig = _outline_source_signature(by_role["outline"])
        existing_outline = read_text(bdir / "outline.md").strip()
        if (
            role_filter is None
            and prev_sig
            and cur_sig == prev_sig
            and existing_outline
            and parse_outline_units(existing_outline)
        ):
            preserved_structured_sig = prev_sig
        else:
            outline_txt, needs_outline_structuring = _compile_outline_notes(book, by_role["outline"])
            _write_artifact(bdir / "outline.md", outline_txt)
            if needs_outline_structuring:
                warnings.append(
                    "Outline notes had no '### Ch N: Title' headings yet. Click 'Structure Outline with AI' or add chapter headings before drafting."
                )
            else:
                sync_chapters(book, outline_txt)
            synced_roles.append("outline")
    else:
        preserved_structured_sig = str(prev_cfg.get("structured_outline_source_sig") or "")
        existing_outline = read_text(bdir / "outline.md").strip()
        if existing_outline and not parse_outline_units(existing_outline):
            needs_outline_structuring = True

    # 8. Optional Chapter Draft Import
    imported_chapters: list[int] = []
    if do_import_chapters and _should_compile("chapter"):
        prof = get_profile(book.book_format)
        ordered_ch = sorted(by_role["chapter"], key=_note_sort_key)
        used_ch: set[int] = set()
        seq = 1
        for n in ordered_ch:
            num_guess, _ = _note_sort_key(n)
            if num_guess >= 999999 or num_guess in used_ch:
                while seq in used_ch:
                    seq += 1
                ch_num = seq
            else:
                ch_num = num_guess
            used_ch.add(ch_num)
            seq = max(seq, ch_num + 1)

            ch_rec = book.get_chapter(ch_num)
            clean_title = _strip_unit_prefix(n["title"]) or n["title"]
            if not ch_rec:
                ch_rec = ChapterRec(number=ch_num, title=clean_title or f"{prof.unit_label} {ch_num}")
                book.chapters.append(ch_rec)
            elif clean_title:
                ch_rec.title = clean_title

            if ch_rec.status == ChapterStatus.accepted:
                warnings.append(f"Skipped accepted {prof.unit_label} {ch_num} during Obsidian sync.")
                continue

            body = (n.get("_cleaned") or "").strip()
            if not body:
                continue
            if not UNIT_HEADING_RE.match(body.splitlines()[0]):
                ch_text = f"## {prof.unit_label} {ch_num}: {ch_rec.title}\n\n{body}\n"
            else:
                ch_text = body + "\n"

            p = chapter_path(bdir, ch_num)
            existing_text = read_text(p)
            if existing_text.strip() != ch_text.strip():
                if existing_text.strip():
                    snapshot_chapter(bdir, ch_rec)
                atomic_write_text(p, ch_text)
                rep = ai_checker.scan(ch_text, book=book, bdir=bdir, chapter_num=ch_num)
                atomic_write_json(report_path(bdir, ch_num), rep)
                m = rep.get("metrics") or {}
                ch_rec.word_count = int(m.get("word_count") or len(ch_text.split()))
                ch_rec.error_count = int(rep.get("error_count") or 0)
                ch_rec.warning_count = int(rep.get("warning_count") or 0)
                ch_rec.tells = ch_rec.error_count + ch_rec.warning_count
                ch_rec.cleanliness_grade = str(rep.get("cleanliness_grade") or "")
                if ch_rec.status == ChapterStatus.pending:
                    ch_rec.status = ChapterStatus.drafted
                imported_chapters.append(ch_num)
        book.chapters.sort(key=lambda c: c.number)
        if book.chapters:
            book.chapter_count = max(book.chapter_count or 0, len(book.chapters))
        synced_roles.append("chapter")

    # 9. Reference notes
    if _should_compile("reference"):
        ref_txt = _join_sections(by_role["reference"])[:MAX_REF_CHARS]
        (bdir / "uploads").mkdir(parents=True, exist_ok=True)
        atomic_write_text(bdir / "uploads" / "_vault_ref.md", ref_txt)
        synced_roles.append("reference")

    # Update Book & obsidian.json state
    book.source_mode = "obsidian"
    book.obsidian_vault_path = str(vault_root)
    save_book(book)

    merged_hashes = dict(prev_cfg.get("note_hashes") or {})
    merged_roles = dict(prev_cfg.get("note_roles") or {})
    if role_filter is None:
        merged_hashes = new_hashes
        merged_roles = new_roles_map
    else:
        for rp in list(merged_hashes.keys()):
            if merged_roles.get(rp) in role_filter and rp not in new_hashes:
                merged_hashes.pop(rp, None)
                merged_roles.pop(rp, None)
        for rp, h in new_hashes.items():
            if new_roles_map.get(rp) in role_filter:
                merged_hashes[rp] = h
                merged_roles[rp] = new_roles_map[rp]

    now_iso = _utcnow_iso()
    saved_cfg = {
        **norm_cfg,
        "last_synced_at": now_iso,
        "needs_outline_structuring": needs_outline_structuring,
        "structured_outline_source_sig": preserved_structured_sig,
        "note_hashes": merged_hashes,
        "note_roles": merged_roles,
    }
    save_obsidian_config(bdir, saved_cfg)

    return {
        "synced_at": now_iso,
        "synced_roles": synced_roles,
        "imported_chapters": imported_chapters,
        "needs_outline_structuring": needs_outline_structuring,
        "warnings": warnings,
        "note_count": len(new_hashes),
        "artifact_stats": _artifact_stats(book, bdir),
    }


def get_vault_status(book: Book, bdir: Path) -> dict[str, Any]:
    """Inspect live vault state against saved `obsidian.json` hashes and return diffs + mapping."""
    cfg = load_obsidian_config(bdir)
    if not cfg or not cfg.get("vault_path"):
        return {
            "linked": False,
            "status": "unlinked",
            "vault_path": book.obsidian_vault_path or "",
            "last_synced_at": None,
            "added": [],
            "modified": [],
            "deleted": [],
            "unmapped_new": [],
            "changed_roles": [],
            "folders": [],
            "notes": [],
            "mapped_counts": {r: 0 for r in OBSIDIAN_ROLES},
            "artifact_stats": _artifact_stats(book, bdir),
            "needs_outline_structuring": bool(read_text(bdir / "outline.md").strip() and not parse_outline_units(read_text(bdir / "outline.md"))),
            "config": None,
            "error": None,
        }

    try:
        vault_root = resolve_vault_root(str(cfg["vault_path"]))
    except ValueError as exc:
        return {
            "linked": True,
            "status": "missing_vault",
            "vault_path": str(cfg.get("vault_path") or ""),
            "last_synced_at": cfg.get("last_synced_at"),
            "added": [],
            "modified": [],
            "deleted": [],
            "unmapped_new": [],
            "changed_roles": [],
            "folders": [],
            "notes": [],
            "mapped_counts": {r: 0 for r in OBSIDIAN_ROLES},
            "artifact_stats": _artifact_stats(book, bdir),
            "needs_outline_structuring": bool(cfg.get("needs_outline_structuring", False)),
            "config": {
                "vault_path": cfg.get("vault_path", ""),
                "folder_rules": cfg.get("folder_rules") or {},
                "file_overrides": cfg.get("file_overrides") or {},
                "tag_rules": cfg.get("tag_rules") or {},
                "write_back_dir": cfg.get("write_back_dir") or "Novelista Export",
                "auto_sync_before_draft": bool(cfg.get("auto_sync_before_draft", True)),
                "import_chapters": bool(cfg.get("import_chapters", False)),
            },
            "error": str(exc),
        }

    collected = _collect_vault_notes(vault_root, write_back_dir=str(cfg.get("write_back_dir") or "Novelista Export"))
    notes = collected["notes"]
    saved_hashes: dict[str, str] = cfg.get("note_hashes") or {}
    saved_roles: dict[str, str] = cfg.get("note_roles") or {}

    added: list[dict[str, str]] = []
    modified: list[dict[str, str]] = []
    deleted: list[dict[str, str]] = []
    unmapped_new: list[dict[str, str]] = []
    changed_roles_set: set[str] = set()
    mapped_counts: dict[str, int] = {r: 0 for r in OBSIDIAN_ROLES}
    seen_paths: set[str] = set()
    public_notes: list[dict[str, Any]] = []

    by_folder: dict[str, list[dict[str, Any]]] = {}

    for n in notes:
        rp = n["rel_path"]
        seen_paths.add(rp)
        eff = resolve_note_role(n, cfg)
        mapped_counts[eff] = mapped_counts.get(eff, 0) + 1
        by_folder.setdefault(n["folder"], []).append(n)

        change = "unchanged"
        if eff == "ignore":
            if rp in saved_hashes:
                deleted.append({"rel_path": rp, "role": saved_roles.get(rp, "unknown")})
                changed_roles_set.add(saved_roles.get(rp, "unknown"))
                change = "unmapped"
            elif n["suggested_role"] != "ignore":
                unmapped_new.append({"rel_path": rp, "suggested_role": n["suggested_role"]})
        else:
            if rp not in saved_hashes:
                added.append({"rel_path": rp, "role": eff})
                changed_roles_set.add(eff)
                change = "added"
            elif saved_hashes[rp] != n["hash"] or saved_roles.get(rp) != eff:
                modified.append({"rel_path": rp, "role": eff})
                changed_roles_set.add(eff)
                if saved_roles.get(rp) and saved_roles.get(rp) != eff:
                    changed_roles_set.add(saved_roles[rp])
                change = "modified"

        public_notes.append(_public_note_view(n, effective_role=eff, change=change))

    for old_rp, old_role in saved_hashes.items():
        if old_rp not in seen_paths:
            deleted.append({"rel_path": old_rp, "role": old_role})
            changed_roles_set.add(old_role)

    folders: list[dict[str, Any]] = []
    for folder in sorted(by_folder.keys(), key=lambda x: (x != "", x.lower())):
        items = by_folder[folder]
        folders.append(
            {
                "path": folder,
                "label": folder or "(Vault Root)",
                "note_count": len(items),
                "total_words": sum(i["word_count"] for i in items),
                "mapped_role": (cfg.get("folder_rules") or {}).get(folder, ""),
            }
        )

    is_dirty = bool(added or modified or deleted)
    outline_txt = read_text(bdir / "outline.md").strip()
    needs_structuring = bool(outline_txt and not parse_outline_units(outline_txt))

    return {
        "linked": True,
        "status": "out_of_sync" if is_dirty else "in_sync",
        "vault_path": str(vault_root),
        "is_obsidian_vault": (vault_root / ".obsidian").is_dir(),
        "last_synced_at": cfg.get("last_synced_at"),
        "added": added,
        "modified": modified,
        "deleted": deleted,
        "unmapped_new": unmapped_new,
        "changed_roles": sorted(r for r in changed_roles_set if r in VALID_ROLE_SET and r != "ignore"),
        "folders": folders,
        "notes": public_notes,
        "mapped_counts": mapped_counts,
        "artifact_stats": _artifact_stats(book, bdir),
        "needs_outline_structuring": needs_structuring,
        "config": {
            "vault_path": str(vault_root),
            "folder_rules": cfg.get("folder_rules") or {},
            "file_overrides": cfg.get("file_overrides") or {},
            "tag_rules": cfg.get("tag_rules") or {},
            "write_back_dir": cfg.get("write_back_dir") or "Novelista Export",
            "auto_sync_before_draft": bool(cfg.get("auto_sync_before_draft", True)),
            "import_chapters": bool(cfg.get("import_chapters", False)),
        },
        "error": None,
    }


async def maybe_auto_sync(book_id: str, *, on_warning: WarnCB = None) -> dict[str, Any] | None:
    """Sync linked Obsidian vault changes before drafting/revising when `auto_sync_before_draft` is enabled."""
    async with book_lock(book_id):
        b = get_book(book_id)
        if not b or b.source_mode != "obsidian":
            return None
        bdir = book_dir(book_id)
        cfg = load_obsidian_config(bdir)
        if not cfg or not cfg.get("auto_sync_before_draft", True):
            return None
        st = get_vault_status(b, bdir)
        if st["status"] == "missing_vault":
            if on_warning:
                on_warning(f"Linked Obsidian vault is unreachable ({st.get('error')}); drafting with last synced files.")
            return None
        if st["status"] != "out_of_sync":
            return None
        # Never overwrite chapters automatically right before drafting/revising one; sync only changed story bible & outline roles!
        sync_roles = [r for r in (st.get("changed_roles") or []) if r not in {"ignore", "chapter"}]
        if not sync_roles:
            return None
        res = compile_vault_to_book(b, bdir, cfg, roles=sync_roles, import_chapters=False, backup=True)
        for w in res.get("warnings") or []:
            if on_warning:
                on_warning(w)
        return res


async def suggest_mapping_with_llm(scan_data: dict[str, Any]) -> dict[str, Any]:
    """Use the configured LLM (`foundation` task) to map an arbitrary vault structure into Novelista roles."""
    notes = scan_data.get("notes") or []
    folders = scan_data.get("folders") or []
    compact_folders = [{"path": f["path"], "note_count": f["note_count"]} for f in folders[:60]]
    compact_notes = [
        {
            "rel_path": n["rel_path"],
            "folder": n["folder"],
            "title": n["title"],
            "tags": n["tags"][:8],
            "words": n["word_count"],
            "excerpt": n["excerpt"][:140],
        }
        for n in notes[:160]
    ]

    system = (
        "You organize an author's arbitrary Obsidian Markdown vault for Novelista.\n"
        f"Allowed roles ONLY: {', '.join(OBSIDIAN_ROLES)}.\n"
        "- `seed`: overall story premise, pitch, synopsis, blurb, or core concept\n"
        "- `world`: setting, geography, magic/tech rules, factions, lore, culture\n"
        "- `characters`: character profiles, cast sheets, relationships\n"
        "- `voice`: writing style notes, tone guides, diction preferences\n"
        "- `canon`: hard baseline rules, timelines, continuity facts\n"
        "- `mystery`: hidden twists, culprit identity, author-only secrets\n"
        "- `outline`: plot beats, chapter plans, scene breakdowns, story arcs\n"
        "- `chapter`: already-drafted manuscript chapters/prose\n"
        "- `reference`: background research or inspiration (not core canon)\n"
        "- `ignore`: unrelated personal notes, templates, daily logs, indexes\n\n"
        "Return ONLY valid JSON with:\n"
        '{"folder_rules": {"FolderName": "role"}, "file_overrides": {"path/to/note.md": "role"}, '
        '"suggested_book": {"title": "...", "genre": "...", "pov": "third_limited|first_person|third_omniscient|second_person", "tense": "past|present"}, '
        '"rationale": "short 1-2 sentence summary"}'
    )
    user = json.dumps(
        {
            "vault_name": Path(str(scan_data.get("vault_path") or "Vault")).name,
            "folders": compact_folders,
            "notes": compact_notes,
        },
        ensure_ascii=False,
    )

    res = await llm_client.complete(
        system=system,
        user=user,
        task="foundation",
        max_tokens=2000,
        creativity="low",
        json_mode=True,
    )
    parsed = parse_llm_json(res.text)
    if not isinstance(parsed, dict):
        raise ValueError("AI mapping returned non-object JSON.")

    valid_folders = {f["path"] for f in folders if f["path"]}
    valid_paths = {n["rel_path"] for n in notes}

    folder_rules: dict[str, str] = {}
    for k, v in (parsed.get("folder_rules") or {}).items():
        fk = str(k).replace("\\", "/").strip("/")
        rv = str(v).strip().lower()
        if fk in valid_folders and rv in VALID_ROLE_SET and rv != "ignore":
            folder_rules[fk] = rv

    file_overrides: dict[str, str] = {}
    for k, v in (parsed.get("file_overrides") or {}).items():
        pk = str(k).replace("\\", "/").strip("/")
        rv = str(v).strip().lower()
        if pk in valid_paths and rv in VALID_ROLE_SET:
            # Skip redundant file override if folder already assigns identical role
            note_folder = Path(pk).parent.as_posix()
            if note_folder == ".":
                note_folder = ""
            if folder_rules.get(note_folder) == rv:
                continue
            file_overrides[pk] = rv

    sb = dict(scan_data.get("suggested_book") or {})
    llm_sb = parsed.get("suggested_book") or {}
    if isinstance(llm_sb, dict):
        if isinstance(llm_sb.get("title"), str) and llm_sb["title"].strip():
            sb["title"] = llm_sb["title"].strip()[:200]
        if isinstance(llm_sb.get("genre"), str) and llm_sb["genre"].strip():
            sb["genre"] = llm_sb["genre"].strip()[:120]
        if str(llm_sb.get("pov") or "") in {p.value for p in POV}:
            sb["pov"] = str(llm_sb["pov"])
        if str(llm_sb.get("tense") or "") in {t.value for t in Tense}:
            sb["tense"] = str(llm_sb["tense"])

    return {
        "folder_rules": folder_rules,
        "file_overrides": file_overrides,
        "suggested_book": sb,
        "rationale": str(parsed.get("rationale") or "").strip(),
    }


async def structure_outline_from_vault(
    book: Book,
    bdir: Path,
    *,
    stream_cb: llm_client.StreamCB | None = None,
    on_warning: WarnCB = None,
) -> str:
    """Normalize raw/unstructured vault outline notes into Novelista's `### Ch N: Title` outline format."""
    prof = get_profile(book.book_format)
    directives = build_directives(book)
    raw_outline = read_text(bdir / "outline.md").strip()
    seed = read_text(bdir / "seed.txt").strip() or book.idea
    chars = read_text(bdir / "characters.md").strip()
    world = read_text(bdir / "world.md").strip()
    mystery = read_text(bdir / "mystery.md").strip()

    count = book.chapter_count or prof.default_units
    per_unit = per_unit_word_target(book) or prof.min_unit_words * 3
    u_label = prof.unit_label
    if u_label.lower() == "chapter":
        u_heading = "Ch"
    else:
        u_heading = u_label

    fp_rule = (
        "- **First-Person POV Lock:** Every unit MUST assign `- POV: <Protagonist Name>` to the same first-person narrator.\n"
        if book.pov == POV.first_person
        else ""
    )

    system = "\n\n---\n\n".join(
        [
            directives,
            f"Convert the author's raw Obsidian outline notes into a clean, structured {count}-{u_label.lower()} Novelista outline (~{per_unit:,} words per {u_label.lower()}).\n"
            "CRITICAL PRESERVATION RULE: Keep EVERY plot turn, character name, workplace, date, and ending intended by the author's notes — do NOT invent unrelated crimes or overwrite the author's plot.\n"
            "Output ONLY structured Markdown:\n"
            + ("1. `## Plot Logic & Resolution Spine` (concise 5-item factual lock summary)\n" if prof.craft_profile == "novel" else "")
            + f"2. Exact headings `### {u_heading} 1: <Title>` through `### {u_heading} {count}: <Title>` (or matching the exact number of chapters present in the author's notes).\n"
            "Under every unit include:\n"
            "- POV: <character name>\n"
            f"{fp_rule}"
            "- Setting & Time:\n"
            "- Opening Hook Word: <fresh concrete word>\n"
            "- 3–5 concise neutral plot bullets (`- ...`, 10–30 words each; ZERO prose paragraphs or quoted dialogue)\n"
            "- **Chapter Ending State:** concrete stop boundary\n\n"
            "End with `## Foreshadowing Ledger` table (`| Item / Clue | Planted (Ch) | Reinforced (Ch) | Paid Off (Ch) |`).",
        ]
    )

    user_parts = [f"Title: {book.title}"]
    if raw_outline:
        user_parts.append(f"Author's Raw Outline Notes (PRESERVE ALL BEATS):\n{raw_outline[:16000]}")
    if seed:
        user_parts.append(f"Seed / Premise:\n{seed[:5000]}")
    if chars:
        user_parts.append(f"Cast Reference:\n{chars[:5000]}")
    if world:
        user_parts.append(f"World Reference:\n{world[:3500]}")
    if mystery:
        user_parts.append(f"Author Secrets:\n{mystery[:2500]}")

    res = await llm_client.complete(
        system=system,
        user="\n\n".join(user_parts),
        task="outline",
        max_tokens=7000,
        creativity="low",
        stream_cb=stream_cb,
    )
    warn_if_cut(res.stop_reason, on_warning, "Structured vault outline")
    allowed_src = allowed_name_sources(book, bdir, raw_outline)
    return scrub_banned_names(strip_llm_chatter(res.text), allowed_src) + "\n"


def push_book_to_vault(
    book: Book,
    bdir: Path,
    *,
    write_back_dir: str | None = None,
    include_manuscript: bool = True,
    include_chapters: bool = True,
    include_summary: bool = True,
    include_canon: bool = True,
    accepted_only: bool = False,
) -> dict[str, Any]:
    """Write Novelista's drafted chapters, manuscript, summary, and canon into an isolated vault subfolder."""
    cfg = load_obsidian_config(bdir) or {}
    raw_vault = cfg.get("vault_path") or book.obsidian_vault_path
    if not raw_vault:
        raise ValueError("Book is not linked to an Obsidian vault.")
    vault_root = resolve_vault_root(str(raw_vault))
    chosen_dir = write_back_dir if write_back_dir is not None else str(cfg.get("write_back_dir") or "Novelista Export")
    target_dir = resolve_write_back_dir(vault_root, chosen_dir)
    target_dir.mkdir(parents=True, exist_ok=True)

    written: list[str] = []
    prof = get_profile(book.book_format)
    slug = safe_slug(book.title)

    if include_chapters:
        ch_dir = target_dir / "Chapters"
        ch_dir.mkdir(parents=True, exist_ok=True)
        for ch in collect_chapters(book, bdir, accepted_only=accepted_only):
            safe_ch_title = re.sub(r'[\\/:*?"<>|]+', "-", ch.title).strip()[:60]
            fname = f"{prof.unit_label} {ch.number:02d}" + (f" - {safe_ch_title}" if safe_ch_title else "") + ".md"
            rec = book.get_chapter(ch.number)
            status_val = rec.status.value if rec else "drafted"
            grade_val = rec.cleanliness_grade if rec else ""
            word_val = rec.word_count if rec and rec.word_count else len(ch.body_md.split())
            fm_lines = [
                "---",
                f'book: "{book.title.replace(chr(34), chr(39))}"',
                f"chapter: {ch.number}",
                f'title: "{ch.title.replace(chr(34), chr(39))}"',
                f"status: {status_val}",
                f"word_count: {word_val}",
            ]
            if grade_val:
                fm_lines.append(f"cleanliness_grade: {grade_val}")
            fm_lines.append("---")
            heading = unit_heading_text(book, ch)
            content = "\n".join(fm_lines) + f"\n\n# {heading}\n\n{ch.body_md.strip()}\n"
            out_path = ch_dir / fname
            atomic_write_text(out_path, content)
            written.append(out_path.relative_to(vault_root).as_posix())

    if include_manuscript:
        ms_md = build_markdown(book, bdir, accepted_only=accepted_only)
        ms_path = target_dir / f"{slug}-manuscript.md"
        atomic_write_text(ms_path, ms_md)
        written.append(ms_path.relative_to(vault_root).as_posix())

    if include_summary:
        sum_txt = read_text(bdir / "summary.md").strip()
        if sum_txt:
            sum_path = target_dir / "Story Summary.md"
            atomic_write_text(sum_path, sum_txt + "\n")
            written.append(sum_path.relative_to(vault_root).as_posix())

    if include_canon:
        canon_txt = read_text(bdir / "canon.md").strip()
        if canon_txt:
            canon_path = target_dir / "Canon Ledger.md"
            atomic_write_text(canon_path, canon_txt + "\n")
            written.append(canon_path.relative_to(vault_root).as_posix())

    # Persist updated write_back_dir preference if changed
    if cfg and chosen_dir != cfg.get("write_back_dir"):
        cfg["write_back_dir"] = chosen_dir.replace("\\", "/").strip("/")
        save_obsidian_config(bdir, cfg)

    return {
        "vault_path": str(vault_root),
        "write_back_dir": chosen_dir.replace("\\", "/").strip("/"),
        "written_files": written,
        "count": len(written),
    }
