export interface OptionMeta {
  value: string;
  label: string;
  desc: string;
  group?: string;
}

export const FORMAT_CARDS = [
  {
    value: "picture_book",
    label: "Picture Book",
    badge: "Ages 3–7 · 12 spreads · ~550w",
    desc: "Read-aloud spreads with illustration prompts and square 8.5x8.5 KDP PDF export.",
    defaultAudience: "picture_book_3_7",
    defaultUnits: 12,
    defaultWords: 550,
    unitLabel: "Spread",
  },
  {
    value: "early_reader",
    label: "Early Reader",
    badge: "Ages 5–8 · 6 chapters · ~2,400w",
    desc: "Short lively chapters with simple syntax and optional spread illustrations.",
    defaultAudience: "early_reader_5_8",
    defaultUnits: 6,
    defaultWords: 2400,
    unitLabel: "Chapter",
  },
  {
    value: "chapter_book",
    label: "Middle Grade / Chapter Book",
    badge: "Ages 8–12 · 10 chapters · ~18k w",
    desc: "Adventurous multi-chapter stories calibrated to young readers without adult grimdark.",
    defaultAudience: "middle_grade_8_12",
    defaultUnits: 10,
    defaultWords: 18000,
    unitLabel: "Chapter",
  },
  {
    value: "short_story",
    label: "Short Story",
    badge: "1–5 parts · ~4,500w",
    desc: "Tight unity of pressure, brisk opening in motion, optional lightweight setting.",
    defaultAudience: "adult",
    defaultUnits: 3,
    defaultWords: 4500,
    unitLabel: "Part",
  },
  {
    value: "novella",
    label: "Novella",
    badge: "6–12 chapters · ~18k w",
    desc: "Focused multi-chapter narrative arc with scene cards, continuity ledger, and polish.",
    defaultAudience: "adult",
    defaultUnits: 7,
    defaultWords: 18000,
    unitLabel: "Chapter",
  },
  {
    value: "novel",
    label: "Full Novel",
    badge: "16–40 chapters · ~65k+ w",
    desc: "Full world bible, character voice matrix, foreshadowing ledger, scene plans, and audit.",
    defaultAudience: "adult",
    defaultUnits: 20,
    defaultWords: 65000,
    unitLabel: "Chapter",
  },
] as const;

export const AUDIENCE_OPTIONS: OptionMeta[] = [
  { value: "picture_book_3_7", label: "Picture Book (3–7)", desc: "Read-aloud clarity, Grades 0–3.8" },
  { value: "early_reader_5_8", label: "Early Reader (5–8)", desc: "Direct action, Grades 1–5" },
  { value: "middle_grade_8_12", label: "Middle Grade (8–12)", desc: "Adventurous, Grades 3–7.5" },
  { value: "young_adult", label: "Young Adult (13–18)", desc: "Sharp voice & stakes, Grades 4.5–10.5" },
  { value: "adult", label: "Adult Trade", desc: "Layered subtext & mature complexity" },
];

export const POV_OPTIONS: OptionMeta[] = [
  { value: "third_limited", label: "Third Limited", desc: "Anchored inside one character per scene" },
  { value: "first_person", label: "First Person ('I')", desc: "Intimate narrator voice & bias" },
  { value: "third_omniscient", label: "Third Omniscient", desc: "Cohesive storyteller observing all" },
  { value: "second_person", label: "Second Person ('You')", desc: "Immediate direct address" },
];

export const TENSE_OPTIONS: OptionMeta[] = [
  { value: "past", label: "Past Tense", desc: "Classic literary past ('walked, said')" },
  { value: "present", label: "Present Tense", desc: "Immediate present ('walks, says')" },
];

export const STYLE_OPTIONS: OptionMeta[] = [
  { value: "storybook_warm", group: "Children & Fable", label: "Warm Storybook", desc: "Gentle read-aloud rhythm, affectionate sensory charm" },
  { value: "whimsical_fable", group: "Children & Fable", label: "Whimsical Fable", desc: "Spry playful phrasing, wry surprise (Dahl / Snicket)" },
  { value: "middle_grade_adventure", group: "Children & Fable", label: "Middle-Grade Adventure", desc: "Brisk camaraderie, wonder, witty problem-solving" },
  { value: "literary_contemporary", group: "Literary & Character", label: "Literary Contemporary", desc: "Precise domestic detail, quiet emotional accuracy" },
  { value: "historical_immersive", group: "Literary & Character", label: "Historical Immersive", desc: "Authentic trades, weather, tools, period-clean idiom" },
  { value: "poetic_lyrical", group: "Literary & Character", label: "Lyrical Literary", desc: "Fresh concrete imagery rooted in place and season" },
  { value: "cozy_mystery", group: "Mystery & Romance", label: "Cozy Mystery", desc: "Clever puzzle clues, quirky community, dry wit" },
  { value: "romantic_banter", group: "Mystery & Romance", label: "Romantic Banter", desc: "Sparkling dialogue, tenderness, emotional timing" },
  { value: "domestic_suspense", group: "Mystery & Romance", label: "Domestic Suspense", desc: "Suburban cracks, small lies, relatable tension" },
  { value: "psychological_thriller", group: "Mystery & Romance", label: "Psychological Thriller", desc: "Creeping social dread, sharp behavioral observation" },
  { value: "gothic_atmospheric", group: "Mystery & Romance", label: "Atmospheric Gothic", desc: "Restrained uncanny mood, brooding architecture" },
  { value: "high_epic", group: "Speculative & Genre", label: "Mythic Epic Fantasy", desc: "Lived-in custom, ancient history, costly oaths" },
  { value: "cyberpunk_noir", group: "Speculative & Genre", label: "Cyberpunk Noir", desc: "Corporate paperwork meets street hardware, cool eye" },
  { value: "anthropological_sf", group: "Speculative & Genre", label: "Anthropological SF", desc: "Ecology, ritual, and human consequence" },
  { value: "hard_sf_clean", group: "Speculative & Genre", label: "Clean Hard SF", desc: "Engineering constraints, clear spatial problem-solving" },
  { value: "satirical_wit", group: "Speculative & Genre", label: "Satirical Wit", desc: "Bureaucratic absurdity and sharp comic timing" },
  { value: "visceral_grit", group: "Speculative & Genre", label: "Gritty Realism", desc: "Mud, cold, blunt speech, zero padding" },
  { value: "pulp_kinetic", group: "Speculative & Genre", label: "Kinetic Noir", desc: "Fast dialogue, sharp physical beats (Leonard)" },
  { value: "custom", group: "Custom", label: "Custom Author Voice", desc: "Define your own influences & tonal brief below" },
];

export const PROVIDER_META = [
  { key: "ollama", label: "Ollama (Local)", needsKey: false, defaultUrl: "http://localhost:11434" },
  { key: "lmstudio", label: "LM Studio (Local)", needsKey: false, defaultUrl: "http://localhost:1234/v1" },
  { key: "anthropic", label: "Anthropic (Claude)", needsKey: true, defaultUrl: "" },
  { key: "openai", label: "OpenAI", needsKey: true, defaultUrl: "https://api.openai.com/v1" },
  { key: "gemini", label: "Google Gemini", needsKey: true, defaultUrl: "https://generativelanguage.googleapis.com/v1beta" },
  { key: "openrouter", label: "OpenRouter", needsKey: true, defaultUrl: "https://openrouter.ai/api/v1" },
] as const;

export const TASK_LABELS: Record<string, string> = {
  seed: "Seed Ideas",
  foundation: "World & Cast",
  voice: "Voice Calibration",
  canon: "Canon Extractor",
  outline: "Book Outline",
  scenes: "Scene Cards",
  draft: "Chapter Drafting",
  revise: "Prose Revision",
  editor: "Acquiring Editor",
  summary: "Continuity Summary",
  audit: "Whole-Book Audit",
  kdp: "KDP Marketing Kit",
};
