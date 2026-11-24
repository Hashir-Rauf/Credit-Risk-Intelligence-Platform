import { callApi } from "@/lib/api";

export const dynamic = "force-dynamic";

// Public tool links. These are plain URLs, not secrets. Override them per environment.
const LINKS = [
  { name: "Grafana", env: "GRAFANA_URL", fallback: "http://localhost:3001", note: "Dashboards: request rate, latency, score distribution, drift." },
  { name: "Prometheus", env: "PROMETHEUS_URL", fallback: "http://localhost:9090", note: "Raw metrics and alert rules (dev stack only)." },
  { name: "MLflow", env: "MLFLOW_UI_URL", fallback: "http://localhost:5000", note: "Local experiment tracking (dev stack only)." },
  { name: "DagsHub (experiments, data)", env: "DAGSHUB_URL", fallback: "https://dagshub.com/Hashir-Rauf/Credit-Risk-Intelligence-Platform", note: "Shared experiments, registry and DVC data." },
];

export default async function MonitoringPage() {
  const health = await callApi("/health");
  const up = health.status === 200;
  const body = health.body as { model_version?: string | null; uptime_s?: number; reason?: string };
  return (
    <>
      <h1>Monitoring</h1>
      <p className="lead">Live status of the scoring service and links to the observability tools.</p>
      <div className="grid2">
        <section className="card" aria-labelledby="status">
          <h2 id="status">Scoring service</h2>
          <p>
            <span className={`status-dot ${up ? "ok" : "bad"}`} aria-hidden="true" />
            <strong>{up ? "Healthy" : "Unavailable"}</strong>
          </p>
          <table>
            <tbody>
              <tr><th>Model version</th><td>{body.model_version ?? "none loaded"}</td></tr>
              <tr><th>Uptime</th><td>{body.uptime_s !== undefined ? `${Math.round(body.uptime_s)} s` : "n/a"}</td></tr>
              {body.reason ? (<tr><th>Reason</th><td>{body.reason}</td></tr>) : null}
            </tbody>
          </table>
        </section>
        <section className="card" aria-labelledby="tools">
          <h2 id="tools">Tools</h2>
          <ul className="links">
            {LINKS.map((link) => {
              const url = process.env[link.env] ?? link.fallback;
              return (
                <li key={link.name}>
                  <a href={url} target="_blank" rel="noopener noreferrer">{link.name}</a>
                  <div className="small muted">{link.note}</div>
                </li>
              );
            })}
          </ul>
        </section>
      </div>
      <p className="small muted" style={{ marginTop: 16 }}>
        Drift detection, alerts and the &quot;simulate drift&quot; control arrive with Phase 8.
      </p>
    </>
  );
}
