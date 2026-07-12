export function svgLocalX(svg, clientX) {
  const rect = svg.getBoundingClientRect();
  const vb = svg.viewBox.baseVal;
  const scaleX = rect.width / vb.width;
  return vb.x + (clientX - rect.left) / scaleX;
}

export function xForIndex(i, { marginLeft, plotWidth, domain }) {
  const [d0, d1] = domain;
  return marginLeft + ((i - d0) / (d1 - d0)) * plotWidth;
}

export function nearestIndex(svg, clientX, { n, marginLeft, plotWidth, domain }) {
  const localX = svgLocalX(svg, clientX);
  const [d0, d1] = domain;
  const frac = (localX - marginLeft) / plotWidth;
  return Math.max(0, Math.min(n - 1, Math.round(d0 + frac * (d1 - d0))));
}

export function fmtMoney(v) {
  return Math.abs(v) >= 1000 ? `$${(v / 1000).toLocaleString(undefined, { maximumFractionDigits: 0 })}K` : `$${v.toLocaleString(undefined, { maximumFractionDigits: 0 })}`;
}

export function indexed(values) {
  const base = values[0];
  return values.map((v) => (v / base) * 100);
}

export function paddedDomain(values, padFrac = 0.05) {
  const min = Math.min(...values);
  const max = Math.max(...values);
  const pad = (max - min) * padFrac;
  return [min - pad, max + pad];
}

const MINUTES_PER_DAY = 24 * 60;

// Fraction (0..1) of the calendar day elapsed at `time` ("HH:MM"). Used to
// spread same-day trade markers by time instead of stacking them on one
// pixel: at low zoom the offset is a sliver of the visible range (looks like
// one point per day), at high zoom it becomes visible screen distance.
// Bars in this dataset aren't limited to regular trading hours (pre/post
// market included), so the fraction spans the full 24h day rather than
// clamping to a 9:30-16:00 window.
export function dayFraction(time) {
  if (!time) return 0;
  const [h, m] = time.split(":").map(Number);
  return (h * 60 + m) / MINUTES_PER_DAY;
}

export function dateTicksForDomain(domain, n) {
  const [d0, d1] = domain;
  const fracs = [0, 0.25, 0.5, 0.75, 1];
  const seen = new Set();
  const ticks = [];
  for (const f of fracs) {
    const i = Math.round(d0 + (d1 - d0) * f);
    if (!seen.has(i)) {
      seen.add(i);
      ticks.push(i);
    }
  }
  return ticks;
}
