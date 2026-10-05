import type { Metadata } from "next";

import { ConceptBrowser } from "@/components/concepts/concept-browser";

export const metadata: Metadata = { title: "Concepts" };

export default async function ConceptsPage({
  searchParams,
}: {
  searchParams: Promise<{ document_id?: string; subject_id?: string; search?: string }>;
}) {
  const params = await searchParams;
  return (
    <ConceptBrowser
      initialDocumentId={params.document_id}
      initialSubjectId={params.subject_id}
      initialSearch={params.search}
    />
  );
}
