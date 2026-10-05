"use client";

import Link from "next/link";
import { useEffect, useRef, useState } from "react";

import { Button } from "@/components/ui/button";
import { Card, CardHeader } from "@/components/ui/card";
import { Alert, Badge, Skeleton } from "@/components/ui/feedback";
import { useDeleteDocument, useDocuments, useRetryDocument } from "@/hooks/use-documents";
import { formatBytes } from "@/lib/upload";
import { cn, formatDate } from "@/lib/utils";
import { toast } from "@/stores/toast-store";
import type { DocumentSummary, ProcessingStage, ProcessingStatus } from "@/types/domain";

const STATUS_STYLE: Record<ProcessingStatus, string> = {
  pending: "bg-slate-100 text-slate-600",
  processing: "bg-amber-50 text-amber-700",
  ready: "bg-emerald-50 text-emerald-700",
  failed: "bg-red-50 text-red-700",
};

const STATUS_LABEL: Record<ProcessingStatus, string> = {
  pending: "Waiting for upload",
  processing: "Processing",
  ready: "Ready",
  failed: "Failed",
};

export const STAGE_LABEL: Record<ProcessingStage, string> = {
  queued: "Queued",
  downloading: "Fetching file",
  extracting_text: "Reading text",
  chunking: "Splitting into sections",
  extracting_concepts: "Finding concepts",
  linking_concepts: "Linking concepts",
  saving: "Saving",
  indexing: "Indexing for search",
  retrying: "Retrying shortly",
  complete: "Complete",
  failed: "Failed",
};

export function StatusBadge({ status }: { status: ProcessingStatus }) {
  return (
    <Badge className={STATUS_STYLE[status]} data-testid="document-status">
      {STATUS_LABEL[status]}
    </Badge>
  );
}

function DocumentRow({ doc }: { doc: DocumentSummary }) {
  const remove = useDeleteDocument();
  const retry = useRetryDocument();
  const [confirming, setConfirming] = useState(false);
  const meta = doc.processing_metadata;
  const progress = Math.round((meta.progress ?? 0) * 100);

  return (
    <li className="py-4" data-testid="document-row" data-status={doc.processing_status}>
      <div className="flex flex-wrap items-start gap-3">
        <div className="min-w-0 flex-1">
          <p className="truncate text-sm font-medium text-slate-900">{doc.title}</p>
          <p className="text-xs text-slate-500">
            {doc.source_filename} · {formatBytes(doc.file_size_bytes)} · uploaded {formatDate(doc.uploaded_at)}
          </p>
        </div>
        <StatusBadge status={doc.processing_status} />
      </div>

      {doc.processing_status === "processing" && (
        <div className="mt-2">
          <p className="text-xs text-slate-600">
            {STAGE_LABEL[meta.stage ?? "queued"] ?? "Processing"} · {progress}%
          </p>
          <div
            role="progressbar"
            aria-label={`Processing ${doc.title}`}
            aria-valuemin={0}
            aria-valuemax={100}
            aria-valuenow={progress}
            className="mt-1 h-1.5 overflow-hidden rounded-full bg-slate-200"
          >
            <div className="h-full rounded-full bg-amber-500 transition-all" style={{ width: `${progress}%` }} />
          </div>
        </div>
      )}

      {doc.processing_status === "ready" && (
        <p className="mt-1 text-xs text-slate-600" data-testid="document-stats">
          {meta.page_count ?? 0} {meta.page_count === 1 ? "page" : "pages"} · {meta.chunk_count ?? 0} sections ·{" "}
          {meta.concept_count ?? 0} concepts
          {meta.embedding_status === "failed" && " · semantic search pending"}
        </p>
      )}

      {doc.processing_status === "failed" && meta.error && (
        <p role="alert" className="mt-1 text-xs text-red-600">
          {meta.error.message}
        </p>
      )}

      {(meta.warnings ?? []).map((w) => (
        <p key={w} className="mt-1 text-xs text-amber-700">
          {w}
        </p>
      ))}

      <div className="mt-2 flex flex-wrap items-center gap-2">
        {doc.processing_status === "ready" && (
          <Link
            href={`/concepts?document_id=${doc.id}`}
            className="text-xs font-medium text-brand-700 hover:underline"
          >
            View concepts →
          </Link>
        )}
        {doc.processing_status === "failed" && (
          <Button variant="secondary" className="px-2 py-1 text-xs" loading={retry.isPending} onClick={() => retry.mutate(doc.id)}>
            Retry
          </Button>
        )}
        {confirming ? (
          <>
            <span className="text-xs text-slate-600">Delete this document and its concepts?</span>
            <Button variant="secondary" className="px-2 py-1 text-xs text-red-700" loading={remove.isPending} onClick={() => remove.mutate(doc.id)}>
              Delete
            </Button>
            <Button variant="ghost" className="px-2 py-1 text-xs" onClick={() => setConfirming(false)}>
              Cancel
            </Button>
          </>
        ) : (
          <Button variant="ghost" className="px-2 py-1 text-xs" onClick={() => setConfirming(true)}>
            Delete
          </Button>
        )}
        {(remove.error || retry.error) && (
          <span role="alert" className="text-xs text-red-600">
            {(remove.error ?? retry.error)?.message}
          </span>
        )}
      </div>
    </li>
  );
}

/** Toast when a document the student is watching finishes (or fails) processing. */
export function useProcessingToasts(documents: DocumentSummary[]) {
  const seen = useRef(new Map<string, ProcessingStatus>());
  useEffect(() => {
    for (const doc of documents) {
      const before = seen.current.get(doc.id);
      if (before && (before === "pending" || before === "processing") && before !== doc.processing_status) {
        if (doc.processing_status === "ready") toast.success(`“${doc.title}” is ready to study`);
        if (doc.processing_status === "failed") toast.error(`“${doc.title}” could not be processed`);
      }
      seen.current.set(doc.id, doc.processing_status);
    }
  }, [documents]);
}

export function DocumentList() {
  const [page, setPage] = useState(1);
  const { data, isLoading, error } = useDocuments({ page, per_page: 20 });
  const documents = data?.data ?? [];
  const pagination = data?.meta.pagination;
  useProcessingToasts(documents);

  return (
    <Card data-testid="document-list">
      <CardHeader title="Your documents" description="Processing runs in the background — this list updates itself." />
      {isLoading ? (
        <div className="space-y-3">
          <Skeleton className="h-14 w-full" />
          <Skeleton className="h-14 w-full" />
        </div>
      ) : error ? (
        <Alert tone="error">Could not load documents. {error.message}</Alert>
      ) : documents.length === 0 ? (
        <p className="rounded-lg border border-dashed border-slate-300 p-6 text-center text-sm text-slate-500">
          No documents yet. Upload your first study material above.
        </p>
      ) : (
        <>
          <ul className={cn("divide-y divide-slate-100")}>
            {documents.map((doc) => (
              <DocumentRow key={doc.id} doc={doc} />
            ))}
          </ul>
          {pagination && pagination.total_pages > 1 && (
            <div className="mt-4 flex items-center justify-between text-sm text-slate-600">
              <Button variant="secondary" disabled={page <= 1} onClick={() => setPage((p) => p - 1)}>
                Previous
              </Button>
              <span>
                Page {pagination.page} of {pagination.total_pages}
              </span>
              <Button variant="secondary" disabled={page >= pagination.total_pages} onClick={() => setPage((p) => p + 1)}>
                Next
              </Button>
            </div>
          )}
        </>
      )}
    </Card>
  );
}
