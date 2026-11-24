import { callApi } from "@/lib/api";

export const dynamic = "force-dynamic";

export default async function AdminPage() {
  const response = await callApi("/model-info");
  const info = response.body as { model_name?: string; model_version?: string; algorithm?: string };
  const loaded = response.status === 200;
  return (
    <>
      <h1>Admin</h1>
      <p className="lead">Model promotion and rollback.</p>
      <div className="grid2">
        <section className="card" aria-labelledby="champion">
          <h2 id="champion">Champion (serving now)</h2>
          {loaded ? (
            <table>
              <tbody>
                <tr><th>Model</th><td>{info.model_name}</td></tr>
                <tr><th>Version</th><td>{info.model_version}</td></tr>
                <tr><th>Algorithm</th><td>{info.algorithm}</td></tr>
              </tbody>
            </table>
          ) : (
            <p className="muted">No model is loaded (status {response.status}).</p>
          )}
        </section>
        <section className="card" aria-labelledby="actions">
          <h2 id="actions">Promote / roll back</h2>
          <p className="small muted">
            Comparing a challenger with the champion and promoting or rolling back are driven by a protected GitHub workflow
            with a required reviewer. That workflow is built in Phase 8; these controls stay disabled until then so nothing
            here can change what is serving.
          </p>
          <div style={{ display: "flex", gap: 8 }}>
            <button type="button" disabled>Promote challenger</button>
            <button type="button" disabled>Roll back</button>
          </div>
        </section>
      </div>
    </>
  );
}
