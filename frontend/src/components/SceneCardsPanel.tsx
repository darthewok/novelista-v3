import { useEffect, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Sparkles, Save, ImagePlus, Trash2, Plus } from "lucide-react";
import { api, FormatProfile, SceneCard } from "@/lib/api";
import { useJobStream } from "@/lib/useJobStream";
import { useAction } from "@/lib/useAction";
import { useToast } from "@/lib/toast";
import { GenerationProgress } from "./GenerationProgress";
import { ErrorLine } from "./ErrorLine";

export function SceneCardsPanel({
  bookId,
  chapterNum,
  format,
}: {
  bookId: string;
  chapterNum: number;
  format?: FormatProfile;
}) {
  const qc = useQueryClient();
  const toast = useToast();
  const [cards, setCards] = useState<SceneCard[]>([]);
  const [dirty, setDirty] = useState(false);
  const [imgKey, setImgKey] = useState(0);

  const { data, refetch } = useQuery({
    queryKey: ["scenes", bookId, chapterNum],
    queryFn: () => api.getScenes(bookId, chapterNum),
  });

  useEffect(() => {
    setCards(data?.scenes || []);
    setDirty(false);
  }, [data, chapterNum]);

  const stream = useJobStream(async (ev) => {
    if (ev.type === "done") {
      await refetch();
      qc.invalidateQueries({ queryKey: ["book", bookId] });
      toast.success("Scene cards generated.");
    }
  });

  const genAct = useAction(async () => {
    const job = await api.genScenes(bookId, chapterNum);
    stream.start(job.id, { bookId, kind: "scenes", chapter: chapterNum });
  });

  const saveAct = useAction(async () => {
    await api.putScenes(bookId, chapterNum, cards);
    setDirty(false);
    qc.invalidateQueries({ queryKey: ["book", bookId] });
    toast.success("Scene cards saved.");
  });

  const uploadAct = useAction(async (file: File) => {
    await api.uploadIllustration(bookId, chapterNum, file);
    setImgKey((k) => k + 1);
    toast.success("Illustration attached to unit.");
  });

  const updateCard = (idx: number, patch: Partial<SceneCard>) => {
    setCards((prev) => prev.map((c, i) => (i === idx ? { ...c, ...patch } : c)));
    setDirty(true);
  };

  const addBlankCard = () => {
    setCards((prev) => [
      ...prev,
      {
        title: `Scene ${prev.length + 1}`,
        word_budget: 500,
        location: "",
        time: "",
        pov: "",
        cast: [],
        start_state: "",
        goal: "",
        turn: "",
        outcome: "",
        hard_stop: "",
        illustration_prompt: "",
      },
    ]);
    setDirty(true);
  };

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <span className="text-xs font-semibold text-ink">
          {format?.key === "picture_book" ? "Spread Plan & Art Direction" : "Scene Cards"}
        </span>
        <div className="flex items-center gap-1.5">
          <button type="button" onClick={() => genAct.run()} disabled={stream.busy || genAct.busy} className="btn-secondary py-1 px-2 text-xs">
            <Sparkles className="h-3 w-3 text-accent" /> {cards.length ? "Re-plan" : "Plan"}
          </button>
          <button type="button" onClick={addBlankCard} className="btn-ghost py-1 px-2 text-xs">
            <Plus className="h-3 w-3" /> Add
          </button>
          {dirty && (
            <button type="button" onClick={() => saveAct.run()} disabled={saveAct.busy} className="btn-primary py-1 px-2 text-xs">
              <Save className="h-3 w-3" /> Save
            </button>
          )}
        </div>
      </div>

      <ErrorLine error={genAct.error || saveAct.error || uploadAct.error} />
      <GenerationProgress state={stream.state} onCancel={stream.cancel} />

      {format?.illustration_support && (
        <div className="rounded-lg border border-border bg-surface-2/60 p-2.5 space-y-2">
          <div className="flex items-center justify-between text-xs font-medium text-ink">
            <span>Unit Illustration (EPUB / PDF)</span>
            <label className="btn-secondary py-1 px-2 text-[11px] cursor-pointer">
              <ImagePlus className="h-3 w-3" /> Upload Image
              <input
                type="file"
                accept=".jpg,.jpeg,.png,.webp"
                className="hidden"
                onChange={(e) => {
                  const f = e.target.files?.[0];
                  if (f) uploadAct.run(f);
                }}
              />
            </label>
          </div>
          <img
            key={imgKey}
            src={`/api/books/${bookId}/chapters/${chapterNum}/illustration?v=${imgKey}`}
            alt=""
            onError={(e) => ((e.currentTarget as HTMLImageElement).style.display = "none")}
            onLoad={(e) => ((e.currentTarget as HTMLImageElement).style.display = "block")}
            className="max-h-36 rounded border border-border object-contain mx-auto"
          />
        </div>
      )}

      {cards.length === 0 ? (
        <p className="text-xs text-muted py-2">
          No scene cards yet. Click <strong>Plan</strong> to break this unit into paced scenes before drafting.
        </p>
      ) : (
        <div className="space-y-2.5 max-h-[450px] overflow-y-auto pr-1">
          {cards.map((sc, idx) => (
            <div key={idx} className="rounded-lg border border-border bg-bg/50 p-2.5 space-y-1.5 text-xs">
              <div className="flex items-center justify-between gap-2">
                <input
                  value={sc.title}
                  onChange={(e) => updateCard(idx, { title: e.target.value })}
                  className="font-semibold text-ink bg-transparent border-b border-transparent focus:border-accent focus:outline-none flex-1"
                />
                <input
                  type="number"
                  value={sc.word_budget}
                  onChange={(e) => updateCard(idx, { word_budget: Number(e.target.value) || 0 })}
                  className="w-16 rounded border border-border bg-surface px-1.5 py-0.5 text-right text-[11px]"
                  title="Word budget"
                />
                <button
                  type="button"
                  onClick={() => {
                    setCards((p) => p.filter((_, i) => i !== idx));
                    setDirty(true);
                  }}
                  className="text-muted hover:text-rose-500"
                >
                  <Trash2 className="h-3.5 w-3.5" />
                </button>
              </div>
              <div className="grid grid-cols-2 gap-1.5">
                <input
                  placeholder="Location"
                  value={sc.location}
                  onChange={(e) => updateCard(idx, { location: e.target.value })}
                  className="input-field py-1 px-2 text-xs"
                />
                <input
                  placeholder="POV"
                  value={sc.pov}
                  onChange={(e) => updateCard(idx, { pov: e.target.value })}
                  className="input-field py-1 px-2 text-xs"
                />
              </div>
              <textarea
                rows={2}
                placeholder="Goal & friction..."
                value={sc.goal}
                onChange={(e) => updateCard(idx, { goal: e.target.value })}
                className="input-field py-1 px-2 text-xs"
              />
              <input
                placeholder="Turn / Outcome"
                value={sc.turn}
                onChange={(e) => updateCard(idx, { turn: e.target.value })}
                className="input-field py-1 px-2 text-xs"
              />
              <input
                placeholder="Hard stop beat"
                value={sc.hard_stop}
                onChange={(e) => updateCard(idx, { hard_stop: e.target.value })}
                className="input-field py-1 px-2 text-xs"
              />
              {format?.illustration_support && (
                <textarea
                  rows={2}
                  placeholder="Illustration Art Prompt..."
                  value={sc.illustration_prompt || ""}
                  onChange={(e) => updateCard(idx, { illustration_prompt: e.target.value })}
                  className="input-field py-1 px-2 text-xs"
                />
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
