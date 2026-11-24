import "server-only";

/**
 * Server-side client for the scoring API. This module must never be imported by a client component:
 * it reads API_KEY and API_URL from the server environment (`server-only` makes the build fail if it is).
 */

const TIMEOUT_MS = 15_000;
const MAX_BODY_BYTES = 200_000;

export class ApiConfigError extends Error {}

function config(): { url: string; key: string } {
  const url = process.env.API_URL;
  const key = process.env.API_KEY;
  if (!url || !key) {
    throw new ApiConfigError("API_URL and API_KEY must be set on the server");
  }
  return { url: url.replace(/\/+$/, ""), key };
}

export type ProxyResult = { status: number; body: unknown };

/** Call the scoring API and return its status and JSON body without ever echoing secrets. */
export async function callApi(path: string, init?: { method?: string; body?: unknown }): Promise<ProxyResult> {
  let cfg: { url: string; key: string };
  try {
    cfg = config();
  } catch {
    return { status: 500, body: errorBody("server_misconfigured", "The server is not configured to reach the API.") };
  }
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), TIMEOUT_MS);
  try {
    const response = await fetch(`${cfg.url}${path}`, {
      method: init?.method ?? "GET",
      headers: {
        "X-API-Key": cfg.key,
        ...(init?.body !== undefined ? { "Content-Type": "application/json" } : {}),
      },
      body: init?.body !== undefined ? JSON.stringify(init.body) : undefined,
      signal: controller.signal,
      cache: "no-store",
    });
    const text = await response.text();
    try {
      return { status: response.status, body: JSON.parse(text) };
    } catch {
      return { status: 502, body: errorBody("bad_gateway", "The API returned something that is not JSON.") };
    }
  } catch (error) {
    const timedOut = error instanceof Error && error.name === "AbortError";
    return {
      status: timedOut ? 504 : 502,
      body: errorBody(timedOut ? "gateway_timeout" : "api_unreachable", timedOut ? "The API took too long to answer." : "The scoring API is not reachable."),
    };
  } finally {
    clearTimeout(timer);
  }
}

export function errorBody(code: string, message: string) {
  return { error: { code, message } };
}

/** Read a small JSON request body; returns null when it is missing, malformed, or too large. */
export async function readJson(request: Request): Promise<unknown | null> {
  const declared = Number(request.headers.get("content-length") ?? "0");
  if (declared > MAX_BODY_BYTES) return null;
  try {
    const text = await request.text();
    if (text.length > MAX_BODY_BYTES) return null;
    return JSON.parse(text);
  } catch {
    return null;
  }
}
