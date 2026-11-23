import { useEffect, useState } from "react";
import { Link, Outlet, useParams } from "react-router-dom";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Edit2, Layers, RefreshCw, Search, Sliders, X } from "lucide-react";
import { api } from "@/lib/api";
import { useAction } from "@/lib/useAction";
import { useToast } from "@/lib/toast";
import { PhaseStepper } from "@/components/PhaseStepper";
import { SearchModal } from "@/components/SearchModal";
import { SeedForm } from "@/components/SeedForm";
import { ErrorLine } from "@/components/ErrorLine";

export default function BookLayout() {
  const { id } = useParams();
  const qc = useQueryClient();
  const toast = useToast();
  const [renaming, setRenaming] = useState(false);
  const [draftTitle, setDraftTitle] = useState("");
  const [showMetaModal, setShowMetaModal] = useState(false);
  const [showSearch, setShowSearch] = useState(false);

  const { data: book, isLoading, error } = useQuery({
    queryKey: ["book", id],
    queryFn: () => api.getBook(id!),
    enabled: Boolean(id),
  });

  const { data: formatsData } = useQuery({
    queryKey: ["formats"],
    queryFn: api.getFormats,
    staleTime: Infinity,
  });

  const isObsidian = book?.source_mode === "obsidian";
  const { data: vaultStatus } = useQuery({
    queryKey: ["obsidian", id],
    queryFn: () => api.getBookObsidian(id!),
    enabled: Boolean(id && isObsidian),
    refetchInterval: 10_000,
  });

  const quickSyncAct = useAction(async () => {
    if (!id) return;
    const res = await api.syncBookObsidian(id);
    qc.invalidateQueries({ queryKey: ["book", id] });
    qc.invalidateQueries({ queryKey: ["obsidian", id] });
    toast.success(`Synced ${res.sync.synced_roles.length} role(s) from Obsidian.`);
  });

  useEffect(() => {
    if (book?.title) {
      document.title = `${book.title} — Novelista v3`;
    }
    return () => {
      document.title = "Novelista v3 — Fiction & Publishing Studio";
    };
  }, [book?.title]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        setShowSearch((s) => !s);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  const patchAct = useAction(async (patch: Record<string, unknown>) => {
    if (!id) return;
    await api.patchBook(id, patch);
    qc.invalidateQueries({ queryKey: ["book", id] });
    qc.invalidateQueries({ queryKey: ["books"] });
    setShowMetaModal(false);
    setRenaming(false);
    toast.success("Book settings updated.");
  });

  if (isLoading) return <div className="py-12 text-center text-sm text-muted">Loading manuscript workspace…</div>;
  if (error || !book) {
    return (
      <div className="card p-8 max-w-md mx-auto mt-10 text-center space-y-3">
        <ErrorLine error={error instanceof Error ? error.message : "Book not found"} />
        <Link to="/" className="btn-secondary inline-flex">
          ← Back to Library
        </Link>
      </div>
    );
  }

  const fmt = formatsData?.formats.find((f) => f.key === book.book_format);

  return (
    <div className="space-y-5">
      <div className="card p-4 space-y-3">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div className="flex items-center gap-2 min-w-0">
            {renaming ? (
              <form
                onSubmit={(e) => {
                  e.preventDefault();
                  if (draftTitle.trim()) patchAct.run({ title: draftTitle.trim() });
                  else setRenaming(false);
                }}
                className="flex items-center gap-2"
              >
                <input
                  autoFocus
                  value={draftTitle}
                  onChange={(e) => setDraftTitle(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === "Escape") setRenaming(false);
                  }}
                  className="input-field font-prose text-lg font-bold py-1"
                />
                <button type="submit" className="btn-primary text-xs py-1">
                  Save
                </button>
              </form>
            ) : (
              <button
                type="button"
                onClick={() => {
                  setDraftTitle(book.title);
                  setRenaming(true);
                }}
                className="group flex items-center gap-2 text-left"
                title="Click to rename book"
              >
                <h1 className="font-prose text-xl font-bold text-ink group-hover:text-accent transition truncate">
                  {book.title}
                </h1>
                <Edit2 className="h-3.5 w-3.5 text-muted opacity-0 group-hover:opacity-100 transition" />
              </button>
            )}
            <span className="rounded-md bg-accent-soft px-2 py-0.5 text-[11px] font-medium text-accent shrink-0">
              {fmt?.label || book.book_format}
            </span>
          </div>

          <div className="flex flex-wrap items-center gap-2">
            {isObsidian && vaultStatus?.linked && (
              <div className="inline-flex items-center gap-1 rounded-lg border border-border bg-surface-2/80 px-2 py-1 text-[11px]">
                <Link
                  to={`/books/${book.id}/vault`}
                  className={`inline-flex items-center gap-1 font-medium ${
                    vaultStatus.status === "out_of_sync"
                      ? "text-amber-700 dark:text-amber-300"
                      : vaultStatus.status === "missing_vault"
                        ? "text-rose-600 dark:text-rose-300"
                        : "text-emerald-700 dark:text-emerald-300"
                  }`}
                >
                  <Layers className="h-3 w-3" />
                  <span>
                    {vaultStatus.status === "out_of_sync"
                      ? `${vaultStatus.added.length + vaultStatus.modified.length + vaultStatus.deleted.length} vault change(s)`
                      : vaultStatus.status === "missing_vault"
                        ? "Vault missing"
                        : "Vault synced"}
                  </span>
                </Link>
                {vaultStatus.status === "out_of_sync" && (
                  <button
                    type="button"
                    onClick={() => quickSyncAct.run()}
                    disabled={quickSyncAct.busy}
                    className="rounded bg-accent px-1.5 py-0.5 text-[10px] font-semibold text-accent-fg hover:opacity-90 inline-flex items-center gap-1 ml-1"
                  >
                    <RefreshCw className={`h-2.5 w-2.5 ${quickSyncAct.busy ? "animate-spin" : ""}`} /> Sync
                  </button>
                )}
              </div>
            )}
            <button type="button" onClick={() => setShowSearch(true)} className="btn-secondary text-xs">
              <Search className="h-3.5 w-3.5" /> Find <kbd className="text-[10px] opacity-60">Ctrl+K</kbd>
            </button>
            <button type="button" onClick={() => setShowMetaModal(true)} className="btn-secondary text-xs">
              <Sliders className="h-3.5 w-3.5" /> Book Setup
            </button>
          </div>
        </div>

        <PhaseStepper book={book} format={fmt} />
      </div>

      <Outlet context={{ book, format: fmt }} />

      <SearchModal bookId={book.id} open={showSearch} onClose={() => setShowSearch(false)} />

      {showMetaModal && (
        <div
          role="dialog"
          aria-modal="true"
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4"
          onClick={(e) => {
            if (e.target === e.currentTarget) setShowMetaModal(false);
          }}
        >
          <div className="card w-full max-w-4xl max-h-[90vh] overflow-y-auto p-6 space-y-4">
            <div className="flex items-center justify-between border-b border-border pb-3">
              <div className="flex items-center gap-3">
                <h2 className="text-lg font-semibold">Edit Book Setup</h2>
                <Link
                  to={`/books/${book.id}/vault`}
                  onClick={() => setShowMetaModal(false)}
                  className="btn-secondary text-xs py-1"
                >
                  <Layers className="h-3.5 w-3.5 text-accent" />
                  {isObsidian ? "Manage Vault" : "Link Obsidian Vault"}
                </Link>
              </div>
              <button type="button" onClick={() => setShowMetaModal(false)} className="btn-ghost p-1">
                <X className="h-4 w-4" />
              </button>
            </div>
            <SeedForm
              initial={{
                title: book.title,
                book_format: book.book_format,
                audience: book.audience,
                chapter_count: book.chapter_count ?? 10,
                target_words: book.target_words ?? 25000,
                pov: book.pov,
                tense: book.tense,
                writing_style: book.writing_style,
                custom_style: book.custom_style,
                genre: book.genre,
                language: book.language,
                idea: book.idea,
              }}
              submitLabel="Save Changes"
              busy={patchAct.busy}
              onSubmit={(v) => patchAct.run(v)}
            />
          </div>
        </div>
      )}
    </div>
  );
}
