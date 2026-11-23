import { useEffect, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Sparkles, Save, Eye, Edit3, RotateCcw, ChevronRight } from "lucide-react";
import { api } from "@/lib/api";
import { useEditable, useUnsavedGuard } from "@/lib/useEditable";
import { useJobStream } from "@/lib/useJobStream";
import { useAction } from "@/lib/useAction";
import { useToast } from "@/lib/toast";
import { ProseMarkdownView } from "@/lib/markdown";
import { GenerationProgress } from "./GenerationProgress";
import { ErrorLine } from "./ErrorLine";

export function ArtifactEditor({
  bookId,
  kind,
  title,
  subtitle,
  generable = true,
  generateLabel = "Generate",
  nextPath,
  nextLabel,
  onNavigateNext,
}: {
  bookId: string;
  kind: "world" | "characters" | "voice" | "canon" | "summary" | "mystery";
  title: string;
  subtitle?: string;
  generable?: boolean;
  generateLabel?: string;
  nextPath?: string;
  nextLabel?: string;
  onNavigateNext?: (path: string) => void;
}) {
  const qc = useQueryClient();
  const toast = useToast();
  const [viewMode, setViewMode] = useState<"edit" | "read">("edit");
  const [showStream, setShowStream] = useState(false);

  const { data, isLoading, error: loadErr, refetch } = useQuery({
    queryKey: ["artifact", bookId, kind],
    queryFn: () => api.getArtifact(bookId, kind),
  });

  const ed = useEditable(data?.text, `${bookId}-${kind}`);
  useUnsavedGuard(ed.dirty);

  const stream = useJobStream(async (ev) => {
    if (ev.type === "done") {
      const fresh = await refetch();
      if (fresh.data?.text !== undefined) {
        ed.acceptExternal(fresh.data.text);
      }
      qc.invalidateQueries({ queryKey: ["book", bookId] });
      toast.success(`${title} updated.`);
    }
  });

  useEffect(() => {
    stream.resume({ bookId, kind: `${kind}_generate` });
  }, [bookId, kind, stream.resume]);

  useEffect(() => {
    if (stream.busy) setShowStream(true);
  }, [stream.busy]);

  const saveAct = useAction(async () => {
    await api.putArtifact(bookId, kind, ed.text);
    ed.markSaved(ed.text);
    qc.invalidateQueries({ queryKey: ["artifact", bookId, kind] });
    qc.invalidateQueries({ queryKey: ["book", bookId] });
    toast.success(`Saved ${title}.`);
  });

  const genAct = useAction(async () => {
    if (ed.text.trim() && !window.confirm(`Regenerate ${title}? Current text will be backed up as previous version.`)) return;
    if (ed.dirty) {
      await api.putArtifact(bookId, kind, ed.text);
      ed.markSaved(ed.text);
    }
    const job = await api.genArtifact(bookId, kind);
    setShowStream(true);
    stream.start(job.id, { bookId, kind: `${kind}_generate` });
  });

  const restoreAct = useAction(async () => {
    const res = await api.restoreArtifactPrev(bookId, kind);
    ed.acceptExternal(res.text);
    qc.invalidateQueries({ queryKey: ["artifact", bookId, kind] });
    toast.success(`Restored previous ${title}.`);
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

  if (isLoading) return <p className="text-sm text-muted py-6">Loading {title}…</p>;
  if (loadErr) return <ErrorLine error={loadErr instanceof Error ? loadErr.message : String(loadErr)} />;

  const wordCount = ed.text.trim() ? ed.text.trim().split(/\s+/).length : 0;

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h2 className="text-lg font-semibold text-ink">{title}</h2>
          {subtitle && <p className="text-xs text-muted">{subtitle}</p>}
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <span className="text-xs text-muted">{wordCount.toLocaleString()} words</span>
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
              <Eye className="h-3.5 w-3.5" /> Reader
            </button>
          </div>

          {data?.has_prev && (
            <button type="button" onClick={() => restoreAct.run()} disabled={restoreAct.busy || stream.busy} className="btn-secondary text-xs">
              <RotateCcw className="h-3.5 w-3.5" /> Restore previous
            </button>
          )}
          {generable && (
            <button type="button" onClick={() => genAct.run()} disabled={genAct.busy || stream.busy} className="btn-secondary text-xs">
              <Sparkles className="h-3.5 w-3.5 text-accent" />
              {ed.text.trim() ? `Regenerate ${title}` : generateLabel}
            </button>
          )}
          <button type="button" onClick={() => saveAct.run()} disabled={!ed.dirty || saveAct.busy} className="btn-primary text-xs">
            <Save className="h-3.5 w-3.5" /> Save
          </button>
        </div>
      </div>

      <ErrorLine error={saveAct.error || genAct.error || restoreAct.error} />
      <GenerationProgress state={stream.state} onCancel={stream.cancel} />

      <div className={`grid gap-4 ${showStream ? "lg:grid-cols-3" : "grid-cols-1"}`}>
        <div className={showStream ? "lg:col-span-2" : ""}>
          {viewMode === "edit" ? (
            <textarea
              aria-label={title}
              value={ed.text}
              onChange={(e) => ed.setLocal(e.target.value)}
              rows={22}
              placeholder={`Write or generate ${title.toLowerCase()}...`}
              className="prose-editor min-h-[520px] font-mono text-sm leading-relaxed"
            />
          ) : (
            <div className="card p-6 min-h-[520px]">
              <ProseMarkdownView text={ed.text} />
            </div>
          )}
        </div>

        {showStream && (
          <div className="card p-4 flex flex-col gap-2 max-h-[540px]">
            <div className="flex items-center justify-between border-b border-border pb-2">
              <span className="text-xs font-semibold text-ink">Live Generation Stream</span>
              <button type="button" onClick={() => setShowStream(false)} className="text-xs text-muted hover:text-ink">
                Hide
              </button>
            </div>
            <pre className="flex-1 overflow-y-auto whitespace-pre-wrap text-xs text-muted font-mono leading-relaxed">
              {stream.state.text || "Waiting for output..."}
            </pre>
          </div>
        )}
      </div>

      {nextPath && nextLabel && onNavigateNext && (
        <div className="flex justify-end pt-2">
          <button type="button" onClick={() => onNavigateNext(nextPath)} className="btn-secondary text-xs">
            {nextLabel} <ChevronRight className="h-3.5 w-3.5" />
          </button>
        </div>
      )}
    </div>
  );
}
