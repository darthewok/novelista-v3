import { Link, Navigate, Route, Routes, useOutletContext, useParams } from "react-router-dom";
import { Layout } from "./Layout";
import { Book } from "./lib/api";
import DashboardPage from "./pages/dashboard";
import SettingsPage from "./pages/settings";
import NewBookPage from "./pages/books/new";
import NewObsidianBookPage from "./pages/books/obsidian";
import BookLayout from "./pages/books/book/layout";
import VaultStudioPage from "./pages/books/book/vault";
import SeedPage from "./pages/books/book/seed";
import WorldPage from "./pages/books/book/world";
import CharactersPage from "./pages/books/book/characters";
import VoicePage from "./pages/books/book/voice";
import CanonPage from "./pages/books/book/canon";
import OutlinePage from "./pages/books/book/outline";
import ChaptersIndexPage from "./pages/books/book/chapters";
import ChapterPage from "./pages/books/book/chapter";
import AuditPage from "./pages/books/book/audit";
import ExportPage from "./pages/books/book/export";

function KeyedChapterRoute() {
  const { id, n } = useParams();
  return <ChapterPage key={`${id}-${n}`} />;
}

function BookIndexRedirect() {
  const { book } = useOutletContext<{ book: Book }>();
  return <Navigate to={book?.source_mode === "obsidian" ? "vault" : "seed"} replace />;
}

function NotFoundPage() {
  return (
    <div className="card p-10 text-center space-y-3 max-w-md mx-auto mt-12">
      <h1 className="text-xl font-semibold">Page not found</h1>
      <p className="text-sm text-muted">That workspace route does not exist.</p>
      <Link to="/" className="btn-primary inline-flex">
        Back to Library
      </Link>
    </div>
  );
}

export default function App() {
  return (
    <Routes>
      <Route element={<Layout />}>
        <Route path="/" element={<DashboardPage />} />
        <Route path="/settings" element={<SettingsPage />} />
        <Route path="/books/new" element={<NewBookPage />} />
        <Route path="/books/obsidian" element={<NewObsidianBookPage />} />
        <Route path="/books/:id" element={<BookLayout />}>
          <Route index element={<BookIndexRedirect />} />
          <Route path="vault" element={<VaultStudioPage />} />
          <Route path="seed" element={<SeedPage />} />
          <Route path="world" element={<WorldPage />} />
          <Route path="characters" element={<CharactersPage />} />
          <Route path="voice" element={<VoicePage />} />
          <Route path="canon" element={<CanonPage />} />
          <Route path="outline" element={<OutlinePage />} />
          <Route path="chapters" element={<ChaptersIndexPage />} />
          <Route path="chapters/:n" element={<KeyedChapterRoute />} />
          <Route path="audit" element={<AuditPage />} />
          <Route path="export" element={<ExportPage />} />
        </Route>
        <Route path="*" element={<NotFoundPage />} />
      </Route>
    </Routes>
  );
}
