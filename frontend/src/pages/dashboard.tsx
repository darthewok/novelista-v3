import { useState } from "react";
import { Link } from "react-router-dom";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Plus, BookOpen } from "lucide-react";
import { api, Book } from "@/lib/api";
import { useAction } from "@/lib/useAction";
import { useToast } from "@/lib/toast";
import { BookCard } from "@/components/BookCard";
import { ErrorLine } from "@/components/ErrorLine";

export default function DashboardPage() {
  const qc = useQueryClient();
  const toast = useToast();
  const [filterFmt, setFilterFmt] = useState("all");

  const { data: books, isLoading, error } = useQuery({
    queryKey: ["books"],
    queryFn: api.listBooks,
  });

  const delAct = useAction(async (b: Book) => {
    if (!window.confirm(`Delete "${b.title}" and all its drafts permanently?`)) return;
    await api.deleteBook(b.id);
    qc.invalidateQueries({ queryKey: ["books"] });
    toast.info(`Deleted "${b.title}".`);
  });

  const filtered = (books || []).filter((b) => filterFmt === "all" || b.book_format === filterFmt);

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center justify-between gap-4">
        <div>
          <h1 className="font-prose text-2xl font-bold text-ink">Your Library</h1>
          <p className="text-sm text-muted">
            Write picture books, short stories, novellas, and novels — guarded against AI prose tics and ready for KDP.
          </p>
        </div>
        <div className="flex items-center gap-2.5">
          {(books?.length || 0) > 0 && (
            <select
              aria-label="Filter by format"
              value={filterFmt}
              onChange={(e) => setFilterFmt(e.target.value)}
              className="input-field w-auto text-xs"
            >
              <option value="all">All formats</option>
              <option value="picture_book">Picture Book</option>
              <option value="early_reader">Early Reader</option>
              <option value="chapter_book">Middle Grade</option>
              <option value="short_story">Short Story</option>
              <option value="novella">Novella</option>
              <option value="novel">Full Novel</option>
            </select>
          )}
          <Link to="/books/new" className="btn-primary">
            <Plus className="h-4 w-4" /> New Book
          </Link>
        </div>
      </div>

      <ErrorLine error={(error instanceof Error ? error.message : null) || delAct.error} />

      {isLoading ? (
        <div className="text-sm text-muted py-12 text-center">Loading your library…</div>
      ) : filtered.length === 0 ? (
        <div className="card p-12 text-center space-y-4 max-w-lg mx-auto">
          <div className="mx-auto flex h-12 w-12 items-center justify-center rounded-2xl bg-accent-soft text-accent">
            <BookOpen className="h-6 w-6" />
          </div>
          <div className="space-y-1">
            <h2 className="text-lg font-semibold text-ink">Start your first manuscript</h2>
            <p className="text-sm text-muted">
              Choose Picture Book, Short Story, Novella, or Novel — calibrate voice, plan scenes, and export straight to EPUB & KDP Print PDF.
            </p>
          </div>
          <Link to="/books/new" className="btn-primary inline-flex">
            <Plus className="h-4 w-4" /> Create a Book
          </Link>
        </div>
      ) : (
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {filtered.map((b) => (
            <BookCard key={b.id} book={b} busy={delAct.busy} onDelete={(bk) => delAct.run(bk)} />
          ))}
        </div>
      )}
    </div>
  );
}
