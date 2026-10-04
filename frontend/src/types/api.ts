/** Response envelope types mirroring docs/API_CONTRACT.md §1–2, §4. */

export type ErrorCode =
  | "VALIDATION_ERROR"
  | "AUTHENTICATION_REQUIRED"
  | "FORBIDDEN"
  | "RESOURCE_NOT_FOUND"
  | "METHOD_NOT_ALLOWED"
  | "CONFLICT"
  | "UNPROCESSABLE_ENTITY"
  | "RATE_LIMITED"
  | "INTERNAL_ERROR"
  | "SERVICE_UNAVAILABLE";

export interface Meta {
  request_id: string | null;
  timestamp: string;
}

export interface Pagination {
  total: number;
  page: number;
  per_page: number;
  total_pages: number;
}

export interface Envelope<T> {
  data: T;
  meta: Meta;
}

export interface PaginatedEnvelope<T> {
  data: T[];
  meta: Meta & { pagination: Pagination };
}

export interface FieldError {
  loc: (string | number)[];
  message: string;
  type: string;
}

export interface ErrorEnvelope {
  error: {
    code: ErrorCode;
    message: string;
    details: { errors?: FieldError[]; field?: string; [key: string]: unknown };
  };
  meta: Meta;
}

export interface PageParams {
  page?: number;
  per_page?: number;
}
