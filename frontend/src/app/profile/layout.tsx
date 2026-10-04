import type { ReactNode } from "react";

import { AppShell } from "@/components/layout/app-shell";

export default function ProfileLayout({ children }: { children: ReactNode }) {
  return <AppShell title="Profile">{children}</AppShell>;
}
