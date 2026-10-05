import type { Metadata } from "next";

import { ConceptDetailView } from "@/components/concepts/concept-detail";

export const metadata: Metadata = { title: "Concept" };

export default async function ConceptPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  return <ConceptDetailView id={id} />;
}
