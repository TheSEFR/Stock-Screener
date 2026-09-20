# Sección nueva: Oportunidades con margen de seguridad

Se incorpora como **sección 6 del PDF**, manteniendo el ranking general. La etiqueta más exigente es `prioridad_alta_para_estudio`; no afirma que una inversión sea excelente o segura por definición.

De las 20 empresas mejor puntuadas (configurable con `--deep-limit`, hasta 500), se consultan estados anuales. Se exigen tres ejercicios consecutivos de EPS y FCF positivos, ingresos y caja no decrecientes entre extremos, caja operativa acumulada / beneficio >=80%, dilución anual <=2% y al menos dos ROIC estimados >=12%. El ROIC usa EBIT después de impuesto efectivo / capital invertido medio (deuda + patrimonio - caja e inversiones a corto). No ajusta goodwill, arrendamientos ni partidas extraordinarias. Periodos anuales con más de 450 días de antigüedad no sirven para esta validación.

La valoración aplica un PER de salida a EPS normalizados: el menor entre el último ejercicio y la mediana de tres. Se convierten las monedas financieras a la unidad del precio. Se asume que el EPS histórico y el precio representan la misma clase de acción; revisar especialmente ADR, cambios de capital y splits. El control de capitalización detecta parte de estas discrepancias, no todas.

| Supuesto | Adverso | Base | Favorable |
|---|---|---|---|
| Crecimiento anual EPS | -5% | Consenso acotado entre 0 y 8% | Consenso acotado entre 0 y 12% |
| PER final | Menor entre mediana de pares y 12 | Menor entre mediana y 18 | Menor entre mediana y 22 |
| Horizonte predeterminado | 5 años | 5 años | 5 años |

**Cálculos:**

- Precio final = EPS normalizado × (1 + crecimiento)^años × PER final.
- Valor descontado = precio final / (1 + tasa exigida)^años.
- Margen de seguridad = 1 - precio observado / valor base descontado.
- Precio máximo mostrado = 75% del valor base, para un margen del 25%.

La tasa predeterminada es 12%, un supuesto de análisis, no rentabilidad garantizada. El consenso de un año no demuestra un crecimiento sostenido de cinco; el modelo limita la extrapolación, pero sigue siendo un supuesto.

Para prioridad alta: superar el ranking principal y todos los controles históricos, deuda neta/EBITDA <=2, crecimiento con cobertura suficiente, margen >=25%, pérdida anual de precio en escenario adverso no peor que -5% y **sensibilidad robusta** (margen >=25% en al menos el 75% de 81 combinaciones de crecimiento, PER de salida, beneficio y tasa, con margen mínimo positivo). Mostrar el escenario adverso no limita la pérdida real: pueden ocurrir resultados peores. No se asignan probabilidades. No se incluyen dividendos, comisiones, fiscalidad ni riesgo de cambio futuro. No es un DCF empresarial ni un valor intrínseco verificado.

`vigilar_precio` indica que los supuestos de precio no cumplen; `revisar_historial` (antes `revisar_calidad`) que hay un problema con las cuentas de los últimos años, con el crecimiento estimado o con los datos del proveedor; `sin_valoracion` que falta información suficiente. La sección explica los motivos y puede quedar sin empresas de prioridad alta.

```powershell
python screener.py --watchlist watchlist.txt --deep-limit 50 --horizon 5 --required-return 0.12 --pdf
```

El snapshot conserva estados y parámetros. `--input` los reutiliza salvo que indiques nuevos valores. Esta sección tampoco está calibrada ni validada mediante un backtest. Los umbrales viven en `conviction.py`; no son estándares financieros universales.
