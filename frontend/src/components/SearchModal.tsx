import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { Search, X } from "lucide-react";
import { api } from "@/lib/api";

export function SearchModal({
  bookId,
  open,
  onClose,
}: {
  bookId: string;
  open: boolean;
  onClose: () => void;
}) {
  const nav = useNavigate();
  const [q, setQ] = useState("");
  const [hits, setHits] = useState<Array<{ label: string; target: string; chapter: number | null; line: number; snippet: string }>>([]);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (!open) return;
    const t = window.setTimeout(async () => {
      if (q.trim().length < 2) {
        setHits([]);
        return;
      }
      setBusy(true);
      try {
        const res = await api.searchBook(bookId, q.trim());
        setHits(res.hits);
      } catch {
        setHits([]);
      } finally {
        setBusy(false);
      }
    }, 220);
    return () => window.clearTimeout(t);
  }, [q, bookId, open]);

  if (!open) return null;

  const go = (target: string) => {
    onClose();
    if (target.startsWith("chapter:")) {
      nav(`/books/${bookId}/chapters/${target.split(":")[1]}`);
    } else {
      nav(`/books/${bookId}/${target}`);
    }
  };

  return (
    <div
      role="dialog"
      aria-modal="true"
      className="fixed inset-0 z-50 flex items-start justify-center bg-black/55 pt-20 p-4"
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div className="card w-full max-w-xl overflow-hidden">
        <div className="flex items-center gap-2 border-b border-border px-3 py-2.5">
          <Search className="h-4 w-4 text-muted" />
          <input
            autoFocus
            value={q}
            onChange={(e) => setQ(e.target.value)}
            placeholder="Search across seed, world, characters, canon, outline, and all chapters…"
            className="flex-1 bg-transparent text-sm text-ink focus:outline-none"
          />
          <button type="button" onClick={onClose} className="btn-ghost p-1">
            <X className="h-4 w-4" />
          </button>
        </div>
        <div className="max-h-96 overflow-y-auto divide-y divide-border">
          {busy && <div className="p-4 text-xs text-muted">Searching…</div>}
          {!busy && q.trim().length >= 2 && hits.length === 0 && (
            <div className="p-4 text-xs text-muted">No matches found.</div>
          )}
          {hits.map((h, i) => (
            <button
              key={i}
              type="button"
              onClick={() => go(h.target)}
              className="w-full text-left px-4 py-2.5 hover:bg-surface-2 transition"
            >
              <div className="flex items-center justify-between text-xs font-semibold text-accent">
                <span>{h.label}</span>
                <span className="text-muted">Line {h.line}</span>
              </div>
              <p className="text-xs text-ink mt-0.5 line-clamp-2">{h.snippet}</p>
            </button>
          ))}
        </div>
      </div>
    </div>
  );
}
