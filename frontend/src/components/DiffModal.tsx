import { useEffect } from "react";
import { X, RotateCcw } from "lucide-react";
import { wordDiff } from "@/lib/diff";

export function DiffModal({
  open,
  title,
  leftLabel,
  rightLabel,
  leftText,
  rightText,
  onRestore,
  restoreBusy,
  onClose,
}: {
  open: boolean;
  title: string;
  leftLabel: string;
  rightLabel: string;
  leftText: string;
  rightText: string;
  onRestore?: () => void;
  restoreBusy?: boolean;
  onClose: () => void;
}) {
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  if (!open) return null;

  const segs = wordDiff(leftText, rightText);

  return (
    <div
      role="dialog"
      aria-modal="true"
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4"
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div className="card w-full max-w-5xl max-h-[88vh] flex flex-col overflow-hidden">
        <div className="flex flex-wrap items-center justify-between gap-2 border-b border-border px-4 py-3">
          <div>
            <h3 className="font-semibold text-sm text-ink">{title}</h3>
            <p className="text-xs text-muted">
              <span className="bg-rose-500/20 text-rose-600 dark:text-rose-300 px-1 rounded">{leftLabel}</span>
              {" → "}
              <span className="bg-emerald-500/20 text-emerald-700 dark:text-emerald-300 px-1 rounded">{rightLabel}</span>
            </p>
          </div>
          <div className="flex items-center gap-2">
            {onRestore && (
              <button type="button" onClick={onRestore} disabled={restoreBusy} className="btn-primary text-xs">
                <RotateCcw className="h-3.5 w-3.5" /> Restore {leftLabel}
              </button>
            )}
            <button type="button" onClick={onClose} className="btn-ghost p-1.5" aria-label="Close">
              <X className="h-4 w-4" />
            </button>
          </div>
        </div>

        <div className="flex-1 overflow-y-auto p-6 font-prose text-base leading-relaxed whitespace-pre-wrap">
          {segs.map((s, i) => {
            if (s.kind === "same") return <span key={i}>{s.text}</span>;
            if (s.kind === "del") {
              return (
                <del key={i} className="bg-rose-500/20 text-rose-700 dark:text-rose-300 px-0.5 rounded line-through">
                  {s.text}
                </del>
              );
            }
            return (
              <ins key={i} className="bg-emerald-500/20 text-emerald-800 dark:text-emerald-200 px-0.5 rounded no-underline">
                {s.text}
              </ins>
            );
          })}
        </div>
      </div>
    </div>
  );
}
