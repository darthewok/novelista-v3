import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { useQueryClient } from "@tanstack/react-query";
import { FolderOpen, RefreshCw, Sparkles, BookOpen, Layers } from "lucide-react";
import { api, ObsidianScanResult } from "@/lib/api";
import { useAction } from "@/lib/useAction";
import { useToast } from "@/lib/toast";
import {
  AUDIENCE_OPTIONS,
  FORMAT_CARDS,
  POV_OPTIONS,
  STYLE_OPTIONS,
  TENSE_OPTIONS,
} from "@/lib/catalog";
import { ErrorLine } from "@/components/ErrorLine";
import { VaultFolderPickerModal } from "@/components/VaultFolderPickerModal";
import { VaultMappingEditor } from "@/components/VaultMappingEditor";

export default function NewObsidianBookPage() {
  const nav = useNavigate();
  const qc = useQueryClient();
  const toast = useToast();

  const [vaultPath, setVaultPath] = useState("");
  const [writeBackDir, setWriteBackDir] = useState("Novelista Export");
  const [pickerOpen, setPickerOpen] = useState(false);

  const [scanData, setScanData] = useState<ObsidianScanResult | null>(null);
  const [folderRules, setFolderRules] = useState<Record<string, string>>({});
  const [fileOverrides, setFileOverrides] = useState<Record<string, string>>({});
  const [aiRationale, setAiRationale] = useState<string | null>(null);

  const [title, setTitle] = useState("");
  const [bookFormat, setBookFormat] = useState("novel");
  const [audience, setAudience] = useState("adult");
  const [chapterCount, setChapterCount] = useState(20);
  const [targetWords, setTargetWords] = useState(65000);
  const [pov, setPov] = useState("third_limited");
  const [tense, setTense] = useState("past");
  const [writingStyle, setWritingStyle] = useState("literary_contemporary");
  const [customStyle, setCustomStyle] = useState("");
  const [genre, setGenre] = useState("");
  const [language, setLanguage] = useState("English");
  const [autoSync, setAutoSync] = useState(true);
  const [importChapters, setImportChapters] = useState(false);

  const applySuggestedBook = (sb: Partial<ObsidianScanResult["suggested_book"]>) => {
    if (sb.title) setTitle(sb.title);
    if (sb.genre !== undefined) setGenre(sb.genre);
    if (sb.book_format) setBookFormat(sb.book_format);
    if (sb.audience) setAudience(sb.audience);
    if (sb.chapter_count) setChapterCount(sb.chapter_count);
    if (sb.target_words) setTargetWords(sb.target_words);
    if (sb.pov) setPov(sb.pov);
    if (sb.tense) setTense(sb.tense);
    if (sb.language) setLanguage(sb.language);
  };

  const scanAct = useAction(async (pathToScan?: string) => {
    const p = (pathToScan ?? vaultPath).trim();
    if (!p) throw new Error("Enter or browse to an Obsidian vault folder first.");
    const res = await api.obsidianScan(p, writeBackDir.trim() || "Novelista Export");
    setVaultPath(res.vault_path);
    setScanData(res);
    setFolderRules(res.suggested_folder_rules);
    setFileOverrides(res.suggested_file_overrides);
    setAiRationale(null);
    applySuggestedBook(res.suggested_book);
    toast.success(`Scanned ${res.total_notes} Markdown notes.`);
  });

  const aiMapAct = useAction(async () => {
    if (!scanData) return;
    const res = await api.obsidianSuggestMapping(scanData.vault_path, writeBackDir.trim() || "Novelista Export");
    setFolderRules(res.folder_rules);
    setFileOverrides(res.file_overrides);
    setAiRationale(res.rationale || "Updated folder and file mappings from note contents.");
    if (res.suggested_book) applySuggestedBook(res.suggested_book);
    toast.success("AI mapping applied.");
  });

  const createAct = useAction(async () => {
    if (!scanData) throw new Error("Scan a vault folder first.");
    const res = await api.createObsidianBook({
      title: title.trim() || "Untitled",
      book_format: bookFormat,
      audience,
      chapter_count: chapterCount,
      target_words: targetWords,
      pov,
      tense,
      writing_style: writingStyle,
      custom_style: customStyle,
      genre,
      language,
      idea: "",
      vault_path: scanData.vault_path,
      folder_rules: folderRules,
      file_overrides: fileOverrides,
      tag_rules: {},
      write_back_dir: writeBackDir.trim() || "Novelista Export",
      auto_sync_before_draft: autoSync,
      import_chapters: importChapters,
    });
    qc.invalidateQueries({ queryKey: ["books"] });
    if (res.sync.warnings.length > 0) {
      toast.info(`Synced with ${res.sync.warnings.length} note(s) to check in Vault Studio.`);
    } else {
      toast.success(`Connected vault and synced ${res.sync.note_count} note(s).`);
    }
    nav(`/books/${res.book.id}/vault`);
  });

  const styleGroups = Array.from(new Set(STYLE_OPTIONS.map((s) => s.group || "Other")));

  return (
    <div className="max-w-5xl mx-auto space-y-5">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="font-prose text-2xl font-bold text-ink">Connect Obsidian Vault</h1>
          <p className="text-sm text-muted">
            Use your Obsidian vault as a live story bible — skip the step-by-step wizard and draft straight from your mapped notes.
          </p>
        </div>

        <div className="inline-flex rounded-lg border border-border bg-surface p-0.5">
          <Link
            to="/books/new"
            className="rounded-md px-3 py-1.5 text-xs font-medium text-muted hover:text-ink inline-flex items-center gap-1.5"
          >
            <Sparkles className="h-3.5 w-3.5" /> Step-by-Step Wizard
          </Link>
          <span className="rounded-md bg-accent px-3 py-1.5 text-xs font-medium text-accent-fg inline-flex items-center gap-1.5">
            <Layers className="h-3.5 w-3.5" /> Obsidian Vault
          </span>
        </div>
      </div>

      <ErrorLine error={scanAct.error || aiMapAct.error || createAct.error} />

      {/* Step 1: Pick & Scan Vault */}
      <div className="card p-5 space-y-3">
        <div className="flex items-center justify-between gap-2">
          <h2 className="text-sm font-semibold text-ink">1. Select Your Obsidian Vault or Book Subfolder</h2>
          {scanData && (
            <span className="rounded-md bg-emerald-500/15 px-2 py-0.5 text-[11px] font-medium text-emerald-700 dark:text-emerald-300">
              {scanData.total_notes} notes scanned{scanData.is_obsidian_vault ? " · .obsidian detected" : ""}
            </span>
          )}
        </div>

        <form
          onSubmit={(e) => {
            e.preventDefault();
            scanAct.run();
          }}
          className="flex flex-wrap items-center gap-2"
        >
          <input
            value={vaultPath}
            onChange={(e) => setVaultPath(e.target.value)}
            placeholder="e.g. C:\Users\you\Obsidian\MyNovelVault"
            className="input-field flex-1 min-w-[240px] font-mono text-xs"
          />
          <button type="button" onClick={() => setPickerOpen(true)} className="btn-secondary text-xs">
            <FolderOpen className="h-3.5 w-3.5" /> Browse Folders
          </button>
          <button type="submit" disabled={scanAct.busy || !vaultPath.trim()} className="btn-primary text-xs">
            <RefreshCw className={`h-3.5 w-3.5 ${scanAct.busy ? "animate-spin" : ""}`} />
            {scanAct.busy ? "Scanning…" : "Scan Vault"}
          </button>
        </form>
        <p className="text-[11px] text-muted">
          You can point to an entire Obsidian vault (regardless of folder names) or a single book folder inside it.
        </p>
      </div>

      {/* Step 2 & 3 */}
      {scanData && (
        <>
          <div className="card p-5 space-y-3">
            <div>
              <h2 className="text-sm font-semibold text-ink">2. Map Folders & Notes to Novelista Roles</h2>
              <p className="text-xs text-muted">
                Folder mappings apply automatically when you add new notes in Obsidian later. Unmapped notes stay ignored.
              </p>
            </div>

            <VaultMappingEditor
              folders={scanData.folders}
              notes={scanData.notes}
              folderRules={folderRules}
              fileOverrides={fileOverrides}
              onChangeFolderRules={setFolderRules}
              onChangeFileOverrides={setFileOverrides}
              onAutoMapAI={() => aiMapAct.run()}
              onResetHeuristics={() => {
                setFolderRules(scanData.suggested_folder_rules);
                setFileOverrides(scanData.suggested_file_overrides);
                setAiRationale(null);
              }}
              aiBusy={aiMapAct.busy}
              aiRationale={aiRationale}
            />
          </div>

          <div className="card p-5 space-y-4">
            <h2 className="text-sm font-semibold text-ink">3. Book Metadata & Live Sync Settings</h2>

            <div className="grid gap-4 sm:grid-cols-3">
              <div className="sm:col-span-2 space-y-1">
                <label htmlFor="obs-title" className="text-xs font-semibold text-muted">Book Title</label>
                <input
                  id="obs-title"
                  className="input-field"
                  value={title}
                  onChange={(e) => setTitle(e.target.value)}
                />
              </div>
              <div className="space-y-1">
                <label htmlFor="obs-genre" className="text-xs font-semibold text-muted">Genre / Shelf</label>
                <input
                  id="obs-genre"
                  className="input-field"
                  placeholder="e.g., Epic Fantasy, Cozy Mystery"
                  value={genre}
                  onChange={(e) => setGenre(e.target.value)}
                />
              </div>
            </div>

            <div className="grid gap-4 sm:grid-cols-4">
              <div className="space-y-1">
                <label htmlFor="obs-format" className="text-xs font-semibold text-muted">Format</label>
                <select
                  id="obs-format"
                  className="input-field"
                  value={bookFormat}
                  onChange={(e) => {
                    const next = e.target.value;
                    setBookFormat(next);
                    const fc = FORMAT_CARDS.find((f) => f.value === next);
                    if (fc) {
                      setAudience(fc.defaultAudience);
                      setChapterCount(fc.defaultUnits);
                      setTargetWords(fc.defaultWords);
                    }
                  }}
                >
                  {FORMAT_CARDS.map((f) => (
                    <option key={f.value} value={f.value}>
                      {f.label}
                    </option>
                  ))}
                </select>
              </div>
              <div className="space-y-1">
                <label htmlFor="obs-audience" className="text-xs font-semibold text-muted">Audience</label>
                <select
                  id="obs-audience"
                  className="input-field"
                  value={audience}
                  onChange={(e) => setAudience(e.target.value)}
                >
                  {AUDIENCE_OPTIONS.map((a) => (
                    <option key={a.value} value={a.value}>
                      {a.label}
                    </option>
                  ))}
                </select>
              </div>
              <div className="space-y-1">
                <label htmlFor="obs-units" className="text-xs font-semibold text-muted">Chapters / Units</label>
                <input
                  id="obs-units"
                  type="number"
                  min={1}
                  max={200}
                  className="input-field"
                  value={chapterCount}
                  onChange={(e) => setChapterCount(Math.max(1, Number(e.target.value) || 1))}
                />
              </div>
              <div className="space-y-1">
                <label htmlFor="obs-words" className="text-xs font-semibold text-muted">Target Words</label>
                <input
                  id="obs-words"
                  type="number"
                  min={150}
                  max={2000000}
                  step={100}
                  className="input-field"
                  value={targetWords}
                  onChange={(e) => setTargetWords(Math.max(150, Number(e.target.value) || 150))}
                />
              </div>
            </div>

            <div className="grid gap-4 sm:grid-cols-4">
              <div className="space-y-1">
                <label htmlFor="obs-pov" className="text-xs font-semibold text-muted">POV</label>
                <select id="obs-pov" className="input-field" value={pov} onChange={(e) => setPov(e.target.value)}>
                  {POV_OPTIONS.map((p) => (
                    <option key={p.value} value={p.value}>
                      {p.label}
                    </option>
                  ))}
                </select>
              </div>
              <div className="space-y-1">
                <label htmlFor="obs-tense" className="text-xs font-semibold text-muted">Tense</label>
                <select id="obs-tense" className="input-field" value={tense} onChange={(e) => setTense(e.target.value)}>
                  {TENSE_OPTIONS.map((t) => (
                    <option key={t.value} value={t.value}>
                      {t.label}
                    </option>
                  ))}
                </select>
              </div>
              <div className="space-y-1">
                <label htmlFor="obs-style" className="text-xs font-semibold text-muted">Prose Voice Style</label>
                <select
                  id="obs-style"
                  className="input-field"
                  value={writingStyle}
                  onChange={(e) => setWritingStyle(e.target.value)}
                >
                  {styleGroups.map((g) => (
                    <optgroup key={g} label={g}>
                      {STYLE_OPTIONS.filter((s) => (s.group || "Other") === g).map((st) => (
                        <option key={st.value} value={st.value}>
                          {st.label}
                        </option>
                      ))}
                    </optgroup>
                  ))}
                </select>
              </div>
              <div className="space-y-1">
                <label htmlFor="obs-lang" className="text-xs font-semibold text-muted">Language</label>
                <input
                  id="obs-lang"
                  className="input-field"
                  value={language}
                  onChange={(e) => setLanguage(e.target.value)}
                />
              </div>
            </div>

            <div className="grid gap-4 sm:grid-cols-2">
              <div className="space-y-1">
                <label htmlFor="obs-custom-style" className="text-xs font-semibold text-muted">Custom Voice Notes (optional)</label>
                <input
                  id="obs-custom-style"
                  className="input-field"
                  placeholder="e.g., dry maritime wit, brisk dialogue"
                  value={customStyle}
                  onChange={(e) => setCustomStyle(e.target.value)}
                />
              </div>
              <div className="space-y-1">
                <label htmlFor="obs-wb-dir" className="text-xs font-semibold text-muted">Write-Back Subfolder inside Vault</label>
                <input
                  id="obs-wb-dir"
                  className="input-field font-mono text-xs"
                  value={writeBackDir}
                  onChange={(e) => setWriteBackDir(e.target.value)}
                />
              </div>
            </div>

            <div className="flex flex-wrap gap-6 pt-1 text-xs text-ink">
              <label className="inline-flex items-center gap-2 cursor-pointer">
                <input
                  type="checkbox"
                  checked={autoSync}
                  onChange={(e) => setAutoSync(e.target.checked)}
                  className="rounded border-border"
                />
                <span>Auto-sync changed Obsidian notes right before drafting or revising chapters</span>
              </label>
              <label className="inline-flex items-center gap-2 cursor-pointer">
                <input
                  type="checkbox"
                  checked={importChapters}
                  onChange={(e) => setImportChapters(e.target.checked)}
                  className="rounded border-border"
                />
                <span>Import existing prose notes mapped as Chapter Drafts on sync</span>
              </label>
            </div>

            <div className="flex justify-end pt-2">
              <button
                type="button"
                disabled={createAct.busy}
                onClick={() => createAct.run()}
                className="btn-primary px-5 py-2.5"
              >
                <BookOpen className="h-4 w-4" />
                {createAct.busy ? "Compiling Vault…" : "Create Linked Book & Sync"}
              </button>
            </div>
          </div>
        </>
      )}

      <VaultFolderPickerModal
        open={pickerOpen}
        initialPath={vaultPath}
        onClose={() => setPickerOpen(false)}
        onSelect={(p) => {
          setVaultPath(p);
          scanAct.run(p);
        }}
      />
    </div>
  );
}