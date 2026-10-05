"use client";

import Link from "next/link";
import { useState } from "react";

import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { SelectField, TextField } from "@/components/ui/field";
import { Alert, Badge, Skeleton } from "@/components/ui/feedback";
import { useConcepts } from "@/hooks/use-concepts";
import { useDebounced } from "@/hooks/use-debounced";
import { useSubjects } from "@/hooks/use-subjects";
import { formatPercent } from "@/lib/utils";
import type { ConceptListItem } from "@/types/domain";

const PER_PAGE = 20;

export function difficultyLabel(value: number): string {
  if (value < 0.4) return "Easy";
  if (value < 0.65) return "Medium";
  return "Hard";
}

function ConceptRow({ concept }: { concept: ConceptListItem }) {
  return (
    <li>
      <Link
        href={`/concepts/${concept.id}`}
        className="flex items-start gap-4 rounded-lg px-3 py-3 transition-colors hover:bg-slate-50"
        data-testid="concept-row"
      >
        <div className="min-w-0 flex-1">
          <p className="text-sm font-medium text-slate-900">{concept.name}</p>
          {concept.description && <p className="mt-0.5 line-clamp-2 text-xs text-slate-500">{concept.description}</p>}
          <p className="mt-1 text-xs text-slate-500">
            {difficultyLabel(concept.difficulty_estimate)} · {concept.prerequisite_count}{" "}
            {concept.prerequisite_count === 1 ? "prerequisite" : "prerequisites"} · {concept.document_count}{" "}
            {concept.document_count === 1 ? "document" : "documents"}
          </p>
        </div>
        <Badge>
          {formatPercent(concept.mastery.level)} · {concept.mastery.label}
        </Badge>
      </Link>
    </li>
  );
}

export function ConceptBrowser({
  initialDocumentId,
  initialSubjectId,
  initialSearch,
}: {
  initialDocumentId?: string;
  initialSubjectId?: string;
  initialSearch?: string;
}) {
  const [search, setSearch] = useState(initialSearch ?? "");
  const [subjectId, setSubjectId] = useState(initialSubjectId ?? "");
  const [documentId, setDocumentId] = useState(initialDocumentId ?? "");
  const [page, setPage] = useState(1);
  const debounced = useDebounced(search.trim(), 300);
  const subjects = useSubjects({ page: 1, per_page: 100 });
  const { data, isLoading, isFetching, error } = useConcepts({
    page,
    per_page: PER_PAGE,
    search: debounced || undefined,
    subject_id: subjectId || undefined,
    document_id: documentId || undefined,
  });
  const concepts = data?.data ?? [];
  const pagination = data?.meta.pagination;
  const filtered = Boolean(debounced || subjectId || documentId);

  return (
    <Card data-testid="concept-browser">
      <div className="mb-4 grid gap-3 sm:grid-cols-[1fr_220px]">
        <TextField
          label="Search concepts"
          type="search"
          value={search}
          placeholder="e.g. quadratic"
          maxLength={200}
          onChange={(e) => {
            setSearch(e.target.value);
            setPage(1);
          }}
        />
        <SelectField
          label="Subject"
          value={subjectId}
          onChange={(e) => {
            setSubjectId(e.target.value);
            setPage(1);
          }}
          options={[
            { value: "", label: "All subjects" },
            ...(subjects.data?.data ?? []).map((s) => ({ value: s.id, label: s.name })),
          ]}
        />
      </div>
      {documentId && (
        <div className="mb-3 flex items-center gap-2 text-xs text-slate-600">
          <Badge className="bg-brand-50 text-brand-700">From one document</Badge>
          <button type="button" className="font-medium text-brand-700 hover:underline" onClick={() => setDocumentId("")}>
            Show all concepts
          </button>
        </div>
      )}

      {isLoading ? (
        <div className="space-y-3">
          <Skeleton className="h-14 w-full" />
          <Skeleton className="h-14 w-full" />
          <Skeleton className="h-14 w-full" />
        </div>
      ) : error ? (
        <Alert tone="error">Could not load concepts. {error.message}</Alert>
      ) : concepts.length === 0 ? (
        <p className="rounded-lg border border-dashed border-slate-300 p-6 text-center text-sm text-slate-500">
          {filtered ? (
            "No concepts match these filters."
          ) : (
            <>
              No concepts yet.{" "}
              <Link href="/upload" className="font-medium text-brand-700 hover:underline">
                Upload study material
              </Link>{" "}
              and we&apos;ll extract them.
            </>
          )}
        </p>
      ) : (
        <>
          <p className="mb-2 text-xs text-slate-500" aria-live="polite">
            {pagination?.total ?? concepts.length} {pagination?.total === 1 ? "concept" : "concepts"}
            {isFetching && " · updating…"}
          </p>
          <ul className="divide-y divide-slate-100">
            {concepts.map((c) => (
              <ConceptRow key={c.id} concept={c} />
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
