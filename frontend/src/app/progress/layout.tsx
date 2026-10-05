import type { ReactNode } from "react";

import { AppShell } from "@/components/layout/app-shell";

export default function ProgressLayout({ children }: { children: ReactNode }) {
  return <AppShell title="Progress">{children}</AppShell>;
}
