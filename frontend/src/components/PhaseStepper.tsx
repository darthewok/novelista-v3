import { NavLink } from "react-router-dom";
import { Check } from "lucide-react";
import { Book, FormatProfile } from "@/lib/api";

const STEPS = [
  { path: "seed", label: "Seed" },
  { path: "world", label: "World" },
  { path: "characters", label: "Cast" },
  { path: "voice", label: "Voice" },
  { path: "canon", label: "Canon & Memory" },
  { path: "outline", label: "Outline" },
  { path: "chapters", label: "Write" },
  { path: "audit", label: "Audit" },
  { path: "export", label: "Publish" },
] as const;

export function PhaseStepper({
  book,
  format,
}: {
  book: Book;
  format?: FormatProfile;
}) {
  const optional = new Set(format?.optional_phases || []);
  const draftedCount = book.chapters.filter((c) => c.status !== "pending").length;

  const isDone = (step: string): boolean => {
    if (step === "seed") return Boolean(book.idea?.trim());
    if (step === "world") return book.status !== "idea";
    if (step === "characters") return book.chapters.length > 0 || book.status !== "idea";
    if (step === "outline") return book.chapters.length > 0;
    if (step === "chapters") return book.chapters.length > 0 && draftedCount === book.chapters.length;
    if (step === "export") return book.status === "ready";
    return false;
  };

  return (
    <nav aria-label="Book workflow phases" className="flex flex-wrap items-center gap-1.5">
      {STEPS.map((s, idx) => {
        const opt = optional.has(s.path);
        const done = isDone(s.path);
        const label = s.path === "chapters" && format ? format.unit_label_plural : s.label;
        return (
          <NavLink
            key={s.path}
            to={`/books/${book.id}/${s.path}`}
            className={({ isActive }) =>
              `inline-flex items-center gap-1.5 rounded-lg px-2.5 py-1.5 text-xs font-medium transition ${
                isActive
                  ? "bg-accent text-accent-fg shadow-sm"
                  : "bg-surface-2/80 text-ink hover:bg-surface-2 border border-transparent hover:border-border"
              }`
            }
          >
            {({ isActive }) => (
              <>
                <span
                  className={`inline-flex h-4 w-4 items-center justify-center rounded-full text-[10px] font-bold ${
                    isActive
                      ? "bg-white/20 text-white"
                      : done
                        ? "bg-emerald-500/20 text-emerald-600 dark:text-emerald-300"
                        : "bg-border text-muted"
                  }`}
                >
                  {done && !isActive ? <Check className="h-2.5 w-2.5" /> : idx + 1}
                </span>
                <span>{label}</span>
                {opt && <span className={`text-[10px] ${isActive ? "text-white/75" : "text-muted"}`}>(opt)</span>}
              </>
            )}
          </NavLink>
        );
      })}
    </nav>
  );
}
