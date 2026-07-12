export default function Tooltip({ tooltip }) {
  if (!tooltip) return null;
  const pad = 14;
  let left = tooltip.x + pad;
  let top = tooltip.y + pad;
  if (left + 260 > window.innerWidth) left = tooltip.x - 260 - pad;
  if (top + 200 > window.innerHeight) top = tooltip.y - 200 - pad;
  return (
    <div className="tooltip" style={{ display: "block", left, top }}>
      {tooltip.content}
    </div>
  );
}
