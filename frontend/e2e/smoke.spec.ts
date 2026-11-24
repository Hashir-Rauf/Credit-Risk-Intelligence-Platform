import { expect, test, type Request } from "@playwright/test";

const KEY = "e2e-secret-key-should-never-reach-the-browser"; // gitleaks:allow (fake key, e2e only)
const MOCK = "http://127.0.0.1:8099";

test("the score page renders the form built from the API schema, with the disclaimer", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "Score a customer" })).toBeVisible();
  await expect(page.getByText("Not a real lending decision tool")).toBeVisible();
  await expect(page.getByLabel("Credit limit")).toBeVisible();
  await expect(page.getByLabel("Status last month")).toBeVisible();
  // 20 model inputs, no sensitive attributes
  expect(await page.locator(".field input, .field select").count()).toBe(20);
  for (const banned of ["Sex", "Gender", "Age", "Marital"]) {
    await expect(page.getByLabel(banned, { exact: false })).toHaveCount(0);
  }
});

test("scoring an example customer shows a band, a score and the factors", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("button", { name: "Struggling" }).click();
  await page.getByRole("button", { name: "Score customer" }).click();
  await expect(page.getByTestId("risk-band")).toHaveText("Higher risk");
  await expect(page.getByTestId("risk-score")).toHaveText("0.82");
  await expect(page.getByText("Worst payment delay (months)")).toBeVisible();

  await page.getByRole("button", { name: "Reliable payer" }).click();
  await page.getByRole("button", { name: "Score customer" }).click();
  await expect(page.getByTestId("risk-band")).toHaveText("Lower risk");
  await expect(page.getByTestId("risk-score")).toHaveText("0.12");
});

test("invalid input is caught in the browser before any request is sent", async ({ page }) => {
  await page.goto("/");
  const before = (await (await page.request.get(`${MOCK}/__state`)).json()).predictCalls as number;
  await page.getByLabel("Status last month").fill("99");
  await page.getByRole("button", { name: "Score customer" }).click();
  await expect(page.getByText("At most 9")).toBeVisible();
  await expect(page.getByRole("alert").first()).toBeVisible();
  const after = (await (await page.request.get(`${MOCK}/__state`)).json()).predictCalls as number;
  expect(after).toBe(before);
});

test("the API key never reaches the browser, but the server attaches it", async ({ page }) => {
  const seen: Request[] = [];
  page.on("request", (request) => seen.push(request));
  const scripts: string[] = [];
  page.on("response", async (response) => {
    if (response.url().endsWith(".js")) scripts.push(await response.text().catch(() => ""));
  });

  await page.goto("/");
  await page.getByRole("button", { name: "Score customer" }).click();
  await expect(page.getByTestId("risk-score")).toBeVisible();

  // nothing the browser sent carries the key, in headers, URL or body
  for (const request of seen) {
    expect(request.url()).not.toContain(KEY);
    expect(JSON.stringify(await request.allHeaders())).not.toContain(KEY);
    expect(request.postData() ?? "").not.toContain(KEY);
  }
  // nothing the browser received contains it either: the page HTML and every script
  expect(await page.content()).not.toContain(KEY);
  for (const script of scripts) expect(script).not.toContain(KEY);
  // and the browser only ever talked to this app's own origin, never to the backend
  for (const request of seen) expect(new URL(request.url()).origin).toBe("http://127.0.0.1:3100");

  // while the mock API did receive the key, attached by the server
  expect((await (await page.request.get(`${MOCK}/__state`)).json()).lastKeySeen).toBe(KEY);
});

test("the route handlers reject bad bodies and expose no secret in errors", async ({ request }) => {
  const notJson = await request.post("/api/predict", { data: "{oops", headers: { "Content-Type": "application/json" } });
  expect(notJson.status()).toBe(400);
  expect(JSON.stringify(await notJson.json())).not.toContain(KEY);
  const scalar = await request.post("/api/predict", { data: '"just a string"', headers: { "Content-Type": "application/json" } });
  expect(scalar.status()).toBe(400);
  const health = await request.get("/api/health");
  expect(health.status()).toBe(200);
  expect(await health.json()).toMatchObject({ frontend: "ok", api: "ok" });
});

test("model, monitoring and admin pages load", async ({ page }) => {
  await page.goto("/model");
  await expect(page.getByRole("heading", { name: "Model & data" })).toBeVisible();
  await expect(page.getByText("Decision threshold")).toBeVisible();
  await expect(page.getByText(/credit-risk-default-model v/)).toBeVisible();
  await page.goto("/monitoring");
  await expect(page.getByText("Healthy")).toBeVisible();
  await page.goto("/admin");
  await expect(page.getByRole("button", { name: "Promote challenger" })).toBeDisabled();
});

test("pages are served with security headers", async ({ request }) => {
  const response = await request.get("/");
  const headers = response.headers();
  expect(headers["x-frame-options"]).toBe("DENY");
  expect(headers["x-content-type-options"]).toBe("nosniff");
  expect(headers["x-powered-by"]).toBeUndefined();
});
