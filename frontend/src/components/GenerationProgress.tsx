import { Loader2, Square, AlertTriangle } from "lucide-react";
import { JobStreamState } from "@/lib/useJobStream";

export function GenerationProgress({
  state,
  onCancel,
}: {
  state: JobStreamState;
  onCancel?: () => void;
}) {
  if (state.status === "idle") return null;
  const active = state.status === "queued" || state.status === "streaming";
  const sec = (state.elapsedMs / 1000).toFixed(1);

  return (
    <div className="card p-3 space-y-2">
      <div className="flex flex-wrap items-center justify-between gap-2 text-xs">
        <div className="flex items-center gap-2">
          {active && <Loader2 className="h-3.5 w-3.5 animate-spin text-accent" />}
          <span className="font-medium text-ink">{state.statusMessage || state.status}</span>
          <span className="text-muted">
            {state.wordCount > 0 ? `${state.wordCount.toLocaleString()} words · ` : ""}
            {sec}s{state.tokensPerSec > 0 ? ` · ${state.tokensPerSec} tok/s` : ""}
          </span>
        </div>
        {active && onCancel && (
          <button type="button" onClick={onCancel} className="btn-secondary py-1 px-2 text-xs">
            <Square className="h-3 w-3" /> Stop
          </button>
        )}
      </div>

      {state.warnings.length > 0 && (
        <div className="space-y-1">
          {state.warnings.map((w, i) => (
            <div key={i} className="flex items-start gap-1.5 rounded bg-amber-500/10 px-2.5 py-1.5 text-xs text-amber-700 dark:text-amber-300">
              <AlertTriangle className="h-3.5 w-3.5 shrink-0 mt-0.5" />
              <span>{w}</span>
            </div>
          ))}
        </div>
      )}

      {state.error && (
        <div className="rounded bg-rose-500/10 px-2.5 py-1.5 text-xs text-rose-600 dark:text-rose-300">
          {state.error}
        </div>
      )}
    </div>
  );
}
