import { useCallback, useState } from "react";

export function useAction<TArgs extends unknown[], TRet>(fn: (...args: TArgs) => Promise<TRet>) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const run = useCallback(
    async (...args: TArgs): Promise<TRet | undefined> => {
      setBusy(true);
      setError(null);
      try {
        return await fn(...args);
      } catch (e) {
        setError(e instanceof Error ? e.message : String(e));
        return undefined;
      } finally {
        setBusy(false);
      }
    },
    [fn],
  );

  const clearError = useCallback(() => setError(null), []);
  return { run, busy, error, clearError };
}
