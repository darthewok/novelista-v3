import { AlertCircle } from "lucide-react";

export function ErrorLine({ error }: { error: string | null | undefined }) {
  if (!error) return null;
  return (
    <div className="flex items-start gap-2 rounded-lg border border-rose-500/40 bg-rose-500/10 px-3 py-2 text-sm text-rose-600 dark:text-rose-300">
      <AlertCircle className="h-4 w-4 shrink-0 mt-0.5" />
      <span>{error}</span>
    </div>
  );
}
