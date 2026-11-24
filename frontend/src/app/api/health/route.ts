import { NextResponse } from "next/server";
import { callApi } from "@/lib/api";

// Liveness of the frontend itself plus the reachability of the scoring API (no secrets in the answer).
export async function GET() {
  const upstream = await callApi("/health");
  const ok = upstream.status === 200;
  return NextResponse.json(
    { frontend: "ok", api: ok ? "ok" : "unavailable", api_status: upstream.status },
    { status: ok ? 200 : 503, headers: { "Cache-Control": "no-store" } },
  );
}
