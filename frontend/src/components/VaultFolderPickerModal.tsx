import { useEffect, useState } from "react";
import { ArrowUp, Check, Folder, FolderOpen, X } from "lucide-react";
import { api, ObsidianBrowseResult } from "@/lib/api";
import { ErrorLine } from "@/components/ErrorLine";

export function VaultFolderPickerModal({
  open,
  initialPath,
  onClose,
  onSelect,
}: {
  open: boolean;
  initialPath?: string;
  onClose: () => void;
  onSelect: (path: string) => void;
}) {
  const [data, setData] = useState<ObsidianBrowseResult | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = async (path: string) => {
    setBusy(true);
    setError(null);
    try {
      const res = await api.obsidianBrowse(path);
      setData(res);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  useEffect(() => {
    if (!open) return;
    load(initialPath?.trim() || "");
  }, [open, initialPath]);

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  if (!open) return null;

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-label="Choose Obsidian Vault Folder"
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4"
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div className="card w-full max-w-2xl max-h-[85vh] flex flex-col overflow-hidden">
        <div className="flex items-center justify-between border-b border-border px-4 py-3">
          <div className="flex items-center gap-2">
            <FolderOpen className="h-4 w-4 text-accent" />
            <h2 className="text-sm font-semibold text-ink">Choose Local Vault or Book Folder</h2>
          </div>
          <button type="button" onClick={onClose} className="btn-ghost p-1" aria-label="Close">
            <X className="h-4 w-4" />
          </button>
        </div>

        <div className="p-4 space-y-3 flex-1 overflow-y-auto">
          {data?.quick_roots && data.quick_roots.length > 0 && (
            <div className="flex flex-wrap items-center gap-1.5">
              {data.quick_roots.map((qr) => (
                <button
                  key={qr.path}
                  type="button"
                  onClick={() => load(qr.path)}
                  className="btn-secondary py-1 px-2 text-xs"
                >
                  {qr.label}
                </button>
              ))}
            </div>
          )}

          {data && (
            <div className="flex items-center justify-between gap-2 rounded-lg border border-border bg-surface-2 px-3 py-2 text-xs">
              <div className="flex items-center gap-2 min-w-0">
                <button
                  type="button"
                  disabled={!data.parent || busy}
                  onClick={() => data.parent && load(data.parent)}
                  className="btn-ghost px-1.5 py-1 text-xs shrink-0"
                  title="Parent folder"
                >
                  <ArrowUp className="h-3.5 w-3.5" />
                </button>
                <span className="font-mono text-ink truncate" title={data.current}>
                  {data.current}
                </span>
              </div>
              {data.is_obsidian_vault && (
                <span className="rounded bg-emerald-500/15 px-2 py-0.5 text-[10px] font-semibold text-emerald-700 dark:text-emerald-300 shrink-0">
                  .obsidian vault
                </span>
              )}
            </div>
          )}

          <ErrorLine error={error} />

          <div className="divide-y divide-border rounded-lg border border-border overflow-hidden">
            {busy && !data ? (
              <div className="p-6 text-center text-xs text-muted">Reading folders…</div>
            ) : !data?.directories.length ? (
              <div className="p-6 text-center text-xs text-muted">No subfolders here.</div>
            ) : (
              data.directories.map((dir) => (
                <div
                  key={dir.path}
                  className="flex items-center justify-between gap-2 px-3 py-2 text-xs hover:bg-surface-2 transition"
                >
                  <button
                    type="button"
                    onClick={() => load(dir.path)}
                    className="flex items-center gap-2 text-left min-w-0 flex-1 font-medium text-ink hover:text-accent"
                  >
                    <Folder className="h-4 w-4 shrink-0 text-accent/80" />
                    <span className="truncate">{dir.name}</span>
                    {dir.is_obsidian_vault && (
                      <span className="rounded bg-emerald-500/15 px-1.5 py-0.5 text-[10px] font-semibold text-emerald-700 dark:text-emerald-300 shrink-0">
                        Vault
                      </span>
                    )}
                  </button>
                  <button
                    type="button"
                    onClick={() => {
                      onSelect(dir.path);
                      onClose();
                    }}
                    className="btn-secondary py-1 px-2 text-[11px] shrink-0"
                  >
                    Use
                  </button>
                </div>
              ))
            )}
          </div>
        </div>

        <div className="flex items-center justify-between border-t border-border px-4 py-3 bg-surface-2/40">
          <span className="text-[11px] text-muted">Pick a whole vault or a single book subfolder inside it.</span>
          <div className="flex items-center gap-2">
            <button type="button" onClick={onClose} className="btn-secondary text-xs">
              Cancel
            </button>
            <button
              type="button"
              disabled={!data?.current}
              onClick={() => {
                if (data?.current) {
                  onSelect(data.current);
                  onClose();
                }
              }}
              className="btn-primary text-xs"
            >
              <Check className="h-3.5 w-3.5" /> Select This Folder
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}