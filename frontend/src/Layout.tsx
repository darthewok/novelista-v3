import { Link, Outlet, useLocation } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { BookMarked, Settings, Sun, Moon, ScrollText, Loader2 } from "lucide-react";
import { api } from "@/lib/api";
import { ThemeId, useTheme } from "@/lib/theme";
import { SettingsTestBanner } from "./components/SettingsTestBanner";

export function Layout() {
  const { theme, setTheme } = useTheme();
  const loc = useLocation();

  const { data: activeJobs } = useQuery({
    queryKey: ["active-jobs"],
    queryFn: () => api.getActiveJobs(),
    refetchInterval: (query) => (query.state.data?.length ? 1500 : 5000),
  });

  const running = activeJobs?.[0];

  const themeBtn = (id: ThemeId, label: string, Icon: typeof Sun) => (
    <button
      type="button"
      onClick={() => setTheme(id)}
      title={`Switch to ${label} theme`}
      className={`rounded-md px-2 py-1 text-xs inline-flex items-center gap-1 transition ${
        theme === id ? "bg-surface text-ink shadow-sm font-medium" : "text-muted hover:text-ink"
      }`}
    >
      <Icon className="h-3.5 w-3.5" />
      <span className="hidden sm:inline">{label}</span>
    </button>
  );

  return (
    <div className="min-h-screen flex flex-col bg-bg text-ink">
      <header className="sticky top-0 z-30 border-b border-border bg-surface/90 backdrop-blur">
        <div className="mx-auto flex max-w-7xl items-center justify-between gap-3 px-4 py-2.5">
          <div className="flex items-center gap-4">
            <Link to="/" className="flex items-center gap-2 font-semibold text-base tracking-tight text-ink hover:text-accent transition">
              <span className="inline-flex h-7 w-7 items-center justify-center rounded-lg bg-accent text-accent-fg">
                <BookMarked className="h-4 w-4" />
              </span>
              <span>Novelista</span>
              <span className="rounded bg-accent-soft px-1.5 py-0.5 text-[10px] font-bold uppercase text-accent">v3</span>
            </Link>
            <Link
              to="/"
              className={`text-xs font-medium ${loc.pathname === "/" ? "text-accent" : "text-muted hover:text-ink"}`}
            >
              Library
            </Link>
          </div>

          <div className="flex items-center gap-2.5">
            {running && (
              <Link
                to={running.chapter ? `/books/${running.book_id}/chapters/${running.chapter}` : `/books/${running.book_id}`}
                className="inline-flex items-center gap-1.5 rounded-full border border-accent/40 bg-accent-soft px-3 py-1 text-xs font-medium text-accent"
              >
                <Loader2 className="h-3 w-3 animate-spin" />
                <span>{running.kind.replace(/_/g, " ")}</span>
              </Link>
            )}

            <div className="inline-flex items-center rounded-lg border border-border bg-surface-2 p-0.5">
              {themeBtn("paper", "Paper", ScrollText)}
              {themeBtn("daylight", "Day", Sun)}
              {themeBtn("ink", "Ink", Moon)}
            </div>

            <Link
              to="/settings"
              className={`btn-secondary text-xs py-1.5 ${loc.pathname === "/settings" ? "border-accent text-accent" : ""}`}
            >
              <Settings className="h-3.5 w-3.5" />
              <span>Settings</span>
            </Link>
          </div>
        </div>
        <SettingsTestBanner />
      </header>

      <main className="mx-auto w-full max-w-7xl flex-1 px-4 py-6">
        <Outlet />
      </main>
    </div>
  );
}
