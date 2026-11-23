import { useMemo, useState } from "react";
import { Folder, FileText, Sparkles, RotateCcw, Search, AlertTriangle } from "lucide-react";
import { ObsidianFolderSummary, ObsidianNoteInfo, ObsidianRole } from "@/lib/api";

export const OBSIDIAN_ROLE_OPTIONS: Array<{ value: ObsidianRole; label: string; desc: string }> = [
  { value: "ignore", label: "Ignore", desc: "Skip this folder or note" },
  { value: "seed", label: "Seed / Premise", desc: "Core story pitch -> seed.txt" },
  { value: "world", label: "World & Lore", desc: "Settings, factions, magic -> world.md" },
  { value: "characters", label: "Characters / Cast", desc: "Profiles & relationships -> characters.md" },
  { value: "voice", label: "Voice & Style", desc: "Tone & syntax guide -> voice.md" },
  { value: "canon", label: "Canon & Timeline", desc: "Hard Day-1 facts -> canon.md" },
  { value: "mystery", label: "Secrets / Twists", desc: "Author-only hidden lore -> mystery.md" },
  { value: "outline", label: "Outline / Beats", desc: "Chapter plan -> outline.md" },
  { value: "chapter", label: "Chapter Drafts", desc: "Existing prose -> chapters/" },
  { value: "reference", label: "Reference Notes", desc: "Background inspiration -> ref context" },
];

export function resolveClientNoteRole(
  note: ObsidianNoteInfo,
  folderRules: Record<string, string>,
  fileOverrides: Record<string, string>,
  tagRules: Record<string, string> = {},
): ObsidianRole {
  if (note.rel_path in fileOverrides) {
    return fileOverrides[note.rel_path] as ObsidianRole;
  }
  const nf = (note.folder || "").replace(/^\/+|\/+$/g, "");
  let bestLen = -1;
  let bestRole: ObsidianRole | null = null;
  for (const [rawFolder, rawRole] of Object.entries(folderRules)) {
    if (!rawRole || rawRole === "ignore") continue;
    const rf = rawFolder.replace(/^\/+|\/+$/g, "");
    if (rf === "") {
      if (nf === "" && bestLen < 0) {
        bestLen = 0;
        bestRole = rawRole as ObsidianRole;
      }
    } else if (nf === rf || nf.startsWith(rf + "/")) {
      const score = rf.length + 1;
      if (score > bestLen) {
        bestLen = score;
        bestRole = rawRole as ObsidianRole;
      }
    }
  }
  if (bestRole) return bestRole;
  for (const t of note.tags || []) {
    const clean = t.replace(/^#/, "").toLowerCase();
    if (tagRules[clean]) return tagRules[clean] as ObsidianRole;
  }
  const fmRole = typeof note.frontmatter?.novelista_role === "string" ? note.frontmatter.novelista_role.toLowerCase() : "";
  if (fmRole && OBSIDIAN_ROLE_OPTIONS.some((o) => o.value === fmRole)) {
    return fmRole as ObsidianRole;
  }
  return "ignore";
}

export function VaultMappingEditor({
  folders,
  notes,
  folderRules,
  fileOverrides,
  tagRules,
  onChangeFolderRules,
  onChangeFileOverrides,
  onAutoMapAI,
  onResetHeuristics,
  aiBusy,
  aiRationale,
}: {
  folders: ObsidianFolderSummary[];
  notes: ObsidianNoteInfo[];
  folderRules: Record<string, string>;
  fileOverrides: Record<string, string>;
  tagRules?: Record<string, string>;
  onChangeFolderRules: (next: Record<string, string>) => void;
  onChangeFileOverrides: (next: Record<string, string>) => void;
  onAutoMapAI?: () => void;
  onResetHeuristics?: () => void;
  aiBusy?: boolean;
  aiRationale?: string | null;
}) {
  const [tab, setTab] = useState<"folders" | "notes">("folders");
  const [search, setSearch] = useState("");
  const [filterRole, setFilterRole] = useState<string>("all");

  const resolvedNotes = useMemo(
    () =>
      notes.map((n) => ({
        note: n,
        role: resolveClientNoteRole(n, folderRules, fileOverrides, tagRules || {}),
        isOverridden: n.rel_path in fileOverrides,
      })),
    [notes, folderRules, fileOverrides, tagRules],
  );

  const summaryByRole = useMemo(() => {
    const counts: Record<string, { count: number; chars: number }> = {};
    for (const r of OBSIDIAN_ROLE_OPTIONS) counts[r.value] = { count: 0, chars: 0 };
    for (const item of resolvedNotes) {
      counts[item.role].count += 1;
      counts[item.role].chars += item.note.char_count;
    }
    return counts;
  }, [resolvedNotes]);

  const filteredNotes = useMemo(() => {
    const q = search.trim().toLowerCase();
    return resolvedNotes.filter(({ note, role }) => {
      if (filterRole !== "all" && role !== filterRole) return false;
      if (!q) return true;
      return (
        note.rel_path.toLowerCase().includes(q) ||
        note.title.toLowerCase().includes(q) ||
        note.tags.some((t) => t.toLowerCase().includes(q))
      );
    });
  }, [resolvedNotes, search, filterRole]);

  const setFolderRole = (folderPath: string, role: ObsidianRole) => {
    const next = { ...folderRules };
    if (role === "ignore") delete next[folderPath];
    else next[folderPath] = role;
    onChangeFolderRules(next);
  };

  const setFileRole = (note: ObsidianNoteInfo, role: ObsidianRole) => {
    const inherited = resolveClientNoteRole(note, folderRules, {}, tagRules || {});
    const next = { ...fileOverrides };
    if (role === inherited) {
      delete next[note.rel_path];
    } else {
      next[note.rel_path] = role;
    }
    onChangeFileOverrides(next);
  };

  const worldChars = summaryByRole.world?.chars || 0;
  const castChars = summaryByRole.characters?.chars || 0;

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="inline-flex rounded-lg border border-border bg-surface p-0.5">
          <button
            type="button"
            onClick={() => setTab("folders")}
            className={`rounded-md px-3 py-1.5 text-xs font-medium inline-flex items-center gap-1.5 ${
              tab === "folders" ? "bg-surface-2 text-ink shadow-sm" : "text-muted hover:text-ink"
            }`}
          >
            <Folder className="h-3.5 w-3.5" /> By Folder ({folders.length})
          </button>
          <button
            type="button"
            onClick={() => setTab("notes")}
            className={`rounded-md px-3 py-1.5 text-xs font-medium inline-flex items-center gap-1.5 ${
              tab === "notes" ? "bg-surface-2 text-ink shadow-sm" : "text-muted hover:text-ink"
            }`}
          >
            <FileText className="h-3.5 w-3.5" /> All Notes ({notes.length})
          </button>
        </div>

        <div className="flex flex-wrap items-center gap-2">
          {onResetHeuristics && (
            <button type="button" onClick={onResetHeuristics} className="btn-secondary text-xs py-1.5">
              <RotateCcw className="h-3.5 w-3.5" /> Reset Auto-Detect
            </button>
          )}
          {onAutoMapAI && (
            <button type="button" onClick={onAutoMapAI} disabled={aiBusy} className="btn-secondary text-xs py-1.5">
              <Sparkles className="h-3.5 w-3.5 text-accent" /> {aiBusy ? "Classifying Vault…" : "Auto-Map with AI"}
            </button>
          )}
        </div>
      </div>

      {/* Summary pills */}
      <div className="flex flex-wrap gap-1.5">
        {OBSIDIAN_ROLE_OPTIONS.filter((r) => r.value !== "ignore" && summaryByRole[r.value]?.count > 0).map((r) => (
          <button
            key={r.value}
            type="button"
            onClick={() => {
              setFilterRole(r.value);
              setTab("notes");
            }}
            className="inline-flex items-center gap-1.5 rounded-full border border-border bg-surface-2 px-2.5 py-1 text-[11px] font-medium text-ink hover:border-accent/60"
          >
            <span className="text-accent font-semibold">{r.label}:</span>
            <span>{summaryByRole[r.value].count}</span>
            <span className="text-muted">({(summaryByRole[r.value].chars / 1000).toFixed(1)}k ch)</span>
          </button>
        ))}
        {summaryByRole.ignore?.count > 0 && (
          <span className="inline-flex items-center rounded-full bg-surface-2/60 px-2.5 py-1 text-[11px] text-muted">
            Ignored: {summaryByRole.ignore.count}
          </span>
        )}
      </div>

      {aiRationale && (
        <div className="rounded-lg border border-accent/30 bg-accent-soft/60 px-3 py-2 text-xs text-ink">
          <span className="font-semibold text-accent">AI Mapping Suggestion:</span> {aiRationale}
        </div>
      )}

      {(worldChars > 5000 || castChars > 6000) && (
        <div className="flex items-start gap-2 rounded-lg border border-amber-500/30 bg-amber-500/10 px-3 py-2 text-xs text-amber-800 dark:text-amber-300">
          <AlertTriangle className="h-4 w-4 shrink-0 mt-0.5" />
          <span>
            {worldChars > 5000 ? `World notes are ${(worldChars / 1000).toFixed(1)}k chars (draft prompts trim World at 5k). ` : ""}
            {castChars > 6000 ? `Cast notes are ${(castChars / 1000).toFixed(1)}k chars (draft prompts trim Cast at 6k). ` : ""}
            Move secondary lore to <strong>Reference Notes</strong> if you want tighter prompts.
          </span>
        </div>
      )}

      {tab === "folders" ? (
        <div className="divide-y divide-border rounded-xl border border-border bg-surface overflow-hidden">
          {folders.length === 0 ? (
            <div className="p-6 text-center text-xs text-muted">No folders with Markdown notes found.</div>
          ) : (
            folders.map((f) => {
              const current = (folderRules[f.path] || "ignore") as ObsidianRole;
              return (
                <div key={f.path || "__root__"} className="flex flex-wrap items-center justify-between gap-3 px-4 py-2.5">
                  <div className="min-w-0">
                    <div className="flex items-center gap-2">
                      <Folder className="h-4 w-4 text-accent shrink-0" />
                      <span className="text-sm font-medium text-ink truncate">{f.label}</span>
                    </div>
                    <div className="text-[11px] text-muted pl-6">
                      {f.note_count} note{f.note_count === 1 ? "" : "s"} · {f.total_words.toLocaleString()} words
                      {f.path ? " · New notes added here auto-sync to this role" : " · Root notes mapped individually"}
                    </div>
                  </div>

                  {f.path === "" ? (
                    <button
                      type="button"
                      onClick={() => {
                        setFilterRole("all");
                        setTab("notes");
                      }}
                      className="btn-secondary text-xs py-1"
                    >
                      Map Root Notes Individually
                    </button>
                  ) : (
                    <select
                      aria-label={`Role for folder ${f.label}`}
                      value={current}
                      onChange={(e) => setFolderRole(f.path, e.target.value as ObsidianRole)}
                      className="input-field w-48 text-xs py-1.5"
                    >
                      {OBSIDIAN_ROLE_OPTIONS.map((opt) => (
                        <option key={opt.value} value={opt.value}>
                          {opt.label}
                        </option>
                      ))}
                    </select>
                  )}
                </div>
              );
            })
          )}
        </div>
      ) : (
        <div className="space-y-3">
          <div className="flex flex-wrap items-center gap-2">
            <div className="relative flex-1 min-w-[200px]">
              <Search className="h-3.5 w-3.5 text-muted absolute left-3 top-1/2 -translate-y-1/2" />
              <input
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                placeholder="Filter notes by filename, path, or #tag..."
                className="input-field pl-8 text-xs"
              />
            </div>
            <select
              aria-label="Filter by assigned role"
              value={filterRole}
              onChange={(e) => setFilterRole(e.target.value)}
              className="input-field w-44 text-xs"
            >
              <option value="all">All roles ({notes.length})</option>
              {OBSIDIAN_ROLE_OPTIONS.map((opt) => (
                <option key={opt.value} value={opt.value}>
                  {opt.label} ({summaryByRole[opt.value]?.count || 0})
                </option>
              ))}
            </select>
          </div>

          <div className="divide-y divide-border rounded-xl border border-border bg-surface max-h-[420px] overflow-y-auto">
            {filteredNotes.length === 0 ? (
              <div className="p-6 text-center text-xs text-muted">No notes match this filter.</div>
            ) : (
              filteredNotes.map(({ note, role, isOverridden }) => (
                <div key={note.rel_path} className="flex flex-wrap items-start justify-between gap-3 px-4 py-2.5">
                  <div className="min-w-0 flex-1 space-y-1">
                    <div className="flex flex-wrap items-center gap-1.5">
                      <span className="text-xs font-semibold text-ink">{note.title}</span>
                      <span className="font-mono text-[11px] text-muted">{note.rel_path}</span>
                      {isOverridden && (
                        <span className="rounded bg-accent-soft px-1.5 py-0.5 text-[10px] font-medium text-accent">
                          file rule
                        </span>
                      )}
                      {note.change && note.change !== "unchanged" && (
                        <span className="rounded bg-amber-500/15 px-1.5 py-0.5 text-[10px] font-semibold text-amber-700 dark:text-amber-300">
                          {note.change}
                        </span>
                      )}
                    </div>
                    {note.excerpt && <p className="text-[11px] text-muted line-clamp-1">{note.excerpt}</p>}
                    <div className="flex flex-wrap items-center gap-2 text-[10px] text-muted">
                      <span>{note.word_count.toLocaleString()} words</span>
                      {note.tags.slice(0, 5).map((t) => (
                        <span key={t} className="rounded bg-surface-2 px-1.5 py-0.5">
                          #{t}
                        </span>
                      ))}
                    </div>
                  </div>

                  <select
                    aria-label={`Role for ${note.rel_path}`}
                    value={role}
                    onChange={(e) => setFileRole(note, e.target.value as ObsidianRole)}
                    className={`input-field w-44 text-xs py-1.5 shrink-0 ${
                      role === "ignore" ? "text-muted" : "border-accent/40 font-medium"
                    }`}
                  >
                    {OBSIDIAN_ROLE_OPTIONS.map((opt) => (
                      <option key={opt.value} value={opt.value}>
                        {opt.label}
                      </option>
                    ))}
                  </select>
                </div>
              ))
            )}
          </div>
        </div>
      )}
    </div>
  );
}