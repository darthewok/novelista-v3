import { useNavigate, useParams } from "react-router-dom";
import { ArtifactEditor } from "@/components/ArtifactEditor";

export default function CharactersPage() {
  const { id } = useParams();
  const nav = useNavigate();

  return (
    <ArtifactEditor
      bookId={id!}
      kind="characters"
      title="Cast & Voice Contrasts"
      subtitle="Goals, blind spots, relational friction, and speech signatures (analytical voice rules — no pre-written chapter quotes to parrot)."
      generateLabel="Generate Cast"
      nextPath={`/books/${id}/voice`}
      nextLabel="Next: Voice Lab"
      onNavigateNext={(p) => nav(p)}
    />
  );
}
