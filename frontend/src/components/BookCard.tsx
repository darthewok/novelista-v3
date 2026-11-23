import { Link } from "react-router-dom";
import { ArrowRight, Trash2, BookOpen } from "lucide-react";
import { Book } from "@/lib/api";
import { FORMAT_CARDS } from "@/lib/catalog";

function smartResumePath(book: Book): string {
  if (!book.idea?.trim()) return `/books/${book.id}/seed`;
  if (!book.chapters.length) return `/books/${book.id}/outline`;
  const nextPending = book.chapters.find((c) => c.status === "pending");
  if (nextPending) return `/books/${book.id}/chapters/${nextPending.number}`;
  if (book.status === "ready") return `/books/${book.id}/export`;
  return `/books/${book.id}/chapters`;
}

export function BookCard({
  book,
  onDelete,
  busy,
}: {
  book: Book;
  onDelete: (b: Book) => void;
  busy?: boolean;
}) {
  const fmt = FORMAT_CARDS.find((f) => f.value === book.book_format);
  const words = book.chapters.reduce((acc, c) => acc + c.word_count, 0);
  const target = book.target_words || 0;
  const pct = target > 0 ? Math.min(100, Math.round((words / target) * 100)) : 0;
  const drafted = book.chapters.filter((c) => c.status !== "pending").length;
  const grades = book.chapters.map((c) => c.cleanliness_grade).filter(Boolean);
  const worstGrade = grades.includes("F")
    ? "F"
    : grades.includes("D")
      ? "D"
      : grades.includes("C")
        ? "C"
        : grades.includes("B")
          ? "B"
          : grades.includes("A")
            ? "A"
            : "";

  const resumeTo = smartResumePath(book);

  return (
    <div className="card p-4 flex flex-col justify-between gap-3 transition hover:border-accent/50">
      <div className="space-y-2.5">
        <div className="flex items-start justify-between gap-2">
          <div className="flex items-center gap-2">
            <span className="inline-flex items-center gap-1 rounded-md bg-accent-soft px-2 py-0.5 text-[11px] font-medium text-accent">
              <BookOpen className="h-3 w-3" />
              {fmt?.label || book.book_format}
            </span>
            <span className="rounded-md bg-surface-2 px-2 py-0.5 text-[11px] font-medium text-muted capitalize">
              {book.status}
            </span>
          </div>
          {worstGrade && (
            <span
              className={`rounded-md px-2 py-0.5 text-xs font-bold ${
                worstGrade === "A"
                  ? "bg-emerald-500/15 text-emerald-700 dark:text-emerald-300"
                  : worstGrade === "B"
                    ? "bg-sky-500/15 text-sky-700 dark:text-sky-300"
                    : worstGrade === "C"
                      ? "bg-amber-500/15 text-amber-700 dark:text-amber-300"
                      : "bg-rose-500/15 text-rose-600 dark:text-rose-300"
              }`}
              title="Lowest cleanliness grade across drafted units"
            >
              Grade {worstGrade}
            </span>
          )}
        </div>

        <Link to={resumeTo} className="block group">
          <h3 className="font-prose text-lg font-semibold text-ink group-hover:text-accent transition line-clamp-1">
            {book.title}
          </h3>
          {book.idea && (
            <p className="mt-1 text-xs text-muted line-clamp-2 leading-relaxed">{book.idea}</p>
          )}
        </Link>

        <div className="space-y-1 pt-1">
          <div className="flex items-center justify-between text-[11px] text-muted">
            <span>
              {words.toLocaleString()}
              {target ? ` / ${target.toLocaleString()}` : ""} words
            </span>
            <span>
              {drafted}/{book.chapters.length || book.chapter_count || 0} {fmt?.unitLabel || "Chapter"}s
            </span>
          </div>
          {target > 0 && (
            <div className="h-1.5 w-full rounded-full bg-surface-2 overflow-hidden">
              <div className="h-full bg-accent transition-all" style={{ width: `${pct}%` }} />
            </div>
          )}
        </div>
      </div>

      <div className="flex items-center justify-between border-t border-border pt-2.5">
        <button
          type="button"
          disabled={busy}
          onClick={() => onDelete(book)}
          className="btn-ghost px-2 py-1 text-xs text-muted hover:text-rose-500"
          title="Delete book"
        >
          <Trash2 className="h-3.5 w-3.5" />
        </button>
        <Link to={resumeTo} className="btn-primary py-1 px-3 text-xs">
          Continue <ArrowRight className="h-3.5 w-3.5" />
        </Link>
      </div>
    </div>
  );
}
