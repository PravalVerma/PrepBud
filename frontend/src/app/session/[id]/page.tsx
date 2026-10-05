import type { Metadata } from "next";

import { SessionRoom } from "@/components/session/session-room";
import { env } from "@/lib/env";

export const metadata: Metadata = { title: "Study session" };
export const dynamic = "force-dynamic";

export default async function SessionPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  return <SessionRoom sessionId={id} wsBaseUrl={env.wsUrl} />;
}
