/** Domain entity types (docs/DOMAIN_MODEL.md, API_CONTRACT.md §3.3–3.4). */

export type DifficultyBand = "novice" | "beginner" | "intermediate" | "advanced";

export type OnboardingState = "new" | "profile_set" | "first_upload" | "first_session" | "complete";

export type MasteryLabel = "novice" | "beginner" | "intermediate" | "proficient" | "mastered";

export interface Profile {
  id: string;
  email: string;
  display_name: string | null;
  avatar_url: string | null;
  grade_level: string | null;
  difficulty_band: DifficultyBand | null;
  language: string | null;
  timezone: string | null;
  preferences: Record<string, unknown>;
  cumulative_stats: {
    total_sessions?: number;
    total_minutes?: number;
    concepts_mastered?: number;
    [key: string]: unknown;
  };
  onboarding_state: OnboardingState | null;
}

export interface ProfileUpdate {
  display_name?: string | null;
  grade_level?: string | null;
  difficulty_band?: DifficultyBand;
  language?: string;
  timezone?: string;
  preferences?: Record<string, unknown>;
  onboarding_state?: OnboardingState;
}

export interface Subject {
  id: string;
  name: string;
  description: string | null;
  icon: string | null;
  course_count: number;
  concept_count: number;
  avg_mastery: number;
  created_at: string | null;
  updated_at: string | null;
}

export interface SubjectCreate {
  name: string;
  description?: string | null;
  icon?: string | null;
}

interface CurriculumNode {
  id: string;
  name: string;
  description: string | null;
  sort_order: number | null;
  created_at: string | null;
  updated_at: string | null;
}

export interface Course extends CurriculumNode {
  subject_id: string;
}

export interface Chapter extends CurriculumNode {
  course_id: string;
}

export interface Section extends CurriculumNode {
  chapter_id: string;
}

export interface CurriculumNodeCreate {
  name: string;
  description?: string | null;
  sort_order?: number;
}

export interface HealthStatus {
  status: "healthy" | "degraded" | "unhealthy";
  version: string;
  services: Record<string, "ok" | "error" | "not_configured">;
}

// --- Content pipeline (API_CONTRACT §3.5–3.6, Phase 3) -------------------------------

export type ProcessingStatus = "pending" | "processing" | "ready" | "failed";

export type ProcessingStage =
  | "queued"
  | "downloading"
  | "extracting_text"
  | "chunking"
  | "extracting_concepts"
  | "linking_concepts"
  | "saving"
  | "indexing"
  | "retrying"
  | "complete"
  | "failed";

export interface ProcessingMetadata {
  page_count?: number;
  chunk_count?: number;
  concept_count?: number;
  new_concept_count?: number;
  relationship_count?: number;
  ocr_pages?: number[];
  stage?: ProcessingStage;
  progress?: number;
  error?: { code: string; message: string };
  embedding_status?: "complete" | "failed" | "skipped";
  warnings?: string[];
}

export interface DocumentSummary {
  id: string;
  title: string;
  source_filename: string;
  mime_type: string;
  file_size_bytes: number | null;
  processing_status: ProcessingStatus;
  processing_metadata: ProcessingMetadata;
  subject_id: string | null;
  course_id: string | null;
  uploaded_at: string | null;
  processed_at: string | null;
}

export interface DocumentDetail extends DocumentSummary {
  section_count: number;
  concepts: { id: string; name: string; section_count: number }[];
}

export interface UploadUrlRequest {
  filename: string;
  mime_type: string;
  file_size_bytes: number;
  subject_id?: string;
  course_id?: string;
  title?: string;
}

export interface UploadUrlResponse {
  upload_url: string;
  document_id: string;
  s3_key: string;
  expires_in_seconds: number;
  upload_headers: Record<string, string>;
}

export interface ConfirmUploadResponse {
  document_id: string;
  processing_status: ProcessingStatus;
  task_id: string | null;
}

export interface MasterySummary {
  level: number;
  label: MasteryLabel;
  last_assessed_at: string | null;
  next_review_at: string | null;
}

export interface ConceptListItem {
  id: string;
  name: string;
  description: string | null;
  difficulty_estimate: number;
  subject_id: string | null;
  chapter_id: string | null;
  mastery: MasterySummary;
  prerequisite_count: number;
  document_count: number;
}

export interface ConceptLink {
  id: string;
  name: string;
  mastery_level: number;
}

export type RelationshipType = "prerequisite" | "related" | "generalisation" | "specialisation";

export interface ConceptDetail {
  id: string;
  name: string;
  description: string | null;
  difficulty_estimate: number;
  subject_id: string | null;
  chapter_id: string | null;
  section_id: string | null;
  mastery: MasterySummary & {
    confidence: number;
    attempt_count: number;
    correct_count: number;
    streak: number;
    history: { date: string; mastery: number; event: string | null }[];
  };
  prerequisites: ConceptLink[];
  dependents: ConceptLink[];
  related_concepts: { id: string; name: string; relationship: RelationshipType }[];
  misconceptions: { id: string; name: string; status: string }[];
  documents: { id: string; title: string; section_count: number }[];
  created_at: string | null;
  metadata: { origin?: string; aliases?: string[] };
}

export interface ConceptGraph {
  nodes: { id: string; name: string; mastery: number; is_target: boolean }[];
  edges: { source: string; target: string; type: RelationshipType }[];
}

export interface ConceptFilters {
  subject_id?: string;
  document_id?: string;
  search?: string;
  mastery_below?: number;
  sort?: "name" | "difficulty_estimate" | "mastery_level" | "created_at";
  order?: "asc" | "desc";
}

export type SearchMode = "hybrid" | "keyword" | "semantic";

export interface SearchHit {
  section_id: string;
  document_id: string;
  document_title: string;
  section_index: number;
  heading: string | null;
  page_numbers: number[];
  snippet: string;
  score: number;
  matched_by: ("keyword" | "semantic")[];
}
