import { callApi } from "@/lib/api";
import type { InputSpec } from "@/lib/fields";

export const dynamic = "force-dynamic";

type Info = {
  model_name: string;
  model_version: string;
  algorithm: string;
  git_sha: string;
  raw_data_md5: string | null;
  model_sha256: string | null;
  threshold: number;
  test_metrics: Record<string, number>;
  quality_gate: { metric: string; passed: boolean; threshold: number; value: number } | null;
  inputs: InputSpec[];
  excluded_sensitive: string[];
  score: { meaning: string };
};

const METRIC_LABELS: Record<string, string> = {
  roc_auc: "ROC-AUC",
  pr_auc: "PR-AUC",
  ks: "KS statistic",
  brier: "Brier score",
  precision: "Precision at threshold",
  recall: "Recall at threshold",
  f1: "F1 at threshold",
  accuracy: "Accuracy",
  n: "Test customers",
  positive_rate: "Default rate in test data",
};

function short(value: string | null, length = 12) {
  return value ? `${value.slice(0, length)}…` : "n/a";
}

function range(spec: InputSpec) {
  if (spec.allowed) return `one of ${spec.allowed.join(", ")}`;
  return `${spec.min ?? "−∞"} to ${spec.max ?? "∞"}${spec.dtype === "integer" ? " (whole numbers)" : ""}`;
}

export default async function ModelPage() {
  const response = await callApi("/model-info");
  if (response.status !== 200) {
    return (
      <>
        <h1>Model &amp; data</h1>
        <div className="card">
          <p className="muted">Model details are unavailable right now (status {response.status}).</p>
        </div>
      </>
    );
  }
  const info = response.body as Info;
  return (
    <>
      <h1>Model &amp; data</h1>
      <p className="lead">What is serving right now, how it was evaluated, and exactly what it takes as input.</p>
      <div className="grid2">
        <section className="card" aria-labelledby="serving">
          <h2 id="serving">Serving</h2>
          <table>
            <tbody>
              <tr><th>Model</th><td>{info.model_name} v{info.model_version}</td></tr>
              <tr><th>Algorithm</th><td>{info.algorithm}</td></tr>
              <tr><th>Decision threshold</th><td>{info.threshold}</td></tr>
              <tr><th>Code version</th><td><code>{info.git_sha}</code></td></tr>
              <tr><th>Training data (md5)</th><td><code>{short(info.raw_data_md5)}</code></td></tr>
              <tr><th>Model file (sha-256)</th><td><code>{short(info.model_sha256)}</code></td></tr>
              <tr><th>Quality gate</th><td>{info.quality_gate ? `${info.quality_gate.passed ? "passed" : "FAILED"}: ${info.quality_gate.metric} ${info.quality_gate.value.toFixed(4)} ≥ ${info.quality_gate.threshold}` : "n/a"}</td></tr>
            </tbody>
          </table>
          <p className="small muted" style={{ marginTop: 12 }}>{info.score.meaning}</p>
        </section>

        <section className="card" aria-labelledby="metrics">
          <h2 id="metrics">Held-out test results</h2>
          <table>
            <tbody>
              {Object.entries(info.test_metrics).map(([key, value]) => (
                <tr key={key}>
                  <th>{METRIC_LABELS[key] ?? key}</th>
                  <td>{key === "n" ? value.toLocaleString() : value.toFixed(4)}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <p className="small muted" style={{ marginTop: 12 }}>
            Measured once on customers the model never saw during training or tuning.
          </p>
        </section>
      </div>

      <section className="card" style={{ marginTop: 20 }} aria-labelledby="inputs">
        <h2 id="inputs">Inputs ({info.inputs.length})</h2>
        <p className="small muted">
          Not used by the model, by design: {info.excluded_sensitive.join(", ")}. Fairness is audited separately.
        </p>
        <table>
          <thead>
            <tr><th>Field</th><td><strong>Accepted values</strong></td></tr>
          </thead>
          <tbody>
            {info.inputs.map((spec) => (
              <tr key={spec.name}>
                <th><code>{spec.name}</code></th>
                <td>{range(spec)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>
    </>
  );
}
