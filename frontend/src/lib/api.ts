/**
 * Browser API client. Calls the same-origin backend-for-frontend route
 * (`/api/backend/*`), which attaches the user's token server-side, and unwraps
 * the API_CONTRACT envelope.
 */
import type {
  Envelope,
  ErrorCode,
  ErrorEnvelope,
  FieldError,
  PageParams,
  PaginatedEnvelope,
} from "@/types/api";
import type {
  Chapter,
  ConceptDetail,
  ConceptFilters,
  ConceptGraph,
  ConceptListItem,
  ConfirmUploadResponse,
  Course,
  CurriculumNodeCreate,
  DocumentDetail,
  DocumentSummary,
  ProcessingStatus,
  Profile,
  ProfileUpdate,
  SearchHit,
  SearchMode,
  Section,
  Subject,
  SubjectCreate,
  UploadUrlRequest,
  UploadUrlResponse,
} from "@/types/domain";

export const API_BASE = "/api/backend";

export class ApiError extends Error {
  readonly status: number;
  readonly code: ErrorCode | "NETWORK_ERROR";
  readonly details: ErrorEnvelope["error"]["details"];
  readonly requestId: string | null;

  constructor(
    status: number,
    code: ApiError["code"],
    message: string,
    details: ErrorEnvelope["error"]["details"] = {},
    requestId: string | null = null,
  ) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
    this.details = details;
    this.requestId = requestId;
  }

  /** Validation messages keyed by field name (last element of `loc`). */
  get fieldErrors(): Record<string, string> {
    const out: Record<string, string> = {};
    for (const e of (this.details.errors ?? []) as FieldError[]) {
      const field = e.loc.filter((p) => p !== "body").at(-1);
      if (field !== undefined) out[String(field)] = e.message.replace(/^Value error, /, "");
    }
    if (typeof this.details.field === "string") out[this.details.field] = this.message;
    return out;
  }
}

type QueryValue = string | number | undefined | null;

/** Query string from page params plus optional filters (empty values are skipped). */
export function query(params?: object): string {
  if (!params) return "";
  const qs = new URLSearchParams();
  for (const [key, value] of Object.entries(params) as [string, QueryValue][]) {
    if (value !== undefined && value !== null && value !== "") qs.set(key, String(value));
  }
  const s = qs.toString();
  return s ? `?${s}` : "";
}

export async function request<T>(method: string, path: string, body?: unknown): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`${API_BASE}${path}`, {
      method,
      headers: body === undefined ? { Accept: "application/json" } : {
        Accept: "application/json",
        "Content-Type": "application/json",
      },
      body: body === undefined ? undefined : JSON.stringify(body),
      credentials: "same-origin",
      cache: "no-store",
    });
  } catch {
    throw new ApiError(0, "NETWORK_ERROR", "Could not reach the server. Check your connection.");
  }

  if (response.status === 204) return undefined as T;

  let payload: unknown = null;
  try {
    payload = await response.json();
  } catch {
    // fall through with null payload
  }

  if (!response.ok) {
    const err = (payload as ErrorEnvelope | null)?.error;
    throw new ApiError(
      response.status,
      err?.code ?? (response.status >= 500 ? "INTERNAL_ERROR" : "VALIDATION_ERROR"),
      err?.message ?? `Request failed (${response.status})`,
      err?.details ?? {},
      (payload as ErrorEnvelope | null)?.meta?.request_id ?? response.headers.get("x-request-id"),
    );
  }
  return payload as T;
}

async function data<T>(method: string, path: string, body?: unknown): Promise<T> {
  return (await request<Envelope<T>>(method, path, body)).data;
}

export const api = {
  getProfile: () => data<Profile>("GET", "/profile"),
  updateProfile: (body: ProfileUpdate) => data<Profile>("PATCH", "/profile", body),

  listSubjects: (params?: PageParams) =>
    request<PaginatedEnvelope<Subject>>("GET", `/subjects${query(params)}`),
  getSubject: (id: string) => data<Subject>("GET", `/subjects/${id}`),
  createSubject: (body: SubjectCreate) => data<Subject>("POST", "/subjects", body),
  updateSubject: (id: string, body: Partial<SubjectCreate>) =>
    data<Subject>("PATCH", `/subjects/${id}`, body),
  deleteSubject: (id: string) => request<void>("DELETE", `/subjects/${id}`),

  listCourses: (subjectId: string, params?: PageParams) =>
    request<PaginatedEnvelope<Course>>("GET", `/subjects/${subjectId}/courses${query(params)}`),
  createCourse: (subjectId: string, body: CurriculumNodeCreate) =>
    data<Course>("POST", `/subjects/${subjectId}/courses`, body),
  listChapters: (courseId: string, params?: PageParams) =>
    request<PaginatedEnvelope<Chapter>>("GET", `/courses/${courseId}/chapters${query(params)}`),
  createChapter: (courseId: string, body: CurriculumNodeCreate) =>
    data<Chapter>("POST", `/courses/${courseId}/chapters`, body),
  listSections: (chapterId: string, params?: PageParams) =>
    request<PaginatedEnvelope<Section>>("GET", `/chapters/${chapterId}/sections${query(params)}`),
  createSection: (chapterId: string, body: CurriculumNodeCreate) =>
    data<Section>("POST", `/chapters/${chapterId}/sections`, body),

  // Documents (API_CONTRACT §3.5)
  requestUploadUrl: (body: UploadUrlRequest) =>
    data<UploadUrlResponse>("POST", "/documents/upload-url", body),
  confirmUpload: (documentId: string) =>
    data<ConfirmUploadResponse>("POST", `/documents/${documentId}/confirm-upload`),
  listDocuments: (
    params?: PageParams & { status?: ProcessingStatus; subject_id?: string; course_id?: string },
  ) => request<PaginatedEnvelope<DocumentSummary>>("GET", `/documents${query(params)}`),
  getDocument: (id: string) => data<DocumentDetail>("GET", `/documents/${id}`),
  deleteDocument: (id: string) => request<void>("DELETE", `/documents/${id}`),

  // Concepts (API_CONTRACT §3.6)
  listConcepts: (params?: PageParams & ConceptFilters) =>
    request<PaginatedEnvelope<ConceptListItem>>("GET", `/concepts${query(params)}`),
  getConcept: (id: string) => data<ConceptDetail>("GET", `/concepts/${id}`),
  getConceptGraph: (id: string, depth = 1) =>
    data<ConceptGraph>("GET", `/concepts/${id}/graph${query({ depth })}`),

  // Hybrid search over document sections
  search: (
    params: PageParams & {
      q: string;
      mode?: SearchMode;
      subject_id?: string;
      document_id?: string;
      concept_id?: string;
    },
  ) => request<PaginatedEnvelope<SearchHit>>("GET", `/search${query(params)}`),
};
