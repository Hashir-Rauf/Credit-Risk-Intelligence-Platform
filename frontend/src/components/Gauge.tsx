import { LOWER_BOUND } from "@/lib/risk";

const CX = 100;
const CY = 100;
const R = 80;

/** Point on the semicircle: score 0 is the left end, 1 the right end. */
function point(score: number, radius = R) {
  const angle = Math.PI * (1 - score);
  return { x: CX + radius * Math.cos(angle), y: CY - radius * Math.sin(angle) };
}

function arc(from: number, to: number) {
  const a = point(from);
  const b = point(to);
  return `M ${a.x.toFixed(2)} ${a.y.toFixed(2)} A ${R} ${R} 0 0 1 ${b.x.toFixed(2)} ${b.y.toFixed(2)}`;
}

export function Gauge({ score, threshold, label }: { score: number; threshold: number; label: string }) {
  const clamped = Math.min(1, Math.max(0, score));
  const tip = point(clamped, R - 6);
  const base = point(clamped, 14);
  return (
    <svg className="gauge" viewBox="0 0 200 120" role="img" aria-label={label}>
      <path d={arc(0, LOWER_BOUND)} fill="none" stroke="var(--lower)" strokeWidth="14" />
      <path d={arc(LOWER_BOUND, threshold)} fill="none" stroke="var(--moderate)" strokeWidth="14" />
      <path d={arc(threshold, 1)} fill="none" stroke="var(--higher)" strokeWidth="14" />
      <line x1={base.x} y1={base.y} x2={tip.x} y2={tip.y} stroke="var(--text)" strokeWidth="3" strokeLinecap="round" />
      <circle cx={CX} cy={CY} r="6" fill="var(--text)" />
      <text x="14" y="116" fontSize="9" fill="var(--muted)">
        0
      </text>
      <text x="178" y="116" fontSize="9" fill="var(--muted)">
        1
      </text>
    </svg>
  );
}
