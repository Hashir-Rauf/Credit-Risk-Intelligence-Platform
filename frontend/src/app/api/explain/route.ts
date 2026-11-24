import { NextResponse } from "next/server";
import { callApi, errorBody, readJson } from "@/lib/api";

const DEFAULT_TOP_K = 5;

export async function POST(request: Request) {
  const body = await readJson(request);
  if (body === null || typeof body !== "object") {
    return NextResponse.json(errorBody("bad_request", "Send a JSON object with the customer fields."), { status: 400 });
  }
  const requested = Number(new URL(request.url).searchParams.get("top_k") ?? DEFAULT_TOP_K);
  const topK = Number.isInteger(requested) && requested >= 1 && requested <= 10 ? requested : DEFAULT_TOP_K;
  const result = await callApi(`/explain?top_k=${topK}`, { method: "POST", body });
  return NextResponse.json(result.body, { status: result.status, headers: { "Cache-Control": "no-store" } });
}
