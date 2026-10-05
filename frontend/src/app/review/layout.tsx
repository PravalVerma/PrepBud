import type { ReactNode } from "react";

import { AppShell } from "@/components/layout/app-shell";

export default function ReviewLayout({ children }: { children: ReactNode }) {
  return <AppShell title="Review & plan">{children}</AppShell>;
}
