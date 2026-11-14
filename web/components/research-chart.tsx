"use client";

import { useMemo, useRef, useState } from "react";

import type { ChartSeries } from "@/lib/research";
import { niceTicks } from "@/lib/research";

/**
 * Cumulative-level line chart: the index and its broad benchmark proxies, all
 * rebased to 1000 on the same base date (one y-axis by construction). Colors
 * are the validated reference categorical slots 1–4 in fixed order; identity
 * is never color-alone — legend + direct end labels + the stats table below.
 */
const SLOT_COLORS = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100"];

const W = 760;
const H = 300;
const M = { top: 12, right: 64, bottom: 28, left: 52 };

export function ResearchChart({
  dates,
  series,
  label,
}: {
  dates: string[];
  series: ChartSeries[];
  label: string;
}) {
  const [hover, setHover] = useState<number | null>(null);
  const svgRef = useRef<SVGSVGElement>(null);

  const geom = useMemo(() => {
    const all = series.flatMap((s) => s.values.filter((v): v is number => v !== null));
    const lo = Math.min(...all);
    const hi = Math.max(...all);
    const ticks = niceTicks(lo, hi, 7);
    const yMin = Math.min(ticks[0], lo);
    const yMax = Math.max(ticks[ticks.length - 1], hi);
    const iw = W - M.left - M.right;
    const ih = H - M.top - M.bottom;
    const x = (i: number) => M.left + (dates.length > 1 ? (i / (dates.length - 1)) * iw : 0);
    const y = (v: number) => M.top + ih - ((v - yMin) / (yMax - yMin)) * ih;
    const paths = series.map((s) => {
      let d = "";
      let pen = false;
      s.values.forEach((v, i) => {
        if (v === null) {
          pen = false;
          return;
        }
        d += `${pen ? "L" : "M"}${x(i).toFixed(1)},${y(v).toFixed(1)}`;
        pen = true;
      });
      return d;
    });
    const years = dates
      .map((d, i) => ({ d, i }))
      .filter(({ d }, n, arr) => n === 0 || d.slice(0, 4) !== arr[n - 1].d.slice(0, 4));
    // End labels, nudged apart so they never overlap.
    const ends = series
      .map((s, n) => {
        const last = [...s.values].reverse().find((v) => v !== null) ?? null;
        return { n, label: s.label, y: last === null ? null : y(last) };
      })
      .filter((e): e is { n: number; label: string; y: number } => e.y !== null)
      .sort((a, b) => a.y - b.y);
    for (let k = 1; k < ends.length; k++) {
      if (ends[k].y - ends[k - 1].y < 13) ends[k].y = ends[k - 1].y + 13;
    }
    return { ticks, x, y, paths, years, ends, iw };
  }, [dates, series]);

  const onMove = (e: React.PointerEvent<SVGSVGElement>) => {
    const svg = svgRef.current;
    if (!svg || dates.length === 0) return;
    const rect = svg.getBoundingClientRect();
    const px = ((e.clientX - rect.left) / rect.width) * W;
    const t = (px - M.left) / geom.iw;
    setHover(Math.max(0, Math.min(dates.length - 1, Math.round(t * (dates.length - 1)))));
  };

  const hx = hover !== null ? geom.x(hover) : 0;
  const tipLeft = hover !== null && hx > W * 0.62;

  return (
    <figure className="as-chart" style={{ margin: 0 }}>
      <div className="as-chart-legend" aria-hidden="true">
        {series.map((s, n) => (
          <span key={s.key}>
            <i style={{ background: SLOT_COLORS[n] }} />
            {s.label}
          </span>
        ))}
      </div>
      <div style={{ position: "relative" }}>
        <svg
          ref={svgRef}
          viewBox={`0 0 ${W} ${H}`}
          role="img"
          aria-label={label}
          style={{ display: "block", width: "100%", height: "auto", touchAction: "pan-y" }}
          onPointerMove={onMove}
          onPointerLeave={() => setHover(null)}
        >
          {geom.ticks.map((t) => (
            <g key={t}>
              <line
                x1={M.left}
                x2={W - M.right}
                y1={geom.y(t)}
                y2={geom.y(t)}
                stroke="var(--color-border)"
                strokeWidth={t === 1000 ? 1.5 : 1}
                strokeDasharray={t === 1000 ? undefined : "2 4"}
              />
              <text x={M.left - 8} y={geom.y(t) + 4} textAnchor="end" className="as-chart-tick">
                {t.toLocaleString("en-US")}
              </text>
            </g>
          ))}
          {geom.years.map(({ d, i }) => (
            <text key={d} x={geom.x(i)} y={H - 8} textAnchor="start" className="as-chart-tick">
              {d.slice(0, 4)}
            </text>
          ))}
          {geom.paths.map((d, n) => (
            <path
              key={series[n].key}
              d={d}
              fill="none"
              stroke={SLOT_COLORS[n]}
              strokeWidth={n === 0 ? 2.5 : 1.75}
              strokeLinejoin="round"
              strokeLinecap="round"
            />
          ))}
          {geom.ends.map((e) => (
            <text key={e.label} x={W - M.right + 6} y={e.y + 4} className="as-chart-endlabel">
              {e.label}
            </text>
          ))}
          {hover !== null ? (
            <g>
              <line
                x1={hx}
                x2={hx}
                y1={M.top}
                y2={H - M.bottom}
                stroke="var(--color-muted)"
                strokeWidth={1}
              />
              {series.map((s, n) => {
                const v = s.values[hover];
                return v === null ? null : (
                  <circle
                    key={s.key}
                    cx={hx}
                    cy={geom.y(v)}
                    r={4}
                    fill={SLOT_COLORS[n]}
                    stroke="var(--color-bg)"
                    strokeWidth={2}
                  />
                );
              })}
            </g>
          ) : null}
        </svg>
        {hover !== null ? (
          <div
            className="as-chart-tip"
            style={{
              left: tipLeft ? undefined : `${(hx / W) * 100 + 1.5}%`,
              right: tipLeft ? `${100 - (hx / W) * 100 + 1.5}%` : undefined,
            }}
          >
            <strong>{dates[hover]}</strong>
            {series.map((s, n) => {
              const v = s.values[hover];
              return (
                <div key={s.key}>
                  <i style={{ background: SLOT_COLORS[n] }} />
                  <span>{s.label}</span>
                  <b>{v === null ? "—" : v.toLocaleString("en-US", { maximumFractionDigits: 1 })}</b>
                </div>
              );
            })}
          </div>
        ) : null}
      </div>
    </figure>
  );
}
