import type { ReactNode } from "react";

import { AppShell } from "@/components/layout/app-shell";

export default function ConceptsLayout({ children }: { children: ReactNode }) {
  return <AppShell title="Concepts">{children}</AppShell>;
}
