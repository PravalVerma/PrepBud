/**
 * Document upload (ARCHITECTURE §2.1): ask the API for a presigned URL, PUT the file
 * straight to object storage with progress, then confirm so processing starts.
 * The file never passes through the Next.js server or the API.
 */
import { api } from "@/lib/api";
import type { ConfirmUploadResponse } from "@/types/domain";

/** SECURITY_MODEL §5.2 — mirrors the backend whitelist. */
export const MAX_UPLOAD_BYTES = 50 * 1024 * 1024;
export const ACCEPTED_TYPES: Record<string, string> = {
  pdf: "application/pdf",
  txt: "text/plain",
  png: "image/png",
  jpg: "image/jpeg",
  jpeg: "image/jpeg",
};
export const ACCEPT_ATTRIBUTE = Object.keys(ACCEPTED_TYPES)
  .map((ext) => `.${ext}`)
  .join(",");

export type FileCheck = { ok: true; mimeType: string } | { ok: false; error: string };

/** The MIME type is derived from the extension (browsers report it inconsistently). */
export function checkFile(file: Pick<File, "name" | "size">): FileCheck {
  const ext = file.name.includes(".") ? file.name.split(".").pop()!.toLowerCase() : "";
  const mimeType = ACCEPTED_TYPES[ext];
  if (!mimeType) {
    return { ok: false, error: "Unsupported file type. Upload a PDF, TXT, PNG or JPEG file." };
  }
  if (file.size === 0) return { ok: false, error: "The file is empty." };
  if (file.size > MAX_UPLOAD_BYTES) return { ok: false, error: "The file is larger than 50 MB." };
  return { ok: true, mimeType };
}

export class UploadError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "UploadError";
  }
}

/** PUT with upload progress (fetch cannot report request-body progress). */
export function putWithProgress(
  url: string,
  body: Blob,
  headers: Record<string, string>,
  onProgress: (fraction: number) => void,
  signal?: AbortSignal,
): Promise<void> {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open("PUT", url);
    for (const [name, value] of Object.entries(headers)) xhr.setRequestHeader(name, value);
    xhr.upload.onprogress = (event) => {
      if (event.lengthComputable) onProgress(event.loaded / event.total);
    };
    xhr.onload = () => {
      if (xhr.status >= 200 && xhr.status < 300) {
        onProgress(1);
        resolve();
      } else {
        reject(new UploadError(`Storage rejected the upload (HTTP ${xhr.status}).`));
      }
    };
    xhr.onerror = () => reject(new UploadError("Network error while uploading the file."));
    xhr.onabort = () => reject(new UploadError("Upload cancelled."));
    signal?.addEventListener("abort", () => xhr.abort(), { once: true });
    xhr.send(body);
  });
}

export interface UploadOptions {
  subjectId?: string;
  courseId?: string;
  onProgress?: (fraction: number) => void;
  signal?: AbortSignal;
}

export async function uploadDocument(file: File, options: UploadOptions = {}): Promise<ConfirmUploadResponse> {
  const check = checkFile(file);
  if (!check.ok) throw new UploadError(check.error);
  const target = await api.requestUploadUrl({
    filename: file.name,
    mime_type: check.mimeType,
    file_size_bytes: file.size,
    ...(options.subjectId ? { subject_id: options.subjectId } : {}),
    ...(options.courseId ? { course_id: options.courseId } : {}),
  });
  await putWithProgress(
    target.upload_url,
    file,
    target.upload_headers,
    options.onProgress ?? (() => {}),
    options.signal,
  );
  return api.confirmUpload(target.document_id);
}

export function formatBytes(bytes: number | null | undefined): string {
  if (!bytes) return "—";
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}
