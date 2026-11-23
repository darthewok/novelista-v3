import { useEffect, useRef, useState } from "react";
import { Link, useOutletContext, useParams } from "react-router-dom";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Play, Pause, Square, ArrowRight } from "lucide-react";
import { api, Book, ChapterSummary, FormatProfile } from "@/lib/api";
import { useJobStream } from "@/lib/useJobStream";
import { useAction } from "@/lib/useAction";
import { useToast } from "@/lib/toast";
import { GenerationProgress } from "@/components/GenerationProgress";
import { ErrorLine } from "@/components/ErrorLine";

type BatchEntry = { jobId: string; chapter: number };

export default function ChaptersIndexPage() {
  const { id } = useParams();
  const qc = useQueryClient();
  const toast = useToast();
  const { book, format } = useOutletContext<{ book: Book; format?: FormatProfile }>();

  const { data: settings } = useQuery({ queryKey: ["settings"], queryFn: api.getSettings });
  const { data: chapters, isLoading, error, refetch } = useQuery({
    queryKey: ["chapters", id],
    queryFn: () => api.listChapters(id!),
    enabled: Boolean(id),
  });

  const [polishEach, setPolishEach] = useState(true);
  const [planScenes, setPlanScenes] = useState(true);
  const [batchId, setBatchId] = useState<string | null>(null);
  const [queueJobs, setQueueJobs] = useState<BatchEntry[]>([]);
  const [queuePos, setQueuePos] = useState<number>(0);
  const [batchError, setBatchError] = useState<string | null>(null);

  const batchRef = useRef<{ batchId: string | null; jobs: BatchEntry[]; pos: number }>({
    batchId: null,
    jobs: [],
    pos: 0,
  });

  const persistBatch = (bId: string | null, jobsList: BatchEntry[], pos: number) => {
    batchRef.current = { batchId: bId, jobs: jobsList, pos };
    setBatchId(bId);
    setQueueJobs(jobsList);
    setQueuePos(pos);
    if (!id) return;
    try {
      if (bId && jobsList.length) {
        sessionStorage.setItem(`novelista3.batch.${id}`, JSON.stringify({ batchId: bId, jobs: jobsList, pos }));
      } else {
        sessionStorage.removeItem(`novelista3.batch.${id}`);
      }
    } catch {
      /* ignore */
    }
  };

  useEffect(() => {
    if (settings) {
      setPolishEach(settings.quality.auto_polish);
      setPlanScenes(settings.quality.auto_scene_cards);
    }
  }, [settings]);

  const stream = useJobStream(async (ev) => {
    await refetch();
    qc.invalidateQueries({ queryKey: ["book", id] });
    if (ev.type === "error") {
      setBatchError(ev.message || "Batch stopped on error.");
      persistBatch(null, [], 0);
      return;
    }
    if (ev.type === "cancelled") {
      persistBatch(null, [], 0);
      toast.info("Batch paused or cancelled.");
      return;
    }
    if (ev.type === "done") {
      const { batchId: curBId, jobs: curJobs, pos: curPos } = batchRef.current;
      const nextPos = curPos + 1;
      if (nextPos < curJobs.length) {
        persistBatch(curBId, curJobs, nextPos);
        const nxt = curJobs[nextPos];
        stream.start(nxt.jobId, { bookId: id!, kind: "draft_batch", chapter: nxt.chapter });
      } else if (curJobs.length > 0) {
        persistBatch(null, [], 0);
        toast.success("All queued drafts completed!");
      }
    }
  });

  // Resume active batch cleanly after navigation
  useEffect(() => {
    if (!id) return;
    let savedRaw: string | null = null;
    try {
      savedRaw = sessionStorage.getItem(`novelista3.batch.${id}`);
    } catch {
      savedRaw = null;
    }
    if (!savedRaw) return;
    (async () => {
      try {
        const parsed = JSON.parse(savedRaw) as { batchId: string; jobs: BatchEntry[]; pos: number };
        for (let i = parsed.pos; i < parsed.jobs.length; i++) {
          const j = await api.getJob(parsed.jobs[i].jobId);
          if (j.status === "queued" || j.status === "running") {
            persistBatch(parsed.batchId, parsed.jobs, i);
            stream.start(j.id, { bookId: id, kind: "draft_batch", chapter: parsed.jobs[i].chapter });
            return;
          }
          if (j.status === "error" || j.status === "cancelled") {
            persistBatch(null, [], 0);
            return;
          }
        }
        persistBatch(null, [], 0);
      } catch {
        persistBatch(null, [], 0);
      }
    })();
  }, [id]);

  const startAllAct = useAction(async () => {
    setBatchError(null);
    const res = await api.draftAll(id!, { polish: polishEach, scene_cards: planScenes });
    const list = res.job_ids.map((jobId, i) => ({ jobId, chapter: res.chapters[i] }));
    persistBatch(res.batch_id, list, 0);
    if (list.length > 0) {
      stream.start(list[0].jobId, { bookId: id!, kind: "draft_batch", chapter: list[0].chapter });
    }
  });

  const pauseAct = useAction(async () => {
    if (!batchId) return;
    await api.pauseBatch(id!, batchId);
    toast.info("Will pause cleanly after the current unit finishes.");
  });

  const cancelAllAct = useAction(async () => {
    if (!batchId) return;
    await api.cancelBatch(id!, batchId);
    persistBatch(null, [], 0);
    await refetch();
    toast.info("Batch cancelled.");
  });

  if (isLoading) return <p className="text-sm text-muted py-6">Loading chapters…</p>;
  if (error) return <ErrorLine error={error instanceof Error ? error.message : String(error)} />;

  const list: ChapterSummary[] = chapters || [];
  const pending = list.filter((c) => c.status === "pending");
  const totalWords = list.reduce((s, c) => s + c.word_count, 0);
  const perUnitTarget =
    book.target_words && list.length > 0 ? Math.round(book.target_words / list.length) : null;
  const maxBar = Math.max(1, perUnitTarget ? Math.round(perUnitTarget * 1.4) : 1000, ...list.map((c) => c.word_count));
  const unitLabel = format?.unit_label || "Chapter";
  const nextPending = pending[0];

  return (
    <div className="space-y-5">
      <div className="card p-4 flex flex-wrap items-center justify-between gap-4">
        <div>
          <h2 className="text-lg font-semibold text-ink">
            {format?.unit_label_plural || "Chapters"} ({list.length})
          </h2>
          <p className="text-xs text-muted">
            Total drafted: <strong>{totalWords.toLocaleString()}</strong>
            {book.target_words ? ` / ${book.target_words.toLocaleString()} target words` : " words"}
            {perUnitTarget ? ` · ~${perUnitTarget.toLocaleString()} words/${unitLabel.toLowerCase()}` : ""}
          </p>
        </div>

        <div className="flex flex-wrap items-center gap-3">
          <label className="flex items-center gap-1.5 text-xs text-ink cursor-pointer">
            <input type="checkbox" checked={planScenes} onChange={(e) => setPlanScenes(e.target.checked)} className="accent-accent" />
            Plan scene cards
          </label>
          <label className="flex items-center gap-1.5 text-xs text-ink cursor-pointer">
            <input type="checkbox" checked={polishEach} onChange={(e) => setPolishEach(e.target.checked)} className="accent-accent" />
            Polish each
          </label>

          {pending.length > 0 && !stream.busy && (
            <button type="button" onClick={() => startAllAct.run()} disabled={startAllAct.busy} className="btn-primary text-xs">
              <Play className="h-3.5 w-3.5" /> Draft all pending ({pending.length})
            </button>
          )}
          {nextPending && !stream.busy && (
            <Link to={`/books/${id}/chapters/${nextPending.number}`} className="btn-secondary text-xs">
              Open {unitLabel} {nextPending.number} <ArrowRight className="h-3.5 w-3.5" />
            </Link>
          )}
        </div>
      </div>

      <ErrorLine error={startAllAct.error || pauseAct.error || cancelAllAct.error || batchError} />

      {queueJobs.length > 0 && (
        <div className="card p-3 space-y-2 border-accent/40">
          <div className="flex flex-wrap items-center justify-between gap-2 text-xs">
            <span className="font-semibold text-accent">
              Batch Drafting: {unitLabel} {queueJobs[queuePos]?.chapter} ({queuePos + 1} of {queueJobs.length})
            </span>
            <div className="flex items-center gap-2">
              <button type="button" onClick={() => pauseAct.run()} className="btn-secondary py-1 px-2 text-xs">
                <Pause className="h-3 w-3" /> Pause after current
              </button>
              <button type="button" onClick={() => cancelAllAct.run()} className="btn-danger py-1 px-2 text-xs">
                <Square className="h-3 w-3" /> Cancel batch
              </button>
            </div>
          </div>
          <GenerationProgress state={stream.state} onCancel={cancelAllAct.run} />
          {stream.state.text && (
            <pre className="max-h-44 overflow-y-auto rounded bg-surface-2 p-2.5 text-xs font-prose whitespace-pre-wrap text-ink">
              {stream.state.text.slice(-1800)}
            </pre>
          )}
        </div>
      )}

      {list.length === 0 ? (
        <div className="card p-8 text-center space-y-2">
          <p className="text-sm text-muted">No {format?.unit_label_plural.toLowerCase() || "chapters"} yet.</p>
          <Link to={`/books/${id}/outline`} className="btn-primary inline-flex text-xs">
            Go to Outline Studio →
          </Link>
        </div>
      ) : (
        <>
          {/* Visual Word-Count Pacing Chart */}
          <div className="card p-4 space-y-2">
            <div className="flex items-center justify-between text-xs">
              <span className="font-semibold text-muted uppercase tracking-wider">Pacing & Length Distribution</span>
              {perUnitTarget && <span className="text-muted">Target ~{perUnitTarget.toLocaleString()}w</span>}
            </div>
            <div className="flex items-end gap-1.5 h-20 pt-2">
              {list.map((c) => {
                const hPct = Math.max(4, Math.round((c.word_count / maxBar) * 100));
                return (
                  <Link
                    key={c.number}
                    to={`/books/${id}/chapters/${c.number}`}
                    title={`${unitLabel} ${c.number}: ${c.word_count.toLocaleString()} words`}
                    className="flex-1 flex flex-col items-center gap-1 group h-full justify-end"
                  >
                    <div
                      className={`w-full rounded-t transition ${
                        c.word_count === 0
                          ? "bg-surface-2"
                          : c.error_count > 0
                            ? "bg-rose-500/75"
                            : c.status === "accepted" || c.status === "polished"
                              ? "bg-emerald-500/80"
                              : "bg-accent/80"
                      } group-hover:opacity-100`}
                      style={{ height: `${hPct}%` }}
                    />
                    <span className="text-[10px] text-muted">{c.number}</span>
                  </Link>
                );
              })}
            </div>
          </div>

          <div className="card overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="border-b border-border bg-surface-2/60 text-left text-xs text-muted">
                <tr>
                  <th className="px-4 py-2.5">#</th>
                  <th className="px-4 py-2.5">Title</th>
                  <th className="px-4 py-2.5">Status</th>
                  <th className="px-4 py-2.5">Words</th>
                  <th className="px-4 py-2.5">AI Grade</th>
                  <th className="px-4 py-2.5">Issues</th>
                  <th className="px-4 py-2.5">Rev</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-border">
                {list.map((c) => (
                  <tr key={c.number} className="hover:bg-surface-2/40 transition">
                    <td className="px-4 py-2.5 font-mono text-xs text-muted">{c.number}</td>
                    <td className="px-4 py-2.5">
                      <Link to={`/books/${id}/chapters/${c.number}`} className="font-medium text-ink hover:text-accent">
                        {c.title}
                      </Link>
                    </td>
                    <td className="px-4 py-2.5">
                      <span
                        className={`rounded-md px-2 py-0.5 text-[11px] font-medium capitalize ${
                          c.status === "accepted"
                            ? "bg-emerald-500/15 text-emerald-700 dark:text-emerald-300"
                            : c.status === "polished"
                              ? "bg-sky-500/15 text-sky-700 dark:text-sky-300"
                              : c.status === "drafted"
                                ? "bg-accent-soft text-accent"
                                : "bg-surface-2 text-muted"
                        }`}
                      >
                        {c.status}
                      </span>
                    </td>
                    <td className="px-4 py-2.5 text-xs text-muted">
                      {c.word_count.toLocaleString()}
                      {perUnitTarget ? ` / ${perUnitTarget.toLocaleString()}` : ""}
                    </td>
                    <td className="px-4 py-2.5 text-xs font-bold">{c.cleanliness_grade || "—"}</td>
                    <td className="px-4 py-2.5 text-xs text-muted">
                      {c.word_count > 0 ? `${c.error_count}E · ${c.warning_count}W` : "—"}
                    </td>
                    <td className="px-4 py-2.5 text-xs text-muted">v{c.version}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
    </div>
  );
}
