import { useCallback, useState } from "react";

export function useTooltip() {
  const [tooltip, setTooltip] = useState(null); // {x, y, content}

  const show = useCallback((clientX, clientY, content) => {
    setTooltip({ x: clientX, y: clientY, content });
  }, []);

  const hide = useCallback(() => setTooltip(null), []);

  return { tooltip, show, hide };
}
