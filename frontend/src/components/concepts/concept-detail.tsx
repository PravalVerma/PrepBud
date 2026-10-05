"use client";

import Link from "next/link";
import type { ReactNode } from "react";

import { difficultyLabel } from "@/components/concepts/concept-browser";
import { ConceptGraphView } from "@/components/concepts/concept-graph";
import { Card, CardHeader } from "@/components/ui/card";
import { Alert, Badge, Skeleton } from "@/components/ui/feedback";
import { useConcept, useSearch } from "@/hooks/use-concepts";
import { ApiError } from "@/lib/api";
import { formatDate, formatPercent } from "@/lib/utils";
import type { ConceptLink, RelationshipType } from "@/types/domain";

const RELATIONSHIP_LABEL: Record<RelationshipType, string> = {
  prerequisite: "Prerequisite",
  related: "Related",
  generalisation: "More general",
  specialisation: "More specific",
};

/** Render `**bold**` search highlights as <mark> without injecting HTML. */
export function highlight(snippet: string): ReactNode[] {
  return snippet.split(/(\*\*[^*]+\*\*)/g).map((part, i) =>
    part.startsWith("**") && part.endsWith("**") && part.length > 4 ? (
      <mark key={i} className="rounded bg-amber-100 px-0.5 text-slate-900">
        {part.slice(2, -2)}
      </mark>
    ) : (
      part
    ),
  );
}

function ConceptLinks({ items, empty }: { items: ConceptLink[]; empty: string }) {
  if (items.length === 0) return <p className="text-sm text-slate-500">{empty}</p>;
  return (
    <ul className="space-y-1">
      {items.map((c) => (
        <li key={c.id} className="flex items-center justify-between gap-3 text-sm">
          <Link href={`/concepts/${c.id}`} className="font-medium text-brand-700 hover:underline">
            {c.name}
          </Link>
          <Badge>{formatPercent(c.mastery_level)}</Badge>
        </li>
      ))}
    </ul>
  );
}

function RelatedMaterial({ conceptId, name }: { conceptId: string; name: string }) {
  const { data, isLoading, error } = useSearch(name, { concept_id: conceptId, per_page: 5 });
  const hits = data?.data ?? [];
  return (
    <Card data-testid="related-material">
      <CardHeader title="From your material" description="Sections that teach this concept (hybrid search)." />
      {isLoading ? (
        <Skeleton className="h-16 w-full" />
      ) : error ? (
        <Alert tone="error">Search is unavailable right now.</Alert>
      ) : hits.length === 0 ? (
        <p className="text-sm text-slate-500">No matching sections found.</p>
      ) : (
        <ul className="space-y-3">
          {hits.map((hit) => (
            <li key={hit.section_id} className="rounded-lg bg-slate-50 p-3" data-testid="search-hit">
              <p className="text-xs font-medium text-slate-600">
                {hit.document_title}
                {hit.heading && ` · ${hit.heading}`}
                {hit.page_numbers.length > 0 && ` · p. ${hit.page_numbers.join(", ")}`}
              </p>
              <p className="mt-1 text-sm text-slate-700">{highlight(hit.snippet)}</p>
            </li>
          ))}
        </ul>
      )}
    </Card>
  );
}

export function ConceptDetailView({ id }: { id: string }) {
  const { data: concept, isLoading, error } = useConcept(id);

  if (isLoading) {
    return (
      <div className="space-y-4">
        <Skeleton className="h-24 w-full" />
        <Skeleton className="h-40 w-full" />
      </div>
    );
  }
  if (error || !concept) {
    const notFound = error instanceof ApiError && error.status === 404;
    return (
      <Alert tone="error">
        {notFound ? "This concept does not exist." : `Could not load the concept. ${error?.message ?? ""}`}{" "}
        <Link href="/concepts" className="font-medium underline">
          Back to concepts
        </Link>
      </Alert>
    );
  }

  const { mastery } = concept;
  return (
    <div className="space-y-6" data-testid="concept-detail">
      <Link href="/concepts" className="text-sm font-medium text-brand-700 hover:underline">
        ← All concepts
      </Link>
      <Card>
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div className="min-w-0">
            <h2 className="text-xl font-semibold text-slate-900">{concept.name}</h2>
            {concept.description && <p className="mt-2 max-w-3xl text-sm text-slate-600">{concept.description}</p>}
          </div>
          <div className="flex flex-wrap gap-2">
            <Badge>Difficulty: {difficultyLabel(concept.difficulty_estimate)}</Badge>
            <Badge className="bg-brand-50 text-brand-700">
              Mastery {formatPercent(mastery.level)} · {mastery.label}
            </Badge>
            <Link
              href={`/session?concept_id=${concept.id}`}
              className="rounded-full bg-brand-600 px-3 py-0.5 text-xs font-medium text-white hover:bg-brand-700"
            >
              Study this concept
            </Link>
          </div>
        </div>
        <dl className="mt-4 grid grid-cols-2 gap-3 text-sm sm:grid-cols-4">
          <div>
            <dt className="text-xs text-slate-500">Attempts</dt>
            <dd className="font-medium">{mastery.attempt_count}</dd>
          </div>
          <div>
            <dt className="text-xs text-slate-500">Correct</dt>
            <dd className="font-medium">{mastery.correct_count}</dd>
          </div>
          <div>
            <dt className="text-xs text-slate-500">Last assessed</dt>
            <dd className="font-medium">{formatDate(mastery.last_assessed_at)}</dd>
          </div>
          <div>
            <dt className="text-xs text-slate-500">Next review</dt>
            <dd className="font-medium">{formatDate(mastery.next_review_at)}</dd>
          </div>
        </dl>
      </Card>

      <div className="grid gap-6 lg:grid-cols-2">
        <Card data-testid="prerequisites">
          <CardHeader title="Learn first" description="Prerequisites for this concept." />
          <ConceptLinks items={concept.prerequisites} empty="No prerequisites — a good starting point." />
        </Card>
        <Card data-testid="dependents">
          <CardHeader title="Unlocks" description="Concepts that build on this one." />
          <ConceptLinks items={concept.dependents} empty="Nothing depends on this concept yet." />
        </Card>
        <Card>
          <CardHeader title="Related concepts" />
          {concept.related_concepts.length === 0 ? (
            <p className="text-sm text-slate-500">No related concepts found.</p>
          ) : (
            <ul className="space-y-1">
              {concept.related_concepts.map((r) => (
                <li key={r.id} className="flex items-center justify-between gap-3 text-sm">
                  <Link href={`/concepts/${r.id}`} className="font-medium text-brand-700 hover:underline">
                    {r.name}
                  </Link>
                  <Badge>{RELATIONSHIP_LABEL[r.relationship] ?? r.relationship}</Badge>
                </li>
              ))}
            </ul>
          )}
        </Card>
        <Card data-testid="concept-documents">
          <CardHeader title="Documents" />
          {concept.documents.length === 0 ? (
            <p className="text-sm text-slate-500">Not linked to any document.</p>
          ) : (
            <ul className="space-y-1 text-sm">
              {concept.documents.map((d) => (
                <li key={d.id} className="flex justify-between gap-3">
                  <span className="font-medium text-slate-800">{d.title}</span>
                  <span className="text-xs text-slate-500">
                    {d.section_count} {d.section_count === 1 ? "section" : "sections"}
                  </span>
                </li>
              ))}
            </ul>
          )}
          {concept.misconceptions.length > 0 && (
            <div className="mt-4">
              <h3 className="text-sm font-semibold text-slate-900">Misconceptions</h3>
              <ul className="mt-1 space-y-1 text-sm">
                {concept.misconceptions.map((m) => (
                  <li key={m.id} className="flex justify-between gap-3">
                    <span>{m.name}</span>
                    <Badge>{m.status}</Badge>
                  </li>
                ))}
              </ul>
            </div>
          )}
        </Card>
      </div>

      <ConceptGraphView conceptId={concept.id} />

      <RelatedMaterial conceptId={concept.id} name={concept.name} />
    </div>
  );
}
