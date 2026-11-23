import { useNavigate } from "react-router-dom";
import { useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { useAction } from "@/lib/useAction";
import { SeedForm } from "@/components/SeedForm";
import { ErrorLine } from "@/components/ErrorLine";

export default function NewBookPage() {
  const nav = useNavigate();
  const qc = useQueryClient();

  const createAct = useAction(async (val: Parameters<typeof api.createBook>[0]) => {
    const book = await api.createBook(val);
    qc.invalidateQueries({ queryKey: ["books"] });
    nav(`/books/${book.id}/seed`);
  });

  return (
    <div className="max-w-4xl mx-auto space-y-5">
      <div>
        <h1 className="font-prose text-2xl font-bold text-ink">New Book Project</h1>
        <p className="text-sm text-muted">
          Choose your book format first — Novelista v3 adapts word targets, craft rules, scene/spread cards, and KDP PDF trim size automatically.
        </p>
      </div>
      <ErrorLine error={createAct.error} />
      <div className="card p-6">
        <SeedForm busy={createAct.busy} onSubmit={(v) => createAct.run(v)} />
      </div>
    </div>
  );
}
