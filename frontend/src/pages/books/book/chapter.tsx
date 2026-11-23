import { useEffect, useRef, useState } from "react";
import { Link, useOutletContext, useParams } from "react-router-dom";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Sparkles,
  Save,
  Check,
  Unlock,
  Scissors,
  Wand2,
  Eye,
  Edit3,
  Maximize2,
  Minimize2,
  Copy,
  ChevronLeft,
  ChevronRight,
  MessageSquare,
  History,
  BookOpen,
  ShieldCheck,
  LayoutList,
  PanelRightClose,
  PanelRightOpen,
} from "lucide-react";
import { api, Book, Finding, FormatProfile } from "@/lib/api";
import { useEditable, useUnsavedGuard } from "@/lib/useEditable";
import { useJobStream } from "@/lib/useJobStream";
import { useAction } from "@/lib/useAction";
import { useToast } from "@/lib/toast";
import { useTheme } from "@/lib/theme";
import { ProseMarkdownView } from "@/lib/markdown";
import { AICheckerPanel } from "@/components/AICheckerPanel";
import { SceneCardsPanel } from "@/components/SceneCardsPanel";
import { DiffModal } from "@/components/DiffModal";
import { GenerationProgress } from "@/components/GenerationProgress";
import { ErrorLine } from "@/components/ErrorLine";

type InspectorTab = "checker" | "scenes" | "editor" | "refs" | "versions";

export default function ChapterPage() {
  const { id, n } = useParams();
  const num = Number(n);
  const qc = useQueryClient();
  const toast = useToast();
  const { proseSize, setProseSize } = useTheme();
  const { book, format } = useOutletContext<{ book: Book; format?: FormatProfile }>();

  const taRef = useRef<HTMLTextAreaElement | null>(null);
  const [viewMode, setViewMode] = useState<"edit" | "read">("edit");
  const [zenMode, setZenMode] = useState(false);
  const [showInspector, setShowInspector] = useState(true);
  const [tab, setTab] = useState<InspectorTab>("checker");
  const [revNotes, setRevNotes] = useState("");
  const [polishAfterRev, setPolishAfterRev] = useState(false);
  const [refTab, setRefTab] = useState<"characters" | "world" | "canon" | "summary" | "outline">("characters");

  // Selection Co-Writer state
  const [selRange, setSelRange] = useState<{ start: number; end: number; text: string } | null>(null);
  const [selCustom, setSelCustom] = useState("");
  const [selCandidate, setSelCandidate] = useState<{ start: number; end: number; original: string; replacement: string } | null>(null);

  // Version Diff Modal state
  const [diffState, setDiffState] = useState<{ ver: number; verText: string } | null>(null);

  const { data: chapter, isLoading, error: loadErr, refetch } = useQuery({
    queryKey: ["chapter", id, num],
    queryFn: () => api.getChapter(id!, num),
    enabled: Boolean(id && Number.isFinite(num)),
  });

  const { data: report, refetch: refetchReport } = useQuery({
    queryKey: ["report", id, num],
    queryFn: () => api.getReport(id!, num),
    enabled: Boolean(id && Number.isFinite(num)),
  });

  const { data: briefData, refetch: refetchBrief } = useQuery({
    queryKey: ["brief", id, num],
    queryFn: () => api.getBrief(id!, num),
    enabled: Boolean(id && Number.isFinite(num)),
  });

  const { data: versionsData, refetch: refetchVersions } = useQuery({
    queryKey: ["versions", id, num],
    queryFn: () => api.listVersions(id!, num),
    enabled: Boolean(id && Number.isFinite(num)),
  });

  const { data: refDoc } = useQuery({
    queryKey: ["ref-peek", id, refTab],
    queryFn: () => (refTab === "outline" ? api.getOutline(id!) : api.getArtifact(id!, refTab)),
    enabled: Boolean(id && showInspector && tab === "refs"),
  });

  const ed = useEditable(chapter?.text, `${id}-${num}`);
  const briefEd = useEditable(briefData?.text, `${id}-${num}-brief`);
  useUnsavedGuard(ed.dirty || briefEd.dirty);

  const stream = useJobStream(async (ev) => {
    if (ev.type === "done") {
      const fresh = await refetch();
      if (fresh.data?.text !== undefined) ed.acceptExternal(fresh.data.text);
      await Promise.all([refetchReport(), refetchBrief(), refetchVersions()]);
      qc.invalidateQueries({ queryKey: ["book", id] });
      qc.invalidateQueries({ queryKey: ["chapters", id] });
      if (ev.type === "done") toast.success("Chapter job finished.");
    }
  });

  useEffect(() => {
    if (id && Number.isFinite(num)) {
      stream.resume({ bookId: id, kind: "chapter_job", chapter: num });
    }
  }, [id, num, stream.resume]);

  useEffect(() => {
    if (stream.busy) {
      setShowInspector(true);
      setTab("editor");
    }
  }, [stream.busy]);

  const saveAct = useAction(async () => {
    const res = await api.putChapter(id!, num, ed.text);
    ed.markSaved(ed.text);
    await Promise.all([refetch(), refetchReport(), refetchVersions()]);
    qc.invalidateQueries({ queryKey: ["book", id] });
    qc.invalidateQueries({ queryKey: ["chapters", id] });
    toast.success(`Saved (${res.word_count.toLocaleString()} words).`);
  });

  const saveBriefAct = useAction(async () => {
    await api.putBrief(id!, num, briefEd.text);
    briefEd.markSaved(briefEd.text);
    toast.success("Revision brief saved.");
  });

  const launchJob = async (starter: () => Promise<{ id: string }>) => {
    if (ed.dirty) {
      await api.putChapter(id!, num, ed.text);
      ed.markSaved(ed.text);
    }
    const job = await starter();
    stream.start(job.id, { bookId: id!, kind: "chapter_job", chapter: num });
  };

  const draftAct = useAction(async () => {
    if (ed.text.trim() && !window.confirm("Redraft this unit? Your current text will be saved in Version History.")) return;
    await launchJob(() => api.draftChapter(id!, num));
  });

  const polishAct = useAction(async () => launchJob(() => api.polishChapter(id!, num)));
  const editorAct = useAction(async () => launchJob(() => api.editorChapter(id!, num)));
  const tightenAct = useAction(async () => launchJob(() => api.tightenChapter(id!, num)));
  const fixTellsAct = useAction(async () => launchJob(() => api.fixTells(id!, num)));
  const reviseAct = useAction(async () => {
    if (briefEd.dirty) {
      await api.putBrief(id!, num, briefEd.text);
      briefEd.markSaved(briefEd.text);
    }
    await launchJob(() => api.reviseChapter(id!, num, { notes: revNotes, polish: polishAfterRev }));
  });

  const acceptAct = useAction(async () => {
    if (ed.dirty) {
      await api.putChapter(id!, num, ed.text);
      ed.markSaved(ed.text);
    }
    if (chapter?.status === "accepted") {
      await api.unacceptChapter(id!, num);
      toast.info("Unit unlocked for editing.");
    } else {
      await api.acceptChapter(id!, num);
      toast.success("Unit accepted as final.");
    }
    await refetch();
    qc.invalidateQueries({ queryKey: ["book", id] });
    qc.invalidateQueries({ queryKey: ["chapters", id] });
  });

  const rescanAct = useAction(async () => {
    if (ed.dirty) {
      await api.putChapter(id!, num, ed.text);
      ed.markSaved(ed.text);
    }
    await api.scanChapter(id!, num);
    await refetchReport();
    toast.info("AI Checker scan refreshed.");
  });

  const fixOneAct = useAction(async (f: Finding) => {
    if (ed.dirty) {
      await api.putChapter(id!, num, ed.text);
      ed.markSaved(ed.text);
    }
    const res = await api.fixFinding(id!, num, {
      finding_id: f.fingerprint,
      start_char: f.start_char,
      end_char: f.end_char,
    });
    ed.acceptExternal(res.text);
    await Promise.all([refetch(), refetchReport(), refetchVersions()]);
    toast.success("Rewrote flagged line cleanly.");
  });

  const ignoreOneAct = useAction(async (f: Finding) => {
    await api.ignoreFinding(id!, num, f.fingerprint);
    await Promise.all([refetchReport(), refetch()]);
    toast.info("Finding ignored.");
  });

  const selAct = useAction(async (action: string, custom: string = "") => {
    if (!selRange) return;
    if (ed.dirty) {
      await api.putChapter(id!, num, ed.text);
      ed.markSaved(ed.text);
    }
    const res = await api.rewriteSelection(id!, num, {
      start_char: selRange.start,
      end_char: selRange.end,
      action,
      instruction: custom,
    });
    setSelCandidate({
      start: res.start_char,
      end: res.end_char,
      original: res.original,
      replacement: res.replacement,
    });
  });

  const restoreVerAct = useAction(async (v: number) => {
    const res = await api.restoreVersion(id!, num, v);
    ed.acceptExternal(res.text);
    setDiffState(null);
    await Promise.all([refetch(), refetchReport(), refetchVersions()]);
    toast.success(`Restored version v${v}.`);
  });

  // Ctrl+S saves focused editor cleanly without colliding
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "s") {
        e.preventDefault();
        if (ed.dirty && !saveAct.busy) saveAct.run();
        else if (briefEd.dirty && !saveBriefAct.busy) saveBriefAct.run();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [ed.dirty, briefEd.dirty, saveAct, saveBriefAct]);

  const jumpToFinding = (f: Finding) => {
    setViewMode("edit");
    window.setTimeout(() => {
      const ta = taRef.current;
      if (!ta) return;
      ta.focus();
      let s0 = f.start_char;
      let s1 = f.end_char;
      if (s1 <= s0) {
        // Fallback line lookup
        const lines = ed.text.split("\n");
        let off = 0;
        for (let i = 0; i < Math.min(lines.length, Math.max(0, f.line - 1)); i++) {
          off += lines[i].length + 1;
        }
        s0 = off;
        s1 = off + (lines[f.line - 1]?.length || 0);
      }
      ta.setSelectionRange(s0, s1);
      // Accurate scroll proportion via line ratio
      const totalLines = Math.max(1, ed.text.split("\n").length);
      const lineRatio = Math.max(0, (f.line - 1) / totalLines);
      ta.scrollTop = Math.max(0, lineRatio * ta.scrollHeight - ta.clientHeight / 3);
    }, 30);
  };

  const handleSelectText = () => {
    const ta = taRef.current;
    if (!ta) return;
    const s0 = ta.selectionStart;
    const s1 = ta.selectionEnd;
    if (s1 - s0 >= 12) {
      setSelRange({ start: s0, end: s1, text: ed.text.slice(s0, s1) });
    } else {
      setSelRange(null);
    }
  };

  if (isLoading) return <div className="py-12 text-center text-sm text-muted">Loading chapter studio…</div>;
  if (loadErr || !chapter) return <ErrorLine error={loadErr instanceof Error ? loadErr.message : "Chapter not found"} />;

  const accepted = chapter.status === "accepted";
  const mutatingBusy = stream.busy || draftAct.busy || polishAct.busy || reviseAct.busy || tightenAct.busy || fixTellsAct.busy;
  const wordCount = ed.text.trim() ? ed.text.trim().split(/\s+/).length : 0;
  const unitCount = book.chapters.length || book.chapter_count || 1;
  const targetPerUnit = book.target_words ? Math.round(book.target_words / unitCount) : null;
  const unitLabel = format?.unit_label || "Chapter";

  return (
    <div className={zenMode ? "fixed inset-0 z-40 overflow-y-auto bg-bg p-6" : "space-y-4"}>
      {/* Header & Navigation */}
      <div className="card p-3 flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-center gap-2 min-w-0">
          {num > 1 && (
            <Link to={`/books/${id}/chapters/${num - 1}`} className="btn-ghost p-1.5" title="Previous unit">
              <ChevronLeft className="h-4 w-4" />
            </Link>
          )}
          <div>
            <div className="flex items-center gap-2">
              <h2 className="font-prose text-lg font-bold text-ink truncate">
                {unitLabel} {chapter.number}: {chapter.title}
              </h2>
              <span className="rounded bg-surface-2 px-2 py-0.5 text-[11px] font-medium capitalize text-muted">
                {chapter.status} · v{chapter.version}
              </span>
              {ed.dirty && (
                <span className="rounded bg-amber-500/15 px-2 py-0.5 text-[11px] font-medium text-amber-700 dark:text-amber-300">
                  Unsaved
                </span>
              )}
            </div>
            <p className="text-xs text-muted">
              {wordCount.toLocaleString()} words{targetPerUnit ? ` (target ~${targetPerUnit.toLocaleString()})` : ""}
              {report?.cleanliness_grade ? ` · Cleanliness Grade ${report.cleanliness_grade}` : ""}
            </p>
          </div>
          {num < book.chapters.length && (
            <Link to={`/books/${id}/chapters/${num + 1}`} className="btn-ghost p-1.5" title="Next unit">
              <ChevronRight className="h-4 w-4" />
            </Link>
          )}
        </div>

        {/* Action Toolbar */}
        <div className="flex flex-wrap items-center gap-1.5">
          <button
            type="button"
            disabled={accepted || mutatingBusy}
            onClick={() => draftAct.run()}
            className="btn-primary text-xs py-1.5"
            title="Draft or redraft this chapter using outline, scene cards, and voice guide"
          >
            <Sparkles className="h-3.5 w-3.5" /> {ed.text.trim() ? "Redraft" : "Draft"}
          </button>
          <button
            type="button"
            disabled={accepted || mutatingBusy || !ed.text.trim()}
            onClick={() => polishAct.run()}
            className="btn-secondary text-xs py-1.5"
            title="Run Acquiring Editor + Revision + AI-tic fixer"
          >
            <Wand2 className="h-3.5 w-3.5 text-accent" /> Polish
          </button>
          <button
            type="button"
            disabled={accepted || mutatingBusy || !ed.text.trim()}
            onClick={() => tightenAct.run()}
            className="btn-ghost text-xs"
            title="Cut ~10% deadweight and over-explanation"
          >
            <Scissors className="h-3.5 w-3.5" /> Tighten
          </button>
          <button
            type="button"
            disabled={accepted || mutatingBusy || !ed.text.trim()}
            onClick={() => fixTellsAct.run()}
            className="btn-ghost text-xs"
            title="Rewrite flagged AI tics sentence by sentence"
          >
            Fix Tells
          </button>

          <div className="h-5 w-px bg-border mx-0.5" />

          <button
            type="button"
            disabled={mutatingBusy || !ed.text.trim()}
            onClick={() => acceptAct.run()}
            className={accepted ? "btn-secondary text-xs py-1.5 text-amber-600" : "btn-secondary text-xs py-1.5"}
          >
            {accepted ? <Unlock className="h-3.5 w-3.5" /> : <Check className="h-3.5 w-3.5 text-emerald-500" />}
            {accepted ? "Un-accept" : "Accept"}
          </button>
          <button
            type="button"
            disabled={!ed.dirty || saveAct.busy}
            onClick={() => saveAct.run()}
            className="btn-primary text-xs py-1.5"
          >
            <Save className="h-3.5 w-3.5" /> Save
          </button>

          <div className="h-5 w-px bg-border mx-0.5" />

          <div className="inline-flex rounded-lg border border-border bg-surface-2 p-0.5">
            <button
              type="button"
              onClick={() => setViewMode("edit")}
              className={`rounded px-2 py-1 text-xs ${viewMode === "edit" ? "bg-surface text-ink font-medium" : "text-muted"}`}
            >
              <Edit3 className="h-3.5 w-3.5" />
            </button>
            <button
              type="button"
              onClick={() => setViewMode("read")}
              className={`rounded px-2 py-1 text-xs ${viewMode === "read" ? "bg-surface text-ink font-medium" : "text-muted"}`}
            >
              <Eye className="h-3.5 w-3.5" />
            </button>
          </div>
          <button
            type="button"
            onClick={() => {
              navigator.clipboard?.writeText(ed.text);
              toast.info("Chapter text copied.");
            }}
            className="btn-ghost p-1.5"
            title="Copy prose"
          >
            <Copy className="h-3.5 w-3.5" />
          </button>
          <button type="button" onClick={() => setZenMode((z) => !z)} className="btn-ghost p-1.5" title="Zen Focus mode">
            {zenMode ? <Minimize2 className="h-3.5 w-3.5" /> : <Maximize2 className="h-3.5 w-3.5" />}
          </button>
          <button
            type="button"
            onClick={() => setShowInspector((s) => !s)}
            className="btn-ghost p-1.5"
            title="Toggle Inspector"
          >
            {showInspector ? <PanelRightClose className="h-4 w-4" /> : <PanelRightOpen className="h-4 w-4" />}
          </button>
        </div>
      </div>

      <ErrorLine
        error={
          saveAct.error ||
          draftAct.error ||
          polishAct.error ||
          editorAct.error ||
          tightenAct.error ||
          fixTellsAct.error ||
          reviseAct.error ||
          fixOneAct.error ||
          selAct.error
        }
      />

      <GenerationProgress state={stream.state} onCancel={stream.cancel} />

      {/* Selection-Level Co-Writer Floating Bar */}
      {selRange && !accepted && viewMode === "edit" && (
        <div className="card p-2.5 flex flex-wrap items-center gap-2 border-accent/40 bg-accent-soft/40">
          <span className="text-xs font-semibold text-accent">
            Co-Writer ({selRange.text.split(/\s+/).length} words selected):
          </span>
          {[
            ["rewrite", "Rewrite"],
            ["show_dont_tell", "Show Don't Tell"],
            ["punch_dialogue", "Punch Dialogue"],
            ["expand_sensory", "Sensory"],
            ["tighten", "Tighten"],
          ].map(([k, lbl]) => (
            <button
              key={k}
              type="button"
              disabled={selAct.busy}
              onClick={() => selAct.run(k)}
              className="btn-secondary py-1 px-2 text-xs"
            >
              {lbl}
            </button>
          ))}
          <form
            className="flex items-center gap-1.5 ml-auto"
            onSubmit={(e) => {
              e.preventDefault();
              if (selCustom.trim()) selAct.run("custom", selCustom.trim());
            }}
          >
            <input
              value={selCustom}
              onChange={(e) => setSelCustom(e.target.value)}
              placeholder="Custom edit instruction…"
              className="input-field py-1 px-2 text-xs w-48"
            />
            <button type="submit" disabled={selAct.busy || !selCustom.trim()} className="btn-primary py-1 px-2 text-xs">
              {selAct.busy ? "…" : "Go"}
            </button>
          </form>
        </div>
      )}

      {/* Selection Co-Writer Diff Preview */}
      {selCandidate && (
        <div className="card p-4 space-y-3 border-accent">
          <div className="flex items-center justify-between">
            <span className="text-xs font-semibold text-accent">Suggested Passage Rewrite</span>
            <div className="flex gap-2">
              <button type="button" onClick={() => setSelCandidate(null)} className="btn-secondary text-xs py-1">
                Discard
              </button>
              <button
                type="button"
                onClick={async () => {
                  const next =
                    ed.text.slice(0, selCandidate.start) +
                    selCandidate.replacement +
                    ed.text.slice(selCandidate.end);
                  ed.setLocal(next);
                  setSelCandidate(null);
                  setSelRange(null);
                  toast.success("Replacement applied — click Save to persist.");
                }}
                className="btn-primary text-xs py-1"
              >
                Apply Replacement
              </button>
            </div>
          </div>
          <div className="grid gap-3 md:grid-cols-2 text-xs font-prose leading-relaxed">
            <div className="rounded-lg bg-rose-500/10 p-3">
              <div className="font-sans font-semibold text-rose-600 mb-1">Original</div>
              {selCandidate.original}
            </div>
            <div className="rounded-lg bg-emerald-500/10 p-3">
              <div className="font-sans font-semibold text-emerald-700 dark:text-emerald-300 mb-1">Rewritten</div>
              {selCandidate.replacement}
            </div>
          </div>
        </div>
      )}

      {/* Main Split Stage */}
      <div className={`grid gap-5 ${showInspector && !zenMode ? "lg:grid-cols-12" : "grid-cols-1"}`}>
        <div className={showInspector && !zenMode ? "lg:col-span-7 xl:col-span-8" : "max-w-4xl mx-auto w-full"}>
          <div className="flex items-center justify-between mb-1.5 text-xs text-muted">
            <span>{accepted ? "Accepted (click Un-accept to edit)" : "Select any passage for targeted Co-Writer edits"}</span>
            <div className="flex items-center gap-1.5">
              <span>Font:</span>
              <button type="button" onClick={() => setProseSize(proseSize - 1)} className="btn-ghost px-1.5 py-0.5 text-xs">
                A-
              </button>
              <span>{proseSize}px</span>
              <button type="button" onClick={() => setProseSize(proseSize + 1)} className="btn-ghost px-1.5 py-0.5 text-xs">
                A+
              </button>
            </div>
          </div>

          {viewMode === "edit" ? (
            <textarea
              ref={taRef}
              aria-label="Chapter prose"
              readOnly={accepted}
              value={ed.text}
              onChange={(e) => ed.setLocal(e.target.value)}
              onMouseUp={handleSelectText}
              onKeyUp={handleSelectText}
              style={{ fontSize: `${proseSize}px` }}
              placeholder={`Click Draft above or start writing ${unitLabel} ${chapter.number}…`}
              className="prose-editor min-h-[620px]"
            />
          ) : (
            <div className="card p-8 min-h-[620px]">
              <ProseMarkdownView text={ed.text} proseMode fontSize={proseSize} />
            </div>
          )}
        </div>

        {/* Right 5-Tab Studio Inspector */}
        {showInspector && !zenMode && (
          <div className="lg:col-span-5 xl:col-span-4 space-y-3">
            <div className="card p-3 space-y-3">
              <div className="grid grid-cols-5 gap-1 rounded-lg bg-surface-2 p-1 text-[11px]">
                {[
                  ["checker", "AI Check", ShieldCheck],
                  ["scenes", "Scenes", LayoutList],
                  ["editor", "Revise", MessageSquare],
                  ["refs", "Refs", BookOpen],
                  ["versions", "History", History],
                ].map(([k, lbl, Icon]) => {
                  const Ic = Icon as typeof ShieldCheck;
                  const active = tab === k;
                  return (
                    <button
                      key={String(k)}
                      type="button"
                      onClick={() => setTab(k as InspectorTab)}
                      className={`rounded-md py-1.5 px-1 flex flex-col items-center gap-0.5 font-medium transition ${
                        active ? "bg-surface text-accent shadow-sm" : "text-muted hover:text-ink"
                      }`}
                    >
                      <Ic className="h-3.5 w-3.5" />
                      <span>{String(lbl)}</span>
                    </button>
                  );
                })}
              </div>

              {tab === "checker" && (
                <AICheckerPanel
                  report={report}
                  dirty={ed.dirty}
                  busy={rescanAct.busy || fixOneAct.busy || mutatingBusy}
                  onJump={jumpToFinding}
                  onRescan={() => rescanAct.run()}
                  onFixOne={async (f) => {
                    await fixOneAct.run(f);
                  }}
                  onIgnore={async (f) => {
                    await ignoreOneAct.run(f);
                  }}
                />
              )}

              {tab === "scenes" && (
                <SceneCardsPanel bookId={id!} chapterNum={num} format={format} />
              )}

              {tab === "editor" && (
                <div className="space-y-3 text-xs">
                  <div className="flex items-center justify-between">
                    <span className="font-semibold text-ink">Editor Notes & Guided Revision</span>
                    <button
                      type="button"
                      disabled={mutatingBusy || !ed.text.trim()}
                      onClick={() => launchJob(() => api.editorChapter(id!, num))}
                      className="btn-secondary py-1 px-2 text-xs"
                    >
                      Get Editor Notes
                    </button>
                  </div>

                  {stream.state.text && (
                    <div className="rounded-lg border border-border bg-surface-2 p-2.5 max-h-40 overflow-y-auto">
                      <div className="font-semibold text-accent mb-1">Live Output</div>
                      <pre className="whitespace-pre-wrap font-prose text-xs">{stream.state.text.slice(-2000)}</pre>
                    </div>
                  )}

                  <div className="space-y-1">
                    <div className="flex items-center justify-between">
                      <label className="font-semibold text-muted">Editorial Brief</label>
                      {briefEd.dirty && (
                        <button type="button" onClick={() => saveBriefAct.run()} className="text-accent hover:underline">
                          Save brief
                        </button>
                      )}
                    </div>
                    <textarea
                      rows={6}
                      value={briefEd.text}
                      onChange={(e) => briefEd.setLocal(e.target.value)}
                      placeholder="Click 'Get Editor Notes' or write your own revision brief…"
                      className="input-field font-mono text-xs"
                    />
                  </div>

                  <div className="space-y-1.5 border-t border-border pt-2.5">
                    <label className="font-semibold text-muted">Direct Revision Instructions</label>
                    <textarea
                      rows={3}
                      value={revNotes}
                      onChange={(e) => setRevNotes(e.target.value)}
                      placeholder="e.g., Make Clara sharper in dialogue; cut the second recap paragraph..."
                      className="input-field text-xs"
                    />
                    <div className="flex items-center justify-between">
                      <label className="flex items-center gap-1.5 cursor-pointer">
                        <input
                          type="checkbox"
                          checked={polishAfterRev}
                          onChange={(e) => setPolishAfterRev(e.target.checked)}
                          className="accent-accent"
                        />
                        <span>Polish after revise</span>
                      </label>
                      <button
                        type="button"
                        disabled={accepted || mutatingBusy || !ed.text.trim()}
                        onClick={() => reviseAct.run()}
                        className="btn-primary py-1.5 px-3 text-xs"
                      >
                        Revise Chapter
                      </button>
                    </div>
                  </div>
                </div>
              )}

              {tab === "refs" && (
                <div className="space-y-2.5 text-xs">
                  <div className="flex flex-wrap gap-1">
                    {(["characters", "world", "canon", "summary", "outline"] as const).map((rt) => (
                      <button
                        key={rt}
                        type="button"
                        onClick={() => setRefTab(rt)}
                        className={`rounded px-2 py-1 capitalize ${
                          refTab === rt ? "bg-accent text-accent-fg font-medium" : "bg-surface-2 text-muted hover:text-ink"
                        }`}
                      >
                        {rt}
                      </button>
                    ))}
                  </div>
                  <div className="max-h-[450px] overflow-y-auto rounded-lg border border-border bg-bg/50 p-3">
                    <ProseMarkdownView text={refDoc?.text || ""} />
                  </div>
                </div>
              )}

              {tab === "versions" && (
                <div className="space-y-2 text-xs">
                  <div className="font-semibold text-ink">
                    Saved Snapshots (Current: v{versionsData?.current_version ?? chapter.version})
                  </div>
                  {(versionsData?.versions || []).length === 0 ? (
                    <p className="text-muted py-2">No historical snapshots yet. Redrafts, revisions, and fixes snapshot automatically.</p>
                  ) : (
                    <div className="space-y-1.5 max-h-[420px] overflow-y-auto">
                      {[...(versionsData?.versions || [])].reverse().map((v) => (
                        <div key={v} className="flex items-center justify-between rounded-lg border border-border bg-bg/50 px-3 py-2">
                          <span className="font-mono font-medium text-ink">Version v{v}</span>
                          <button
                            type="button"
                            onClick={async () => {
                              const vd = await api.getVersion(id!, num, v);
                              setDiffState({ ver: v, verText: vd.text });
                            }}
                            className="btn-secondary py-1 px-2 text-xs"
                          >
                            Diff & Restore
                          </button>
                        </div>
                      ))}
                    </div>
                  )}
                </div>
              )}
            </div>
          </div>
        )}
      </div>

      <DiffModal
        open={Boolean(diffState)}
        title={`Compare v${diffState?.ver ?? ""} vs Current Draft`}
        leftLabel={`v${diffState?.ver ?? ""}`}
        rightLabel={`Current (v${chapter.version})`}
        leftText={diffState?.verText || ""}
        rightText={ed.text}
        onRestore={diffState && !accepted ? () => restoreVerAct.run(diffState.ver) : undefined}
        restoreBusy={restoreVerAct.busy}
        onClose={() => setDiffState(null)}
      />
    </div>
  );
}
