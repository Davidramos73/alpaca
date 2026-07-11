import { useCallback, useRef, useState } from "react";
import { svgLocalX } from "../lib/chartMath";

// Zoom by dragging a horizontal range, wheel to zoom around the cursor,
// double click to reset. Mirrors the original vanilla-JS attachZoom().
export function useZoom({ svgRef, n, marginLeft, plotWidth }) {
  const [domain, setDomain] = useState([0, n - 1]);
  const [dragRect, setDragRect] = useState(null);
  const dragStartRef = useRef(null);

  const idxAt = useCallback(
    (x, d) => d[0] + ((x - marginLeft) / plotWidth) * (d[1] - d[0]),
    [marginLeft, plotWidth]
  );

  const onPointerDown = useCallback(
    (e) => {
      const svg = svgRef.current;
      const x = svgLocalX(svg, e.clientX);
      dragStartRef.current = x;
      e.target.setPointerCapture(e.pointerId);
      setDragRect({ x, width: 0 });
    },
    [svgRef]
  );

  const onPointerMove = useCallback(
    (e) => {
      if (dragStartRef.current === null) return;
      const svg = svgRef.current;
      const cur = svgLocalX(svg, e.clientX);
      const x1 = Math.min(dragStartRef.current, cur);
      const x2 = Math.max(dragStartRef.current, cur);
      setDragRect({ x: x1, width: x2 - x1 });
    },
    [svgRef]
  );

  const endDrag = useCallback(
    (e) => {
      if (dragStartRef.current === null) return;
      const svg = svgRef.current;
      const cur = svgLocalX(svg, e.clientX);
      const x1 = Math.min(dragStartRef.current, cur);
      const x2 = Math.max(dragStartRef.current, cur);
      dragStartRef.current = null;
      setDragRect(null);
      if (x2 - x1 <= 5) return;
      const i1 = Math.max(0, Math.floor(idxAt(x1, domain)));
      const i2 = Math.min(n - 1, Math.ceil(idxAt(x2, domain)));
      if (i2 - i1 < 2) return;
      setDomain([i1, i2]);
    },
    [svgRef, domain, idxAt, n]
  );

  const onPointerUp = endDrag;
  const onPointerCancel = useCallback(() => {
    dragStartRef.current = null;
    setDragRect(null);
  }, []);

  const onDoubleClick = useCallback(() => setDomain([0, n - 1]), [n]);

  const onWheel = useCallback(
    (e) => {
      e.preventDefault();
      const svg = svgRef.current;
      const [d0, d1] = domain;
      const range = d1 - d0;
      const factor = e.deltaY < 0 ? 0.8 : 1.25;
      const newRange = Math.max(2, Math.min(n - 1, range * factor));
      const localX = svgLocalX(svg, e.clientX);
      const frac = Math.max(0, Math.min(1, (localX - marginLeft) / plotWidth));
      const centerIdx = d0 + frac * range;
      let newD0 = centerIdx - frac * newRange;
      let newD1 = newD0 + newRange;
      if (newD0 < 0) {
        newD1 -= newD0;
        newD0 = 0;
      }
      if (newD1 > n - 1) {
        newD0 -= newD1 - (n - 1);
        newD1 = n - 1;
      }
      setDomain([Math.max(0, newD0), newD1]);
    },
    [svgRef, domain, n, marginLeft, plotWidth]
  );

  return {
    domain,
    dragRect,
    handlers: { onPointerDown, onPointerMove, onPointerUp, onPointerCancel, onWheel, onDoubleClick },
  };
}
