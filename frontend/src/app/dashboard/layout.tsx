import type { ReactNode } from "react";

import { AppShell } from "@/components/layout/app-shell";

export default function DashboardLayout({ children }: { children: ReactNode }) {
  return <AppShell title="Dashboard">{children}</AppShell>;
}
