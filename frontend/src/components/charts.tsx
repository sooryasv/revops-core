import { Money } from "./ui";
import { formatPaise } from "../lib/format";

interface BarDatum { label: string; value: number; detail?: string }

/** Horizontal bars drawn by hand: the width is only a ratio for display; the number shown is the server's figure. */
export function BarList({ data, caption }: { data: BarDatum[]; caption: string }) {
  const max = Math.max(1, ...data.map((d) => d.value));
  return (
    <figure className="barlist">
      <figcaption className="sr-only">{caption}</figcaption>
      <ul>
        {data.map((d) => (
          <li key={d.label}>
            <span className="bar-label">{d.label}</span>
            <span className="bar-track" aria-hidden="true">
              <span className="bar-fill" style={{ width: `${(d.value / max) * 100}%` }} />
            </span>
            <span className="bar-value">
              <Money paise={d.value} />
              {d.detail && <span className="muted small"> · {d.detail}</span>}
            </span>
          </li>
        ))}
      </ul>
    </figure>
  );
}

interface Point { label: string; billed: number; collected: number }

/** Two-series line chart in SVG. Axis labels use the server's integer paise formatted for display only. */
export function CollectionsChart({ points }: { points: Point[] }) {
  const w = 640, h = 220, pad = 36;
  const max = Math.max(1, ...points.flatMap((p) => [p.billed, p.collected]));
  const x = (i: number) => pad + (points.length <= 1 ? 0 : (i * (w - 2 * pad)) / (points.length - 1));
  const y = (v: number) => h - pad - (v / max) * (h - 2 * pad);
  const path = (key: "billed" | "collected") => points.map((p, i) => `${i ? "L" : "M"}${x(i)},${y(p[key])}`).join(" ");
  return (
    <figure>
      <svg viewBox={`0 0 ${w} ${h}`} role="img" aria-label="Billed and collected over time" className="chart">
        <line x1={pad} y1={h - pad} x2={w - pad} y2={h - pad} className="axis" />
        <line x1={pad} y1={pad} x2={pad} y2={h - pad} className="axis" />
        <text x={pad - 4} y={pad} textAnchor="end" className="tick">{formatPaise(max)}</text>
        <text x={pad - 4} y={h - pad} textAnchor="end" className="tick">₹0</text>
        {points.length > 0 && <text x={pad} y={h - 8} className="tick">{points[0]?.label}</text>}
        {points.length > 1 && <text x={w - pad} y={h - 8} textAnchor="end" className="tick">{points[points.length - 1]?.label}</text>}
        <path d={path("billed")} className="line line-billed" fill="none" />
        <path d={path("collected")} className="line line-collected" fill="none" />
      </svg>
      <figcaption className="legend">
        <span><i className="swatch swatch-billed" /> Billed</span>
        <span><i className="swatch swatch-collected" /> Collected</span>
      </figcaption>
    </figure>
  );
}
