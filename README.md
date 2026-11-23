# Novelista v3

**A local-first, Bring-Your-Own-Key (BYOK) AI fiction studio and Amazon KDP publishing pipeline.**

Novelista v3 takes a story premise through structured planning, voice calibration, scene-planned drafting, deterministic anti-AI detection, contextual revision, whole-manuscript auditing, and print/eBook export — running as a single local process with plain Markdown and JSON files on disk.

```text
Windows:      double-click start.bat        (stop with stop.bat)
macOS/Linux:  chmod +x start.command stop.command && ./start.command
```

> **Zero database. Zero Node.js at runtime. Zero telemetry.**  
> Only **Python 3.11+** is required. The prebuilt web UI (`frontend/dist/`) is served directly by FastAPI on `http://127.0.0.1:8000` (auto-selecting `8000–8010`).

---

## Table of Contents

- [Why Novelista v3? (USPs)](#why-novelista-v3-usps)
- [What's New in v3 (Latest Updates)](#whats-new-in-v3-latest-updates)
- [Requirements & Minimum Hardware Specs](#requirements--minimum-hardware-specs)
- [Quickstart & Installation](#quickstart--installation)
- [How to Use (Step-by-Step Guide)](#how-to-use-step-by-step-guide)
- [What to Avoid (Pitfalls & Best Practices)](#what-to-avoid-pitfalls--best-practices)
- [Repository & On-Disk Data Layout](#repository--on-disk-data-layout)
- [Development & Testing](#development--testing)
- [License](#license)

---

## Why Novelista v3? (USPs)

### 1. 100% Local-First, Transparent & Private
- **Plain-file storage:** Every project lives under `data/novels/<book_id>/` as human-readable Markdown (`.md`) and JSON (`.json`) files you can inspect, back up, or hand-edit at any stage.
- **Keys never leave your machine:** API keys live only in `data/settings.json` (gitignored) and are sent strictly to the provider endpoint you configure.
- **Strictly offline-safe UI:** Zero external CDN scripts, web fonts, or tracking. A build-time check (`frontend/scripts/check-no-external.mjs`) fails the frontend build if any unapproved remote URL enters `dist/`.

### 2. Six Format-Adaptive Craft Pipelines (Not One-Size-Fits-All)
Instead of forcing dark thriller templates onto every book, Novelista v3 adapts craft rules, target lengths, Flesch-Kincaid readability bands, optional phases, illustration prompts, and KDP trim sizes to the exact format you choose:

| Format | Unit | Defaults (Units / Words) | Word Range | KDP Trim | Optional Phases | Audience Readability (FK) |
|---|---|---|---|---|---|---|
| **Children's Picture Book** (`picture_book`) | Spread | 12 spreads · ~550w | 150–1,500w | `8.5×8.5` | World, Canon | Ages 3–7 (`0.0–3.8`) |
| **Early Reader** (`early_reader`) | Chapter | 6 chapters · ~2,400w | 600–6,000w | `5.5×8.5` | Canon | Ages 5–8 (`1.0–5.0`) |
| **Chapter Book / Middle Grade** (`chapter_book`) | Chapter | 10 chapters · ~18,000w | 5,000–45,000w | `5.5×8.5` | — | Ages 8–12 (`3.0–7.5`) |
| **Short Story** (`short_story`) | Part | 3 parts · ~4,500w | 800–10,000w | `5×8` | World, Canon | Adult (`4.5–14.0`) |
| **Novella** (`novella`) | Chapter | 7 chapters · ~18,000w | 8,000–45,000w | `5.5×8.5` | — | Adult (`4.5–14.0`) |
| **Full Novel** (`novel`) | Chapter | 20 chapters · ~65,000w | 35,000–250,000w | `6×9` | — | Adult (`4.5–14.0`) |

- **5 Audiences:** Picture Book (3–7), Early Reader (5–8), Middle Grade (8–12), Young Adult (13–18, FK `4.5–10.5`), Adult Trade (FK `4.5–14.0`).
- **4 POVs & 2 Tenses:** First Person, Third Limited, Third Omniscient, Second Person; Past or Present tense.
- **18 Writing Styles + Custom Author Voice:** Grouped across Children & Fable, Literary & Character, Mystery & Romance, and Speculative & Genre — or write your own tonal brief.

### 3. Four-Layer Anti-AI Guardrail Engine
Instead of relying on unreliable numeric "1–10 quality scores" from an LLM judge, Novelista v3 combines prompt discipline, context isolation, deterministic stylometry, and targeted editorial passes:

1. **Craft Prevention & Unrelated Voice Calibration:** Combines format-specific positive craft habits with banned/restricted word lists (`backend/app/pipeline/prompts/anti_slop.md`), and builds `voice.md` using **unrelated dummy specimens** so Chapter 1 cannot plagiarize sample passages.
2. **Context Firewall (`context_builder.py`):**
   - Strictly filters `canon.md`, `summary.md`, and `continuity.json` to units `< n` — revising Chapter 2 never leaks Chapters 3+ back into the prompt.
   - Keeps the system prompt 100% chapter-invariant for provider prompt caching (dynamic POV locks stay in the user turn).
   - Injects later-unit scope fences, future-clue locks, retired motifs, previous chapter opening words, and standalone final-unit closure locks within a `num_ctx` token budget.
3. **49-Rule Deterministic & Stylometric AI Checker (`ai_checker.py`):** Scans every draft and edit in milliseconds (dialogue-exempt where appropriate) across 7 categories, outputting exact character spans, `A–F` cleanliness grades, 14 quantitative metrics, and per-finding Fix/Ignore:
   - *Corruption & Integrity:* `token_meltdown`, `script_leak`, `meta_leak`, `artefact`, `unbalanced_quotes`, `paragraph_loop` (exact + 3-gram Jaccard similarity + unique-paragraph ratio), `copied_text`, `exemplar_copy`.
   - *Continuity & Locks:* `pov_break`, `gender_mismatch` (honorifics + narration pronouns), `future_clue_leak`, `sequel_bait`, `tense_drift`, `anachronism`, `regional_spelling`.
   - *Banned AI Lexicon & Clichés:* `tier1_slop` (inflection-aware), `tier2_repeat`, `tier3_filler`, `modern_fiction_cliche`, `vague_something`, `cliche_ai_name`.
   - *AI Syntax Habits:* `not_just_but`, `negation_contrast`, `triads`, `the_way`, `adjacent_echo`, `theme_stated`, `chapter_ending_echo`.
   - *Punctuation & Formatting:* `em_dash_forbidden`, `em_dash_density`, `em_dash_overuse`, `mixed_quotes`, `semicolon_density`, `ellipsis_spam`, `section_breaks`.
   - *Narration Craft & Rhythm:* `sentence_rhythm` (burstiness CV), `staccato_fragments`, `transition_openers`, `participle_openers`, `paragraph_uniformity`, `paragraph_opener_monotony`, `felt_density`, `telling_patterns`, `emotion_naming`, `filter_words`, `low_dialogue`, `adverb_dialogue_tags`.
   - *Scope & Readability:* `word_count`, `readability_mismatch` (Flesch-Kincaid vs. target audience).
4. **Voice-Aware Editing & Whole-Book Audit:**
   - **Acquiring Editor (`editor_pass.py`)** + **Polish Loop:** Produces a structured revision brief, feeds checker findings into full-context revision with length-preservation and error-regression guards, and repairs hard mechanical errors surgically (`slop_fix.py`).
   - **Selection Co-Writer:** Highlight any 12+ character passage in the editor for targeted `Rewrite`, `Show Don't Tell`, `Punch Dialogue`, `Sensory`, `Tighten`, or `Custom` edits with side-by-side preview.
   - **Adversarial Cut (`Tighten`):** Trims ~10% filler/over-explanation/telling using unique exact/fuzzy span matching.
   - **Whole-Manuscript Audit + Audit Fix:** Cross-chapter simile repetition, opener matrix, continuity verification, an Acquiring Editor verdict (`ready` / `needs_polish` / `needs_revision`), and selective or one-click **Fix All**.

### 4. Pure-Python Amazon KDP Publishing Studio
Export ready-to-upload packages directly from **Publish & Export** without installing Pandoc, Calibre, or LaTeX:
- **EPUB 3.0** (`mimetype` first, `nav.xhtml` + EPUB 2 `toc.ncx`, embedded cover & spread illustrations, front/back matter).
- **KDP Print PDF** (`fpdf2` in `5×8`, `5.5×8.5`, `6×9`, and `8.5×8.5` trim sizes with headers, page numbers, and illustrations).
- **DOCX (Word)**, **Combined Markdown**, and **Complete Project ZIP**.
- **Export Accepted Units Only** filter, cover image uploader, editable **Front & Back Matter**, and a **KDP Marketing Kit** generator (HTML-ready blurb, 7 unique-stem `<= 50`-char backend keywords, official BISG BISAC categories, comp titles, and cover art direction brief).

### 5. Six Providers × Twelve Independent Task Roles
Connect local or cloud endpoints and assign a different model to each of the **12 pipeline roles** (`Seed Ideas`, `World & Cast`, `Voice Calibration`, `Canon Extractor`, `Book Outline`, `Scene Cards`, `Chapter Drafting`, `Prose Revision`, `Acquiring Editor`, `Continuity Summary`, `Whole-Book Audit`, `KDP Marketing Kit`):
- **Local:** `Ollama` (native `/api/chat` with `num_ctx`, `repeat_penalty`, `repeat_last_n`, `top_p`, `min_p`), `LM Studio`
- **Cloud BYOK:** `Anthropic`, `OpenAI`, `Google Gemini`, `OpenRouter`

---

## What's New in v3 (Latest Updates)

Novelista v3 is a ground-up architectural successor to v2 (`novelista-v2`), resolving structural continuity leaks, small-model collapse modes, concurrency races, and UI roughness:

- **One-Click Whole-Manuscript Audit Fix (`audit.py` / `audit.tsx`):** Check off specific global or per-chapter audit issues (or click **Fix All**), automatically revise unlocked drafted chapters, fix remaining hard errors, refresh running continuity memory, and optionally re-run the audit.
- **Genre-Aware Pipeline Routing (`is_mystery_genre`):** Decouples non-mystery stories (literary drama, cozy fiction, romance, historical, children's) from hardcoded crime/arrest/police assumptions across outlining, scene planning, canon extraction, manuscript auditing, and KDP metadata.
- **Single-Pass Continuous Chapters (`<= 2,500w`) + Scene-by-Scene Long Chapters:** Short and medium units draft as 1 unified scene card to eliminate mid-chapter `* * *` plot reboots, while longer chapters (`>= 2,500w`) use bounded scene cards with boundary-replay stripping.
- **49-Rule AI Checker & Em-Dash Guardrail:** Adds token-meltdown detection, family-surname-safe honorific and pronoun checks, Day-1 `world.md`-aware future-clue locks, standalone ending `sequel_bait` checks, and deterministic conversion of Unicode em-dashes/en-dashes/double-hyphens (`—`, `–`, `--`) into clean ASCII punctuation (`-` / ` - `).
- **Safer Auto-Polish & Regression Guard:** Reserves isolated span rewriting (`slop_fix`) strictly for hard deterministic/continuity errors (`AUTO_FIX_RULES`), feeds style warnings into full-context revision (`chapter_revise`), enforces 90–115% length preservation, and rolls back edits that introduce new errors.
- **Native JSON Enforcement & Prompt Caching:** Uses native JSON response modes across Ollama, Gemini, OpenAI, and OpenRouter (with deterministic salvage fallback), ephemeral prompt caching on Anthropic, single-instance launcher locks, and API-aware SPA routing (`_SpaFallbackRoute` returns `404` instead of `405` on unknown `/api/*` paths).

---

## Requirements & Minimum Hardware Specs

### Software Requirements

| Component | Requirement | Notes |
|---|---|---|
| **Python** | **3.11 or newer** (tested up to 3.14) | Only runtime prerequisite; `.venv` is auto-created by `start.bat` / `start.command`. |
| **Operating System** | Windows 10/11, macOS, or Linux | Native launchers provided for Windows and Unix shells. |
| **LLM Access** | Local server (**Ollama** / **LM Studio**) **or** Cloud API key | Supports Anthropic, OpenAI, Google Gemini, OpenRouter. |
| **Node.js** | *Not required at runtime* (20+ for UI dev only) | Prebuilt `frontend/dist/` is included in this repository. |
| **Database / Pandoc / LaTeX** | **None required** | Pure-Python JSON/Markdown storage and EPUB/PDF/DOCX exporters. |

### Minimum & Recommended Hardware / Model Specs

Model capability directly determines continuity, voice stability, and plot logic across multi-chapter books:

| Tier | Hardware | Recommended Models | Context (`num_ctx`) | Best Suited For |
|---|---|---|---|---|
| **Cloud BYOK** *(Lowest local hardware)* | 4 GB RAM · 500 MB free disk · No GPU | **Anthropic:** Sonnet 4.5 / Opus 4.1 (`Haiku 4.5` on `canon` & `summary`)<br>**OpenAI:** `gpt-4.1` (`gpt-4.1-mini` on `canon` & `summary`)<br>**Gemini:** `gemini-2.5-pro` (`gemini-2.5-flash` on extraction) | Provider max (auto) | **All formats**, especially full novellas & 65k+ novels. Mixed Anthropic presets roughly ~$4–$9 per 80k-word novel (varies by polish passes). |
| **Local Minimum** *(Smoke-testing only)* | 8–16 GB RAM · 6–8 GB VRAM GPU (or 16 GB Apple Silicon) | 7B–8B quantized instruct model (e.g. Q4_K_M+) | `16384`–`32768` | UI exploration, Short Stories, simple Picture Books. *(Models `< 7B` trigger an in-app Settings warning; `4B` models struggle with continuity and loops.)* |
| **Local Sweet Spot — Short & Children's** | 16–32 GB RAM · **16 GB VRAM** GPU (e.g. RTX 4060 Ti 16GB / RX 9070 XT / 24 GB+ Apple Silicon) | **12B–14B** model (e.g. `gemma3:12b`) | `32768` | **Children's Picture Books, Early Readers, Short Stories.** Tested live on `gemma3:12b` (16 GB VRAM, Vulkan): Picture Books hit Grade `A-` with `0.00/1k` AI tics; novellas pass mechanical checks cleanly (`0` errors) though complex detective clue-chains may still need human outline/audit steering. |
| **Local Recommended — Full Novellas & Novels** | 32–64 GB RAM · **24–48 GB VRAM** (or 48–64 GB Apple Silicon) | **27B–70B+** class models (with a fast 7B–12B on `canon` & `summary`) | `32768`–`65536` | **Novellas, Middle Grade, and Full Novels** requiring multi-chapter clue chains, subtext, timeline arithmetic, and consistent voice across 20+ chapters. |

*Disk space:* ~250 MB for the Python virtual environment and dependencies, plus ~1–5 MB per book project (excluding any local LLM weights downloaded by Ollama or LM Studio).

---

## Quickstart & Installation

### 1. Clone the Repository

```bash
git clone https://github.com/darthewok/novelista-v3.git
cd novelista-v3
```

### 2. Launch the App

**Windows:**
- Double-click **`start.bat`** (or run `start.bat` from Command Prompt / PowerShell).
- It finds Python 3.11+, creates `backend\.venv`, installs `backend\requirements.txt`, starts `uvicorn` on the first free port in `8000–8010`, and opens your browser.
- Stop cleanly with **`stop.bat`** (or **`stop.bat --full`** to also terminate `ollama.exe`).

**macOS / Linux:**
```bash
chmod +x start.command stop.command
./start.command
```
- Stop cleanly with `./stop.command`.

> If you are using a local model, make sure **Ollama** (`ollama serve`) or **LM Studio** (local server started) is running before drafting.

### 3. Manual Launch & Environment Variables (Optional)

```bash
cd backend
python -m venv .venv
# Windows: .venv\Scripts\activate   |   macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Supported environment overrides (`backend/app/config.py`):
- `NOVELISTA_DATA_DIR` — custom location for `settings.json` and `novels/` (defaults to `./data`)
- `NOVELISTA_FRONTEND_DIST` — custom path to built static assets (defaults to `./frontend/dist`)
- `NOVELISTA_HOST` / `NOVELISTA_PORT` — host/port config defaults (`127.0.0.1` / `8000`)

---

## How to Use (Step-by-Step Guide)

### Step 1 — Configure Provider & Models (`/settings`)
1. Open **Settings** in the top bar and select your **AI Provider** (`Ollama`, `LM Studio`, `Anthropic`, `OpenAI`, `Google Gemini`, or `OpenRouter`).
2. Enter your API key (cloud providers) or verify the local server URL (`http://localhost:11434` for Ollama, `http://localhost:1234/v1` for LM Studio). Use **Clear** anytime to remove a stored key.
3. Keep **`num_ctx` at `32768`** (or higher for 20+ chapter novels) and keep Ollama `repeat_penalty` around `1.12` (`repeat_last_n: 256`).
4. Assign models per task role using the detected-model dropdowns, **Apply to all**, or the **Apply Preset** button for cloud providers. Put cheaper/faster models on `Canon Extractor` and `Continuity Summary`.
5. Configure **Quality Guardrails & Pipeline Automation**:
   - `Auto-Polish after drafting` (runs Editor brief + Revision + hard-error fixer)
   - `Auto-plan Scene Cards before drafting`
   - `Auto-continue truncated chapters`
   - `Max Polish Passes` (default `2`) and `Target Tells / 1,000w` (default `4.0`)
6. Click **Save Settings** — this automatically verifies connectivity and surfaces any `< 7B` model warnings.

### Step 2 — Create a New Book (`/books/new`)
1. Click **New Book** on the Library dashboard.
2. Pick one of the **6 Formats** (`Picture Book`, `Early Reader`, `Chapter Book / Middle Grade`, `Short Story`, `Novella`, `Full Novel`) — defaults for Audience, Units, Target Words, and KDP trim adjust automatically.
3. Choose **POV**, **Tense**, **Language** (`English (US)` / `English (UK)`), and **Writing Style** (18 presets or `Custom Author Voice`).
4. Enter your story **Premise / Idea** (or leave it ready to brainstorm in Seed).

### Step 3 — Build the Foundation (`Seed → World → Cast → Voice → Canon & Memory`)
Follow the top phase stepper in order (steps marked `(opt)` can be skipped for Picture Books or Short Stories):
1. **Seed:** Paste/refine your premise, optionally upload `.md`, `.txt`, or `.pdf` reference material (world notes, research, plot beats), or generate **10 Story Candidates** and click **Use** on your favorite.
2. **World:** Generate and hand-edit setting rules, geography, era details, and sensory atmosphere (`world.md`).
3. **Cast (`characters.md`):** Generate cast profiles, goals, relationships, workplaces, and dialogue contrast — double-check ages, pronouns, and spelling here.
4. **Voice (`voice.md`):** Click **Generate 3 Voice Trials** to compare three distinct fiction openings, pick one to build your full voice calibration (with unrelated style specimens and anti-exemplars), or edit manually.
5. **Canon & Memory:**
   - **Canon Ledger (`canon.md`):** Compile hard immutable facts (timelines, ownership, physical laws) from your Seed, World, and Cast.
   - **Story So Far (`summary.md`):** Auto-updated after every chapter draft; edit anytime to fix continuity memory.
   - **Author Secrets (`mystery.md`):** Optional hidden twists/motives fed *only* to the Outline generator so early chapters never spoil twists prematurely.

### Step 4 — Generate & Sync the Outline (`Outline`)
1. Generate the unit-by-unit outline (`### Ch N: Title`, `### Spread N: Title`, or `### Part N: Title`) including Plot Logic & Resolution Spine and foreshadowing rows.
2. Verify all plot payoffs, ending closure, and clue ordering directly in the Markdown editor, then click **Save & Sync** so the chapter list reflects your structure.

### Step 5 — Draft, Co-Write & Polish (`Write` & `Chapter Studio`)
- **Batch Drafting (`Chapters`):** Review your pacing bar chart, toggle `Polish each` / `Auto scene cards`, and click **Draft all pending**. You can **Pause after current** or **Cancel** anytime and resume across page navigation.
- **Chapter Studio (`/books/:id/chapters/:n`):**
  - **Toolbar:** `Draft` / `Redraft`, `Polish`, `Tighten` (~10% cut), `Fix Tells`, `Accept` / `Un-accept` (locks finished chapters), `Save`, Edit/Reader view toggle, Zen Focus mode, and adjustable serif font (`14–22px`).
  - **Selection Co-Writer:** Highlight 12+ characters in Edit mode to pop up the floating bar (`Rewrite`, `Show Don't Tell`, `Punch Dialogue`, `Sensory`, `Tighten`, or custom instruction) and inspect the inline diff before applying.
  - **5-Tab Inspector:**
    - `AI Check`: View grade (`A–F`), 6 metric tiles, filter Errors/Warnings, click any finding to jump to its exact line, click **Fix** (rewrites that single line in your book's voice) or **Ignore**.
    - `Scenes`: Inspect, regenerate, or edit scene/spread cards (and upload or view spread illustrations for Picture Books / Early Readers).
    - `Revise`: Edit the Acquiring Editor's Revision Brief or enter custom revision notes (`Polish after`).
    - `Refs`: Quick read-only peek at Characters, World, Canon, Story So Far, and Outline without leaving the chapter.
    - `History`: Browse every saved snapshot (`v0`, `v1`, …), inspect a word-level LCS **Diff**, or **Restore** an earlier draft.

### Step 6 — Whole-Manuscript Audit & Audit Fix (`Audit`)
1. Click **Run Full Manuscript Audit** once chapters are drafted.
2. Review cross-chapter repeated similes, the Unit Cleanliness Matrix, and the **Acquiring Editor Letter**.
3. Check the specific global or per-chapter issues you want resolved (or leave all checked) and click **Fix Selected** / **Fix All** (with `Re-run audit after fixing` enabled if desired). *Accepted/locked units are safely skipped.*

### Step 7 — Publish & Export (`Publish`)
1. Upload a cover image (`.jpg`, `.png`, `.webp`) and fill in **Front & Back Matter** (Author Name, Subtitle, Imprint, Copyright Year, Dedication, Acknowledgments, Author Bio).
2. Click **Generate KDP Kit** to draft your HTML product blurb, 7 backend keywords (`<= 50` chars each), BISAC categories, and Cover Art Direction Brief — edit and **Save Kit**.
3. Download your manuscript in one click: **EPUB 3**, **KDP PDF** (`5×8`, `5.5×8.5`, `6×9`, or `8.5×8.5`), **DOCX**, **Markdown**, or **Project ZIP** (optionally filtering to *Accepted Units Only*).

### Keyboard Shortcuts
- **`Ctrl+K` / `Cmd+K`**: Open full-book Search across outline, foundation docs, and all chapters.
- **`Ctrl+S` / `Cmd+S`**: Save the currently edited document or chapter/brief.
- **`Escape`**: Close Version Diff modal or cancel inline title rename.

---

## What to Avoid (Pitfalls & Best Practices)

1. **Do NOT leave Ollama's context size at the default `4096`.**  
   Chapter drafting prompts easily run 8k–14k tokens with craft rules, voice guide, canon, and context. Always keep `num_ctx` at **`32768`** (default in Novelista v3) or higher so the system prompt isn't silently truncated.
2. **Do NOT expect 4B–8B local models (or 12B models on intricate mystery novels) to match 30B+/frontier reasoning.**  
   A 12B GPU model excels at Picture Books and clean prose mechanics, but complex detective timelines, multi-clue chains, and 20-chapter character continuity need 27B–70B+ local models or a cloud API key on planning/drafting/audit roles. Keep `Canon Extractor` and `Continuity Summary` on fast/cheap models (`Haiku`, `Flash`, `gpt-4.1-mini`).
3. **Do NOT hit `Draft all pending` before proofreading `Seed`, `Cast`, `Canon`, and `Outline`.**  
   Any wrong character age, duplicated workplace, missed clue timing, or unresolved ending in `outline.md` or `canon.md` compounds across every single chapter downstream. Five minutes of editing `canon.md` and `outline.md` saves hours of chapter rewrites.
4. **Do NOT paste full sample story scenes or dialogue into `voice.md` or `characters.md`.**  
   Models strongly copy-paste concrete in-world snippets from foundation docs directly into Chapter 1 (triggering `exemplar_copy` errors). Keep `voice.md` exemplars strictly in unrelated settings/characters, and keep `characters.md` free of pre-written chapter scenes.
5. **Do NOT leave regional spelling, genre boundaries, or standalone ending expectations vague in `Seed`.**  
   Explicitly specify whether the story is *not* a murder mystery if you're writing literary drama, set the exact US/UK language choice in book settings, and state standalone closure rules if you don't want sequel cliffhangers.
6. **Do NOT set `Max Polish Passes` to `4–5` on small local models.**  
   Over-revising with a 7B–12B model washes out voice and causes length drift. Keep `Max Polish Passes` at `1` or `2`, then use **Selection Co-Writer** or **Audit Fix** on specific scenes.
7. **Do NOT mark chapters `Accepted` before running `Audit Fix` if you want them revised.**  
   `Accept` intentionally write-locks chapters across drafting, polishing, and `Audit Fix`. Un-accept a unit whenever you want automated fixes applied to it.
8. **Do NOT run multiple server instances against the same `data/` folder.**  
   Always launch via `start.bat` / `./start.command` and shut down with `stop.bat` / `./stop.command`. Running two separate `uvicorn` processes on ports `8000` and `8001` risks stale frontend/backend mismatches or concurrent writes.
9. **Do NOT upload exports to Amazon KDP without final human verification.**  
   Always inspect the generated EPUB in Kindle Previewer (or an eBook reader) and the PDF page layout, double-check BISAC categories and comp titles in your KDP Marketing Kit, and comply with Amazon KDP's AI-generated / AI-assisted content disclosure guidelines.

---

## Repository & On-Disk Data Layout

```text
novelista-v3/
├── start.bat / stop.bat            Windows single-instance launchers (stop.bat --full stops Ollama)
├── start.command / stop.command    macOS/Linux launchers
├── backend/
│   ├── pyproject.toml              Python >= 3.11 package config & pytest settings
│   ├── requirements.txt            12 runtime + 2 test dependencies
│   ├── app/
│   │   ├── main.py                 FastAPI app + API-safe SPA fallback
│   │   ├── config.py               Env path configuration (data, dist, host, port)
│   │   ├── formats.py              6 format profiles + Flesch-Kincaid audience bands
│   │   ├── settings.py             6 providers, 12 task roles, masked key storage
│   │   ├── store.py / storage.py   Atomic JSON/Markdown store, locks, version backups
│   │   ├── events.py / jobs.py     Multi-subscriber SSE stream + pauseable batch queue
│   │   ├── llm_client.py           Provider clients, retries, JSON mode, prompt caching
│   │   ├── routers/                REST & SSE endpoints (books, seed, foundation, outline, chapters, audit, uploads, settings, stream)
│   │   ├── pipeline/               19 craft/generation/guardrail modules + prompts/
│   │   └── export/                 Pure-Python EPUB 3, KDP PDF, DOCX, Markdown & ZIP
│   └── tests/                      66 automated unit & router integration tests
├── frontend/
│   ├── dist/                       Prebuilt offline-verified SPA (served directly by FastAPI)
│   ├── src/                        React 19 + TypeScript + Tailwind + TanStack Query source
│   └── scripts/check-no-external.mjs
└── data/
    └── novels/                     Empty on clone (.gitkeep); books & settings.json are gitignored
```

Each book you create is stored cleanly under `data/novels/<book_id>/`:
- `book.json` — metadata, format, status, chapter index, front/back matter, ignored findings
- `seed.txt`, `world.md`, `characters.md`, `voice.md`, `canon.md`, `summary.md`, `mystery.md`, `outline.md`
- `continuity.json`, `kdp_kit.json`, `kdp_kit.md`
- `chapters/` (`ch_001.md` + `ch_001.v0.md` snapshots), `scenes/`, `briefs/`, `reports/`, `uploads/`

---

## Development & Testing

**Backend tests (66 tests — isolated via temp `NOVELISTA_DATA_DIR`, no live API keys required):**

```bash
# Windows
cd backend
.venv\Scripts\python -m pytest

# macOS / Linux
cd backend
.venv/bin/python -m pytest
```

**Frontend development & production rebuild (requires Node.js 20+):**

```bash
cd frontend
npm install
npm run dev      # Vite dev server on :5173 proxying /api to 127.0.0.1:8000
npm run build    # tsc --noEmit + vite build + check-no-external.mjs
```

---

## License

MIT — see [LICENSE](LICENSE).
