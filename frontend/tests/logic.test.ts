import { describe, expect, it } from "vitest";
import {
  GROUPS,
  PRESETS,
  toPayload,
  validateField,
  validateForm,
  valuesFromCustomer,
  type InputSpec,
} from "../src/lib/fields";
import { LOWER_BOUND, describeError, formatScore, riskBand, sortFactors } from "../src/lib/risk";

// The same 20 inputs the API publishes in /model-info (shape and rules copied from feature_schema.json).
const SPECS: InputSpec[] = [
  { name: "LIMIT_BAL", dtype: "number", min: 1, max: 5_000_000 },
  { name: "EDUCATION", dtype: "integer", allowed: [0, 1, 2, 3, 4, 5, 6] },
  ...Array.from({ length: 6 }, (_, i) => ({ name: `PAY_${i + 1}`, dtype: "integer" as const, min: -2, max: 9 })),
  ...Array.from({ length: 6 }, (_, i) => ({ name: `BILL_AMT${i + 1}`, dtype: "number" as const, min: -1_000_000, max: 5_000_000 })),
  ...Array.from({ length: 6 }, (_, i) => ({ name: `PAY_AMT${i + 1}`, dtype: "number" as const, min: 0, max: 10_000_000 })),
];
const byName = Object.fromEntries(SPECS.map((s) => [s.name, s]));

describe("riskBand", () => {
  it("anchors the top band on the model threshold", () => {
    expect(riskBand(0.5, 0.5).id).toBe("higher");
    expect(riskBand(0.49, 0.5).id).toBe("moderate");
    expect(riskBand(0.9, 0.5).id).toBe("higher");
  });
  it("has a lower band below the lower bound", () => {
    expect(riskBand(LOWER_BOUND - 0.01, 0.5).id).toBe("lower");
    expect(riskBand(LOWER_BOUND, 0.5).id).toBe("moderate");
    expect(riskBand(0, 0.5).id).toBe("lower");
  });
  it("never calls the score a probability", () => {
    for (const score of [0, 0.2, 0.4, 0.8]) {
      const text = JSON.stringify(riskBand(score, 0.5)).toLowerCase();
      expect(text).not.toContain("probability of default");
    }
  });
});

describe("formatting and helpers", () => {
  it("formats scores with two decimals", () => {
    expect(formatScore(0.4096)).toBe("0.41");
    expect(formatScore(1)).toBe("1.00");
  });
  it("sorts factors by absolute contribution", () => {
    const factor = (feature: string, contribution: number) => ({ feature, label: feature, value: 0, contribution, direction: "" });
    const sorted = sortFactors([factor("a", 0.1), factor("b", -0.5), factor("c", 0.3)]);
    expect(sorted.map((f) => f.feature)).toEqual(["b", "c", "a"]);
  });
  it("describes API errors and falls back safely", () => {
    expect(describeError({ error: { message: "boom" } }, "x")).toBe("boom");
    expect(describeError(null, "fallback")).toBe("fallback");
    expect(describeError({}, "fallback")).toBe("fallback");
  });
});

describe("validateField", () => {
  it("rejects empty, non-numeric and non-integer input", () => {
    expect(validateField(byName.PAY_1, "")).toBe("Required");
    expect(validateField(byName.PAY_1, "abc")).toBe("Enter a number");
    expect(validateField(byName.PAY_1, "1.5")).toBe("Whole number only");
  });
  it("enforces ranges and allowed sets", () => {
    expect(validateField(byName.PAY_1, "10")).toBe("At most 9");
    expect(validateField(byName.PAY_1, "-3")).toBe("At least -2");
    expect(validateField(byName.EDUCATION, "9")).toMatch(/Choose one of/);
    expect(validateField(byName.LIMIT_BAL, "0")).toBe("At least 1");
  });
  it("accepts valid values", () => {
    expect(validateField(byName.PAY_1, "-2")).toBeNull();
    expect(validateField(byName.EDUCATION, "2")).toBeNull();
    expect(validateField(byName.LIMIT_BAL, "50000.5")).toBeNull();
  });
});

describe("presets and payloads", () => {
  it("every preset is valid against the API schema", () => {
    for (const preset of PRESETS) {
      expect(validateForm(SPECS, valuesFromCustomer(preset.values)), preset.id).toEqual({});
    }
  });
  it("every form field exists in the API schema, and every schema field has a form field", () => {
    const formFields = GROUPS.flatMap((g) => g.fields.map((f) => f.name)).sort();
    expect(formFields).toEqual(SPECS.map((s) => s.name).sort());
  });
  it("never includes sensitive attributes", () => {
    const formFields = GROUPS.flatMap((g) => g.fields.map((f) => f.name));
    for (const banned of ["SEX", "AGE", "MARRIAGE"]) expect(formFields).not.toContain(banned);
    for (const preset of PRESETS) expect(Object.keys(preset.values)).not.toContain("SEX");
  });
  it("builds a numeric payload with exactly the schema fields", () => {
    const payload = toPayload(SPECS, valuesFromCustomer(PRESETS[0].values));
    expect(Object.keys(payload).sort()).toEqual(SPECS.map((s) => s.name).sort());
    expect(Object.values(payload).every((v) => typeof v === "number")).toBe(true);
  });
  it("reports every invalid field of a whole form", () => {
    const values = valuesFromCustomer(PRESETS[0].values);
    values.PAY_1 = "99";
    values.LIMIT_BAL = "";
    expect(Object.keys(validateForm(SPECS, values)).sort()).toEqual(["LIMIT_BAL", "PAY_1"]);
  });
});
