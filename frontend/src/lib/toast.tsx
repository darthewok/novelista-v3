import React, { createContext, useCallback, useContext, useState } from "react";
import { CheckCircle2, AlertCircle, Info, X } from "lucide-react";

interface ToastItem {
  id: number;
  kind: "success" | "error" | "info";
  message: string;
}

interface ToastCtx {
  success: (msg: string) => void;
  error: (msg: string) => void;
  info: (msg: string) => void;
}

const Ctx = createContext<ToastCtx>({
  success: () => {},
  error: () => {},
  info: () => {},
});

let nextId = 1;

export function ToastProvider({ children }: { children: React.ReactNode }) {
  const [items, setItems] = useState<ToastItem[]>([]);

  const push = useCallback((kind: ToastItem["kind"], message: string) => {
    const id = nextId++;
    setItems((prev) => [...prev.slice(-4), { id, kind, message }]);
    window.setTimeout(() => {
      setItems((prev) => prev.filter((x) => x.id !== id));
    }, 4200);
  }, []);

  const dismiss = (id: number) => setItems((prev) => prev.filter((x) => x.id !== id));

  return (
    <Ctx.Provider
      value={{
        success: (m) => push("success", m),
        error: (m) => push("error", m),
        info: (m) => push("info", m),
      }}
    >
      {children}
      <div className="fixed bottom-4 right-4 z-50 flex flex-col gap-2 max-w-sm pointer-events-none">
        {items.map((t) => (
          <div
            key={t.id}
            className={`pointer-events-auto flex items-start gap-2.5 rounded-xl border px-3.5 py-2.5 text-sm shadow-lg ${
              t.kind === "error"
                ? "border-rose-500/40 bg-rose-950/95 text-rose-100"
                : t.kind === "success"
                  ? "border-emerald-500/40 bg-surface text-ink"
                  : "border-border bg-surface text-ink"
            }`}
          >
            {t.kind === "success" && <CheckCircle2 className="h-4 w-4 shrink-0 text-emerald-500 mt-0.5" />}
            {t.kind === "error" && <AlertCircle className="h-4 w-4 shrink-0 text-rose-400 mt-0.5" />}
            {t.kind === "info" && <Info className="h-4 w-4 shrink-0 text-accent mt-0.5" />}
            <span className="flex-1 leading-snug">{t.message}</span>
            <button onClick={() => dismiss(t.id)} className="opacity-60 hover:opacity-100">
              <X className="h-3.5 w-3.5" />
            </button>
          </div>
        ))}
      </div>
    </Ctx.Provider>
  );
}

export function useToast() {
  return useContext(Ctx);
}
