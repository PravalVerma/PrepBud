import { forwardToBackend } from "@/lib/backend";
import { env } from "@/lib/env";
import { getAccessToken } from "@/lib/session";

export const dynamic = "force-dynamic";

type Context = { params: Promise<{ path: string[] }> };

async function handler(request: Request, { params }: Context): Promise<Response> {
  const { path } = await params;
  return forwardToBackend({
    request,
    path,
    token: await getAccessToken(),
    baseUrl: env.apiUrl,
  });
}

export { handler as DELETE, handler as GET, handler as PATCH, handler as POST, handler as PUT };
