import { useEffect, useState } from "react";
import { Link, useOutletContext, useParams } from "react-router-dom";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  AlertTriangle,
  ArrowRight,
  CheckCircle2,
  FolderOpen,
  Link2,
  RefreshCw,
  Save,
  Sparkles,
  Unlink,
  UploadCloud,
} from "lucide-react";
import { api, Book, FormatProfile, ObsidianScanResult } from "@/lib/api";
import { useAction } from "@/lib/useAction";
import { useJobStream } from "@/lib/useJobStream";
import { useToast } from "@/lib/toast";
import { ErrorLine } from "@/components/ErrorLine";
import { GenerationProgress } from "@/components/GenerationProgress";
import { VaultFolderPickerModal } from "@/components/VaultFolderPickerModal";
import { VaultMappingEditor } from "@/components/VaultMappingEditor";

const ARTIFACT_CARDS: Array<{ key: string; label: string; to: string }> = [
  { key: "seed", label: "Seed / Premise", to: "seed" },
  { key: "world", label: "World & Lore", to: "world" },
  { key: "characters", label: "Cast & Voice Matrix", to: "characters" },
  { key: "voice", label: "Voice Guide", to: "voice" },
  { key: "canon", label: "Canon Ledger", to: "canon" },
  { key: "mystery", label: "Author Secrets", to: "canon" },
  { key: "outline", label: "Outline", to: "outline" },
  { key: "reference", label: "Reference Pack", to: "seed" },
];

export default function VaultStudioPage() {
  const { id } = useParams();
  const qc = useQueryClient();
  const toast = useToast();
  const { book, format } = useOutletContext<{ book: Book; format?: FormatProfile }>();

  const { data: status, isLoading, error, refetch } = useQuery({
    queryKey: ["obsidian", id],
    queryFn: () => api.getBookObsidian(id!),
    enabled: Boolean(id),
    refetchInterval: 8000,
  });

  // Unlinked connect state
  const [connectPath, setConnectPath] = useState("");
  const [pickerOpen, setPickerOpen] = useState(false);
  const [unlinkedScan, setUnlinkedScan] = useState<ObsidianScanResult | null>(null);

  // Editable mapping state
  const [folderRules, setFolderRules] = useState<Record<string, string>>({});
  const [fileOverrides, setFileOverrides] = useState<Record<string, string>>({});
  const [autoSync, setAutoSync] = useState(true);
  const [importChapters, setImportChapters] = useState(false);
  const [writeBackDir, setWriteBackDir] = useState("Novelista Export");
  const [mappingDirty, setMappingDirty] = useState(false);
  const [aiRationale, setAiRationale] = useState<string | null>(null);
  const [lastWarnings, setLastWarnings] = useState<string[]>([]);

  // Push-back options
  const [pushManuscript, setPushManuscript] = useState(true);
  const [pushChapters, setPushChapters] = useState(true);
  const [pushSummary, setPushSummary] = useState(true);
  const [pushCanon, setPushCanon] = useState(true);
  const [pushAcceptedOnly, setPushAcceptedOnly] = useState(false);
  const [pushedFiles, setPushedFiles] = useState<string[]>([]);

  useEffect(() => {
    if (status?.linked && status.config && !mappingDirty) {
      setFolderRules(status.config.folder_rules || {});
      setFileOverrides(status.config.file_overrides || {});
      setAutoSync(status.config.auto_sync_before_draft);
      setImportChapters(status.config.import_chapters);
      setWriteBackDir(status.config.write_back_dir || "Novelista Export");
    }
  }, [status, mappingDirty]);

  const outlineStream = useJobStream(async (ev) => {
    if (ev.type === "done") {
      await refetch();
      qc.invalidateQueries({ queryKey: ["book", id] });
      qc.invalidateQueries({ queryKey: ["outline", id] });
      toast.success("Vault outline structured and chapters synced.");
    }
  });

  useEffect(() => {
    if (id) outlineStream.resume({ bookId: id, kind: "obsidian_structure_outline" });
  }, [id, outlineStream.resume]);

  const syncAct = useAction(async () => {
    const res = await api.syncBookObsidian(id!);
    setLastWarnings(res.sync.warnings || []);
    qc.invalidateQueries({ queryKey: ["book", id] });
    qc.invalidateQueries({ queryKey: ["obsidian", id] });
    toast.success(`Synced ${res.sync.synced_roles.length} role(s) from Obsidian.`);
  });

  const saveMappingAct = useAction(async (opts?: { syncNow?: boolean; nextAutoSync?: boolean }) => {
    const targetVault = status?.linked ? status.vault_path : unlinkedScan?.vault_path;
    if (!targetVault) throw new Error("Scan a vault folder first.");
    const res = await api.putBookObsidian(id!, {
      vault_path: targetVault,
      folder_rules: folderRules,
      file_overrides: fileOverrides,
      tag_rules: status?.config?.tag_rules || {},
      write_back_dir: writeBackDir.trim() || "Novelista Export",
      auto_sync_before_draft: opts?.nextAutoSync ?? autoSync,
      import_chapters: importChapters,
      sync_now: opts?.syncNow ?? true,
    });
    setMappingDirty(false);
    setLastWarnings(res.sync?.warnings || []);
    qc.invalidateQueries({ queryKey: ["book", id] });
    qc.invalidateQueries({ queryKey: ["books"] });
    qc.invalidateQueries({ queryKey: ["obsidian", id] });
    toast.success(opts?.syncNow === false ? "Vault settings saved." : "Mapping saved and synced from Obsidian.");
  });

  const aiMapAct = useAction(async () => {
    const res = status?.linked
      ? await api.suggestBookObsidianMapping(id!)
      : await api.obsidianSuggestMapping(unlinkedScan!.vault_path, writeBackDir);
    setFolderRules(res.folder_rules);
    setFileOverrides(res.file_overrides);
    setMappingDirty(true);
    setAiRationale(res.rationale || "AI updated folder and file mapping rules.");
    toast.success("AI mapping suggested — click Save Mapping & Sync to apply.");
  });

  const scanUnlinkedAct = useAction(async (pathToScan?: string) => {
    const p = (pathToScan ?? connectPath).trim();
    if (!p) throw new Error("Enter or browse to a vault path first.");
    const res = await api.obsidianScan(p, writeBackDir.trim() || "Novelista Export");
    setConnectPath(res.vault_path);
    setUnlinkedScan(res);
    setFolderRules(res.suggested_folder_rules);
    setFileOverrides(res.suggested_file_overrides);
    setMappingDirty(true);
    toast.success(`Scanned ${res.total_notes} notes.`);
  });

  const structureAct = useAction(async () => {
    const job = await api.structureObsidianOutline(id!);
    outlineStream.start(job.id, { bookId: id!, kind: "obsidian_structure_outline" });
  });

  const pushAct = useAction(async () => {
    const res = await api.pushBookObsidian(id!, {
      write_back_dir: writeBackDir.trim() || "Novelista Export",
      include_manuscript: pushManuscript,
      include_chapters: pushChapters,
      include_summary: pushSummary,
      include_canon: pushCanon,
      accepted_only: pushAcceptedOnly,
    });
    setPushedFiles(res.written_files);
    qc.invalidateQueries({ queryKey: ["obsidian", id] });
    toast.success(`Wrote ${res.count} Markdown file(s) to ${res.write_back_dir}/.`);
  });

  const unlinkAct = useAction(async () => {
    if (!window.confirm("Unlink this Obsidian vault? All compiled files and chapters stay intact in Novelista.")) return;
    await api.unlinkBookObsidian(id!);
    setMappingDirty(false);
    qc.invalidateQueries({ queryKey: ["book", id] });
    qc.invalidateQueries({ queryKey: ["books"] });
    qc.invalidateQueries({ queryKey: ["obsidian", id] });
    toast.info("Vault unlinked. Switched book to standard mode.");
  });

  if (isLoading) return <p className="text-sm text-muted py-6">Inspecting Obsidian vault…</p>;
  if (error || !status) return <ErrorLine error={error instanceof Error ? error.message : "Failed to load vault state"} />;

  // Unlinked flow: let user connect a vault to this existing book
  if (!status.linked) {
    return (
      <div className="space-y-4">
        <div className="card p-5 space-y-3">
          <div className="flex items-center gap-2">
            <Link2 className="h-4 w-4 text-accent" />
            <h2 className="text-base font-semibold text-ink">Link an Obsidian Vault to "{book.title}"</h2>
          </div>
          <p className="text-xs text-muted">
            Connect any local Obsidian folder so changes to your worldbuilding, character notes, or outline sync into this book automatically.
          </p>

          <ErrorLine error={scanUnlinkedAct.error || saveMappingAct.error || aiMapAct.error} />

          <div className="flex flex-wrap items-center gap-2">
            <input
              value={connectPath}
              onChange={(e) => setConnectPath(e.target.value)}
              placeholder="Path to Obsidian vault or book folder..."
              className="input-field flex-1 min-w-[240px] font-mono text-xs"
            />
            <button type="button" onClick={() => setPickerOpen(true)} className="btn-secondary text-xs">
              <FolderOpen className="h-3.5 w-3.5" /> Browse Folders
            </button>
            <button type="button" onClick={() => scanUnlinkedAct.run()} disabled={scanUnlinkedAct.busy} className="btn-primary text-xs">
              <RefreshCw className={`h-3.5 w-3.5 ${scanUnlinkedAct.busy ? "animate-spin" : ""}`} /> Scan Vault
            </button>
          </div>
        </div>

        {unlinkedScan && (
          <div className="card p-5 space-y-4">
            <VaultMappingEditor
              folders={unlinkedScan.folders}
              notes={unlinkedScan.notes}
              folderRules={folderRules}
              fileOverrides={fileOverrides}
              onChangeFolderRules={(n) => {
                setFolderRules(n);
                setMappingDirty(true);
              }}
              onChangeFileOverrides={(n) => {
                setFileOverrides(n);
                setMappingDirty(true);
              }}
              onAutoMapAI={() => aiMapAct.run()}
              aiBusy={aiMapAct.busy}
              aiRationale={aiRationale}
            />
            <div className="flex justify-end">
              <button type="button" disabled={saveMappingAct.busy} onClick={() => saveMappingAct.run({ syncNow: true })} className="btn-primary">
                <Link2 className="h-4 w-4" /> Connect & Sync Vault
              </button>
            </div>
          </div>
        )}

        <VaultFolderPickerModal
          open={pickerOpen}
          initialPath={connectPath}
          onClose={() => setPickerOpen(false)}
          onSelect={(p) => {
            setConnectPath(p);
            scanUnlinkedAct.run(p);
          }}
        />
      </div>
    );
  }

  const totalChanges = status.added.length + status.modified.length + status.deleted.length;

  return (
    <div className="space-y-5">
      <ErrorLine error={syncAct.error || saveMappingAct.error || aiMapAct.error || structureAct.error || pushAct.error || unlinkAct.error} />

      {/* Live Sync Status Banner */}
      <div className="card p-5 space-y-4">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div className="space-y-1 min-w-0">
            <div className="flex flex-wrap items-center gap-2">
              <h2 className="text-base font-semibold text-ink">Linked Obsidian Vault</h2>
              {status.status === "in_sync" && (
                <span className="inline-flex items-center gap-1 rounded-full bg-emerald-500/15 px-2.5 py-0.5 text-xs font-medium text-emerald-700 dark:text-emerald-300">
                  <CheckCircle2 className="h-3.5 w-3.5" /> In Sync
                </span>
              )}
              {status.status === "out_of_sync" && (
                <span className="inline-flex items-center gap-1 rounded-full bg-amber-500/15 px-2.5 py-0.5 text-xs font-semibold text-amber-700 dark:text-amber-300">
                  <AlertTriangle className="h-3.5 w-3.5" /> {totalChanges} Vault Change{totalChanges === 1 ? "" : "s"}
                </span>
              )}
              {status.status === "missing_vault" && (
                <span className="inline-flex items-center gap-1 rounded-full bg-rose-500/15 px-2.5 py-0.5 text-xs font-semibold text-rose-600 dark:text-rose-300">
                  <AlertTriangle className="h-3.5 w-3.5" /> Vault Path Unreachable
                </span>
              )}
            </div>
            <div className="font-mono text-xs text-muted break-all">{status.vault_path}</div>
            {status.last_synced_at && (
              <div className="text-[11px] text-muted">Last synced: {new Date(status.last_synced_at).toLocaleString()}</div>
            )}
          </div>

          <div className="flex flex-wrap items-center gap-2">
            <button type="button" onClick={() => refetch()} className="btn-secondary text-xs">
              <RefreshCw className="h-3.5 w-3.5" /> Check
            </button>
            <button
              type="button"
              onClick={() => syncAct.run()}
              disabled={syncAct.busy || status.status === "missing_vault"}
              className="btn-primary text-xs"
            >
              <RefreshCw className={`h-3.5 w-3.5 ${syncAct.busy ? "animate-spin" : ""}`} />
              {syncAct.busy ? "Syncing…" : "Sync from Obsidian Now"}
            </button>
          </div>
        </div>

        {status.error && (
          <div className="rounded-lg bg-rose-500/10 px-3 py-2 text-xs text-rose-600 dark:text-rose-300">{status.error}</div>
        )}

        {totalChanges > 0 && (
          <div className="rounded-lg border border-amber-500/30 bg-amber-500/5 p-3 space-y-1.5 text-xs">
            <div className="font-semibold text-ink">Detected edits in Obsidian waiting to sync:</div>
            <div className="grid gap-1 sm:grid-cols-3 text-[11px]">
              {status.added.map((a) => (
                <div key={`a-${a.rel_path}`} className="truncate text-emerald-700 dark:text-emerald-300">
                  + {a.rel_path} <span className="opacity-75">({a.role})</span>
                </div>
              ))}
              {status.modified.map((m) => (
                <div key={`m-${m.rel_path}`} className="truncate text-amber-700 dark:text-amber-300">
                  ~ {m.rel_path} <span className="opacity-75">({m.role})</span>
                </div>
              ))}
              {status.deleted.map((d) => (
                <div key={`d-${d.rel_path}`} className="truncate text-rose-600 dark:text-rose-300">
                  - {d.rel_path} <span className="opacity-75">({d.role})</span>
                </div>
              ))}
            </div>
          </div>
        )}

        {lastWarnings.length > 0 && (
          <div className="space-y-1">
            {lastWarnings.map((w, i) => (
              <div key={i} className="flex items-start gap-1.5 rounded bg-amber-500/10 px-3 py-2 text-xs text-amber-800 dark:text-amber-300">
                <AlertTriangle className="h-3.5 w-3.5 shrink-0 mt-0.5" />
                <span>{w}</span>
              </div>
            ))}
          </div>
        )}

        <div className="flex flex-wrap items-center justify-between gap-3 border-t border-border pt-3 text-xs">
          <label className="inline-flex items-center gap-2 cursor-pointer text-ink">
            <input
              type="checkbox"
              checked={autoSync}
              onChange={(e) => {
                const v = e.target.checked;
                setAutoSync(v);
                saveMappingAct.run({ syncNow: false, nextAutoSync: v });
              }}
              className="rounded border-border"
            />
            <span>Auto-sync changed notes right before drafting or revising any {format?.unit_label.toLowerCase() || "chapter"}</span>
          </label>

          {book.chapters.length > 0 && !status.needs_outline_structuring && (
            <Link to={`/books/${book.id}/chapters`} className="btn-primary py-1.5 px-3 text-xs">
              Go to Write {format?.unit_label_plural || "Chapters"} <ArrowRight className="h-3.5 w-3.5" />
            </Link>
          )}
        </div>
      </div>

      {/* Outline Structuring Alert (if raw prose outline imported) */}
      {status.needs_outline_structuring && (
        <div className="card border-accent/40 bg-accent-soft/40 p-4 space-y-3">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div>
              <h3 className="text-sm font-semibold text-ink">Outline Needs Chapter Headings</h3>
              <p className="text-xs text-muted">
                Your Obsidian outline notes do not yet use <code className="text-[11px]">### Ch N: Title</code> headings. Convert them automatically while keeping all your plot beats, or edit the Outline tab directly.
              </p>
            </div>
            <div className="flex items-center gap-2">
              <Link to={`/books/${book.id}/outline`} className="btn-secondary text-xs">
                Open Outline Editor
              </Link>
              <button
                type="button"
                disabled={structureAct.busy || outlineStream.busy}
                onClick={() => structureAct.run()}
                className="btn-primary text-xs"
              >
                <Sparkles className="h-3.5 w-3.5" />
                {outlineStream.busy ? "Structuring…" : "Structure Outline with AI"}
              </button>
            </div>
          </div>
          <GenerationProgress state={outlineStream.state} onCancel={outlineStream.cancel} />
        </div>
      )}

      {/* Compiled Artifacts Overview */}
      <div className="card p-5 space-y-3">
        <div className="flex items-center justify-between gap-2">
          <h3 className="text-sm font-semibold text-ink">Compiled Novelista Story Bible</h3>
          <span className="text-[11px] text-muted">Click any module to inspect or tweak the compiled Markdown</span>
        </div>
        <div className="grid gap-2.5 sm:grid-cols-2 lg:grid-cols-4">
          {ARTIFACT_CARDS.map((card) => {
            const st = status.artifact_stats?.[card.key];
            const noteCount = status.mapped_counts?.[card.key] || 0;
            const words = st?.words || 0;
            const exists = Boolean(st?.exists);
            return (
              <Link
                key={card.key}
                to={`/books/${book.id}/${card.to}`}
                className="rounded-lg border border-border bg-surface-2/40 p-3 hover:border-accent/60 transition flex flex-col justify-between gap-1"
              >
                <div className="flex items-center justify-between gap-2">
                  <span className="text-xs font-semibold text-ink">{card.label}</span>
                  <span
                    className={`rounded px-1.5 py-0.5 text-[10px] font-medium ${
                      exists ? "bg-emerald-500/15 text-emerald-700 dark:text-emerald-300" : "bg-surface-2 text-muted"
                    }`}
                  >
                    {exists ? "Ready" : "Empty"}
                  </span>
                </div>
                <div className="text-[11px] text-muted">
                  {noteCount} vault note{noteCount === 1 ? "" : "s"} · {words.toLocaleString()} words
                </div>
              </Link>
            );
          })}
        </div>
      </div>

      {/* Push Back to Obsidian */}
      <div className="card p-5 space-y-3">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <h3 className="text-sm font-semibold text-ink">Write Back to Obsidian Vault</h3>
            <p className="text-xs text-muted">
              Save drafted chapters, full manuscript, Story Summary, and Canon Ledger back into an isolated vault folder (ignored during inbound sync).
            </p>
          </div>
          <button type="button" onClick={() => pushAct.run()} disabled={pushAct.busy} className="btn-secondary text-xs">
            <UploadCloud className="h-3.5 w-3.5 text-accent" />
            {pushAct.busy ? "Writing…" : "Push to Vault"}
          </button>
        </div>

        <div className="grid gap-3 sm:grid-cols-2 items-end">
          <div className="space-y-1">
            <label htmlFor="wb-sub" className="text-xs font-semibold text-muted">Subfolder inside Vault</label>
            <input
              id="wb-sub"
              value={writeBackDir}
              onChange={(e) => setWriteBackDir(e.target.value)}
              className="input-field font-mono text-xs"
            />
          </div>
          <div className="flex flex-wrap gap-3 text-xs text-ink pb-1">
            <label className="inline-flex items-center gap-1.5 cursor-pointer">
              <input type="checkbox" checked={pushChapters} onChange={(e) => setPushChapters(e.target.checked)} />
              <span>Chapters</span>
            </label>
            <label className="inline-flex items-center gap-1.5 cursor-pointer">
              <input type="checkbox" checked={pushManuscript} onChange={(e) => setPushManuscript(e.target.checked)} />
              <span>Manuscript</span>
            </label>
            <label className="inline-flex items-center gap-1.5 cursor-pointer">
              <input type="checkbox" checked={pushSummary} onChange={(e) => setPushSummary(e.target.checked)} />
              <span>Summary</span>
            </label>
            <label className="inline-flex items-center gap-1.5 cursor-pointer">
              <input type="checkbox" checked={pushCanon} onChange={(e) => setPushCanon(e.target.checked)} />
              <span>Canon</span>
            </label>
            <label className="inline-flex items-center gap-1.5 cursor-pointer">
              <input type="checkbox" checked={pushAcceptedOnly} onChange={(e) => setPushAcceptedOnly(e.target.checked)} />
              <span>Accepted only</span>
            </label>
          </div>
        </div>

        {pushedFiles.length > 0 && (
          <div className="rounded-lg bg-emerald-500/10 px-3 py-2 text-[11px] text-emerald-800 dark:text-emerald-300 font-mono space-y-0.5">
            {pushedFiles.slice(0, 8).map((f) => (
              <div key={f}>✓ {f}</div>
            ))}
            {pushedFiles.length > 8 && <div>…and {pushedFiles.length - 8} more files</div>}
          </div>
        )}
      </div>

      {/* Mapping Rules Editor */}
      <div className="card p-5 space-y-4">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <h3 className="text-sm font-semibold text-ink">Folder & Note Mapping</h3>
            <p className="text-xs text-muted">Adjust which vault folders or individual notes feed each Novelista stage.</p>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <label className="inline-flex items-center gap-1.5 text-xs text-ink mr-2 cursor-pointer">
              <input
                type="checkbox"
                checked={importChapters}
                onChange={(e) => {
                  setImportChapters(e.target.checked);
                  setMappingDirty(true);
                }}
              />
              <span>Import chapter drafts on sync</span>
            </label>
            <button
              type="button"
              onClick={() => saveMappingAct.run({ syncNow: true })}
              disabled={saveMappingAct.busy}
              className="btn-primary text-xs"
            >
              <Save className="h-3.5 w-3.5" />
              {mappingDirty ? "Save Mapping & Re-Sync" : "Re-Sync All"}
            </button>
            <button type="button" onClick={() => unlinkAct.run()} disabled={unlinkAct.busy} className="btn-ghost text-xs text-rose-600 dark:text-rose-400">
              <Unlink className="h-3.5 w-3.5" /> Unlink Vault
            </button>
          </div>
        </div>

        <VaultMappingEditor
          folders={status.folders}
          notes={status.notes}
          folderRules={folderRules}
          fileOverrides={fileOverrides}
          tagRules={status.config?.tag_rules}
          onChangeFolderRules={(next) => {
            setFolderRules(next);
            setMappingDirty(true);
          }}
          onChangeFileOverrides={(next) => {
            setFileOverrides(next);
            setMappingDirty(true);
          }}
          onAutoMapAI={() => aiMapAct.run()}
          aiBusy={aiMapAct.busy}
          aiRationale={aiRationale}
        />
      </div>
    </div>
  );
}