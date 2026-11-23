export type StreamEvent =
  | { type: "started"; kind?: string; chapter?: number }
  | { type: "token"; text: string; chapter?: number }
  | { type: "status"; message: string }
  | { type: "warning"; message: string }
  | { type: "artifact"; name: string; data?: unknown }
  | { type: "tighten"; report?: { applied_cuts?: number; words_removed?: number; percent_removed?: number } }
  | { type: "fix_tells"; report?: { deduped_paragraphs?: number; rewritten_spans?: number; skipped_spans?: number } }
  | { type: "done" }
  | { type: "error"; message: string }
  | { type: "cancelled"; message?: string };

export function openJobStream(
  jobId: string,
  onEvent: (ev: StreamEvent) => void,
): () => void {
  let es: EventSource | null = null;
  let closedCleanly = false;
  let reconnects = 0;
  let retryTimer: number | null = null;

  const fail = (message: string) => {
    if (closedCleanly) return;
    closedCleanly = true;
    es?.close();
    es = null;
    onEvent({ type: "error", message });
  };

  const connect = () => {
    if (closedCleanly) return;
    const src = new EventSource(`/api/jobs/${encodeURIComponent(jobId)}/events`);
    es = src;

    src.onmessage = (msg) => {
      try {
        const ev = JSON.parse(msg.data) as StreamEvent;
        if (ev.type === "done" || ev.type === "error" || ev.type === "cancelled") {
          closedCleanly = true;
        }
        onEvent(ev);
        if (closedCleanly) {
          src.close();
          es = null;
        }
      } catch {
        /* ignore malformed frame */
      }
    };

    src.onerror = () => {
      src.close();
      if (es === src) es = null;
      if (closedCleanly) return;

      void (async () => {
        try {
          const res = await fetch(`/api/jobs/${encodeURIComponent(jobId)}`, {
            signal: AbortSignal.timeout(8000),
          });
          if (!res.ok) {
            fail("Connection to generation stream interrupted.");
            return;
          }
          const job = (await res.json()) as { status?: string; error?: string | null };
          if (closedCleanly) return;
          if (job.status === "done") {
            closedCleanly = true;
            onEvent({ type: "done" });
            return;
          }
          if (job.status === "error") {
            fail(job.error || "Generation failed.");
            return;
          }
          if (job.status === "cancelled") {
            closedCleanly = true;
            onEvent({ type: "cancelled", message: job.error || undefined });
            return;
          }
          if ((job.status === "queued" || job.status === "running") && reconnects < 4) {
            reconnects += 1;
            retryTimer = window.setTimeout(connect, 1200);
            return;
          }
          fail("Connection to generation stream interrupted.");
        } catch {
          if (!closedCleanly && reconnects < 4) {
            reconnects += 1;
            retryTimer = window.setTimeout(connect, 1200);
          } else {
            fail("Connection to generation stream interrupted.");
          }
        }
      })();
    };
  };

  connect();

  return () => {
    closedCleanly = true;
    if (retryTimer !== null) {
      window.clearTimeout(retryTimer);
      retryTimer = null;
    }
    es?.close();
    es = null;
  };
}
