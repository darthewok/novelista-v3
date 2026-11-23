import { useState } from "react";
import { RefreshCw, Wand2, EyeOff, CheckCircle2, AlertTriangle, XCircle } from "lucide-react";
import { Finding, Report } from "@/lib/api";

export function AICheckerPanel({
  report,
  dirty,
  busy,
  onJump,
  onRescan,
  onFixOne,
  onIgnore,
}: {
  report: Report | undefined;
  dirty?: boolean;
  busy?: boolean;
  onJump: (f: Finding) => void;
  onRescan: () => void;
  onFixOne: (f: Finding) => Promise<void>;
  onIgnore: (f: Finding) => Promise<void>;
}) {
  const [filter, setFilter] = useState<"all" | "error" | "warning">("all");
  const [fixingId, setFixingId] = useState<string | null>(null);

  const m = report?.metrics || {};
  const scanned = Boolean(m && Object.keys(m).length > 0);
  const findings = (report?.findings || []).filter((f) => filter === "all" || f.severity === filter);

  return (
    <div className="space-y-3">
      {/* Header + Grade */}
      <div className="flex items-center justify-between gap-2">
        <div className="flex items-center gap-2">
          {scanned ? (
            <span
              className={`inline-flex h-7 w-7 items-center justify-center rounded-lg text-sm font-bold ${
                report?.cleanliness_grade === "A"
                  ? "bg-emerald-500/15 text-emerald-700 dark:text-emerald-300"
                  : report?.cleanliness_grade === "B"
                    ? "bg-sky-500/15 text-sky-700 dark:text-sky-300"
                    : report?.cleanliness_grade === "C"
                      ? "bg-amber-500/15 text-amber-700 dark:text-amber-300"
                      : "bg-rose-500/15 text-rose-600 dark:text-rose-300"
              }`}
            >
              {report?.cleanliness_grade || "—"}
            </span>
          ) : (
            <span className="inline-flex h-7 px-2 items-center rounded-lg bg-surface-2 text-xs text-muted">Unscanned</span>
          )}
          <div className="text-xs">
            <div className="font-semibold text-ink">
              {scanned
                ? `${report?.error_count ?? 0} errors · ${report?.warning_count ?? 0} warnings`
                : "Not scanned yet"}
            </div>
            {dirty && <div className="text-amber-600 dark:text-amber-300">Edited since last scan</div>}
          </div>
        </div>
        <button type="button" onClick={onRescan} disabled={busy} className="btn-secondary py-1 px-2 text-xs">
          <RefreshCw className="h-3 w-3" /> Scan
        </button>
      </div>

      {/* Metrics Strip */}
      {scanned && (
        <div className="grid grid-cols-3 gap-1.5 text-[11px]">
          <div className="rounded-lg bg-surface-2 px-2 py-1.5">
            <div className="text-muted">Tells / 1k</div>
            <div className="font-semibold text-ink">{m.tells_per_1000 ?? 0}</div>
          </div>
          <div className="rounded-lg bg-surface-2 px-2 py-1.5">
            <div className="text-muted">Sent. Burst</div>
            <div className="font-semibold text-ink">CV {m.sentence_len_cv ?? 0}</div>
          </div>
          <div className="rounded-lg bg-surface-2 px-2 py-1.5">
            <div className="text-muted">Read Grade</div>
            <div className="font-semibold text-ink">FK {m.flesch_kincaid_grade ?? 0}</div>
          </div>
          <div className="rounded-lg bg-surface-2 px-2 py-1.5">
            <div className="text-muted">Dialogue</div>
            <div className="font-semibold text-ink">{m.dialogue_pct ?? 0}%</div>
          </div>
          <div className="rounded-lg bg-surface-2 px-2 py-1.5">
            <div className="text-muted">Felt / 1k</div>
            <div className="font-semibold text-ink">{m.felt_per_1000 ?? 0}</div>
          </div>
          <div className="rounded-lg bg-surface-2 px-2 py-1.5">
            <div className="text-muted">Unique Paras</div>
            <div className="font-semibold text-ink">{Math.round((m.unique_paragraph_ratio ?? 1) * 100)}%</div>
          </div>
        </div>
      )}

      {/* Filter Pills */}
      {scanned && (report?.tells_total ?? 0) > 0 && (
        <div className="flex items-center gap-1 text-xs">
          {(["all", "error", "warning"] as const).map((f) => (
            <button
              key={f}
              type="button"
              onClick={() => setFilter(f)}
              className={`rounded-md px-2 py-1 capitalize ${
                filter === f ? "bg-accent text-accent-fg font-medium" : "bg-surface-2 text-muted hover:text-ink"
              }`}
            >
              {f}
            </button>
          ))}
        </div>
      )}

      {/* Findings List */}
      {!scanned ? (
        <p className="text-xs text-muted py-2">Click Scan or Draft/Save to run the 49-rule AI tic detector.</p>
      ) : findings.length === 0 ? (
        <div className="flex items-center gap-2 rounded-lg border border-emerald-500/30 bg-emerald-500/10 p-3 text-xs text-emerald-700 dark:text-emerald-300">
          <CheckCircle2 className="h-4 w-4 shrink-0" />
          <span>Clean! Zero AI tics or warnings detected.</span>
        </div>
      ) : (
        <div className="space-y-2 max-h-[420px] overflow-y-auto pr-1">
          {findings.map((f, idx) => {
            const hasSpan = f.end_char > f.start_char;
            return (
              <div
                key={`${f.fingerprint}-${idx}`}
                className={`rounded-lg border p-2.5 text-xs space-y-1.5 ${
                  f.severity === "error"
                    ? "border-rose-500/35 bg-rose-500/5"
                    : "border-amber-500/35 bg-amber-500/5"
                }`}
              >
                <div className="flex items-center justify-between gap-2">
                  <button
                    type="button"
                    onClick={() => onJump(f)}
                    className="inline-flex items-center gap-1 font-semibold text-ink hover:text-accent text-left"
                  >
                    {f.severity === "error" ? (
                      <XCircle className="h-3.5 w-3.5 text-rose-500 shrink-0" />
                    ) : (
                      <AlertTriangle className="h-3.5 w-3.5 text-amber-500 shrink-0" />
                    )}
                    <span>L{f.line} · {f.rule}</span>
                  </button>
                  <div className="flex items-center gap-1">
                    {hasSpan && (
                      <button
                        type="button"
                        disabled={busy || fixingId === f.fingerprint}
                        onClick={async () => {
                          setFixingId(f.fingerprint);
                          try {
                            await onFixOne(f);
                          } finally {
                            setFixingId(null);
                          }
                        }}
                        className="rounded bg-accent text-accent-fg px-1.5 py-0.5 text-[11px] font-medium hover:opacity-90 disabled:opacity-50 inline-flex items-center gap-1"
                        title="Rewrite only this sentence in your book's voice"
                      >
                        <Wand2 className="h-3 w-3" /> {fixingId === f.fingerprint ? "…" : "Fix"}
                      </button>
                    )}
                    <button
                      type="button"
                      disabled={busy}
                      onClick={() => onIgnore(f)}
                      className="rounded bg-surface-2 px-1.5 py-0.5 text-[11px] text-muted hover:text-ink"
                      title="Ignore this finding"
                    >
                      <EyeOff className="h-3 w-3" />
                    </button>
                  </div>
                </div>
                {f.quote && (
                  <button
                    type="button"
                    onClick={() => onJump(f)}
                    className="block w-full text-left italic text-ink/90 hover:underline"
                  >
                    “{f.quote}”
                  </button>
                )}
                <p className="text-muted leading-snug">{f.message}</p>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
