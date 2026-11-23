import { useEffect, useMemo, useRef, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Sparkles, CheckCircle2, AlertTriangle, Wrench } from "lucide-react";
import { api } from "@/lib/api";
import { useJobStream } from "@/lib/useJobStream";
import { useAction } from "@/lib/useAction";
import { useToast } from "@/lib/toast";
import { GenerationProgress } from "@/components/GenerationProgress";
import { ErrorLine } from "@/components/ErrorLine";

export default function AuditPage() {
  const { id } = useParams();
  const qc = useQueryClient();
  const toast = useToast();
  const activeKindRef = useRef<"manuscript_audit" | "audit_fix">("manuscript_audit");
  const [rerunAfterFix, setRerunAfterFix] = useState(true);
  const rerunRef = useRef(true);
  rerunRef.current = rerunAfterFix;

  const { data, isLoading, error, refetch } = useQuery({
    queryKey: ["audit", id],
    queryFn: () => api.getAudit(id!),
    enabled: Boolean(id),
  });

  const det = data?.deterministic;
  const ed = data?.editorial;

  const globalIssues: string[] = useMemo(
    () => (ed ? [...(ed.structural_issues || []), ...(ed.continuity_risks || []), ...(ed.ai_style_patterns || [])] : []),
    [ed],
  );
  const topFixes: Array<{ chapter: number; action: string }> = useMemo(() => ed?.top_fixes || [], [ed]);
  const totalIssues = globalIssues.length + topFixes.length;
  const showCheckboxes = totalIssues > 1;
  const auditSig = useMemo(() => JSON.stringify({ g: globalIssues, p: topFixes }), [globalIssues, topFixes]);

  const [selectedGlobals, setSelectedGlobals] = useState<Set<number>>(() => new Set());
  const [selectedPriorities, setSelectedPriorities] = useState<Set<number>>(() => new Set());

  useEffect(() => {
    setSelectedGlobals(new Set(globalIssues.map((_, i) => i)));
    setSelectedPriorities(new Set(topFixes.map((_, i) => i)));
  }, [auditSig, globalIssues.length, topFixes.length]);

  const stream = useJobStream(async (ev) => {
    if (ev.type === "done") {
      await Promise.all([
        refetch(),
        id ? qc.invalidateQueries({ queryKey: ["book", id] }) : Promise.resolve(),
        id ? qc.invalidateQueries({ queryKey: ["chapters", id] }) : Promise.resolve(),
      ]);
      if (activeKindRef.current === "audit_fix") {
        toast.success(rerunRef.current ? "Audit fixes applied and manuscript re-audited." : "Audit fixes applied.");
      } else {
        toast.success("Manuscript audit completed.");
      }
    }
  });

  useEffect(() => {
    if (!id) return;
    let hasFixJob = false;
    try {
      hasFixJob = Boolean(sessionStorage.getItem(`novelista3.job.${id}.audit_fix.all`));
    } catch {
      hasFixJob = false;
    }
    if (hasFixJob) {
      activeKindRef.current = "audit_fix";
      stream.resume({ bookId: id, kind: "audit_fix" });
    } else {
      activeKindRef.current = "manuscript_audit";
      stream.resume({ bookId: id, kind: "manuscript_audit" });
    }
  }, [id, stream.resume]);

  const runAct = useAction(async () => {
    activeKindRef.current = "manuscript_audit";
    const job = await api.runAudit(id!);
    stream.start(job.id, { bookId: id!, kind: "manuscript_audit" });
  });

  const fixAct = useAction(async (fixAll: boolean) => {
    const pickedGlobals = fixAll ? globalIssues : globalIssues.filter((_, i) => selectedGlobals.has(i));
    const pickedPriorities = fixAll ? topFixes : topFixes.filter((_, i) => selectedPriorities.has(i));
    activeKindRef.current = "audit_fix";
    const job = await api.fixAuditIssues(id!, {
      global_issues: pickedGlobals,
      priority_fixes: pickedPriorities,
      fix_all: fixAll,
      rerun_audit: rerunAfterFix,
    });
    stream.start(job.id, { bookId: id!, kind: "audit_fix" });
  });

  if (isLoading) return <p className="text-sm text-muted py-6">Loading audit metrics…</p>;
  if (error) return <ErrorLine error={error instanceof Error ? error.message : String(error)} />;

  const selectedCount = showCheckboxes ? selectedGlobals.size + selectedPriorities.size : totalIssues;
  const busyAny = runAct.busy || fixAct.busy || stream.busy;

  const toggleGlobal = (idx: number) => {
    setSelectedGlobals((prev) => {
      const next = new Set(prev);
      if (next.has(idx)) next.delete(idx);
      else next.add(idx);
      return next;
    });
  };

  const togglePriority = (idx: number) => {
    setSelectedPriorities((prev) => {
      const next = new Set(prev);
      if (next.has(idx)) next.delete(idx);
      else next.add(idx);
      return next;
    });
  };

  const selectAllIssues = () => {
    setSelectedGlobals(new Set(globalIssues.map((_, i) => i)));
    setSelectedPriorities(new Set(topFixes.map((_, i) => i)));
  };

  const clearAllIssues = () => {
    setSelectedGlobals(new Set());
    setSelectedPriorities(new Set());
  };

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h2 className="text-lg font-semibold text-ink">Whole-Manuscript Editorial Audit</h2>
          <p className="text-xs text-muted">
            Cross-chapter pacing, repeated similes/openers, foreshadowing payoffs, and an Acquiring Editor publishability review.
          </p>
        </div>
        <button type="button" onClick={() => runAct.run()} disabled={busyAny} className="btn-primary text-xs">
          <Sparkles className="h-3.5 w-3.5" /> Run Full Manuscript Audit
        </button>
      </div>

      <ErrorLine error={runAct.error || fixAct.error} />
      <GenerationProgress state={stream.state} onCancel={stream.cancel} />

      {ed && (
        <div className="card p-5 space-y-4">
          <div className="flex items-center justify-between">
            <h3 className="font-semibold text-base text-ink">Acquiring Editor Letter</h3>
            <span
              className={`rounded-lg px-2.5 py-1 text-xs font-semibold uppercase ${
                ed.publishability_verdict === "ready"
                  ? "bg-emerald-500/15 text-emerald-700 dark:text-emerald-300"
                  : ed.publishability_verdict === "needs_polish"
                    ? "bg-amber-500/15 text-amber-700 dark:text-amber-300"
                    : "bg-rose-500/15 text-rose-600 dark:text-rose-300"
              }`}
            >
              {String(ed.publishability_verdict || "reviewed").replace(/_/g, " ")}
            </span>
          </div>
          <p className="font-prose text-sm leading-relaxed text-ink">{ed.executive_summary}</p>

          {totalIssues > 0 && (
            <div className="flex flex-wrap items-center justify-between gap-3 rounded-lg border border-border bg-surface-2 px-3.5 py-2.5 text-xs">
              <div className="flex flex-wrap items-center gap-3">
                {showCheckboxes && (
                  <>
                    <span className="font-semibold text-ink">
                      {selectedCount} of {totalIssues} selected
                    </span>
                    <div className="flex items-center gap-2">
                      <button type="button" onClick={selectAllIssues} disabled={busyAny} className="text-accent font-medium hover:underline">
                        Select all
                      </button>
                      <span className="text-border">·</span>
                      <button type="button" onClick={clearAllIssues} disabled={busyAny} className="text-muted hover:text-ink hover:underline">
                        Clear
                      </button>
                    </div>
                  </>
                )}
                <label className="flex items-center gap-1.5 text-ink cursor-pointer">
                  <input
                    type="checkbox"
                    checked={rerunAfterFix}
                    onChange={(e) => setRerunAfterFix(e.target.checked)}
                    disabled={busyAny}
                    className="accent-accent"
                  />
                  Re-run audit after fixing
                </label>
              </div>

              <div className="flex flex-wrap items-center gap-2">
                {totalIssues === 1 ? (
                  <button type="button" onClick={() => fixAct.run(true)} disabled={busyAny} className="btn-primary text-xs">
                    <Wrench className="h-3.5 w-3.5" /> Fix Issue
                  </button>
                ) : selectedCount > 0 && selectedCount < totalIssues ? (
                  <>
                    <button type="button" onClick={() => fixAct.run(false)} disabled={busyAny} className="btn-primary text-xs">
                      <Wrench className="h-3.5 w-3.5" /> Fix Selected ({selectedCount})
                    </button>
                    <button type="button" onClick={() => fixAct.run(true)} disabled={busyAny} className="btn-secondary text-xs">
                      <Wrench className="h-3.5 w-3.5" /> Fix All ({totalIssues})
                    </button>
                  </>
                ) : (
                  <>
                    {selectedCount === 0 && (
                      <button type="button" disabled className="btn-secondary text-xs opacity-50">
                        <Wrench className="h-3.5 w-3.5" /> Fix Selected (0)
                      </button>
                    )}
                    <button type="button" onClick={() => fixAct.run(true)} disabled={busyAny} className="btn-primary text-xs">
                      <Wrench className="h-3.5 w-3.5" /> Fix All ({totalIssues})
                    </button>
                  </>
                )}
              </div>
            </div>
          )}

          <div className="grid gap-4 md:grid-cols-2 text-xs">
            <div className="space-y-1.5">
              <div className="font-semibold text-emerald-700 dark:text-emerald-300 flex items-center gap-1">
                <CheckCircle2 className="h-3.5 w-3.5" /> Strengths
              </div>
              <ul className="list-disc pl-4 space-y-1 text-ink/90">
                {(ed.strengths || []).map((s: string, i: number) => (
                  <li key={i}>{s}</li>
                ))}
              </ul>
            </div>
            <div className="space-y-1.5">
              <div className="font-semibold text-amber-700 dark:text-amber-300 flex items-center gap-1">
                <AlertTriangle className="h-3.5 w-3.5" /> Structural & AI Style Issues
              </div>
              {showCheckboxes ? (
                <ul className="space-y-1.5 text-ink/90">
                  {globalIssues.map((s: string, i: number) => (
                    <li key={i}>
                      <label className="flex items-start gap-2 cursor-pointer leading-relaxed">
                        <input
                          type="checkbox"
                          checked={selectedGlobals.has(i)}
                          onChange={() => toggleGlobal(i)}
                          disabled={busyAny}
                          className="mt-1 accent-accent shrink-0"
                        />
                        <span>{s}</span>
                      </label>
                    </li>
                  ))}
                </ul>
              ) : (
                <ul className="list-disc pl-4 space-y-1 text-ink/90">
                  {globalIssues.map((s: string, i: number) => (
                    <li key={i}>{s}</li>
                  ))}
                </ul>
              )}
            </div>
          </div>

          {topFixes.length > 0 && (
            <div className="border-t border-border pt-3 space-y-2">
              <div className="text-xs font-semibold text-ink">Priority {det?.unit_label || "Chapter"} Fixes</div>
              <div className="space-y-1.5">
                {topFixes.map((tf: { chapter: number; action: string }, i: number) => (
                  <div key={i} className="flex items-start justify-between gap-2 rounded-lg bg-surface-2 px-3 py-2 text-xs">
                    {showCheckboxes ? (
                      <label className="flex items-start gap-2 cursor-pointer flex-1 min-w-0 leading-relaxed">
                        <input
                          type="checkbox"
                          checked={selectedPriorities.has(i)}
                          onChange={() => togglePriority(i)}
                          disabled={busyAny}
                          className="mt-0.5 accent-accent shrink-0"
                        />
                        <span>
                          <strong>{det?.unit_label || "Unit"} {tf.chapter}:</strong> {tf.action}
                        </span>
                      </label>
                    ) : (
                      <span>
                        <strong>{det?.unit_label || "Unit"} {tf.chapter}:</strong> {tf.action}
                      </span>
                    )}
                    <Link to={`/books/${id}/chapters/${tf.chapter}`} className="text-accent font-medium shrink-0 hover:underline">
                      Open →
                    </Link>
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>
      )}

      {det && (
        <div className="grid gap-4 md:grid-cols-2">
          <div className="card p-4 space-y-2 text-xs">
            <h3 className="font-semibold text-ink">Overused Cross-Chapter Similes</h3>
            {det.overused_similes?.length ? (
              det.overused_similes.map((s: { phrase: string; count: number }, i: number) => (
                <div key={i} className="flex justify-between border-b border-border/50 py-1">
                  <span>“{s.phrase}”</span>
                  <span className="font-semibold text-amber-600">{s.count}x</span>
                </div>
              ))
            ) : (
              <p className="text-muted">No repeated similes across units.</p>
            )}
          </div>

          <div className="card p-4 space-y-2 text-xs">
            <h3 className="font-semibold text-ink">Unit Cleanliness Matrix</h3>
            <div className="space-y-1 max-h-60 overflow-y-auto">
              {(det.chapters || []).map((c: any) => (
                <Link
                  key={c.number}
                  to={`/books/${id}/chapters/${c.number}`}
                  className="flex items-center justify-between rounded px-2 py-1 hover:bg-surface-2"
                >
                  <span className="truncate">
                    {det.unit_label} {c.number}: {c.title}
                  </span>
                  <span className="font-mono text-muted shrink-0 ml-2">
                    {c.word_count}w · Grade {c.grade || "—"} · {c.tells_per_1000 ?? 0}/1k
                  </span>
                </Link>
              ))}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
