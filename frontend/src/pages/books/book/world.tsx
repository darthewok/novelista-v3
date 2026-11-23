import { useNavigate, useOutletContext, useParams } from "react-router-dom";
import { Book, FormatProfile } from "@/lib/api";
import { ArtifactEditor } from "@/components/ArtifactEditor";

export default function WorldPage() {
  const { id } = useParams();
  const nav = useNavigate();
  const { format } = useOutletContext<{ book: Book; format?: FormatProfile }>();
  const isOpt = format?.optional_phases?.includes("world");

  return (
    <ArtifactEditor
      bookId={id!}
      kind="world"
      title={isOpt ? "Setting & Mood Sketch (Optional)" : "World & Setting Bible"}
      subtitle={
        isOpt
          ? "Optional lightweight sensory setting, colors, and props for your picture book or short story."
          : "Geography, daily economy, social constraints, unwritten rules, and sensory palette."
      }
      generateLabel="Generate World Bible"
      nextPath={`/books/${id}/characters`}
      nextLabel="Next: Cast & Characters"
      onNavigateNext={(p) => nav(p)}
    />
  );
}
