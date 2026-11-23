import { useEffect, useState } from "react";
import { Link, useNavigate, useOutletContext, useParams } from "react-router-dom";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Sparkles, Save, Eye, Edit3, ChevronRight } from "lucide-react";
import { api, Book, FormatProfile } from "@/lib/api";
import { useEditable, useUnsavedGuard } from "@/lib/useEditable";
import { useJobStream } from "@/lib/useJobStream";
import { useAction } from "@/lib/useAction";
import { useToast } from "@/lib/toast";
import { ProseMarkdownView } from "@/lib/markdown";
import { GenerationProgress } from "@/components/GenerationProgress";
import { ErrorLine } from "@/components/ErrorLine";

export default function OutlinePage() {
  const { id } = useParams();
  const nav = useNavigate();
  const qc = useQueryClient();
  const toast = useToast();
  const { format } = useOutletContext<{ book: Book; format?: FormatProfile }>();
  const [viewMode, setViewMode] = useState<"edit" | "read">("edit");

  const { data, isLoading, error: loadErr, refetch } = useQuery({
    queryKey: ["outline", id],
    queryFn: () => api.getOutline(id!),
    enabled: Boolean(id),
  });

  const ed = useEditable(data?.text, `${id}-outline`);
  useUnsavedGuard(ed.dirty);

  const stream = useJobStream(async (ev) => {
    if (ev.type === "done") {
      const fresh = await refetch();
      if (fresh.data?.text !== undefined) ed.acceptExternal(fresh.data.text);
      qc.invalidateQueries({ queryKey: ["book", id] });
      toast.success("Outline generated and chapters synced.");
    }
  });

  useEffect(() => {
    if (id) stream.resume({ bookId: id, kind: "outline_generate" });
  }, [id, stream.resume]);

  const saveAct = useAction(async () => {
    const res = await api.putOutline(id!, ed.text);
    ed.markSaved(ed.text);
    await refetch();
    qc.invalidateQueries({ queryKey: ["book", id] });
    toast.success(`Outline saved — ${res.chapter_count} ${format?.unit_label_plural.toLowerCase() || "chapters"} synced.`);
  });

  const genAct = useAction(async () => {
    if (ed.text.trim() && !window.confirm("Regenerate outline? Existing text is backed up cleanly.")) return;
    if (ed.dirty) {
      await api.putOutline(id!, ed.text);
      ed.markSaved(ed.text);
    }
    const job = await api.genOutline(id!);
    stream.start(job.id, { bookId: id!, kind: "outline_generate" });
  });

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "s") {
        e.preventDefault();
        if (ed.dirty && !saveAct.busy) saveAct.run();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [ed.dirty, saveAct]);

  if (isLoading) return <p className="text-sm text-muted py-6">Loading outline…</p>;
  if (loadErr) return <ErrorLine error={loadErr instanceof Error ? loadErr.message : String(loadErr)} />;

  const chapters = data?.chapters || [];

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h2 className="text-lg font-semibold text-ink">
            {format?.key === "picture_book" ? "Spread-by-Spread Storyboard" : "Book Outline & Foreshadowing Ledger"}
          </h2>
          <p className="text-xs text-muted">
            Use <code className="text-[11px]">### Ch N: Title</code> (or <code className="text-[11px]">### Spread N: Title</code>) headings — saving syncs your units automatically.
          </p>
        </div>

        <div className="flex flex-wrap items-center gap-2">
          {ed.dirty && <span className="rounded bg-amber-500/15 px-2 py-0.5 text-xs font-medium text-amber-700 dark:text-amber-300">Unsaved</span>}
          <div className="inline-flex rounded-lg border border-border bg-surface p-0.5">
            <button
              type="button"
              onClick={() => setViewMode("edit")}
              className={`rounded-md px-2.5 py-1 text-xs font-medium inline-flex items-center gap-1 ${viewMode === "edit" ? "bg-surface-2 text-ink" : "text-muted"}`}
            >
              <Edit3 className="h-3.5 w-3.5" /> Edit
            </button>
            <button
              type="button"
              onClick={() => setViewMode("read")}
              className={`rounded-md px-2.5 py-1 text-xs font-medium inline-flex items-center gap-1 ${viewMode === "read" ? "bg-surface-2 text-ink" : "text-muted"}`}
            >
              <Eye className="h-3.5 w-3.5" /> Preview
            </button>
          </div>
          <button type="button" onClick={() => genAct.run()} disabled={genAct.busy || stream.busy} className="btn-secondary text-xs">
            <Sparkles className="h-3.5 w-3.5 text-accent" /> {ed.text.trim() ? "Regenerate Outline" : "Generate Outline"}
          </button>
          <button type="button" onClick={() => saveAct.run()} disabled={!ed.dirty || saveAct.busy} className="btn-primary text-xs">
            <Save className="h-3.5 w-3.5" /> Save & Sync
          </button>
        </div>
      </div>

      <ErrorLine error={saveAct.error || genAct.error} />
      <GenerationProgress state={stream.state} onCancel={stream.cancel} />

      <div className="grid gap-4 lg:grid-cols-3">
        <div className="lg:col-span-2">
          {viewMode === "edit" ? (
            <textarea
              aria-label="Outline"
              rows={24}
              value={ed.text}
              onChange={(e) => ed.setLocal(e.target.value)}
              placeholder="### Ch 1: Title&#10;- POV: ...&#10;- Beat 1..."
              className="prose-editor min-h-[540px] font-mono text-sm leading-relaxed"
            />
          ) : (
            <div className="card p-6 min-h-[540px]">
              <ProseMarkdownView text={ed.text} />
            </div>
          )}
        </div>

        <div className="card p-4 space-y-3 max-h-[560px] flex flex-col">
          <div className="flex items-center justify-between border-b border-border pb-2">
            <h3 className="text-xs font-semibold uppercase tracking-wider text-muted">
              Detected {format?.unit_label_plural || "Chapters"} ({chapters.length})
            </h3>
            {chapters.length > 0 && (
              <Link to={`/books/${id}/chapters`} className="text-xs text-accent hover:underline font-medium">
                Open workspace →
              </Link>
            )}
          </div>

          {stream.busy ? (
            <pre className="flex-1 overflow-y-auto whitespace-pre-wrap text-xs text-muted font-mono">
              {stream.state.text || "Planning outline…"}
            </pre>
          ) : chapters.length === 0 ? (
            <p className="text-xs text-muted">No chapters synced yet. Generate or write an outline, then Save & Sync.</p>
          ) : (
            <div className="flex-1 overflow-y-auto space-y-1.5 pr-1">
              {chapters.map((c) => (
                <Link
                  key={c.number}
                  to={`/books/${id}/chapters/${c.number}`}
                  className="flex items-center justify-between rounded-lg border border-border bg-bg/50 px-2.5 py-2 text-xs hover:border-accent/50 transition"
                >
                  <span className="font-medium text-ink truncate">
                    {format?.unit_label || "Ch"} {c.number}: {c.title}
                  </span>
                  <span className="text-[11px] text-muted capitalize shrink-0 ml-2">{c.status}</span>
                </Link>
              ))}
            </div>
          )}
        </div>
      </div>

      {chapters.length > 0 && (
        <div className="flex justify-end">
          <button type="button" onClick={() => nav(`/books/${id}/chapters`)} className="btn-primary text-xs">
            Start Drafting {format?.unit_label_plural || "Chapters"} <ChevronRight className="h-3.5 w-3.5" />
          </button>
        </div>
      )}
    </div>
  );
}
