import { NextResponse } from "next/server";
import { callApi } from "@/lib/api";

export async function GET() {
  const result = await callApi("/model-info");
  return NextResponse.json(result.body, { status: result.status, headers: { "Cache-Control": "no-store" } });
}
