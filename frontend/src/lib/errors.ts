/** User-facing error text and retry policy shared by queries and mutations. */
import { ApiError } from "@/lib/api";

export function errorMessage(error: unknown): string {
  if (error instanceof ApiError) {
    if (error.code === "NETWORK_ERROR") return "You're offline — check your connection and try again.";
    if (error.status >= 500) {
      return `Something went wrong on our side.${error.requestId ? ` (ref ${error.requestId})` : ""}`;
    }
    return error.message;
  }
  return error instanceof Error ? error.message : "Something went wrong.";
}

/** Retry transient failures (network, 5xx, rate limiting) with backoff; never 4xx. */
export function shouldRetry(count: number, error: unknown): boolean {
  if (error instanceof ApiError && error.status >= 400 && error.status < 500 && error.status !== 429) {
    return false;
  }
  return count < 2;
}
