/**
 * Field metadata for the scoring form. The authoritative rules (types, ranges, allowed values) come
 * from the API's /model-info `inputs` list at run time; this file only adds human-friendly labels,
 * grouping and example customers. Sensitive attributes (SEX, MARRIAGE, AGE) are deliberately absent:
 * the model does not use them.
 */

export type InputSpec = {
  name: string;
  dtype: "number" | "integer";
  min?: number;
  max?: number;
  allowed?: number[];
};

export type Customer = Record<string, number>;

const MONTHS = ["last month", "2 months ago", "3 months ago", "4 months ago", "5 months ago", "6 months ago"];

export const GROUPS: { title: string; hint: string; fields: { name: string; label: string }[] }[] = [
  {
    title: "Account",
    hint: "Credit limit and education level.",
    fields: [
      { name: "LIMIT_BAL", label: "Credit limit" },
      { name: "EDUCATION", label: "Education level" },
    ],
  },
  {
    title: "Repayment status",
    hint: "-2 no consumption, -1 paid in full, 0 revolving credit, 1..9 months late.",
    fields: MONTHS.map((month, i) => ({ name: `PAY_${i + 1}`, label: `Status ${month}` })),
  },
  {
    title: "Bill amounts",
    hint: "Statement balance each month.",
    fields: MONTHS.map((month, i) => ({ name: `BILL_AMT${i + 1}`, label: `Bill ${month}` })),
  },
  {
    title: "Payments made",
    hint: "Amount paid each month.",
    fields: MONTHS.map((month, i) => ({ name: `PAY_AMT${i + 1}`, label: `Paid ${month}` })),
  },
];

export const EDUCATION_LABELS: Record<number, string> = {
  1: "Graduate school",
  2: "University",
  3: "High school",
  4: "Other",
  0: "Unknown (0)",
  5: "Unknown (5)",
  6: "Unknown (6)",
};

function customer(limit: number, education: number, pay: number[], bill: number[], paid: number[]): Customer {
  const out: Customer = { LIMIT_BAL: limit, EDUCATION: education };
  for (let i = 0; i < 6; i++) {
    out[`PAY_${i + 1}`] = pay[i];
    out[`BILL_AMT${i + 1}`] = bill[i];
    out[`PAY_AMT${i + 1}`] = paid[i];
  }
  return out;
}

export const PRESETS: { id: string; label: string; description: string; values: Customer }[] = [
  {
    id: "reliable",
    label: "Reliable payer",
    description: "Pays in full, low balances.",
    values: customer(200000, 2, [-1, -1, -1, -1, -1, -1], [8000, 9000, 7500, 8200, 8800, 9100], [9000, 7500, 8200, 8800, 9100, 9000]),
  },
  {
    id: "revolving",
    label: "Carries a balance",
    description: "Always on time, but pays only a little each month.",
    values: customer(80000, 2, [0, 0, 0, 0, 0, 0], [42000, 40500, 39000, 38000, 36500, 35000], [2000, 2000, 2000, 2000, 2000, 2000]),
  },
  {
    id: "stretched",
    label: "Stretched",
    description: "Near the limit, recent late payments.",
    values: customer(50000, 3, [1, 2, 0, 0, 0, 0], [49000, 48000, 47000, 46000, 45000, 44000], [1500, 1000, 1000, 1200, 1000, 1000]),
  },
  {
    id: "struggling",
    label: "Struggling",
    description: "Months late, almost no payments.",
    values: customer(30000, 3, [3, 3, 2, 2, 2, 2], [31000, 30500, 29800, 29000, 28000, 27000], [0, 0, 500, 0, 0, 0]),
  },
];

/** Check one field against the API's rule for it; returns an error message or null. */
export function validateField(spec: InputSpec, raw: string): string | null {
  if (raw.trim() === "") return "Required";
  const value = Number(raw);
  if (!Number.isFinite(value)) return "Enter a number";
  if (spec.dtype === "integer" && !Number.isInteger(value)) return "Whole number only";
  if (spec.allowed && !spec.allowed.includes(value)) return `Choose one of ${spec.allowed.join(", ")}`;
  if (spec.min !== undefined && value < spec.min) return `At least ${spec.min}`;
  if (spec.max !== undefined && value > spec.max) return `At most ${spec.max}`;
  return null;
}

/** Validate the whole form; returns a map of field name -> message (empty when valid). */
export function validateForm(specs: InputSpec[], values: Record<string, string>): Record<string, string> {
  const errors: Record<string, string> = {};
  for (const spec of specs) {
    const message = validateField(spec, values[spec.name] ?? "");
    if (message) errors[spec.name] = message;
  }
  return errors;
}

/** Form strings -> the JSON the API expects (numbers only, only the known fields). */
export function toPayload(specs: InputSpec[], values: Record<string, string>): Customer {
  const payload: Customer = {};
  for (const spec of specs) payload[spec.name] = Number(values[spec.name]);
  return payload;
}

export function valuesFromCustomer(customerValues: Customer): Record<string, string> {
  return Object.fromEntries(Object.entries(customerValues).map(([k, v]) => [k, String(v)]));
}
