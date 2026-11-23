from __future__ import annotations

import json
import time
from pathlib import Path

from app import obsidian
from app.pipeline import ai_checker
from app.pipeline._common import allowed_name_sources, read_reference_material
from app.storage import atomic_write_text, read_text
from app.store import book_dir
from tests._llm_mock import install_mock_llm


def _wait_job(client, jid: str, timeout: float = 5.0) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        r = client.get(f"/api/jobs/{jid}")
        assert r.status_code == 200
        d = r.json()
        if d["status"] in {"done", "error", "cancelled"}:
            return d
        time.sleep(0.02)
    raise AssertionError(f"Job {jid} did not finish within {timeout}s")


def _make_sample_vault(root: Path) -> Path:
    vault = root / "ObsidianNovelVault"
    (vault / ".obsidian").mkdir(parents=True, exist_ok=True)
    (vault / ".obsidian" / "workspace.json").write_text("{}", encoding="utf-8")
    (vault / ".trash").mkdir(parents=True, exist_ok=True)
    (vault / ".trash" / "Old.md").write_text("Deleted note", encoding="utf-8")
    (vault / "Novelista Export" / "Chapters").mkdir(parents=True, exist_ok=True)
    (vault / "Novelista Export" / "Chapters" / "Old Export.md").write_text("Should never be scanned back in", encoding="utf-8")

    # Root files (arbitrary naming)
    (vault / "Story Pitch.md").write_text(
        "---\nbook_title: The Glass Meridian\ngenre: Maritime Mystery\npov: third_limited\ntense: past\n---\n"
        "# Pitch\n"
        "A cartographer uncovers falsified reef charts off [[Harbor/Breakwater|the Breakwater]].\n"
        "%% private scratch note %%\n",
        encoding="utf-8",
    )
    (vault / "Random Grocery List.md").write_text("- milk\n- sourdough bread\n", encoding="utf-8")

    # Arbitrary folders
    (vault / "Lore & Setting").mkdir(parents=True, exist_ok=True)
    (vault / "Lore & Setting" / "Port Meridian.md").write_text(
        "---\ntype: world\n---\n"
        "Foghorns sound every twelve minutes along the basalt seawall.\n"
        "![[Snippets/Sea Wall Rule]]\n"
        "![[cover-sketch.png]]\n",
        encoding="utf-8",
    )
    (vault / "Snippets").mkdir(parents=True, exist_ok=True)
    (vault / "Snippets" / "Sea Wall Rule.md").write_text(
        "No lantern may burn amber past midnight.\n![[Snippets/Cyclic A]]\n",
        encoding="utf-8",
    )
    (vault / "Snippets" / "Cyclic A.md").write_text("![[Snippets/Sea Wall Rule]]\nWatchtower bells chime twice.", encoding="utf-8")

    # Characters (explicitly test banned-list name Elena Vance!)
    (vault / "People").mkdir(parents=True, exist_ok=True)
    (vault / "People" / "Elena Vance.md").write_text(
        "---\nrole: character\nname: Elena Vance\npronouns: she/her\nage: 34\nworkplace: Harbor Survey Office\n---\n"
        "Sharp-eyed hydrographer who checks every brass sextant twice.\n"
        "```dataview\nTABLE file.mtime FROM #cast\n```\n",
        encoding="utf-8",
    )
    (vault / "People" / "Tomas Lind.md").write_text(
        "---\nrole: character\npronouns: he/him\nage: 47\nworkplace: Lighthouse Dock\n---\n"
        "Steady harbor pilot who speaks in terse sentences.\n",
        encoding="utf-8",
    )

    # Voice, Canon, Secrets, Reference
    (vault / "Voice Notes.md").write_text(
        "---\nnovelista_role: voice\n---\n"
        "- Short concrete verbs, salt and brass imagery, active voice.\n",
        encoding="utf-8",
    )
    (vault / "Timeline & Rules.md").write_text(
        "---\nnovelista_role: canon\n---\n"
        "- October 14: Supply schooner *Kestrel* arrives at low tide.\n",
        encoding="utf-8",
    )
    (vault / "Hidden Culprit.md").write_text(
        "---\nnovelista_role: mystery\n---\n"
        "- The harbormaster altered the sounding weights.\n",
        encoding="utf-8",
    )
    (vault / "Research" / "Tide Tables.md").parent.mkdir(parents=True, exist_ok=True)
    (vault / "Research" / "Tide Tables.md").write_text(
        "---\ntags: [research]\n---\nSpring tides peak at 4.2 meters.\n",
        encoding="utf-8",
    )

    # Multi-file plot outline folder
    (vault / "Plot Beats").mkdir(parents=True, exist_ok=True)
    (vault / "Plot Beats" / "01 - Sounding Lead.md").write_text(
        "---\npov: Elena Vance\nchapter: 1\n---\n"
        "- Elena Vance inspects the warped brass lead line at dawn.\n"
        "- Tomas Lind warns her off the outer reef.\n",
        encoding="utf-8",
    )
    (vault / "Plot Beats" / "02 - Lantern Signal.md").write_text(
        "---\npov: Elena Vance\nchapter: 2\n---\n"
        "- An unscheduled amber lantern blinks from the seawall.\n",
        encoding="utf-8",
    )

    return vault


def test_markdown_cleaning_wikilinks_embeds_and_comments():
    note_index = {
        "a": ("a", "---\ntags: [lore]\n---\nAlpha line.\n![[B]]"),
        "b": ("b", "Beta line.\n![[A]]"),  # deliberate cycle
    }
    raw = (
        "Intro with [[Folder/Character Name|Cara]] and [[Simple Note#Heading]].\n"
        "%% secret obsidian comment %%\n"
        "```dataview\nLIST FROM #tag\n```\n"
        "![[A]]\n"
        "![[diagram.png]]\n"
        "> [!note] Dock Rule\n"
        "> Keep lines coiled."
    )
    cleaned = obsidian.clean_obsidian_markdown(raw, note_index=note_index)
    assert "Cara" in cleaned
    assert "Simple Note — Heading" in cleaned
    assert "secret obsidian comment" not in cleaned
    assert "dataview" not in cleaned
    assert "diagram.png" not in cleaned
    assert "Alpha line." in cleaned
    assert "Beta line." in cleaned
    assert "**Dock Rule**" in cleaned


def test_role_resolution_precedence():
    note = {
        "rel_path": "World/Cities/Port.md",
        "folder": "World/Cities",
        "tags": ["canon", "draft"],
        "frontmatter": {"novelista_role": "seed"},
    }
    # file_override beats everything
    assert obsidian.resolve_note_role(
        note,
        {
            "file_overrides": {"World/Cities/Port.md": "mystery"},
            "folder_rules": {"World": "world", "World/Cities": "reference"},
            "tag_rules": {"canon": "canon"},
        },
    ) == "mystery"
    # More specific folder beats parent folder, tag, and frontmatter
    assert obsidian.resolve_note_role(
        note,
        {
            "file_overrides": {},
            "folder_rules": {"World": "world", "World/Cities": "reference"},
            "tag_rules": {"canon": "canon"},
        },
    ) == "reference"
    # Tag beats frontmatter when no folder matches
    assert obsidian.resolve_note_role(
        note,
        {"file_overrides": {}, "folder_rules": {}, "tag_rules": {"canon": "canon"}},
    ) == "canon"
    # Frontmatter fallback
    assert obsidian.resolve_note_role(note, {"file_overrides": {}, "folder_rules": {}, "tag_rules": {}}) == "seed"


def test_write_back_path_security_and_scan_exclusions(tmp_path, client):
    vault = _make_sample_vault(tmp_path)

    # Scan excludes .obsidian, .trash, and write_back_dir
    res = client.post("/api/obsidian/scan", json={"vault_path": str(vault), "write_back_dir": "Novelista Export"})
    assert res.status_code == 200
    data = res.json()
    rel_paths = {n["rel_path"] for n in data["notes"]}
    assert "Story Pitch.md" in rel_paths
    assert "People/Elena Vance.md" in rel_paths
    assert ".obsidian/workspace.json" not in rel_paths
    assert ".trash/Old.md" not in rel_paths
    assert "Novelista Export/Chapters/Old Export.md" not in rel_paths
    assert data["is_obsidian_vault"] is True
    assert data["suggested_book"]["title"] == "The Glass Meridian"
    assert data["suggested_book"]["genre"] == "Maritime Mystery"

    # Write-back directory traversal rejected
    for bad_dir in ("../outside", "/abs/path", "C:\\temp", ".obsidian", ""):
        try:
            obsidian.resolve_write_back_dir(vault, bad_dir)
            raise AssertionError(f"Expected ValueError for {bad_dir!r}")
        except ValueError:
            pass

    # Browse folders API works
    br = client.post("/api/obsidian/browse", json={"path": str(tmp_path)})
    assert br.status_code == 200
    bdata = br.json()
    assert any(d["name"] == "ObsidianNovelVault" and d["is_obsidian_vault"] for d in bdata["directories"])


def test_obsidian_linked_live_vault_full_lifecycle(tmp_path, client, monkeypatch):
    vault = _make_sample_vault(tmp_path)
    seen_prompts: list[dict[str, str]] = []

    def _mock(task: str, sys: str, usr: str) -> str:
        seen_prompts.append({"task": task, "system": sys, "user": usr})
        if task == "foundation" and "allowed roles" in sys.lower():
            return json.dumps(
                {
                    "folder_rules": {
                        "People": "characters",
                        "Lore & Setting": "world",
                        "Plot Beats": "outline",
                        "Research": "reference",
                    },
                    "file_overrides": {
                        "Story Pitch.md": "seed",
                        "Voice Notes.md": "voice",
                        "Timeline & Rules.md": "canon",
                        "Hidden Culprit.md": "mystery",
                    },
                    "suggested_book": {"title": "The Glass Meridian", "genre": "Maritime Mystery", "pov": "third_limited"},
                    "rationale": "Mapped story folders and root bible notes cleanly.",
                }
            )
        if task == "outline":
            return (
                "## Plot Logic & Resolution Spine\n"
                "1. Elena Vance (she/her, 34, Harbor Survey Office).\n\n"
                "### Ch 1: Sounding Lead\n- POV: Elena Vance\n- Inspects lead line.\n\n"
                "### Ch 2: Lantern Signal\n- POV: Elena Vance\n- Tracks amber lantern.\n"
            )
        if task == "scenes":
            return json.dumps(
                {
                    "chapter": 1,
                    "scenes": [
                        {
                            "title": "Sounding Lead",
                            "word_budget": 600,
                            "location": "Harbor Pier",
                            "time": "Dawn",
                            "pov": "Elena Vance",
                            "cast": ["Elena Vance", "Tomas Lind"],
                            "start_state": "Fog on pier",
                            "goal": "Measure depth",
                            "turn": "Lead weight altered",
                            "outcome": "Suspects tampering",
                            "hard_stop": "Bell strikes six",
                            "illustration_prompt": "",
                        }
                    ],
                }
            )
        if task == "summary":
            return '{"summary": "Elena Vance discovered altered lead weights on the pier.", "state": "alive=Elena Vance, Tomas Lind"}'
        if task == "canon":
            return "- Brass sounding lead weight was shaved by two ounces."
        return " ".join(
            ["Elena Vance weighed the brass sounding lead in cold dawn light beside Tomas Lind on the seawall."] * 18
        )

    install_mock_llm(monkeypatch, _mock)

    # 1. AI Mapping suggestion
    sug = client.post("/api/obsidian/suggest-mapping", json={"vault_path": str(vault)})
    assert sug.status_code == 200
    sdata = sug.json()
    assert sdata["folder_rules"]["People"] == "characters"
    assert sdata["folder_rules"]["Plot Beats"] == "outline"
    assert "Random Grocery List.md" not in sdata["file_overrides"]

    # 2. Create Obsidian-linked book (arbitrary mapping skipping wizard)
    create_res = client.post(
        "/api/obsidian/books",
        json={
            "title": "The Glass Meridian",
            "book_format": "novella",
            "chapter_count": 2,
            "target_words": 4000,
            "pov": "third_limited",
            "tense": "past",
            "genre": "Maritime Mystery",
            "vault_path": str(vault),
            "folder_rules": sdata["folder_rules"],
            "file_overrides": sdata["file_overrides"],
            "write_back_dir": "Novelista Export",
            "auto_sync_before_draft": True,
            "import_chapters": False,
        },
    )
    assert create_res.status_code == 201
    created = create_res.json()
    book = created["book"]
    bid = book["id"]
    assert book["source_mode"] == "obsidian"
    assert len(book["chapters"]) == 2
    assert book["chapters"][0]["title"] == "Sounding Lead"

    bdir = book_dir(bid)
    chars_txt = read_text(bdir / "characters.md")
    assert "## Elena Vance (she/her, age 34, Harbor Survey Office)" in chars_txt
    assert "dataview" not in chars_txt.lower()
    assert "Spring tides peak" in read_reference_material(bdir)

    # Pronoun extraction works directly from compiled Obsidian characters.md
    full_g, tok_g, _ = ai_checker.extract_character_genders(bdir)
    assert full_g.get("elena vance") == "female"
    assert tok_g.get("tomas") == "male"

    # Banned-list cast name (`Elena Vance`) NOT in seed.txt is still whitelisted & not flagged
    assert "Elena Vance" in allowed_name_sources(None, bdir)
    rep_elena = ai_checker.scan(
        " ".join(["Elena Vance checked the brass sextant by the seawall before sunrise."] * 14),
        book=obsidian.get_book(bid),
        bdir=bdir,
        chapter_num=1,
    )
    assert not any(f["rule"] == "cliche_ai_name" for f in rep_elena["findings"])

    # 3. Initial vault status is in_sync
    st1 = client.get(f"/api/books/{bid}/obsidian").json()
    assert st1["linked"] is True
    assert st1["status"] == "in_sync"

    # 4. Simulate live edits in Obsidian: modify World note + add new Character note + append runtime ## Ch 1 to canon.md
    (vault / "Lore & Setting" / "Port Meridian.md").write_text(
        "---\ntype: world\n---\nLIVE_VAULT_TOKEN_BELL: Iron harbor gate locks at dusk.\n",
        encoding="utf-8",
    )
    (vault / "People" / "Mira Solis.md").write_text(
        "---\npronouns: she/her\nage: 29\n---\nNight watch telegraph clerk.\n",
        encoding="utf-8",
    )
    atomic_write_text(bdir / "canon.md", read_text(bdir / "canon.md").strip() + "\n\n## Ch 1\n- Existing Ch1 fact kept safe.\n")

    st2 = client.get(f"/api/books/{bid}/obsidian").json()
    assert st2["status"] == "out_of_sync"
    assert any(m["rel_path"] == "Lore & Setting/Port Meridian.md" for m in st2["modified"])
    assert any(a["rel_path"] == "People/Mira Solis.md" for a in st2["added"])

    # 5. Drafting Ch 1 triggers auto_sync_before_draft automatically and picks up LIVE_VAULT_TOKEN_BELL + Mira Solis!
    draft_job = client.post(f"/api/books/{bid}/chapters/1/draft", json={"polish": False, "scene_cards": False}).json()
    assert _wait_job(client, draft_job["id"])["status"] == "done"

    draft_prompts = [p["user"] for p in seen_prompts if p["task"] == "draft"]
    assert draft_prompts and "LIVE_VAULT_TOKEN_BELL" in draft_prompts[-1]
    assert "Mira Solis" in draft_prompts[-1]

    # Verify Elena Vance was NOT scrubbed during drafting
    ch1 = client.get(f"/api/books/{bid}/chapters/1").json()
    assert "Elena Vance" in ch1["text"]

    # Modify Timeline & Rules.md (canon) and sync manually: ## Ch 1 canon section MUST survive!
    (vault / "Timeline & Rules.md").write_text(
        "---\nnovelista_role: canon\n---\n- October 14: Harbor lighthouse lamp uses whale oil.\n",
        encoding="utf-8",
    )
    sync_res = client.post(f"/api/books/{bid}/obsidian/sync").json()
    assert sync_res["status"]["status"] == "in_sync"
    canon_after = read_text(bdir / "canon.md")
    assert "whale oil" in canon_after
    assert "## Ch 1" in canon_after

    # 6. Push back to Obsidian write_back_dir + verify traversal rejected + verify subsequent scan ignores export folder
    bad_push = client.post(f"/api/books/{bid}/obsidian/push", json={"write_back_dir": "../../oops"})
    assert bad_push.status_code == 400

    push_res = client.post(
        f"/api/books/{bid}/obsidian/push",
        json={"write_back_dir": "Novelista Export", "include_chapters": True, "include_manuscript": True},
    )
    assert push_res.status_code == 200
    pdata = push_res.json()
    assert pdata["count"] >= 3
    exported_ch = vault / "Novelista Export" / "Chapters" / "Chapter 01 - Sounding Lead.md"
    assert exported_ch.exists()
    assert "book: \"The Glass Meridian\"" in exported_ch.read_text(encoding="utf-8")

    # Exported files inside write_back_dir must NOT trigger out_of_sync
    st3 = client.get(f"/api/books/{bid}/obsidian").json()
    assert st3["status"] == "in_sync"

    # 7. Accepted chapter protection on chapter import sync
    acc = client.post(f"/api/books/{bid}/chapters/1/accept")
    assert acc.status_code == 200
    (vault / "Drafts").mkdir(parents=True, exist_ok=True)
    (vault / "Drafts" / "Ch 01 - Overwrite Attempt.md").write_text("Should NOT overwrite accepted chapter 1.", encoding="utf-8")
    (vault / "Drafts" / "Ch 02 - Fresh Draft.md").write_text("Imported draft text for chapter two.", encoding="utf-8")
    link_upd = client.put(
        f"/api/books/{bid}/obsidian",
        json={
            "vault_path": str(vault),
            "folder_rules": {**sdata["folder_rules"], "Drafts": "chapter"},
            "file_overrides": sdata["file_overrides"],
            "tag_rules": {},
            "write_back_dir": "Novelista Export",
            "auto_sync_before_draft": True,
            "import_chapters": True,
            "sync_now": True,
        },
    )
    assert link_upd.status_code == 200
    assert any("Skipped accepted" in w for w in link_upd.json()["sync"]["warnings"])
    ch1_after = client.get(f"/api/books/{bid}/chapters/1").json()
    assert "Should NOT overwrite" not in ch1_after["text"]
    ch2_after = client.get(f"/api/books/{bid}/chapters/2").json()
    assert "Imported draft text for chapter two." in ch2_after["text"]

    # 8. Unlink vault preserves compiled artifacts and switches source_mode back to wizard
    un = client.delete(f"/api/books/{bid}/obsidian")
    assert un.status_code == 200
    assert un.json()["book"]["source_mode"] == "wizard"
    assert (bdir / "world.md").exists()
    assert not (bdir / "obsidian.json").exists()


def test_unstructured_outline_and_structure_job(tmp_path, client, monkeypatch):
    vault = tmp_path / "FreeformVault"
    vault.mkdir(parents=True, exist_ok=True)
    (vault / "Idea.md").write_text("Two clockmakers race to fix a sunken bell tower.", encoding="utf-8")
    (vault / "Notes.md").write_text("First they drain the cellar, then they forge the bronze clapper.", encoding="utf-8")

    def _mock(task: str, sys: str, usr: str) -> str:
        if task == "outline":
            return (
                "## Plot Logic & Resolution Spine\n"
                "1. Clockmakers drain cellar and forge clapper.\n\n"
                "### Ch 1: Draining the Cellar\n- POV: Mara\n- Drain cellar.\n\n"
                "### Ch 2: Bronze Clapper\n- POV: Mara\n- Forge clapper.\n"
            )
        return "ok"

    install_mock_llm(monkeypatch, _mock)

    res = client.post(
        "/api/obsidian/books",
        json={
            "title": "Sunken Bell",
            "vault_path": str(vault),
            "folder_rules": {},
            "file_overrides": {"Idea.md": "seed", "Notes.md": "outline"},
        },
    )
    assert res.status_code == 201
    data = res.json()
    bid = data["book"]["id"]
    assert data["sync"]["needs_outline_structuring"] is True
    assert len(data["book"]["chapters"]) == 0

    job = client.post(f"/api/books/{bid}/obsidian/structure-outline").json()
    assert _wait_job(client, job["id"])["status"] == "done"

    st_resp = client.get(f"/api/books/{bid}/obsidian")
    assert st_resp.status_code == 200
    st = st_resp.json()
    assert st["needs_outline_structuring"] is False
    book_after = client.get(f"/api/books/{bid}").json()
    assert [c["title"] for c in book_after["chapters"]] == ["Draining the Cellar", "Bronze Clapper"]

    # Editing a non-outline note (`Idea.md`) and syncing must preserve AI-structured outline!
    (vault / "Idea.md").write_text("Two clockmakers race to fix a sunken harbor bell tower at low tide.", encoding="utf-8")
    sync1 = client.post(f"/api/books/{bid}/obsidian/sync").json()
    assert "seed" in sync1["sync"]["synced_roles"]
    assert "outline" not in sync1["sync"]["synced_roles"]
    assert [c["title"] for c in sync1["book"]["chapters"]] == ["Draining the Cellar", "Bronze Clapper"]

    # Editing the outline note itself (`Notes.md`) overrides the structured outline cleanly
    (vault / "Notes.md").write_text(
        "### Ch 1: Low Tide Entry\n- Enter cellar.\n\n### Ch 2: Bell Cast\n- Cast new bronze clapper.\n",
        encoding="utf-8",
    )
    sync2 = client.post(f"/api/books/{bid}/obsidian/sync").json()
    assert "outline" in sync2["sync"]["synced_roles"]
    assert [c["title"] for c in sync2["book"]["chapters"]] == ["Low Tide Entry", "Bell Cast"]
