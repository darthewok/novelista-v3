import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { AlertTriangle, X } from "lucide-react";
import { api } from "@/lib/api";

export function SettingsTestBanner() {
  const [dismissedKey, setDismissedKey] = useState<string | null>(null);
  const { data } = useQuery({
    queryKey: ["connection-test"],
    queryFn: api.testSettings,
    staleTime: 60_000,
    retry: false,
  });

  const issue = !data
    ? null
    : !data.ok
      ? data.error || `Cannot reach ${data.provider}`
      : !data.models_present
        ? "One or more task models were not found on the provider."
        : data.warnings?.[0] || null;

  useEffect(() => {
    if (!issue) setDismissedKey(null);
  }, [issue]);

  if (!issue || dismissedKey === issue) return null;

  return (
    <div className="border-b border-amber-500/30 bg-amber-500/10 px-4 py-2 text-xs text-amber-800 dark:text-amber-200">
      <div className="mx-auto flex max-w-7xl items-center justify-between gap-3">
        <div className="flex items-center gap-2">
          <AlertTriangle className="h-3.5 w-3.5 shrink-0" />
          <span>{issue}</span>
          <Link to="/settings" className="underline font-semibold hover:opacity-80">
            Open Settings →
          </Link>
        </div>
        <button type="button" onClick={() => setDismissedKey(issue)} className="opacity-70 hover:opacity-100" aria-label="Dismiss">
          <X className="h-3.5 w-3.5" />
        </button>
      </div>
    </div>
  );
}
