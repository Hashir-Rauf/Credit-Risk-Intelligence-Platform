import { ScoreForm } from "@/components/ScoreForm";
import { callApi } from "@/lib/api";
import type { InputSpec } from "@/lib/fields";

export const dynamic = "force-dynamic"; // the form is built from the live model's schema

export default async function ScorePage() {
  const info = await callApi("/model-info");
  const body = info.body as { inputs?: InputSpec[]; threshold?: number; model_version?: string };

  return (
    <>
      <h1>Score a customer</h1>
      <p className="lead">
        Enter a customer&apos;s account, repayment and billing history. The model returns a risk score and the factors that
        moved it. Everything here goes through the server, which holds the API key.
      </p>
      {info.status === 200 && body.inputs && body.threshold !== undefined ? (
        <ScoreForm inputs={body.inputs} threshold={body.threshold} modelVersion={body.model_version ?? "unknown"} />
      ) : (
        <div className="card">
          <h2>The scoring service is not available</h2>
          <p className="muted">
            The model is not loaded or the API cannot be reached (status {info.status}). Check the{" "}
            <a href="/monitoring">monitoring page</a>, then reload.
          </p>
        </div>
      )}
    </>
  );
}
