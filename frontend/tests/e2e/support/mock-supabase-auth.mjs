/**
 * Minimal stand-in for Supabase Auth (GoTrue) used by the E2E suite, so the full
 * stack (Next.js → FastAPI → PostgreSQL) can be exercised without a real
 * Supabase project. Issues ES256 JWTs and publishes them via JWKS exactly like a
 * current Supabase project; the backend verifies them through the same JWKS path
 * it uses in production.
 *
 *   node tests/e2e/support/mock-supabase-auth.mjs   (PORT, default 54329)
 *
 * Supported: POST /auth/v1/signup, POST /auth/v1/token?grant_type=password|refresh_token,
 * GET /auth/v1/user, POST /auth/v1/logout, GET /auth/v1/.well-known/jwks.json.
 */
import { createServer } from "node:http";
import { createHash, generateKeyPairSync, randomBytes, randomUUID, sign } from "node:crypto";

const PORT = Number(process.env.PORT ?? 54329);
const BASE = `http://127.0.0.1:${PORT}`;
const ISSUER = `${BASE}/auth/v1`;
const KID = "e2e-es256";

const { privateKey, publicKey } = generateKeyPairSync("ec", { namedCurve: "P-256" });
const jwk = { ...publicKey.export({ format: "jwk" }), kid: KID, alg: "ES256", use: "sig" };

/** email → { id, email, password, user_metadata, created_at } */
const users = new Map();
/** refresh token → email */
const refreshTokens = new Map();

const b64url = (buf) => Buffer.from(buf).toString("base64url");

function signJwt(claims) {
  const header = b64url(JSON.stringify({ alg: "ES256", typ: "JWT", kid: KID }));
  const payload = b64url(JSON.stringify(claims));
  const signature = sign("sha256", Buffer.from(`${header}.${payload}`), {
    key: privateKey,
    dsaEncoding: "ieee-p1363",
  });
  return `${header}.${payload}.${b64url(signature)}`;
}

function publicUser(u) {
  return {
    id: u.id,
    aud: "authenticated",
    role: "authenticated",
    email: u.email,
    email_confirmed_at: u.created_at,
    user_metadata: u.user_metadata,
    app_metadata: { provider: "email", providers: ["email"] },
    identities: [],
    created_at: u.created_at,
    updated_at: u.created_at,
  };
}

function session(u) {
  const now = Math.floor(Date.now() / 1000);
  const access_token = signJwt({
    iss: ISSUER,
    sub: u.id,
    aud: "authenticated",
    role: "authenticated",
    email: u.email,
    user_metadata: u.user_metadata,
    app_metadata: { provider: "email" },
    session_id: randomUUID(),
    iat: now,
    exp: now + 3600,
  });
  const refresh_token = randomBytes(16).toString("hex");
  refreshTokens.set(refresh_token, u.email);
  return {
    access_token,
    token_type: "bearer",
    expires_in: 3600,
    expires_at: now + 3600,
    refresh_token,
    user: publicUser(u),
  };
}

function userFromBearer(req) {
  const token = (req.headers.authorization ?? "").replace(/^Bearer /i, "");
  try {
    const claims = JSON.parse(Buffer.from(token.split(".")[1], "base64url").toString());
    return [...users.values()].find((u) => u.id === claims.sub) ?? null;
  } catch {
    return null;
  }
}

async function readBody(req) {
  const chunks = [];
  for await (const chunk of req) chunks.push(chunk);
  const raw = Buffer.concat(chunks).toString();
  return raw ? JSON.parse(raw) : {};
}

function send(res, status, body) {
  res.writeHead(status, {
    "Content-Type": "application/json",
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Headers": "*",
  });
  res.end(body === undefined ? "" : JSON.stringify(body));
}

const authError = (res, status, code, msg) =>
  send(res, status, { code: status, error_code: code, msg, error: code, error_description: msg });

createServer(async (req, res) => {
  const url = new URL(req.url, BASE);
  try {
    if (req.method === "OPTIONS") return send(res, 204);
    if (req.method === "GET" && url.pathname === "/health") return send(res, 200, { ok: true });
    if (req.method === "GET" && url.pathname === "/auth/v1/.well-known/jwks.json") {
      return send(res, 200, { keys: [jwk] });
    }
    if (req.method === "POST" && url.pathname === "/auth/v1/signup") {
      const { email, password, data } = await readBody(req);
      if (users.has(email)) return authError(res, 422, "user_already_exists", "User already registered");
      const user = {
        id: randomUUID(),
        email,
        password: createHash("sha256").update(password).digest("hex"),
        user_metadata: data ?? {},
        created_at: new Date().toISOString(),
      };
      users.set(email, user);
      return send(res, 200, session(user)); // auto-confirm
    }
    if (req.method === "POST" && url.pathname === "/auth/v1/token") {
      const body = await readBody(req);
      const grant = url.searchParams.get("grant_type");
      if (grant === "password") {
        const user = users.get(body.email);
        const hash = createHash("sha256").update(body.password ?? "").digest("hex");
        if (!user || user.password !== hash) {
          return authError(res, 400, "invalid_credentials", "Invalid login credentials");
        }
        return send(res, 200, session(user));
      }
      if (grant === "refresh_token") {
        const email = refreshTokens.get(body.refresh_token);
        if (!email) return authError(res, 400, "refresh_token_not_found", "Invalid Refresh Token");
        refreshTokens.delete(body.refresh_token);
        return send(res, 200, session(users.get(email)));
      }
      return authError(res, 400, "unsupported_grant_type", "Unsupported grant type");
    }
    if (req.method === "GET" && url.pathname === "/auth/v1/user") {
      const user = userFromBearer(req);
      return user ? send(res, 200, publicUser(user)) : authError(res, 401, "bad_jwt", "invalid JWT");
    }
    if (req.method === "POST" && url.pathname === "/auth/v1/logout") return send(res, 204);
    return authError(res, 404, "not_found", `No mock for ${req.method} ${url.pathname}`);
  } catch (err) {
    return authError(res, 500, "unexpected_failure", String(err));
  }
}).listen(PORT, "127.0.0.1", () => {
  console.log(`mock supabase auth listening on ${BASE}`);
});
