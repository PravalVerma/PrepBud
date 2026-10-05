"use client";

import { useQueryClient } from "@tanstack/react-query";
import { type ChangeEvent, type DragEvent, useId, useRef, useState } from "react";

import { Card, CardHeader } from "@/components/ui/card";
import { SelectField } from "@/components/ui/field";
import { documentsKey } from "@/hooks/use-documents";
import { useSubjects } from "@/hooks/use-subjects";
import { ApiError } from "@/lib/api";
import { ACCEPT_ATTRIBUTE, checkFile, formatBytes, uploadDocument } from "@/lib/upload";
import { cn } from "@/lib/utils";

type Phase = "uploading" | "queued" | "error";

interface UploadItem {
  key: string;
  name: string;
  size: number;
  progress: number;
  phase: Phase;
  error?: string;
}

function errorMessage(err: unknown): string {
  if (err instanceof ApiError) {
    if (err.code === "RATE_LIMITED") return "Upload limit reached — try again later.";
    return err.fieldErrors.file_size_bytes ?? err.message;
  }
  return err instanceof Error ? err.message : "Upload failed.";
}

export function UploadPanel() {
  const queryClient = useQueryClient();
  const subjects = useSubjects({ page: 1, per_page: 100 });
  const [subjectId, setSubjectId] = useState("");
  const [items, setItems] = useState<UploadItem[]>([]);
  const [dragging, setDragging] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);
  const inputId = useId();

  const update = (key: string, patch: Partial<UploadItem>) =>
    setItems((all) => all.map((i) => (i.key === key ? { ...i, ...patch } : i)));

  async function start(file: File) {
    const key = `${file.name}-${file.size}-${Date.now()}-${Math.random()}`;
    const check = checkFile(file);
    setItems((all) => [
      { key, name: file.name, size: file.size, progress: 0, phase: check.ok ? "uploading" : "error", error: check.ok ? undefined : check.error },
      ...all,
    ]);
    if (!check.ok) return;
    try {
      await uploadDocument(file, {
        subjectId: subjectId || undefined,
        onProgress: (progress) => update(key, { progress }),
      });
      update(key, { phase: "queued", progress: 1 });
    } catch (err) {
      update(key, { phase: "error", error: errorMessage(err) });
    } finally {
      void queryClient.invalidateQueries({ queryKey: documentsKey });
    }
  }

  function addFiles(files: FileList | null) {
    for (const file of Array.from(files ?? [])) void start(file);
  }

  function onDrop(event: DragEvent<HTMLElement>) {
    event.preventDefault();
    setDragging(false);
    addFiles(event.dataTransfer.files);
  }

  function onChange(event: ChangeEvent<HTMLInputElement>) {
    addFiles(event.target.files);
    event.target.value = "";
  }

  const subjectOptions = [
    { value: "", label: "No subject" },
    ...(subjects.data?.data ?? []).map((s) => ({ value: s.id, label: s.name })),
  ];

  return (
    <Card data-testid="upload-panel">
      <CardHeader
        title="Upload study material"
        description="PDF, text or photos of notes (PNG/JPEG), up to 50 MB. We extract the concepts it teaches."
      />
      <div className="mb-4 max-w-xs">
        <SelectField
          label="Subject (optional)"
          value={subjectId}
          onChange={(e) => setSubjectId(e.target.value)}
          options={subjectOptions}
        />
      </div>

      <label
        htmlFor={inputId}
        data-testid="dropzone"
        onDragOver={(e) => {
          e.preventDefault();
          setDragging(true);
        }}
        onDragLeave={() => setDragging(false)}
        onDrop={onDrop}
        className={cn(
          "flex cursor-pointer flex-col items-center justify-center gap-2 rounded-xl border-2 border-dashed px-6 py-10 text-center transition-colors",
          dragging ? "border-brand-500 bg-brand-50" : "border-slate-300 hover:border-brand-500",
        )}
      >
        <span aria-hidden className="text-3xl text-brand-600">
          ⇪
        </span>
        <span className="text-sm font-medium text-slate-800">Drop files here or click to browse</span>
        <span className="text-xs text-slate-500">PDF · TXT · PNG · JPEG — max 50 MB</span>
        <input
          ref={inputRef}
          id={inputId}
          type="file"
          multiple
          accept={ACCEPT_ATTRIBUTE}
          onChange={onChange}
          className="sr-only"
          aria-label="Choose files to upload"
        />
      </label>

      {items.length > 0 && (
        <ul className="mt-4 space-y-2" aria-label="Uploads">
          {items.map((item) => (
            <li key={item.key} className="rounded-lg bg-slate-50 px-3 py-2" data-testid="upload-item">
              <div className="flex items-center justify-between gap-3 text-sm">
                <span className="truncate font-medium text-slate-800">{item.name}</span>
                <span className="shrink-0 text-xs text-slate-500">
                  {item.phase === "uploading" && `${Math.round(item.progress * 100)}%`}
                  {item.phase === "queued" && "Uploaded — processing"}
                  {item.phase === "error" && "Failed"}
                  {" · "}
                  {formatBytes(item.size)}
                </span>
              </div>
              {item.phase === "error" ? (
                <p role="alert" className="mt-1 text-xs text-red-600">
                  {item.error}
                </p>
              ) : (
                <div
                  role="progressbar"
                  aria-label={`Uploading ${item.name}`}
                  aria-valuemin={0}
                  aria-valuemax={100}
                  aria-valuenow={Math.round(item.progress * 100)}
                  className="mt-2 h-1.5 overflow-hidden rounded-full bg-slate-200"
                >
                  <div
                    className={cn("h-full rounded-full transition-all", item.phase === "queued" ? "bg-emerald-500" : "bg-brand-600")}
                    style={{ width: `${Math.round(item.progress * 100)}%` }}
                  />
                </div>
              )}
            </li>
          ))}
        </ul>
      )}
    </Card>
  );
}
