"use client";

import { useMemo, useState } from "react";
import { Gauge } from "@/components/Gauge";
import {
  EDUCATION_LABELS,
  GROUPS,
  PRESETS,
  toPayload,
  validateField,
  validateForm,
  valuesFromCustomer,
  type InputSpec,
} from "@/lib/fields";
import { describeError, formatScore, riskBand, sortFactors, type Factor } from "@/lib/risk";

type Result = {
  score: number;
  flagged: boolean;
  factors: Factor[];
  requestId: string;
  factorsUnavailable: boolean;
};

type Props = { inputs: InputSpec[]; threshold: number; modelVersion: string };

export function ScoreForm({ inputs, threshold, modelVersion }: Props) {
  const specs = useMemo(() => Object.fromEntries(inputs.map((spec) => [spec.name, spec])), [inputs]);
  const [values, setValues] = useState<Record<string, string>>(() => valuesFromCustomer(PRESETS[0].values));
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState(false);
  const [failure, setFailure] = useState<string | null>(null);
  const [result, setResult] = useState<Result | null>(null);

  function setField(name: string, raw: string) {
    setValues((current) => ({ ...current, [name]: raw }));
    const spec = specs[name];
    if (spec && errors[name]) {
      const message = validateField(spec, raw);
      setErrors((current) => {
        const next = { ...current };
        if (message) next[name] = message;
        else delete next[name];
        return next;
      });
    }
  }

  function applyPreset(id: string) {
    const preset = PRESETS.find((p) => p.id === id);
    if (!preset) return;
    setValues(valuesFromCustomer(preset.values));
    setErrors({});
    setResult(null);
    setFailure(null);
  }

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    setFailure(null);
    const problems = validateForm(inputs, values);
    setErrors(problems);
    if (Object.keys(problems).length > 0) {
      setFailure("Fix the highlighted fields and try again.");
      return;
    }
    setBusy(true);
    try {
      const payload = toPayload(inputs, values);
      const [scored, explained] = await Promise.all([
        fetch("/api/predict", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) }),
        fetch("/api/explain?top_k=5", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) }),
      ]);
      const scoredBody = await scored.json();
      if (!scored.ok) {
        setResult(null);
        setFailure(describeError(scoredBody, "The scoring service rejected the request."));
        return;
      }
      const prediction = scoredBody.predictions[0];
      let factors: Factor[] = [];
      let factorsUnavailable = false;
      if (explained.ok) {
        factors = sortFactors((await explained.json()).explanations[0].factors);
      } else {
        factorsUnavailable = true;
      }
      setResult({
        score: prediction.risk_score,
        flagged: prediction.prediction === 1,
        factors,
        requestId: scoredBody.request_id,
        factorsUnavailable,
      });
    } catch {
      setResult(null);
      setFailure("Could not reach the server. Check your connection and try again.");
    } finally {
      setBusy(false);
    }
  }

  const band = result ? riskBand(result.score, threshold) : null;
  const maxFactor = result ? Math.max(...result.factors.map((f) => Math.abs(f.contribution)), 1e-9) : 1;

  return (
    <div className="layout">
      <form className="card" onSubmit={submit} noValidate aria-label="Customer details">
        <div className="presets" role="group" aria-label="Example customers">
          {PRESETS.map((preset) => (
            <button key={preset.id} type="button" onClick={() => applyPreset(preset.id)} title={preset.description}>
              {preset.label}
            </button>
          ))}
        </div>

        {GROUPS.map((group) => (
          <fieldset key={group.title}>
            <legend>{group.title}</legend>
            <p className="hint">{group.hint}</p>
            <div className="fields">
              {group.fields.map((field) => {
                const spec = specs[field.name];
                if (!spec) return null;
                const id = `f-${field.name}`;
                const error = errors[field.name];
                return (
                  <div className="field" key={field.name}>
                    <label htmlFor={id}>{field.label}</label>
                    {spec.allowed && field.name === "EDUCATION" ? (
                      <select id={id} value={values[field.name] ?? ""} onChange={(e) => setField(field.name, e.target.value)}>
                        {spec.allowed.map((option) => (
                          <option key={option} value={option}>
                            {EDUCATION_LABELS[option] ?? option}
                          </option>
                        ))}
                      </select>
                    ) : (
                      <input
                        id={id}
                        inputMode="numeric"
                        value={values[field.name] ?? ""}
                        aria-invalid={error ? "true" : "false"}
                        aria-describedby={error ? `${id}-error` : undefined}
                        onChange={(e) => setField(field.name, e.target.value)}
                      />
                    )}
                    <div className="error" id={`${id}-error`} role={error ? "alert" : undefined}>
                      {error}
                    </div>
                  </div>
                );
              })}
            </div>
          </fieldset>
        ))}

        <button type="submit" className="primary" disabled={busy}>
          {busy ? "Scoring…" : "Score customer"}
        </button>
        {failure ? (
          <div className="alert" role="alert">
            {failure}
          </div>
        ) : null}
      </form>

      <aside className="card result" aria-live="polite" aria-label="Result">
        {result && band ? (
          <>
            <Gauge score={result.score} threshold={threshold} label={`Risk score ${formatScore(result.score)} of 1, ${band.label}`} />
            <div style={{ textAlign: "center" }}>
              <div className="score" data-testid="risk-score">
                {formatScore(result.score)}
              </div>
              <span className={`band ${band.id}`} data-testid="risk-band">
                {band.label}
              </span>
              <p className="small muted" style={{ marginTop: 8 }}>
                {band.description} {result.flagged ? "Flagged at the model's threshold." : "Not flagged at the model's threshold."}
              </p>
            </div>
            <h2 style={{ marginTop: 20 }}>What moved the score</h2>
            {result.factorsUnavailable ? (
              <p className="small muted">The explanation service did not answer; the score above is still valid.</p>
            ) : (
              <ul className="factors">
                {result.factors.map((factor) => {
                  const width = (Math.abs(factor.contribution) / maxFactor) * 50;
                  const up = factor.contribution > 0;
                  return (
                    <li key={factor.feature}>
                      <span>{factor.label}</span> <span className="muted">({up ? "raises" : "lowers"} the score)</span>
                      <div className="factor-bar" aria-hidden="true">
                        <span className={up ? "up" : "down"} style={{ width: `${width}%` }} />
                      </div>
                    </li>
                  );
                })}
              </ul>
            )}
            <p className="small muted">
              Factors describe the model&apos;s behaviour, not causes in the real world. Model version {modelVersion}. Request{" "}
              <code>{result.requestId}</code>.
            </p>
          </>
        ) : (
          <>
            <h2>Result</h2>
            <p className="muted">Choose an example or edit the fields, then press “Score customer”.</p>
            <p className="small muted">
              Sensitive attributes (sex, marital status, age) are not collected and are not used by the model.
            </p>
          </>
        )}
      </aside>
    </div>
  );
}
