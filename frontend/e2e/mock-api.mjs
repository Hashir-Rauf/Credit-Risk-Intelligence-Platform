// A tiny stand-in for the scoring API, used only by the Playwright smoke test.
// It enforces the X-API-Key header and remembers the last key it saw, so the test can prove that the
// Next.js server (not the browser) is the one attaching the key.
import http from "node:http";

const PORT = Number(process.env.MOCK_API_PORT ?? 8099);
const KEY = process.env.MOCK_API_KEY ?? "e2e-secret-key-should-never-reach-the-browser"; // gitleaks:allow (fake key, e2e only)

const inputs = [
  { name: "LIMIT_BAL", dtype: "number", min: 1, max: 5000000 },
  { name: "EDUCATION", dtype: "integer", allowed: [0, 1, 2, 3, 4, 5, 6] },
  ...[1, 2, 3, 4, 5, 6].map((i) => ({ name: `PAY_${i}`, dtype: "integer", min: -2, max: 9 })),
  ...[1, 2, 3, 4, 5, 6].map((i) => ({ name: `BILL_AMT${i}`, dtype: "number", min: -1000000, max: 5000000 })),
  ...[1, 2, 3, 4, 5, 6].map((i) => ({ name: `PAY_AMT${i}`, dtype: "number", min: 0, max: 10000000 })),
];

let lastKeySeen = null;
let predictCalls = 0;

function send(res, status, body) {
  res.writeHead(status, { "Content-Type": "application/json" });
  res.end(JSON.stringify(body));
}

function readBody(req) {
  return new Promise((resolve) => {
    let data = "";
    req.on("data", (chunk) => (data += chunk));
    req.on("end", () => {
      try {
        resolve(JSON.parse(data || "{}"));
      } catch {
        resolve(null);
      }
    });
  });
}

const server = http.createServer(async (req, res) => {
  const url = new URL(req.url, `http://127.0.0.1:${PORT}`);
  if (url.pathname === "/__state") return send(res, 200, { lastKeySeen, predictCalls });
  if (url.pathname === "/health") return send(res, 200, { status: "ok", model_loaded: true, model_version: "e2e", uptime_s: 12 });

  lastKeySeen = req.headers["x-api-key"] ?? null;
  if (lastKeySeen !== KEY) return send(res, 401, { error: { code: "unauthorized", message: "missing or invalid API key" } });

  if (url.pathname === "/model-info") {
    return send(res, 200, {
      model_name: "credit-risk-default-model",
      model_version: "e2e",
      algorithm: "MockForest",
      git_sha: "e2e0000",
      raw_data_md5: "0".repeat(32),
      model_sha256: "f".repeat(64),
      threshold: 0.5,
      test_metrics: { roc_auc: 0.7883, pr_auc: 0.5782, n: 4500 },
      quality_gate: { metric: "roc_auc", passed: true, threshold: 0.77, value: 0.7883 },
      inputs,
      excluded_sensitive: ["SEX", "MARRIAGE", "AGE"],
      score: { meaning: "ranking score in [0, 1]; not a calibrated probability of default." },
    });
  }
  if (url.pathname === "/predict" && req.method === "POST") {
    predictCalls += 1;
    const body = await readBody(req);
    const late = Number(body?.PAY_1 ?? 0);
    const score = late >= 2 ? 0.82 : 0.12;
    return send(res, 200, {
      request_id: "e2e-request",
      model_version: "e2e",
      threshold: 0.5,
      score_type: "uncalibrated_risk_score",
      predictions: [{ risk_score: score, prediction: score >= 0.5 ? 1 : 0 }],
    });
  }
  if (url.pathname === "/explain" && req.method === "POST") {
    return send(res, 200, {
      request_id: "e2e-request",
      explanations: [
        {
          risk_score: 0.5,
          factors: [
            { feature: "pay_delay_max", label: "Worst payment delay (months)", value: 3, contribution: 0.31, direction: "increases risk" },
            { feature: "util_mean", label: "Credit utilisation, 6-month average", value: 0.9, contribution: 0.12, direction: "increases risk" },
            { feature: "pay_ratio_mean", label: "Share of bill paid, 6-month average", value: 1.0, contribution: -0.2, direction: "decreases risk" },
          ],
        },
      ],
    });
  }
  return send(res, 404, { error: { code: "not_found", message: "no such route" } });
});

server.listen(PORT, "127.0.0.1", () => console.log(`mock API on ${PORT}`));
