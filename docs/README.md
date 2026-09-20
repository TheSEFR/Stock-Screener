# Screener revisado

Herramienta para priorizar empresas que merecen estudio, con separación entre **candidata**, **descartada_riesgo**, **revisar_datos** y **no_cumple**. No ejecuta órdenes. No necesita IA ni una API de IA. El ranking es heurístico: no está demostrado que supere al mercado.

## Empezar

Extrae todo el ZIP en una carpeta. `screener.py` necesita `screener_core.py` y `conviction.py` a su lado. El original de Descargas no se ha modificado.

Prueba sin red ni dependencias, con Python 3.10 o posterior:

```powershell
python screener.py --demo
```

Consulta `informes/resultados.csv` y `informes/resultados.json`. Las empresas DEMO son ficticias. La entrega contiene código y documentación; no incluye informes ni HTML.

Para datos reales, se recomienda Python 3.12 y un entorno propio:

```powershell
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
Copy-Item watchlist.example.txt watchlist.txt
# Edita watchlist.txt: un ticker Yahoo por línea.
.venv\Scripts\python screener.py --watchlist watchlist.txt
```

La lista de ejemplo enseña la sintaxis; es demasiado pequeña y heterogénea para generar comparaciones fiables por industria. No se obtiene una lista completa del mercado automáticamente.

## Opciones

```powershell
# CSV y JSON; añadir PDF con el diseño del programa original:
python screener.py --watchlist watchlist.txt --pdf
# Noticias de 7 días, últimas notas de firmas e insiders SEC:
python screener.py --watchlist watchlist.txt --enrich
# Traducción externa opcional (envía titulares/descripciones al servicio):
python screener.py --watchlist watchlist.txt --enrich --translate
# Repetir el cálculo sobre datos guardados, sin consultas nuevas:
python screener.py --input informes/snapshot.json --output repeticion
# Enviar voluntariamente al chat configurado, una vez revisada la salida:
python screener.py --watchlist watchlist.txt --pdf --send
# Pruebas offline:
python -m unittest -v test_screener.py
```

`--send` es la única opción que envía un mensaje y requiere `--pdf`. No se ha utilizado durante la revisión. El programa no crea un bot ni un servicio que responda botones: eso lo hace `bot_listener.py` (cron de GitHub Actions cada 10 minutos), que llama a `generate_and_send_report()`. Esa función pide ahora el envío de forma explícita (`--pdf --enrich --send`) y lanza un error si falla; `screener.yml` ejecuta el mismo comando cada día. El PDF y los resultados se guardan en `informes/` y el mensaje de Telegram recupera el botón «Generar informe ahora». Sin credenciales, las salidas locales funcionan.

## Limitaciones conocidas

- **No hay backtest:** la rentabilidad de estas reglas no está demostrada.
- **EDGAR no está probado en vivo:** falta configurar un `SEC_EDGAR_USER_AGENT` real (nombre y correo). Hasta entonces el dato de insiders es «desconocido».
- **FMP y Telegram no están validados** con credenciales reales.

Estas frases también aparecen en el aviso legal del PDF. Retíralas de allí solo cuando cada punto esté realmente comprobado.

Copia `.env.example` a `.env` y configura solo los proveedores que uses. SEC necesita una identificación real. FMP es opcional; la disponibilidad del endpoint depende de tu cuenta. No se asume un plan gratuito concreto. Las noticias y traducciones no se usan para puntuar.

## Qué considera una candidata

**Valor/crecimiento (4 puntos):**

1. P/E positivo con descuento de al menos 20% frente a la mediana de cinco o más *otras* empresas de la misma industria y país, dentro de tu lista.
2. Flujo de caja libre / capitalización, convertidos a la misma moneda, de al menos 5%.
3. EPS estimado del siguiente año fiscal al menos 15% superior al actual, con EPS positivos, al menos tres analistas en ambos periodos y dispersión acotada. Crecimientos superiores al 100% no aportan señal por posible efecto de base.
4. Crecimiento de ingresos del proveedor de al menos 5%. Se guarda como dato histórico reportado, no como previsión independiente verificada.

**Calidad (4 puntos):** ROE >=15%, margen operativo positivo y al menos igual a la mediana de pares, deuda/patrimonio entre 0 y 100%, ratio corriente >=1,5. Este conjunto NO es el Piotroski F-Score.

Para entrar: al menos **3/4** de valor, **2/4** de calidad, **7/8 criterios con datos** y ningún bloqueo de riesgo. Orden: **55% valor/crecimiento + 45% calidad**, con cobertura y ticker como desempate reproducible. Faltantes nunca elevan la nota. Las ponderaciones son decisiones de diseño no optimizadas mediante datos históricos.

Además se exige: cotización fechada en los últimos 7 días naturales; cierre financiero en los últimos 180; EPS, EBITDA, FCF, margen y patrimonio por acción positivos; deuda neta/EBITDA <=4; volumen medio por precio de al menos 1 millón USD/día; moneda/capitalización verificables. El volumen por precio es una aproximación a liquidez, no profundidad de libro ni garantía de ejecución. El cierre financiero no equivale a la fecha de publicación o revisión del dato.

Banca, seguros, REIT e inmobiliario se remiten a **revisión con otro modelo**. No aplicarles automáticamente los ratios industriales evita clasificaciones engañosas, pero limita la cobertura de esta versión.

El PEG queda informativo: mezclar P/E histórico con crecimiento de un año no es un PEG plurianual robusto y no debe duplicar la señal de crecimiento. Compras de insiders, consenso y precios objetivo también son contexto. La cesta temática heredada es estática, no validada políticamente y no pasa por el filtro estricto.

## Salidas y trazabilidad

La nueva sección **Oportunidades con margen de seguridad** se explica en [OPORTUNIDADES.md](OPORTUNIDADES.md). Añade tres años de calidad histórica y escenarios de valoración al PDF, manteniendo el ranking general. Por defecto revisa históricos de hasta 20 empresas (`--deep-limit`), con horizonte de 5 años (`--horizon`) y descuento del 12% (`--required-return`).

- `resultados.csv`: importación a Excel, con importes USD y razones.
- `resultados.json`: métricas completas, estados e incidencias por proveedor/ticker.
- `snapshot.json`: datos de entrada, tipos de cambio utilizados y fecha para reproducir la decisión; no contiene claves.
- PDF opcional: portada, índice, tablas y fichas con formato heredado. Los JSON son la referencia completa para los descartes y fechas.

`--input` reevalúa a la fecha del snapshot: NO actualiza precios y NO es un backtest. Usar una carpeta distinta por ejecución si quieres conservar un histórico; sin `--output` se sobrescribe el informe anterior. Código de salida 0: ejecución completa; 2: ninguna empresa recuperada; 3: se solicitaron PDF sin fpdf2. Otras incidencias de configuración generan error visible. La descarga incompleta se registra y no se interpreta como ausencia de oportunidades.

Las consultas son secuenciales. SEC tiene pausas, plazo máximo y reintentos acotados de GET; no hay caché persistente ni reintentos automáticos de envío de Telegram. SEC consulta hasta 60 Form 4 por empresa y únicamente la ventana disponible: un resultado incompleto se marca desconocido. P puede representar compra privada. No se reconstruyen todas las enmiendas, no se agregan importes ni se atribuye una compra al CEO sin verificarlo.

## Cómo mejorar la detección con evidencia

Antes de aumentar la complejidad, guardar resultados durante meses y contrastar por qué se seleccionó cada empresa. Para validar rentabilidad hace falta un universo histórico con empresas excluidas de bolsa, fundamentales disponibles **en aquella fecha**, divisas, dividendos, splits, costes, calendario y benchmark adecuado. Un backtest con los fundamentales actuales aplicados al pasado daría resultados engañosos.

Después: contrastar los históricos y escenarios de la sección adicional con las cuentas oficiales, ampliar a cinco años de caja normalizada y analizar vencimientos de deuda. Para bancos harían falta capital regulatorio, rentabilidad y calidad del crédito; para REIT, FFO/AFFO, deuda y ocupación. Para insiders faltan reconciliar enmiendas, importe relativo al patrimonio, rol y varias compras independientes. Estas ampliaciones sectoriales y de insiders no están implementadas.

## Dependencias y activos

Probado con Python 3.12. Se incluyen versiones de dependencias directas; no es un lockfile completo de transitivas. No instalar el paquete antiguo `fpdf` junto a `fpdf2`. Logo `assets/sef_logo.png` y fuente opcional `assets/FreeSerif.ttf` conservan sus rutas si los aportas; no venían adjuntos y no se han copiado. Sin ellos el PDF usa el texto de marca y omite la tipografía árabe.

Yahoo/yfinance puede cambiar, limitar solicitudes o devolver datos incompletos. Revisar sus condiciones de uso y confirmar cifras decisivas con cuentas y comunicados de la compañía. `REVISION.md` recoge los defectos y las fuentes técnicas consultadas.

## Diagnóstico de fuentes

`python check_providers.py` comprueba Yahoo en seis acciones de distintos mercados y SEC (mapa CIK, submissions, companyfacts y un XML Form 4). Guarda `diagnostico_proveedores.json`, sin credenciales. SEC queda pendiente hasta configurar un User-Agent con nombre y correo reales. Código de salida 2 indica una comprobación parcial o pendiente; no implica que todo el proveedor esté caído. El diagnóstico consulta la red y no envía informes. Ver PROVEEDORES.md.

## Mejoras añadidas después de la revisión

- **Histórico permanente de decisiones:** cada ejecución real guarda en `history/decisiones.jsonl` la fecha, la versión de las reglas (`RULES_VERSION` en `forward_test.py`) y las candidatas con su precio, caja, calidad y valoración. El workflow diario lo sube al repositorio. La demo y `--input` no guardan nada.
- **Seguimiento mensual / prueba hacia adelante** (`forward_test.py`, la lanza `monthly_report.py` cada mes y envía `history/prueba_adelante.md`): el screener guarda cada día las tres listas del informe (`principales`, `pequena_capitalizacion` y `cesta_tematica`). El informe mensual las recoge por secciones, agrupa cada acción por el **mes en que entró** y muestra cómo ha crecido cada tanda frente a SPY (rentabilidad ajustada por dividendos, sin costes), con tabla por mes, detalle por acción, pendientes (menos de 30 días) y tickers sin precio. La cesta temática es una lista fija sin filtrar y se cuenta aparte. **No es un backtest** (no usa datos históricos de cada fecha): mide lo que pasó con recomendaciones reales a partir de ahora. Con menos de 30 acciones evaluables por sección avisa de que no permite concluir.
- **Alertas por cambios** (`alerts.py`): compara con la decisión anterior y avisa de nuevas candidatas, salidas, precio que entra en el umbral de estudio, caída del FCL, pérdida de calidad y cambios de prioridad. `--only-changes` evita enviar el mismo informe si no hay cambios (lo usa el workflow diario; `/informe` siempre envía). Se guarda en `informes/alertas.md`.
- **Sensibilidad de la valoración** (`conviction.sensitivity`): margen de seguridad en 81 combinaciones de crecimiento, PER de salida, beneficio y tasa de descuento. Se muestra en la sección 6 del PDF. Para obtener `prioridad_alta_para_estudio` la conclusión debe ser **robusta**: margen ≥25% en al menos el 75% de las combinaciones y margen mínimo positivo. Si no lo es, la empresa se queda en `vigilar_precio` con el motivo «Conclusión frágil». Los umbrales (75%, margen mínimo > 0) son una decisión de diseño no calibrada con datos históricos.
- **Modelos por sector** (`sector_models.py`): banca y seguros (P/B, ROE, ROA), inmobiliario (FCF como sustituto tosco del AFFO, deuda neta/EBITDA), cíclicas (PER sobre beneficio normalizado) y tecnología (regla del 40). Son informativos, no cambian `eligible`; salen en `resultados.json` y en la columna `sector_model` del CSV. No evalúan capital regulatorio, morosidad, ocupación ni vencimientos de deuda.
- **Contraste con SEC** (`reconcile.py`): compara ingresos y beneficio neto anuales de Yahoo con `companyfacts` de SEC para emisores en USD. Una discrepancia mayor del 5% baja una «prioridad alta» a `revisar_calidad`. Sin `SEC_EDGAR_USER_AGENT` real queda `sin_verificar`. **No probado en vivo**: solo con respuestas simuladas.
- **Ejecución robusta:** `--resume` reutiliza lo ya descargado hoy (`cache/`) si una ejecución se interrumpió; las escrituras son atómicas y un JSON corrupto se ignora. Los errores por ticker siguen en `resultados.json`. Las dependencias están fijadas en `requirements.txt`.

Sigue sin existir un backtest: la rentabilidad de las reglas no está demostrada.

## Cobertura mundial

- **Universo** (`universes/*.txt` + `watchlist.txt`): `--universe global` carga todas las listas (unos 340 valores de 30 países); `--universe europa,asia_pacifico` carga solo algunas. Son listas escritas a mano, **no índices oficiales completos**: no se actualizan solas y pueden faltar empresas. Un ticker que Yahoo no reconozca se registra como error sin romper la ejecución. El workflow diario y `/informe` usan `global`.
- **Comparables por escalones** (`peer_scope` en `resultados.json`): 1) misma industria y país, 2) misma industria y región (Norteamérica, Europa, Asia-Pacífico, Latinoamérica, Oriente Medio y África), 3) mismo sector y región (comparación más gruesa). Se exigen cinco comparables en cada escalón y nunca se cruza de región. Cuanto más grueso el escalón, menos preciso el P/E de referencia.
- **Empresas duplicadas** (ADR y cotización local, doble cotización): se detectan por la descripción del negocio, se evalúa la cotización más líquida y la otra queda con estado `duplicada`, sin contar como candidata ni como comparable.
- **Datos reconstruidos** (`derived`): si Yahoo no da EPS, P/E o valor contable en la ficha (p. ej. algunos valores coreanos), se reconstruyen desde los estados anuales solo si el precio y las cuentas están en la misma moneda. Es un EPS **anual**, no de los últimos 12 meses, así que el P/E puede estar desfasado.
- **Yahoo** cubre bien los principales mercados del mundo pero sin garantías; el contraste con SEC solo cubre emisores de EEUU.
