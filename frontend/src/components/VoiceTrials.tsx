import { useEffect } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Sparkles, Check } from "lucide-react";
import { api } from "@/lib/api";
import { useJobStream } from "@/lib/useJobStream";
import { useAction } from "@/lib/useAction";
import { useToast } from "@/lib/toast";
import { GenerationProgress } from "./GenerationProgress";
import { ErrorLine } from "./ErrorLine";

export function VoiceTrials({
  bookId,
  onVoiceGenerated,
}: {
  bookId: string;
  onVoiceGenerated: (newText: string) => void;
}) {
  const qc = useQueryClient();
  const toast = useToast();

  const { data, refetch } = useQuery({
    queryKey: ["voice-trials", bookId],
    queryFn: () => api.getVoiceTrials(bookId),
  });

  const trialsStream = useJobStream(async (ev) => {
    if (ev.type === "done") {
      await refetch();
      toast.success("3 voice trials generated.");
    }
  });

  const pickStream = useJobStream(async (ev) => {
    if (ev.type === "done") {
      const fresh = await api.getArtifact(bookId, "voice");
      onVoiceGenerated(fresh.text);
      qc.invalidateQueries({ queryKey: ["artifact", bookId, "voice"] });
      toast.success("Project Voice Guide built cleanly (anti-slop rules preserved).");
    }
  });

  useEffect(() => {
    trialsStream.resume({ bookId, kind: "voice_trials" });
    pickStream.resume({ bookId, kind: "voice_generate" });
  }, [bookId, trialsStream.resume, pickStream.resume]);

  const genAct = useAction(async () => {
    const job = await api.genVoiceTrials(bookId);
    trialsStream.start(job.id, { bookId, kind: "voice_trials" });
  });

  const pickAct = useAction(async (choice: number) => {
    const job = await api.genArtifact(bookId, "voice", { choice });
    pickStream.start(job.id, { bookId, kind: "voice_generate" });
  });

  const trials = data?.trials || [];
  const busy = trialsStream.busy || pickStream.busy || genAct.busy || pickAct.busy;

  return (
    <div className="card p-4 space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <h3 className="font-semibold text-sm text-ink">Step A — Compare 3 Opening Voice Registers</h3>
          <p className="text-xs text-muted">
            Generate three distinct narrative voices on your opening moment, then pick one to calibrate the Voice Guide below.
          </p>
        </div>
        <button type="button" onClick={() => genAct.run()} disabled={busy} className="btn-primary text-xs">
          <Sparkles className="h-3.5 w-3.5" /> {trials.length ? "Regenerate 3 Trials" : "Generate 3 Voice Trials"}
        </button>
      </div>

      <ErrorLine error={genAct.error || pickAct.error} />
      <GenerationProgress state={trialsStream.state} onCancel={trialsStream.cancel} />
      <GenerationProgress state={pickStream.state} onCancel={pickStream.cancel} />

      {trialsStream.busy && (
        <pre className="rounded-lg border border-border bg-surface-2 p-3 text-xs font-prose whitespace-pre-wrap max-h-64 overflow-y-auto">
          {trialsStream.state.text || "Drafting trials…"}
        </pre>
      )}

      {pickStream.busy && (
        <pre className="rounded-lg border border-border bg-surface-2 p-3 text-xs font-mono whitespace-pre-wrap max-h-56 overflow-y-auto">
          {pickStream.state.text || "Building Voice Guide…"}
        </pre>
      )}

      {trials.length > 0 ? (
        <div className="grid gap-3 md:grid-cols-3">
          {trials.map((t) => (
            <div key={t.index} className="rounded-xl border border-border bg-bg/50 p-3 flex flex-col justify-between gap-3">
              <div className="space-y-2">
                <div className="text-xs font-semibold text-accent">
                  Trial {t.index} — {t.register}
                </div>
                <p className="font-prose text-sm leading-relaxed text-ink whitespace-pre-wrap">{t.text}</p>
              </div>
              <button
                type="button"
                disabled={busy}
                onClick={() => pickAct.run(t.index)}
                className="btn-secondary w-full text-xs"
              >
                <Check className="h-3.5 w-3.5 text-accent" /> Use Trial {t.index} for Voice Guide
              </button>
            </div>
          ))}
        </div>
      ) : data?.text ? (
        <div className="space-y-2">
          <pre className="rounded-lg border border-border bg-surface-2 p-3 text-xs whitespace-pre-wrap font-prose">{data.text}</pre>
          <div className="flex gap-2">
            {[1, 2, 3].map((idx) => (
              <button key={idx} type="button" disabled={busy} onClick={() => pickAct.run(idx)} className="btn-secondary text-xs">
                Use Trial {idx}
              </button>
            ))}
          </div>
        </div>
      ) : null}
    </div>
  );
}
