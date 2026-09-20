# Estado de Yahoo, EDGAR y posibles fuentes adicionales

## Comprobación real de Yahoo

Prueba del 20/09/2026, 19:07 UTC, con yfinance 1.7.0. Se recuperaron **6 de 6 empresas**, sin excepciones de proveedor registradas en esa ejecución. No se garantiza que el servicio siga disponible ni que todas sus cifras sean exactas.

| Ticker | Moneda de precio / estados | Ejercicios anuales comunes | Años de ROIC calculables | Estimación EPS utilizable según filtros |
|---|---|---:|---:|---|
| AAPL | USD / USD | 3 | 3 | Sí |
| MSFT | USD / USD | 3 | 3 | Sí |
| SAP.DE | EUR / EUR | 3 | 3 | Sí |
| SHEL.L | GBp / USD | 3 | 3 | Sí |
| 7203.T | JPY / JPY | 3 | 3 | Sí |
| 005930.KS | KRW / KRW | 3 | 2 | No |

Las últimas cotizaciones estaban fechadas el 18/09/2026. Samsung sí devolvió estimaciones, pero no superó las reglas para utilizarlas: esto es una exclusión metodológica, no una caída de conexión. Se obtuvieron conversiones USD y se comprobó el tratamiento de GBp. Esta muestra verifica conectividad y esquema en seis casos, no toda la cobertura mundial ni la calidad económica de las cifras.

**Conclusión:** Yahoo sirve como fuente operativa del screener. Mantener detección de faltantes, fechas e incidencias: no considerarlo una fuente auditada ni garantía de tiempo real. El proyecto oficial describe su uso para investigación y las condiciones del acceso a Yahoo: [yfinance oficial](https://github.com/ranaroussi/yfinance).

## Estado de EDGAR

**No certificado en conexión real desde el script:** falta configurar un User-Agent con nombre y correo reales. No se ha inventado una identidad ni utilizado un correo de terceros. La SEC pide identificar las consultas automatizadas y publica un máximo de 10 solicitudes por segundo; este cliente limita un proceso a unas cuatro. [Política SEC](https://www.sec.gov/search-filings/edgar-search-assistance/accessing-edgar-data).

El lector XML y el flujo de CIK → submissions → Form 4 sí están cubiertos por pruebas con respuestas simuladas, incluyendo namespaces, retirada del prefijo XSL, fecha de operación, ventana incompleta y fallo HTTP. Un error o una consulta parcial devuelve desconocido, nunca se convierte en «no hay compras».

No confundir eso con una prueba live. La consulta de `data.sec.gov` mediante la herramienta web tampoco permitió leer el JSON; esa limitación de la herramienta no demuestra una caída de EDGAR. `check_providers.py` deja el estado `pendiente_identificacion` y podrá ejecutar la prueba real al configurar el dato.

La cobertura SEC es la de entidades que presentan documentos ante la SEC; no sustituye a todos los reguladores y cuentas locales del mundo. El indicador actual consulta Form 4, no verifica automáticamente todos los fundamentales de Yahoo.

## ¿Hace falta otro proveedor?

**No es obligatorio añadir uno para una primera versión de investigación.** Priorizaría:

1. **Configurar y comprobar SEC** antes de afirmar que insiders funciona en producción.
2. **Ampliar SEC con Company Facts** para contrastar ingresos, beneficio, caja y deuda de declarantes compatibles. Es una ampliación del mismo proveedor, no otra suscripción. La SEC ofrece datos XBRL mediante API, pero mapear conceptos, unidades, periodos y correcciones exige trabajo adicional. El diagnóstico comprueba el endpoint; el motor todavía NO reconcilia esas cifras con Yahoo. [APIs oficiales de SEC](https://www.sec.gov/search-filings/edgar-application-programming-interfaces).
3. **FMP como respaldo opcional** cuando hagan falta estimaciones o cobertura adicional. Ya existe un adaptador de estimaciones en esta versión; requiere clave y acceso al endpoint según el plan. No se ha probado con una cuenta del usuario ni implementado un respaldo completo de todos los fundamentales. [Financial Estimates, documentación oficial](https://site.financialmodelingprep.com/developer/docs/stable/financial-estimates).

No añadiría simultáneamente varios proveedores equivalentes: aumenta coste y discrepancias de periodos/unidades sin demostrar mejor selección. Para un backtest serio la necesidad distinta es un proveedor con fundamentales históricos disponibles en cada fecha, universo con bajas y costes; no basta con otra API de precios actuales.

Ningún proveedor convierte por sí solo un filtro en detector de rentabilidad extraordinaria. Las mejoras útiles son cobertura verificable, normalización, trazabilidad y validación temporal de la estrategia.
