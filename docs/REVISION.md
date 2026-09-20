# Revisión del screener original

Archivo revisado: `C:/Users/saif.eddine/Downloads/screener.py`. Fecha: 20/09/2026. Se conservan las salidas de reporte y se separa un motor de reglas comprobable. No se han ejecutado compras ni enviado mensajes.

| Prioridad | Fallo del original | Consecuencia | Cambio |
|---|---|---|---|
| Alta | `analyze`: interpreta `earningsGrowth` como consenso anual, sin verificar horizonte | PEG y selección basados en una premisa no demostrada | `earnings_estimate` FY0/FY+1, EPS positivos, cobertura y dispersión explícitas |
| Alta | `fmp_growth_and_coverage`: toma los dos primeros registros | Puede comparar años incorrectos o lejanos | Endpoint stable, orden por fecha, dos cierres futuros consecutivos |
| Alta | `is_small_cap`: compara capitalización local con límite USD | Empresas internacionales mal clasificadas | Capitalización USD y dato desconocido cuando no hay FX |
| Alta | `avg_dollar_volume`: volumen en moneda local tratado como dólares | Filtro de liquidez incoherente entre mercados | Conversión USD y normalización de peniques/céntimos |
| Alta | `fcf_yield`: divide FCF de estados entre cap de otra moneda | Yield falso en emisores internacionales/ADR | Conversión por `financialCurrency`, control de unidades |
| Alta | `is_excluded`: un dato ausente nunca excluye | Ausencia de riesgo medido se confunde con seguridad | Estado revisar_datos y requisitos mínimos para candidatas |
| Alta | `net_debt_to_ebitda`: caja ausente se convierte en cero | Ratio inventado; EBITDA no positivo desaparece como N/D | Caja obligatoria y EBITDA negativo bloqueante |
| Alta | Score variable por datos disponibles | Una empresa puede mejorar nota al perder datos | Denominadores fijos y cobertura >=7/8 |
| Media | PEG y crecimiento suman como señales separadas | Doble premio a una única estimación | PEG informativo, crecimiento una sola vez |
| Alta | Misma deuda/liquidez para industria, bancos y REIT | Modelos no comparables y falsos descartes/aprobaciones | Sectores que requieren otro modelo se separan |
| Media | Media por sector incluye al candidato, outliers y grupos diminutos | Comparación frágil o circular | Mediana de cinco otros pares de industria/país; sin fallback global |
| Alta | Sin fechas de cotización/fundamentales ni validación NaN/Inf | Datos viejos o no finitos pueden pasar controles | Conversión numérica finita y comprobación de fechas |
| Media | Calidad presentada como Piotroski con evidencia de otro modelo | Respaldo académico indebidamente trasladado | Nombre y límites de heurística propia, sin atribuir rentabilidad |
| Alta | Form 4: fallo de XML/red devuelve False | Desconocido se convierte en «no hay compra» | Estado ternario True/False/None |
| Media | Form 4 filtra fecha de presentación, no de operación | Compras antiguas presentadas tarde parecen recientes | Fecha de operación, namespaces y XML sin prefijo XSL |
| Media | P identificado exclusivamente como compra en mercado abierto | Confunde también compras privadas | Etiqueta ajustada al significado oficial; no puntúa |
| Media | SEC genérico con correo ficticio, sin límite explícito | Bloqueos y resultados incompletos | Identidad configurable real, pausas y reintentos acotados |
| Alta | Fallo de un `Ticker.info` aborta toda la lista | No hay informe aunque otros tickers funcionen | Aislamiento por ticker y registro de incidencias sin secretos |
| Media | Notas de bancos sin vigencia ni última nota por firma | Compra antigua puede sobrevivir a una rebaja reciente | Deduplicar última nota y limitar a 90 días |
| Media | Noticias llamadas recientes sin verificar fecha | Titulares viejos y duplicados | Fecha <=7 días, deduplicación y URL validada |
| Media | Sentimiento por substring | «record», «cut» o negaciones producen interpretaciones falsas | No inferir impacto bursátil automático |
| Media | Empate conserva orden de watchlist | Preferencia implícita por el orden de entrada | Desempate determinista por ticker |
| Media | `generate_and_send_report` siempre envía Telegram | Probar el programa genera mensajes externos | Envío únicamente con `--send`, sin botón sin backend |
| Media | Enriquecimiento repetido en varias listas | Solicitudes y latencia duplicadas | Enriquecimiento una vez de las candidatas principales |
| Media | Small caps vacías se describen como inexistentes | Confunde exclusión por reglas con ausencia del universo | Mensaje de ausencia de candidatas que superen reglas |
| Baja | Crecimiento cero mostrado como n/d | Cero y desconocido se confunden | Comprobación explícita de None |
| Media | PDF habla de datos actuales y generado por IA | Atribuciones no sustentadas por el script | Última cotización disponible y reglas heurísticas |

## Fuentes primarias consultadas

- [yfinance: get_earnings_estimate](https://ranaroussi.github.io/yfinance/reference/api/yfinance.Ticker.get_earnings_estimate.html): periodos 0q, +1q, 0y y +1y y columnas de estimaciones. Fundamenta el uso de periodos explícitos; no demuestra el origen de `earningsGrowth` ni garantiza vigencia.
- [Código del proveedor yfinance, analysis.py](https://github.com/ranaroussi/yfinance/blob/main/yfinance/scrapers/analysis.py): estructura de tablas y campos de análisis.
- [FMP: documentación oficial](https://site.financialmodelingprep.com/developer/docs): endpoint stable `analyst-estimates`, parámetros y proyecciones; acceso sujeto al plan.
- [SEC: instrucciones oficiales Form 4](https://www.sec.gov/files/form4.pdf): P incluye compras de mercado abierto o privadas.
- [SEC: acceso automatizado a EDGAR](https://www.sec.gov/search-filings/edgar-search-assistance/accessing-edgar-data): identificación y límite máximo publicado de 10 solicitudes/segundo; el código usa como máximo aproximadamente cuatro en un proceso.
- [yfinance: proyecto oficial](https://github.com/ranaroussi/yfinance): finalidad de investigación y condiciones del acceso a Yahoo.

## Qué se ha validado y qué queda abierto

Las pruebas automatizadas comprueban reglas, errores de datos y generación de salidas con fixtures sintéticas. No prueban rentabilidad ni ausencia de errores de proveedores. El PDF de demostración sirve para verificar formato, no para tomar decisiones. Las pruebas live, si se completan, son comprobaciones de conectividad y esquema en el momento de la revisión; sus resultados se documentan en VALIDACION.md.

No se ha validado FMP con una clave del usuario, ni un envío de Telegram, ni una reconstrucción integral del histórico SEC. No se han añadido datos fundamentales históricos point-in-time ni un backtest de rendimiento. El modelo es deliberadamente conservador: puede dejar pocas o ninguna candidata cuando la lista no tiene suficientes pares o Yahoo no aporta datos. No bajar esos requisitos sin revisar qué información se está perdiendo.
