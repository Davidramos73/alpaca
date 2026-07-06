# Walk-Forward — Bitácora de hallazgos

Diseño original: [`docs/superpowers/specs/2026-07-04-walk-forward-design.md`](superpowers/specs/2026-07-04-walk-forward-design.md).

Este archivo es una bitácora viva: cada vez que se corre `walk_forward.py` con datos nuevos, se agrega una entrada abajo para poder comparar la evolución en el tiempo. No reemplaza los `.log`/`.csv` que genera cada corrida (esos quedan como evidencia cruda) — acá va la interpretación y las conclusiones.

## Cómo retomar esto la semana que viene

1. Actualizar el caché con datos nuevos (borrar el `.pkl` viejo o correr con un `--date-end` más reciente para que `load_bars()` descargue el rango ampliado).
2. Repetir al menos una corrida comparable a las de abajo (mismo símbolo, mismos `--intervals`, mismo `--train-weeks`) para que el veredicto sea comparable en el tiempo.
3. Agregar una entrada nueva en "Historial de corridas" con fecha, comando exacto, resultado y qué cambió respecto a la entrada anterior.
4. Si el patrón se sostiene entrada tras entrada, ahí sí empieza a valer la pena tratarlo como señal real y no como ruido de una sola muestra.

---

## Pregunta central

¿Vale la pena que el bot ajuste `buy_drop_pct` / `sell_rise_pct` / intervalo automáticamente semana a semana en base al optimizador, en vez de usar un valor fijo?

## Hallazgos acumulados (al 2026-07-06)

### 1. TSLA, enero–julio 2026 (27 semanas, intervalo 20, train-weeks 1)

**El auto-ajuste NO se justifica.**

| Estrategia | ROI |
|---|---|
| Fija-mediana (drop=1%, rise=4%, intervalo=20) | **+13.32%** |
| WF-pico (re-optimiza con el pico de la semana anterior) | +9.59% |
| WF-meseta (versión robusta, promedio de vecindario) | +1.51% |
| Oráculo (techo teórico, imposible en vivo) | +19.39% |

Con 27 semanas de datos, un parámetro fijo (la mediana histórica) superó a las dos variantes de auto-ajuste. La meseta —pensada para ser la opción "robusta"— fue la peor, lo que sugiere que perseguir el óptimo puntual agrega ruido más que señal en este símbolo/período.

Comando: `python3 walk_forward.py --symbol TSLA --date-start 2026-01-01 --date-end 2026-07-03 --intervals 20 --train-weeks 1`
Artefactos: `walkforward_TSLA_20260704_193331.log` / `.csv`

### 2. TSLA, 1 año completo — julio 2025 a julio 2026 (53 semanas, intervalo 20, train-weeks 1)

**Confirma el hallazgo anterior, ahora con muchísima más potencia estadística.**

| Estrategia | ROI |
|---|---|
| Fija-mediana (drop=1%, rise=3%, intervalo=20) | **+26.18%** |
| WF-pico | +26.12% (prácticamente idéntico a Fija-mediana) |
| WF-meseta | +19.77% |
| Oráculo (techo teórico) | +40.30% |

El dato clave: con 53 semanas la autocorrelación lag-1 cayó a **-0.01 (drop) / +0.01 (rise)** — esencialmente cero. Con la muestra de 27 semanas había algo de correlación aparente que podía ser ruido de muestra chica; con el doble de datos esa correlación se disuelve por completo. Esto es evidencia sólida de que el óptimo semanal de TSLA no tiene memoria: no hay nada que "seguir" de una semana a la otra, así que perseguirlo (WF-pico/WF-meseta) no puede sumar valor por diseño. El *regret* también bajó (0.71pp promedio vs cifras más altas y erráticas antes), reforzando la misma lectura.

Nota aparte: el Oráculo (+40.30%) le saca una diferencia grande a la mejor estrategia realista (+26.18%). Esa brecha no se explica por mala elección de parámetros — con `max_buys=10` fijo, el techo teórico asume una combinación distinta y óptima cada semana sin las limitaciones de continuidad del portfolio real. No cambia la conclusión (fijo sigue ganándole a adaptativo), pero conviene no interpretar esa brecha como "margen para mejorar el auto-ajuste".

Comando: `python3 walk_forward.py --symbol TSLA --date-start 2025-07-01 --date-end 2026-07-01 --intervals 20 --train-weeks 1`
Artefactos: `walkforward_TSLA_20260705_111225.log` / `.csv`, caché `cache_TSLA_20250701_20260701_1Min.pkl` (234,811 velas)

### 3. TSLA, 1.5 años — enero 2025 a julio 2026 (79 semanas, intervalo 20, train-weeks 1)

**Tercera confirmación consecutiva: el auto-ajuste semanal sigue sin justificarse.**

| Estrategia | ROI |
|---|---|
| Fija-mediana (drop=1%, rise=3%, intervalo=20) | **+22.54%** |
| WF-pico | +21.22% |
| WF-meseta | +19.37% |
| Oráculo (techo teórico) | +53.56% |

Mismo orden que en las dos corridas anteriores (fijo > pico > meseta). La autocorrelación de rise se movió de +0.01 (53 semanas) a -0.13 (79 semanas) — sigue siendo ruido, no evidencia de persistencia real; el *regret* peor-caso subió bastante (2.37pp → 6.00pp) al sumar todo 2025, señal de que hubo al menos una semana con un cambio de régimen fuerte, pero el promedio (1.09pp) y la mediana (0.70pp) se mantienen bajos. Los ROI totales bajan un poco respecto a la corrida de 53 semanas simplemente porque ahora entra medio año más de historia con su propio comportamiento de precio — lo que importa es que el *orden* entre estrategias no cambia.

Comando: `python3 walk_forward.py --symbol TSLA --date-start 2025-01-01 --date-end 2026-07-01 --intervals 20 --train-weeks 1`
Artefactos: `walkforward_TSLA_20260705_151739.log` / `.csv`, caché `cache_TSLA_20250101_20260701_1Min.pkl` (350,595 velas)

### 4. TSLA con `--period month` — julio 2025 a junio 2026 (12 meses, intervalo 20, train-periods 1)

**Primer resultado con granularidad mensual (feature nueva de `walk_forward.py`) — y por primera vez el auto-ajuste SÍ le gana al fijo.**

| Estrategia | ROI |
|---|---|
| Fija-mediana (drop=1%, rise=5%, intervalo=20) | +24.11% |
| WF-pico | +26.58% |
| **WF-meseta** | **+26.71%** |
| Oráculo (techo teórico) | +21.36% |

A diferencia de las tres corridas semanales anteriores, acá WF-meseta (la variante "robusta") le gana a Fija-mediana. Un detalle importante: el Oráculo (+21.36%) queda **por debajo** de WF-meseta y WF-pico — a primera vista parece un contrasentido (¿cómo el "techo teórico" pierde contra una estrategia realista?), pero tiene explicación: el Oráculo elige el pico óptimo de cada mes evaluado de forma aislada (portfolio fresco), sin considerar qué posiciones quedaron abiertas de meses anteriores en el portfolio continuo real — no es un óptimo global verdadero, es un heurístico greedy mes a mes. Cuando el portfolio se encadena (como hacen todas las estrategias reales via `simulate_adaptive`), un pico aislado óptimo puede terminar siendo subóptimo dado el estado real heredado.

Con solo **12 períodos** (vs. 53-79 semanales), esta es la muestra más chica de las corridas de TSLA hasta ahora — el *regret* promedio (1.96pp) y peor caso (4.25pp) son más altos que en las corridas semanales de 53-79 semanas, consistente con menos poder estadístico. No tomar esto todavía como "la granularidad mensual funciona mejor que la semanal" — hace falta repetir con más años de historia y/o otros símbolos antes de sacar esa conclusión.

Comando: `python3 walk_forward.py --symbol TSLA --date-start 2025-07-01 --date-end 2026-07-01 --intervals 20 --period month --train-periods 1`
Artefactos: `walkforward_TSLA_20260706_010043.log` / `.csv` (mismo caché de 1 año que la corrida semanal #2, reutilizado sin red)

### 5. SPCX, junio–julio 2026 (solo 4 semanas, intervalo 10 vs 20)

**Resultado no concluyente por tamaño de muestra — pero con una lección de riesgo real.**

- Con **solo 4 semanas** (3 de aplicación), el *regret* y la varianza de los parámetros óptimos no tienen peso estadístico. No se puede sacar conclusión firme sobre auto-ajuste vs fijo con esta muestra.
- Lo más importante del caso SPCX: **hasta el Oráculo (techo teórico) dio negativo con intervalo 10** (-7.42%). Eso significa que el problema no era "qué % elegir" — ninguna combinación del grid (1-10%) hubiera salvado el período. La causa fue un rally fuerte hasta ~$225 seguido de un desplome a ~$147 (recuperación parcial a ~$161 al cierre): la estrategia de grid (comprar caídas, vender subas) quedó totalmente cargada (10/10 posiciones) comprando durante el desplome sin llegar a vender.
- **El intervalo importa, pero no de forma monótona.** Con intervalo 20 el Oráculo dio +0.64% (vs -7.42% a intervalo 10) y el *regret* fue ~5-6x menor. Sin embargo, con un único parámetro fijo para todo el período (`optimize.py`, sin re-optimización semanal), intervalo 10 fue el **mejor** de 6 intervalos probados (5/10/15/20/30/60 → ROI +2.71%/+1.87%/+1.79%/+2.39%/+1.39%/+2.29%). O sea: intervalo 10 no es malo en sí — es que a esa granularidad el óptimo semanal es más ruidoso y generaliza peor de una semana a la otra que a intervalo 20. Dejando competir todos los intervalos juntos semana a semana, el ganador varió sin patrón claro: 5, 10, 10, 60.

Comandos:
```
python3 walk_forward.py --symbol SPCX --date-start 2026-01-01 --date-end 2026-07-03 --intervals 10 --train-weeks 1
python3 walk_forward.py --symbol SPCX --date-start 2026-01-01 --date-end 2026-07-03 --intervals 20 --train-weeks 1
python3 optimize.py     --symbol SPCX --date-start 2026-01-01 --date-end 2026-07-03 --intervals 5,10,15,20,30,60
python3 walk_forward.py --symbol SPCX --date-start 2026-01-01 --date-end 2026-07-03 --intervals 5,10,15,20,30,60 --train-weeks 1
```
Artefactos: `walkforward_SPCX_20260704_204636.*` (intervalo 10), `walkforward_SPCX_20260704_205113.*` (intervalo 20), `optimize_SPCX_20260704_205133.*`, `walkforward_SPCX_20260704_210538.*` (multi-intervalo)

## Conclusiones provisorias (a validar con más datos)

1. **Para TSLA con granularidad semanal, el auto-ajuste NO ayuda — confirmado en tres muestras crecientes (27, 53 y 79 semanas), con la autocorrelación oscilando cerca de 0 en las dos más grandes.** Es la conclusión más sólida de la bitácora hasta ahora. Falta ver si se sostiene en otros símbolos.
2. **El riesgo más grande no es el % de drop/rise — es la falta de un freno ante un movimiento de tendencia fuerte** (rally o crash), visto en SPCX. Ningún parámetro del grid soluciona eso; sería un mecanismo aparte (ej. circuit breaker de drawdown máximo) independiente del optimizador.
3. **El intervalo de muestreo interactúa con el ruido del optimizador semanal** de forma no trivial: intervalos más finos pueden dar el mejor resultado con un parámetro fijo de todo el período, pero producir peor transferencia semana a semana bajo re-optimización. No asumir "más fino es peor" ni "más fino es mejor" sin volver a medir.
4. **La hipótesis de "elegir símbolos por volatilidad" se refinó:** lo que importa para esta estrategia no es la volatilidad cruda, sino que el precio oscile en rango sin tendencia sostenida (ver caso SPCX). Antes de correr walk-forward completo símbolo por símbolo, tendría sentido armar un filtro rápido tipo "recorrido total de precio vs. desplazamiento neto" para preseleccionar candidatos — todavía no construido.
5. **Con granularidad mensual (feature nueva `--period month`), el resultado cambió: WF-meseta le ganó a Fija-mediana en TSLA (único dato hasta ahora, 12 meses).** Contradice el patrón semanal, pero la muestra es chica (12 vs 53-79) — no alcanza para concluir que "mensual funciona mejor que semanal" todavía. Además se detectó que el Oráculo puede quedar por debajo de una estrategia realista cuando la granularidad es gruesa, porque el Oráculo optimiza cada período de forma aislada sin considerar el portfolio heredado — un matiz a tener en cuenta al interpretar "techo teórico" en corridas mensuales.

## Próximos pasos sugeridos para la próxima sesión

- [x] Repetir el análisis de TSLA con el rango de fechas extendido (más semanas → autocorrelación y regret más confiables). — Hecho 2026-07-05 con 1 año (53 semanas) y 1.5 años (79 semanas): confirma el hallazgo en ambas, autocorrelación oscila cerca de 0.
- [x] Agregar soporte para granularidad mensual a `walk_forward.py` (`--period month`). — Hecho 2026-07-06 (spec + plan + implementación vía subagentes, 24/24 tests). Primera corrida real: WF-meseta le gana a Fija-mediana en TSLA (ver hallazgo #4 arriba) — resultado a confirmar con más datos.
- [ ] Repetir `--period month` con más años de historia y/o más símbolos para ver si "mensual favorece al auto-ajuste" es señal real o ruido de una muestra de 12 períodos.
- [ ] Repetir SPCX con más historia si Alpaca la tiene disponible, para ver si el patrón de rally-crash de estas 4 semanas fue una anomalía o es representativo del símbolo.
- [ ] Sumar 1-2 símbolos más (mismo comando, cambiando `--symbol`) para ver si la conclusión "fija le gana a auto-ajuste" se sostiene fuera de TSLA. Candidato natural: armar antes el filtro de rango-vs-tendencia del punto 4 de arriba para elegir mejor qué símbolos probar.
- [ ] Si el patrón de "movimientos de tendencia fuerte rompen el grid" se repite, evaluar diseñar un mecanismo de freno (fuera del alcance de `walk_forward.py`; sería un cambio en `tradebot.py`/`backtest.py`).

## Historial de corridas

| Fecha | Símbolo | Rango | Intervalos | period / train | Veredicto | Notas |
|---|---|---|---|---|---|---|
| 2026-07-06 | TSLA | 2025-07-01 → 2026-07-01 | 20 | month / train-periods 1 | SE justifica (WF-meseta +26.71% > WF-pico +26.58% > Fija +24.11%; Oráculo +21.36%) | Primera corrida con `--period month` (12 meses). Oráculo por debajo de estrategias reales — explicable (óptimo aislado por período, no global). Muestra chica, a confirmar |
| 2026-07-06 | TSLA | 2026-01-01 → 2026-01-10 (sanity check) | 20 | week / train-weeks 1 | — (no es análisis real) | Corrida de verificación (`walkforward_TSLA_20260706_004853.csv`) hecha durante el desarrollo del feature `--period`, solo 5 velas de datos; sin valor analítico, se conserva por prolijidad |
| 2026-07-05 | TSLA | 2025-01-01 → 2026-07-01 | 20 | week / train-weeks 1 | NO se justifica (Fija +22.54% > WF-pico +21.22% > WF-meseta +19.37%; Oráculo +53.56%) | 1.5 años (79 sem). Tercera confirmación consecutiva del mismo orden de estrategias |
| 2026-07-05 | TSLA | 2025-07-01 → 2026-07-01 | 20 | week / train-weeks 1 | NO se justifica (Fija +26.18% > WF-pico +26.12% > WF-meseta +19.77%; Oráculo +40.30%) | 1 año completo (53 sem). Autocorrelación ≈0 en drop y rise — confirma el hallazgo anterior con mucha más fuerza |
| 2026-07-04 | TSLA | 2026-01-01 → 2026-07-03 | 20 | week / train-weeks 1 | NO se justifica (Fija +13.32% > WF-pico +9.59% > WF-meseta +1.51%; Oráculo +19.39%) | Muestra grande (27 sem), primer resultado de referencia |
| 2026-07-04 | SPCX | 2026-01-01 → 2026-07-03 (datos reales solo desde 06-12) | 10 | week / train-weeks 1 | SE justifica (WF-meseta -8.72% > Fija -13.03%), pero todo el torneo perdió plata | Rally a $225 y crash a $147; Oráculo también negativo (-7.42%) → problema no es el %, es el riesgo de tendencia |
| 2026-07-04 | SPCX | 2026-01-01 → 2026-07-03 | 20 | week / train-weeks 1 | SE justifica (WF-pico -8.92% > Fija -10.06%) | Oráculo positivo (+0.64%) con este intervalo — mucho mejor transferencia semana a semana que con intervalo 10 |
| 2026-07-04 | SPCX | 2026-01-01 → 2026-07-03 | 5,10,15,20,30,60 (multi) | week / train-weeks 1 | — (solo estabilidad, no torneo comparado en este doc) | Intervalo ganador por semana sin patrón claro: 5, 10, 10, 60 |

_(Agregar filas nuevas arriba de esta línea a medida que se repita el análisis.)_
