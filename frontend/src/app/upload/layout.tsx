import type { ReactNode } from "react";

import { AppShell } from "@/components/layout/app-shell";

export default function UploadLayout({ children }: { children: ReactNode }) {
  return <AppShell title="Upload material">{children}</AppShell>;
}
