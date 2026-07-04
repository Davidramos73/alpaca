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

## Hallazgos acumulados (al 2026-07-04)

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

### 2. SPCX, junio–julio 2026 (solo 4 semanas, intervalo 10 vs 20)

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

1. **Sin evidencia todavía de que el auto-ajuste semanal ayude.** TSLA con muestra grande (27 semanas) dice que no. SPCX es muestra insuficiente para opinar.
2. **El riesgo más grande no es el % de drop/rise — es la falta de un freno ante un movimiento de tendencia fuerte** (rally o crash). Ningún parámetro del grid soluciona eso; sería un mecanismo aparte (ej. circuit breaker de drawdown máximo) independiente del optimizador.
3. **El intervalo de muestreo interactúa con el ruido del optimizador semanal** de forma no trivial: intervalos más finos pueden dar el mejor resultado con un parámetro fijo de todo el período, pero producir peor transferencia semana a semana bajo re-optimización. No asumir "más fino es peor" ni "más fino es mejor" sin volver a medir.

## Próximos pasos sugeridos para la próxima sesión

- [ ] Repetir el análisis de TSLA con el rango de fechas extendido (más semanas → autocorrelación y regret más confiables).
- [ ] Repetir SPCX con más historia si Alpaca la tiene disponible, para ver si el patrón de rally-crash de estas 4 semanas fue una anomalía o es representativo del símbolo.
- [ ] Sumar 1-2 símbolos más (mismo comando, cambiando `--symbol`) para ver si la conclusión "fija le gana a auto-ajuste" se sostiene fuera de TSLA.
- [ ] Si el patrón de "movimientos de tendencia fuerte rompen el grid" se repite, evaluar diseñar un mecanismo de freno (fuera del alcance de `walk_forward.py`; sería un cambio en `tradebot.py`/`backtest.py`).

## Historial de corridas

| Fecha | Símbolo | Rango | Intervalos | train-weeks | Veredicto | Notas |
|---|---|---|---|---|---|---|
| 2026-07-04 | TSLA | 2026-01-01 → 2026-07-03 | 20 | 1 | NO se justifica (Fija +13.32% > WF-pico +9.59% > WF-meseta +1.51%; Oráculo +19.39%) | Muestra grande (27 sem), primer resultado de referencia |
| 2026-07-04 | SPCX | 2026-01-01 → 2026-07-03 (datos reales solo desde 06-12) | 10 | 1 | SE justifica (WF-meseta -8.72% > Fija -13.03%), pero todo el torneo perdió plata | Rally a $225 y crash a $147; Oráculo también negativo (-7.42%) → problema no es el %, es el riesgo de tendencia |
| 2026-07-04 | SPCX | 2026-01-01 → 2026-07-03 | 20 | 1 | SE justifica (WF-pico -8.92% > Fija -10.06%) | Oráculo positivo (+0.64%) con este intervalo — mucho mejor transferencia semana a semana que con intervalo 10 |
| 2026-07-04 | SPCX | 2026-01-01 → 2026-07-03 | 5,10,15,20,30,60 (multi) | 1 | — (solo estabilidad, no torneo comparado en este doc) | Intervalo ganador por semana sin patrón claro: 5, 10, 10, 60 |

_(Agregar filas nuevas arriba de esta línea a medida que se repita el análisis.)_
