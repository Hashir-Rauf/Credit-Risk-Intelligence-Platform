export type Band = { id: "lower" | "moderate" | "higher"; label: string; description: string };

/** Score below this is "lower risk" (about the bottom 40% of the training scores). */
export const LOWER_BOUND = 0.3;

/**
 * Map a risk score to a band. The score is a model ranking score, NOT a probability of default
 * (the model is uncalibrated, see docs/reviews/phase-2-review.md), so bands describe relative
 * standing and are anchored on the model's own decision threshold.
 */
export function riskBand(score: number, threshold: number): Band {
  if (score >= threshold) {
    return { id: "higher", label: "Higher risk", description: "Scores at or above the model's decision threshold." };
  }
  if (score >= LOWER_BOUND) {
    return { id: "moderate", label: "Moderate risk", description: "Between the lower band and the decision threshold." };
  }
  return { id: "lower", label: "Lower risk", description: "Scores in the lowest range the model produces." };
}

export function formatScore(score: number): string {
  return score.toFixed(2);
}

export type Factor = {
  feature: string;
  label: string;
  value: number;
  contribution: number;
  direction: string;
};

/** Largest absolute contribution first (the API already sorts, this keeps the UI correct regardless). */
export function sortFactors(factors: Factor[]): Factor[] {
  return [...factors].sort((a, b) => Math.abs(b.contribution) - Math.abs(a.contribution));
}

export function describeError(body: unknown, fallback: string): string {
  const error = (body as { error?: { message?: string } } | null)?.error;
  return error?.message ?? fallback;
}
