#!/usr/bin/env bash
# Corre optimize.py --export-equity-json y encadena plot_equity.py sobre el
# JSON resultante, para no tener que copiar rutas a mano entre los dos pasos.
# Cualquier argumento se reenvía tal cual a optimize.py (--symbol, --date-start,
# --date-end, --buy-amount, --fee-pct, --no-profit-pool, --intervals con un
# solo valor).
set -euo pipefail
cd "$(dirname "$0")"

optimize_out=$(mktemp)
trap 'rm -f "$optimize_out"' EXIT

python3 optimize.py --export-equity-json "$@" | tee "$optimize_out"

# Buscar el primer mensaje del JSON principal (sin "con trailing")
json_path=$(grep -E '^JSON de equity\s+:' "$optimize_out" | head -n1 | sed -E 's/^JSON de equity\s*:\s*//')
if [ -z "$json_path" ]; then
    echo "No se encontró la ruta del JSON de equity en la salida de optimize.py — ¿falló la corrida?" >&2
    exit 1
fi

plot_out=$(mktemp)
trap 'rm -f "$optimize_out" "$plot_out"' EXIT

python3 plot_equity.py "$json_path" | tee "$plot_out"

html_path=$(grep "HTML generado" "$plot_out" | sed -E 's/^HTML generado:\s*//')
if [ -z "$html_path" ]; then
    echo "No se encontró la ruta del HTML en la salida de plot_equity.py" >&2
    exit 1
fi

abs_path=$(realpath "$html_path")
file_url="file://$abs_path"

echo ""
echo "Gráfico listo:"
echo "$file_url"

if command -v xdg-open >/dev/null 2>&1; then
    xdg-open "$file_url" >/dev/null 2>&1 &
elif command -v open >/dev/null 2>&1; then
    open "$file_url" >/dev/null 2>&1 &
fi