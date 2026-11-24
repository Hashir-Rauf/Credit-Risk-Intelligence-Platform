import { NextResponse } from "next/server";
import { callApi, errorBody, readJson } from "@/lib/api";

export async function POST(request: Request) {
  const body = await readJson(request);
  if (body === null || typeof body !== "object") {
    return NextResponse.json(errorBody("bad_request", "Send a JSON object with the customer fields."), { status: 400 });
  }
  const result = await callApi("/predict", { method: "POST", body });
  return NextResponse.json(result.body, { status: result.status, headers: { "Cache-Control": "no-store" } });
}
