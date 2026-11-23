import { useNavigate, useParams } from "react-router-dom";
import { useQueryClient } from "@tanstack/react-query";
import { ArtifactEditor } from "@/components/ArtifactEditor";
import { VoiceTrials } from "@/components/VoiceTrials";

export default function VoicePage() {
  const { id } = useParams();
  const nav = useNavigate();
  const qc = useQueryClient();

  return (
    <div className="space-y-6">
      <VoiceTrials
        bookId={id!}
        onVoiceGenerated={() => {
          qc.invalidateQueries({ queryKey: ["artifact", id, "voice"] });
        }}
      />
      <ArtifactEditor
        bookId={id!}
        kind="voice"
        title="Step B — Project Voice Guide"
        subtitle="Part 1 protects universal craft; Part 2 encodes your chosen register, rhythm rules, and unrelated style specimens."
        generateLabel="Build Default Voice Guide"
        nextPath={`/books/${id}/canon`}
        nextLabel="Next: Canon & Memory"
        onNavigateNext={(p) => nav(p)}
      />
    </div>
  );
}
