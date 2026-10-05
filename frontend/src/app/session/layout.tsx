import "katex/dist/katex.min.css";

import type { ReactNode } from "react";

import { AppShell } from "@/components/layout/app-shell";

export default function SessionLayout({ children }: { children: ReactNode }) {
  return <AppShell title="Study session">{children}</AppShell>;
}
