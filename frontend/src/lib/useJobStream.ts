import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "./api";
import { openJobStream, StreamEvent } from "./sse";

export type StreamStatus = "idle" | "queued" | "streaming" | "done" | "error" | "cancelled";

export interface StreamMeta {
  bookId: string;
  kind: string;
  chapter?: number;
}

export interface JobStreamState {
  jobId: string | null;
  status: StreamStatus;
  text: string;
  statusMessage: string;
  warnings: string[];
  artifacts: string[];
  tightenReport: { applied_cuts?: number; words_removed?: number; percent_removed?: number } | null;
  fixReport: { deduped_paragraphs?: number; rewritten_spans?: number; skipped_spans?: number } | null;
  error: string | null;
  elapsedMs: number;
  wordCount: number;
  tokensPerSec: number;
}

const INITIAL: JobStreamState = {
  jobId: null,
  status: "idle",
  text: "",
  statusMessage: "",
  warnings: [],
  artifacts: [],
  tightenReport: null,
  fixReport: null,
  error: null,
  elapsedMs: 0,
  wordCount: 0,
  tokensPerSec: 0,
};

function storeKey(meta: StreamMeta): string {
  return `novelista3.job.${meta.bookId}.${meta.kind}.${meta.chapter ?? "all"}`;
}

export function useJobStream(onDone?: (ev: StreamEvent) => void) {
  const [state, setState] = useState<JobStreamState>(INITIAL);
  const closeRef = useRef<(() => void) | null>(null);
  const startTimeRef = useRef<number>(0);
  const firstTokenTimeRef = useRef<number>(0);
  const charCountRef = useRef<number>(0);
  const metaRef = useRef<StreamMeta | null>(null);
  const onDoneRef = useRef(onDone);
  onDoneRef.current = onDone;

  const stopTimerRef = useRef<number | null>(null);

  const clearTimer = () => {
    if (stopTimerRef.current !== null) {
      window.clearInterval(stopTimerRef.current);
      stopTimerRef.current = null;
    }
  };

  useEffect(() => {
    return () => {
      closeRef.current?.();
      clearTimer();
    };
  }, []);

  const attach = useCallback((jobId: string, meta?: StreamMeta) => {
    closeRef.current?.();
    clearTimer();
    if (meta) {
      metaRef.current = meta;
      try {
        sessionStorage.setItem(storeKey(meta), jobId);
      } catch {
        /* ignore */
      }
    }
    startTimeRef.current = Date.now();
    firstTokenTimeRef.current = 0;
    charCountRef.current = 0;

    setState({
      ...INITIAL,
      jobId,
      status: "queued",
      statusMessage: "Waiting for model...",
    });

    stopTimerRef.current = window.setInterval(() => {
      setState((prev) => {
        if (prev.status !== "queued" && prev.status !== "streaming") return prev;
        const now = Date.now();
        const elapsedMs = now - startTimeRef.current;
        const genMs = firstTokenTimeRef.current ? Math.max(250, now - firstTokenTimeRef.current) : 0;
        const tokensPerSec = genMs > 0 ? Math.round(((charCountRef.current / 4) / (genMs / 1000)) * 10) / 10 : 0;
        return { ...prev, elapsedMs, tokensPerSec };
      });
    }, 350);

    closeRef.current = openJobStream(jobId, (ev) => {
      if (ev.type === "started") {
        setState((s) => ({ ...s, status: "streaming", statusMessage: "Generating..." }));
      } else if (ev.type === "token") {
        if (!firstTokenTimeRef.current) firstTokenTimeRef.current = Date.now();
        charCountRef.current += ev.text.length;
        setState((s) => {
          const next = s.text + ev.text;
          const wc = next.trim() ? next.trim().split(/\s+/).length : 0;
          return { ...s, status: "streaming", text: next, wordCount: wc };
        });
      } else if (ev.type === "status") {
        setState((s) => ({ ...s, statusMessage: ev.message }));
      } else if (ev.type === "warning") {
        setState((s) => ({ ...s, warnings: [...s.warnings, ev.message] }));
      } else if (ev.type === "artifact") {
        setState((s) => ({ ...s, artifacts: [...s.artifacts, ev.name] }));
      } else if (ev.type === "tighten" && ev.report) {
        setState((s) => ({ ...s, tightenReport: ev.report || null }));
      } else if (ev.type === "fix_tells" && ev.report) {
        setState((s) => ({ ...s, fixReport: ev.report || null }));
      } else if (ev.type === "done" || ev.type === "error" || ev.type === "cancelled") {
        clearTimer();
        if (metaRef.current) {
          try {
            sessionStorage.removeItem(storeKey(metaRef.current));
          } catch {
            /* ignore */
          }
        }
        setState((s) => ({
          ...s,
          status: ev.type,
          statusMessage: ev.type === "done" ? "Complete" : ev.type === "cancelled" ? "Cancelled" : "Error",
          error: ev.type === "error" ? ev.message : ev.type === "cancelled" ? (ev.message || null) : null,
          elapsedMs: Date.now() - startTimeRef.current,
        }));
        onDoneRef.current?.(ev);
      }
    });
  }, []);

  const resume = useCallback(
    async (meta: StreamMeta) => {
      let jid: string | null = null;
      try {
        jid = sessionStorage.getItem(storeKey(meta));
      } catch {
        jid = null;
      }
      if (!jid) return;
      try {
        const j = await api.getJob(jid);
        if (j.status === "queued" || j.status === "running") {
          attach(jid, meta);
        } else {
          sessionStorage.removeItem(storeKey(meta));
        }
      } catch {
        try {
          sessionStorage.removeItem(storeKey(meta));
        } catch {
          /* ignore */
        }
      }
    },
    [attach],
  );

  const cancel = useCallback(async () => {
    const jid = state.jobId;
    if (!jid) return;
    try {
      await api.cancelJob(jid);
    } catch {
      /* ignore */
    }
  }, [state.jobId]);

  const reset = useCallback(() => {
    closeRef.current?.();
    clearTimer();
    if (metaRef.current) {
      try {
        sessionStorage.removeItem(storeKey(metaRef.current));
      } catch {
        /* ignore */
      }
    }
    metaRef.current = null;
    setState(INITIAL);
  }, []);

  const busy = state.status === "queued" || state.status === "streaming";
  return { state, busy, start: attach, resume, cancel, reset };
}
