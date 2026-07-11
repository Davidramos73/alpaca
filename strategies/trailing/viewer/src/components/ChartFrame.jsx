import { useEffect, useRef } from "react";

export default function ChartFrame({
  id,
  svgRef,
  width,
  height,
  margin,
  dragRect,
  zoomHandlers,
  onHitPointerMove,
  onHitPointerLeave,
  crosshairX,
  gridContent,
  xLabels,
  children,
}) {
  const plotW = width - margin.left - margin.right;
  const plotH = height - margin.top - margin.bottom;
  const hitRef = useRef(null);

  // React's synthetic onWheel is registered as a passive listener, so
  // preventDefault() inside it can't stop the page from scrolling. Attach
  // a native listener instead so wheel-zoom doesn't also scroll the page.
  useEffect(() => {
    const el = hitRef.current;
    if (!el) return;
    el.addEventListener("wheel", zoomHandlers.onWheel, { passive: false });
    return () => el.removeEventListener("wheel", zoomHandlers.onWheel);
  }, [zoomHandlers.onWheel]);

  return (
    <svg
      ref={svgRef}
      viewBox={`0 0 ${width} ${height}`}
      style={{ aspectRatio: `${width}/${height}` }}
      onDoubleClick={zoomHandlers.onDoubleClick}
    >
      <defs>
        <clipPath id={`clip-${id}`}>
          <rect x={margin.left} y={0} width={plotW} height={height} />
        </clipPath>
      </defs>
      {gridContent}
      {xLabels}
      {crosshairX != null && (
        <line
          className="crosshair"
          x1={crosshairX}
          x2={crosshairX}
          y1={margin.top}
          y2={height - margin.bottom}
        />
      )}
      <rect
        ref={hitRef}
        className="hitrect"
        x={margin.left}
        y={margin.top}
        width={plotW}
        height={plotH}
        fill="transparent"
        onPointerMove={(e) => {
          if (dragRect) {
            zoomHandlers.onPointerMove(e);
          } else {
            onHitPointerMove(e);
          }
        }}
        onPointerLeave={onHitPointerLeave}
        onPointerDown={zoomHandlers.onPointerDown}
        onPointerUp={zoomHandlers.onPointerUp}
        onPointerCancel={zoomHandlers.onPointerCancel}
      />
      <g clipPath={`url(#clip-${id})`}>{children}</g>
      {dragRect && (
        <rect
          className="zoom-selection"
          x={dragRect.x}
          y={margin.top}
          width={dragRect.width}
          height={plotH}
        />
      )}
    </svg>
  );
}
