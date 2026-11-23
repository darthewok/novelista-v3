import { useEffect } from "react";
import { useNavigate, useOutletContext, useParams } from "react-router-dom";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Sparkles, Save, Upload, Trash2, Check, ChevronRight } from "lucide-react";
import { api, Book, FormatProfile } from "@/lib/api";
import { useEditable, useUnsavedGuard } from "@/lib/useEditable";
import { useJobStream } from "@/lib/useJobStream";
import { useAction } from "@/lib/useAction";
import { useToast } from "@/lib/toast";
import { GenerationProgress } from "@/components/GenerationProgress";
import { ErrorLine } from "@/components/ErrorLine";

export default function SeedPage() {
  const { id } = useParams();
  const nav = useNavigate();
  const qc = useQueryClient();
  const toast = useToast();
  const { format } = useOutletContext<{ book: Book; format?: FormatProfile }>();

  const { data: seedData, isLoading } = useQuery({
    queryKey: ["seed", id],
    queryFn: () => api.getSeed(id!),
    enabled: Boolean(id),
  });

  const { data: candData, refetch: refetchCands } = useQuery({
    queryKey: ["seed-candidates", id],
    queryFn: () => api.getSeedCandidates(id!),
    enabled: Boolean(id),
  });

  const { data: uploadsData, refetch: refetchUploads } = useQuery({
    queryKey: ["uploads", id],
    queryFn: () => api.listUploads(id!),
    enabled: Boolean(id),
  });

  const ed = useEditable(seedData?.text, `${id}-seed`);
  useUnsavedGuard(ed.dirty);

  const stream = useJobStream(async (ev) => {
    if (ev.type === "done") {
      await refetchCands();
      toast.success("10 story candidates generated.");
    }
  });

  useEffect(() => {
    if (id) stream.resume({ bookId: id, kind: "seed_candidates" });
  }, [id, stream.resume]);

  const saveAct = useAction(async () => {
    await api.putSeed(id!, ed.text);
    ed.markSaved(ed.text);
    qc.invalidateQueries({ queryKey: ["book", id] });
    qc.invalidateQueries({ queryKey: ["seed", id] });
    toast.success("Seed saved.");
  });

  const genAct = useAction(async () => {
    if (ed.dirty) {
      await api.putSeed(id!, ed.text);
      ed.markSaved(ed.text);
    }
    const job = await api.genSeedCandidates(id!);
    stream.start(job.id, { bookId: id!, kind: "seed_candidates" });
  });

  const chooseAct = useAction(async (idx: number) => {
    const res = await api.chooseSeed(id!, { index: idx });
    ed.acceptExternal(res.text);
    qc.invalidateQueries({ queryKey: ["book", id] });
    qc.invalidateQueries({ queryKey: ["seed", id] });
    toast.success(`Selected candidate #${idx} as active seed.`);
  });

  const uploadAct = useAction(async (file: File) => {
    await api.uploadRef(id!, file);
    await refetchUploads();
    toast.success(`Uploaded reference "${file.name}".`);
  });

  const delUploadAct = useAction(async (name: string) => {
    await api.deleteUpload(id!, name);
    await refetchUploads();
    toast.info(`Removed "${name}".`);
  });

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "s") {
        e.preventDefault();
        if (ed.dirty && !saveAct.busy) saveAct.run();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [ed.dirty, saveAct]);

  const nextStep = format?.optional_phases?.includes("world") ? "characters" : "world";
  const cands = candData?.candidates || [];

  if (isLoading) return <p className="text-sm text-muted py-6">Loading seed studio…</p>;

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h2 className="text-lg font-semibold text-ink">Story Seed & Reference Material</h2>
          <p className="text-xs text-muted">
            Write your premise directly or brainstorm 10 distinct candidates with your configured model.
          </p>
        </div>
        <div className="flex items-center gap-2">
          {ed.dirty && <span className="rounded bg-amber-500/15 px-2 py-0.5 text-xs font-medium text-amber-700 dark:text-amber-300">Unsaved</span>}
          <button type="button" onClick={() => genAct.run()} disabled={genAct.busy || stream.busy} className="btn-secondary text-xs">
            <Sparkles className="h-3.5 w-3.5 text-accent" /> Generate 10 Candidates
          </button>
          <button type="button" onClick={() => saveAct.run()} disabled={!ed.dirty || saveAct.busy} className="btn-primary text-xs">
            <Save className="h-3.5 w-3.5" /> Save Seed
          </button>
        </div>
      </div>

      <ErrorLine error={saveAct.error || genAct.error || chooseAct.error || uploadAct.error || delUploadAct.error} />
      <GenerationProgress state={stream.state} onCancel={stream.cancel} />

      <div className="grid gap-5 lg:grid-cols-3">
        <div className="lg:col-span-2 space-y-2">
          <label htmlFor="seed-editor" className="text-xs font-semibold text-muted">
            Active Story Premise
          </label>
          <textarea
            id="seed-editor"
            rows={12}
            value={ed.text}
            onChange={(e) => ed.setLocal(e.target.value)}
            placeholder="Describe your central character, setting, core dilemma, stakes, and tone..."
            className="prose-editor min-h-[260px] text-sm leading-relaxed"
          />
        </div>

        <div className="card p-4 space-y-3">
          <div className="flex items-center justify-between">
            <h3 className="text-xs font-semibold uppercase tracking-wider text-muted">Reference Notes (.md / .txt / .pdf)</h3>
            <label className="btn-secondary py-1 px-2 text-xs cursor-pointer">
              <Upload className="h-3 w-3" /> Upload
              <input
                type="file"
                accept=".md,.txt,.pdf"
                className="hidden"
                onChange={(e) => {
                  const f = e.target.files?.[0];
                  if (f) uploadAct.run(f);
                  e.currentTarget.value = "";
                }}
              />
            </label>
          </div>
          <p className="text-xs text-muted">
            Uploaded lore, outlines, or character notes are automatically fed into Seed, World, Cast, and Outline.
          </p>
          <div className="space-y-1.5">
            {(uploadsData?.files || []).length === 0 ? (
              <p className="text-xs text-muted italic">No reference files uploaded.</p>
            ) : (
              uploadsData!.files.map((f) => (
                <div key={f.name} className="flex items-center justify-between rounded-lg bg-surface-2 px-2.5 py-1.5 text-xs">
                  <span className="truncate font-medium text-ink">{f.name}</span>
                  <div className="flex items-center gap-2 shrink-0">
                    <span className="text-muted">{Math.max(1, Math.round(f.size / 1024))} KB</span>
                    <button
                      type="button"
                      onClick={() => delUploadAct.run(f.name)}
                      className="text-muted hover:text-rose-500"
                      title="Delete file"
                    >
                      <Trash2 className="h-3.5 w-3.5" />
                    </button>
                  </div>
                </div>
              ))
            )}
          </div>
        </div>
      </div>

      {stream.busy && (
        <div className="card p-4 space-y-2">
          <div className="text-xs font-semibold text-ink">Streaming Candidates…</div>
          <pre className="text-xs text-muted whitespace-pre-wrap max-h-64 overflow-y-auto font-sans">
            {stream.state.text || "Brainstorming…"}
          </pre>
        </div>
      )}

      {cands.length > 0 && (
        <div className="space-y-3">
          <h3 className="text-sm font-semibold text-ink">Generated Story Candidates ({cands.length})</h3>
          <div className="grid gap-3 md:grid-cols-2">
            {cands.map((c) => (
              <div key={c.index} className="card p-4 flex flex-col justify-between gap-3">
                <div className="space-y-1">
                  <div className="text-xs font-bold text-accent">Candidate #{c.index} — {c.title}</div>
                  <p className="text-xs text-ink/90 leading-relaxed whitespace-pre-wrap">{c.text}</p>
                </div>
                <button
                  type="button"
                  disabled={chooseAct.busy}
                  onClick={() => chooseAct.run(c.index)}
                  className="btn-secondary w-full text-xs"
                >
                  <Check className="h-3.5 w-3.5 text-accent" /> Pick Candidate #{c.index}
                </button>
              </div>
            ))}
          </div>
        </div>
      )}

      <div className="flex justify-end">
        <button type="button" onClick={() => nav(`/books/${id}/${nextStep}`)} className="btn-secondary text-xs">
          Next: {nextStep === "world" ? "World Bible" : "Cast & Characters"} <ChevronRight className="h-3.5 w-3.5" />
        </button>
      </div>
    </div>
  );
}
