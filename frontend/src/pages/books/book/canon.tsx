import { useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { ArtifactEditor } from "@/components/ArtifactEditor";

export default function CanonPage() {
  const { id } = useParams();
  const nav = useNavigate();
  const [tab, setTab] = useState<"canon" | "summary" | "mystery">("canon");

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-2 border-b border-border pb-3">
        {[
          { k: "canon", label: "Canon Ledger (Hard Facts)" },
          { k: "summary", label: "Story So Far (Running Memory)" },
          { k: "mystery", label: "Author Secrets (Outline-Only)" },
        ].map((t) => (
          <button
            key={t.k}
            type="button"
            onClick={() => setTab(t.k as typeof tab)}
            className={`rounded-lg px-3 py-1.5 text-xs font-medium transition ${
              tab === t.k ? "bg-accent text-accent-fg" : "bg-surface-2 text-muted hover:text-ink"
            }`}
          >
            {t.label}
          </button>
        ))}
      </div>

      {tab === "canon" && (
        <ArtifactEditor
          bookId={id!}
          kind="canon"
          title="Canon Ledger"
          subtitle="Hard timelines, objects, rules, and per-chapter facts (chapters only see facts strictly before them)."
          generateLabel="Compile Canon from World & Cast"
          nextPath={`/books/${id}/outline`}
          nextLabel="Next: Outline Studio"
          onNavigateNext={(p) => nav(p)}
        />
      )}
      {tab === "summary" && (
        <ArtifactEditor
          bookId={id!}
          kind="summary"
          title="Story So Far — Running Continuity Memory"
          subtitle="Auto-updated after every chapter draft. Edit directly anytime to correct continuity details before drafting later chapters."
          generable={false}
          nextPath={`/books/${id}/outline`}
          nextLabel="Next: Outline Studio"
          onNavigateNext={(p) => nav(p)}
        />
      )}
      {tab === "mystery" && (
        <ArtifactEditor
          bookId={id!}
          kind="mystery"
          title="Author-Only Secrets & Hidden Twists"
          subtitle="Fed ONLY to the Outline generator so twists and true motives are woven subtly without spoiling early chapters."
          generable={false}
          nextPath={`/books/${id}/outline`}
          nextLabel="Next: Outline Studio"
          onNavigateNext={(p) => nav(p)}
        />
      )}
    </div>
  );
}
