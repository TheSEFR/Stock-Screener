<p align="center">
  <img src="https://capsule-render.vercel.app/api?type=waving&color=0:0F2A4A,100:1E5C8A&height=200&section=header&text=Stock%20Screener&fontSize=60&fontColor=ffffff&animation=fadeIn&fontAlignY=38&desc=Screener%20automatico%20de%20oportunidades%20de%20compra&descAlignY=58&descSize=18" alt="Stock Screener banner" />
</p>

<p align="center">
  <img alt="Python" src="https://img.shields.io/badge/Python-3.13-blue?logo=python&logoColor=white">
  <img alt="License" src="https://img.shields.io/badge/Licencia-Uso%20personal-lightgrey">
  <img alt="GitHub Actions" src="https://img.shields.io/badge/Automatizado%20con-GitHub%20Actions-2088FF?logo=githubactions&logoColor=white">
  <img alt="Telegram" src="https://img.shields.io/badge/Entrega-Telegram-26A5E4?logo=telegram&logoColor=white">
</p>

## Acerca de

**Stock Screener** analiza una watchlist de acciones de EE.UU., Europa y
Asia, las puntúa con fundamentales reales (P/E frente a su sector, PEG,
crecimiento de beneficios, insider buying y un factor de calidad tipo
Piotroski), y entrega dos informes en PDF por Telegram sin que tengas que
tocar nada: uno **diario** con el ranking completo, y uno **mensual/anual**
que compara el precio real de cierre en el inicio del año fiscal de cada
empresa contra su precio actual y el objetivo de los analistas. Todo corre
gratis en GitHub Actions — cero servidor propio, cero coste.

> ⚠️ **Aviso**: estos informes se generan automáticamente a partir de datos
> públicos (Yahoo Finance, SEC EDGAR y, opcionalmente, Financial Modeling
> Prep). No constituyen asesoramiento financiero ni recomendación de
> inversión personalizada.

## Índice

- [Acerca de](#acerca-de)
- [¿Qué hace?](#qué-hace)
- [Fuentes de datos combinadas](#fuentes-de-datos-combinadas)
- [Criterios del ranking](#criterios-del-ranking)
- [Factor de Calidad (desempate)](#factor-de-calidad-desempate)
- [Configuración](#configuración)
  - [1. Crear el bot de Telegram](#1-crear-el-bot-de-telegram)
  - [2. Configurar los secrets en GitHub](#2-configurar-los-secrets-en-github)
  - [3. Ejecutar](#3-ejecutar)
- [Personalizar la watchlist](#personalizar-la-watchlist)
- [Cesta temática "Trump trade"](#cesta-temática-trump-trade)
- [Diseño del PDF](#diseño-del-pdf)
- [Informe mensual/anual (Tabla seguimiento acciones)](#informe-mensualanual-tabla-seguimiento-acciones)
- [Estructura del proyecto](#estructura-del-proyecto)
- [Automatización (GitHub Actions)](#automatización-github-actions)
- [Glosario de métricas](#glosario-de-métricas)

## ¿Qué hace?

Para cada ticker de la [watchlist](watchlist.txt), `screener.py`:

1. Descarga fundamentales vía [yfinance](https://pypi.org/project/yfinance/)
   (precio actual, precio objetivo de analistas, P/E, capitalización de
   mercado, cobertura de analistas, crecimiento de beneficios, compras de
   insiders, recomendación de analistas).
2. Calcula un **score** según los [criterios del ranking](#criterios-del-ranking),
   comparando el P/E contra la media de su propio sector.
3. Se queda con el **Top 10** general, un **Top 5 de empresas de pequeña
   capitalización** (menos de 2.000 millones de USD) y una **cesta temática
   "Trump trade"** (ver más abajo), y enriquece las tres con noticias
   recientes traducidas al español, sentimiento heurístico de esas noticias
   y bancos con nota de compra fuerte.
4. Genera un **PDF** (`informe.pdf`) con portada, índice navegable y, para
   cada grupo (tabla resumen principal, pequeña capitalización, cesta
   "Trump trade"), su tabla y justo debajo la ficha detallada por acción de
   ese mismo grupo (precio, P/E, crecimiento, calidad, bancos y
   descripción). Cierra con noticias y un glosario con hipervínculos.
5. Envía el PDF como documento a un chat/canal de **Telegram**.

Además, `bot_listener.py` escucha el botón *"Generar informe ahora"* (o el
comando `/informe`) en Telegram para disparar el informe bajo demanda, fuera
del horario programado.

**¿El informe cambia cada vez o es un valor fijo/anual?** Cambia cada vez
que se genera. No hay ningún dato cacheado ni anual: cada ejecución hace
consultas en vivo a Yahoo Finance (y a los respaldos configurados) en ese
momento, así que el precio, el P/E, la recomendación, etc. reflejan el
mercado a la hora en que se generó ESE informe concreto, no un histórico ni
una foto fija. Verás la fecha y hora exactas en la portada del PDF
("Informe generado el ..."). Lo de "interanual" en el crecimiento de
beneficios se refiere a la ventana temporal que mide ese dato (este año vs.
el año pasado), no a la frecuencia de generación del informe.

## Fuentes de datos combinadas

Yahoo Finance (vía `yfinance`) es la fuente principal, pero no es una API
oficial (es scraping) y tiene huecos conocidos: no publica insider trading
fuera de EE.UU., y en acciones con poca cobertura de analistas (small/micro
caps) a menudo le faltan crecimiento y recomendación. Para eso, el informe
combina dos fuentes adicionales, ambas **opcionales y con fallback**: si no
están configuradas o fallan, todo sigue funcionando exactamente igual que
solo con Yahoo.

| Fuente | Qué aporta | Configuración |
|---|---|---|
| **SEC EDGAR** | Fuente PRIMARIA y oficial de insider buying (Form 4) para acciones que reportan a la SEC (EE.UU.). Gratis, sin API key. Si Yahoo tiene el dato pero EDGAR no encuentra el ticker o falla la consulta, se usa Yahoo como respaldo. No amplía cobertura fuera de EE.UU. (esas empresas no presentan Form 4 en ningún sitio). | Opcional pero recomendado: variable `SEC_EDGAR_USER_AGENT` con algo que te identifique (la SEC exige un User-Agent descriptivo), ej. `"MiScreener contacto@tudominio.com"`. Sin ella se usa un valor genérico que funciona pero no es buena práctica. |
| **Financial Modeling Prep (FMP)** | Respaldo de crecimiento, número de analistas y recomendación **solo** cuando Yahoo no tiene cobertura suficiente. Cuando se usa, aparece marcado como `(via FMP)` en la ficha detallada de cada acción del PDF. | Requiere una API key propia (gratis en [financialmodelingprep.com](https://site.financialmodelingprep.com), plan free = 250 peticiones/día). Variable `FMP_API_KEY`. Sin ella, esta llamada se salta directamente. |

Añade estas variables como secrets de GitHub (igual que `TELEGRAM_BOT_TOKEN`)
o en tu `.env` local si quieres activarlas.

## Criterios del ranking

Cada acción suma un punto por cada condición que cumple:

| Check | Condición |
|---|---|
| **P/E bajo** | P/E de la acción por debajo del P/E medio de su mismo sector |
| **PEG bueno** | PEG < 1.5 |
| **Crecimiento** | Crecimiento de beneficios interanual esperado > 15% |
| **Insider buying** | Algún directivo/accionista relevante compró acciones en los últimos 90 días |

El score se muestra como `aciertos/aplicables` (ej. `3/3`), no siempre sobre 4:
si Yahoo Finance no publica un dato para un ticker (típicamente insider
buying fuera de EEUU, o P/E, PEG o crecimiento en acciones con poca
cobertura), ese criterio no cuenta ni a favor ni en contra — no penalizamos
una acción por un dato que estructuralmente no puede tener. En caso de
empate se desempata por PEG (menor es mejor), nunca por el orden en el que
aparece el ticker en `watchlist.txt`.

**¿De dónde sale el crecimiento?** El campo de crecimiento de beneficios
viene de `earningsGrowth` en el módulo `financialData` de Yahoo Finance, el
mismo bloque de datos que agrega precios objetivo y recomendaciones de
analistas: es decir, es un **consenso de los analistas que cubren esa
acción**, no un cálculo propio de este script ni un dato verificado de
forma independiente. Por eso depende directamente de cuántos analistas
cubran el ticker (campo `numberOfAnalystOpinions`, mostrado como columna
"# Analistas" en el informe): con mucha cobertura (grandes tecnológicas de
EEUU) suele ser una cifra robusta; con poca o ninguna cobertura (típico en
small/micro caps, o en acciones poco seguidas fuera de EEUU) puede estar
desactualizada, basada en muy pocas estimaciones, o no existir.

**¿Para qué fecha es ese crecimiento?** Ojo, esto NO es un reloj de 12 meses
desde hoy: compara el **año fiscal** de la empresa (actual/próximo) contra su
año fiscal anterior. El año fiscal no tiene por qué coincidir con el año
natural — el de Apple, por ejemplo, termina en septiembre, no en diciembre.
Así que "interanual" significa "ese año fiscal vs. el anterior de esa misma
empresa", no necesariamente "2026 vs. 2025" en sentido de calendario. Un 60%
de crecimiento no significa lo mismo a 1 año fiscal que a 10. Eso sí: ni
Yahoo ni yfinance documentan públicamente el detalle exacto de este campo
concreto, así que es la convención más probable, no una certeza verificada
al 100%.

El Top 10 (general) y el Top 5 (pequeña capitalización) con mayor score son
los que se incluyen en el informe. En caso de empate en el score, desempata
el **factor de Calidad** (ver más abajo) antes que el PEG.

## Factor de Calidad (desempate)

Además del score anterior, cada acción tiene una columna **Calidad**
(`aciertos/aplicables`, igual formato que Score): una versión simplificada
del [Piotroski F-Score](https://en.wikipedia.org/wiki/Piotroski_F-score),
con 4 señales de solidez financiera:

| Check | Condición |
|---|---|
| **ROE bueno** | Return on Equity > 15% |
| **Margen bueno** | Margen operativo por encima de la media de su mismo sector |
| **Deuda baja** | Deuda/Patrimonio < 100% |
| **Liquidez buena** | Current ratio > 1.5 |

**¿Por qué no se mezcla con el Score principal?** El estudio original de
Piotroski (1976-1996) encontró que las acciones con F-Score alto batieron a
las de F-Score bajo por ~23 puntos porcentuales al año — pero aplicado
**solo a acciones ya baratas** (value), no a todo el mercado; usado de forma
aislada el efecto es mucho más débil. Por eso aquí la Calidad no se suma al
Score: se usa como **criterio de desempate**, después del Score y antes del
PEG (ver `rank_top()` en `screener.py`), reforzando el ranking de
valor/crecimiento en vez de sustituirlo.

Como con cualquier factor de este tipo: ningún patrón histórico garantiza
rendimiento futuro, y su efecto documentado tiende a debilitarse con el
tiempo (más gente lo usa, el mercado lo arbitra). Esto mejora el criterio de
desempate, no convierte el screener en una fórmula ganadora.

## Configuración

### 1. Crear el bot de Telegram

1. Habla con [@BotFather](https://t.me/BotFather) y crea un bot con `/newbot`.
   Guarda el **token** que te da.
2. Añade el bot al chat/canal donde quieras recibir los informes y consigue el
   **chat ID** (por ejemplo escribiéndole y consultando
   `https://api.telegram.org/bot<TOKEN>/getUpdates`).

### 2. Configurar los secrets en GitHub

En **Settings → Secrets and variables → Actions** del repositorio, añade:

| Secret | Descripción |
|---|---|
| `TELEGRAM_BOT_TOKEN` | Token del bot obtenido de BotFather |
| `TELEGRAM_CHAT_ID` | ID del chat/canal donde se enviará el informe |
| `FMP_API_KEY` (opcional) | Ver [Fuentes de datos combinadas](#fuentes-de-datos-combinadas) |
| `SEC_EDGAR_USER_AGENT` (opcional) | Ver [Fuentes de datos combinadas](#fuentes-de-datos-combinadas) |

Para desarrollo local, crea un archivo `.env` en la raíz del proyecto (no se
sube al repo) con las mismas variables:

```env
TELEGRAM_BOT_TOKEN=tu_token
TELEGRAM_CHAT_ID=tu_chat_id
FMP_API_KEY=tu_api_key_de_fmp
SEC_EDGAR_USER_AGENT=MiScreener contacto@tudominio.com
```

### 3. Ejecutar

```bash
pip install -r requirements.txt
python screener.py
```

Esto genera `informe.pdf` y lo envía al chat de Telegram configurado.

## Personalizar la watchlist

Edita [`watchlist.txt`](watchlist.txt): un ticker por línea (formato de
Yahoo Finance, ej. `ASML.AS`, `005930.KS`). Las líneas que empiezan con `#`
se ignoran y se usan como comentarios/agrupaciones.

La watchlist incluye un bloque de empresas de pequeña/micro capitalización
(EE.UU.) para alimentar la sección 2 del informe. La clasificación
"pequeña capitalización" (< 2.000 millones de USD) es **dinámica**: se
calcula en cada ejecución con el `marketCap` real de ese momento, así que
si una acción crece por encima del umbral simplemente deja de aparecer ahí
sin tocar el código.

## Cesta temática "Trump trade"

> ⚠️ **Esto NO es el patrimonio personal de Donald Trump** ni sale de ningún
> informe de activos declarado (esos informes, cuando existen, son sobre
> todo inmuebles y negocios privados, no una cartera de acciones cotizadas).

La sección 3 del informe es una cesta temática (`TRUMP_TRADE_THEMES` en
`screener.py`) con acciones que la prensa financiera (Goldman Sachs,
Kiplinger, Bloomberg, Investing.com, entre otros) asocia repetidamente con
políticas de su administración:

| Ticker | Tema |
|---|---|
| `DJT` | Empresa de Trump (Trump Media & Technology Group) |
| `LMT`, `RTX`, `NOC` | Defensa (gasto militar) |
| `NUE` | Aranceles al acero / manufactura doméstica |
| `XOM` | Energía (petróleo y gas domésticos) |
| `COIN` | Cripto (política regulatoria favorable) |
| `JPM` | Banca (desregulación financiera) |
| `GEO` | Inmigración (contratos de detención con ICE) |

Son tesis especulativas y muy sensibles a titulares y giros de política: por
ejemplo, GEO Group subió fuerte tras la elección de 2024 por sus contratos
de detención con ICE y luego borró esas subidas cuando hubo backlash público
por las condiciones de los centros. Que una acción aparezca aquí **no es una
recomendación de compra ni de venta**, solo documenta una narrativa de
mercado — se rankea con los mismos criterios que el resto del informe.

## Diseño del PDF

El informe usa un lenguaje visual sobrio (titulares en negrita pegados al
cuerpo del texto, sin reglas ni bloques de color separandolos, un unico
color de acento -navy- reservado para el logo y los enlaces) inspirado en
el formato de las cartas/notas de analisis financiero, para que se lea
como un documento serio y no como una diapositiva de colores. **No es una
plantilla real de J.P. Morgan Chase & Co. ni de ningun otro banco o entidad
financiera regulada**, ni esta afiliado, respaldado o revisado por ellos:
es una interpretación genérica de ese lenguaje visual.

El logo ("SEF-Financial" + una gata tricolor, `assets/sef_logo.png`) es una
marca propia del usuario, generada para este proyecto personal, y se repite
en la esquina superior de cada pagina. Los numeros (precios, P/E, %, etc.)
se formatean con el convenio español/europeo (punto de millar, coma
decimal: `1.234,56`), no con el convenio anglosajon por defecto de Python.

Para distinguir de un vistazo en qué sección del informe se está, las
páginas de "Empresas de pequeña capitalización" llevan un fondo cálido muy
suave, y las de "Noticias recientes" y "Glosario de variables" un fondo frío
igual de suave (el resto del informe queda en blanco); el texto negro sigue
siendo perfectamente legible sobre ambos tintes.

## Informe mensual/anual (Tabla seguimiento acciones)

Además del informe diario, `monthly_report.py` genera un **segundo PDF**
("Tabla seguimiento acciones") el día 1 de cada mes, y adicionalmente uno
anual el 1 de enero, comparando por cada acción:

| Columna | De dónde sale |
|---|---|
| **Inicio F.Y. / Fin F.Y.** | Mes/año en que empieza y termina el año fiscal ACTUAL de la empresa (no siempre coincide con el año natural). |
| **Precio inicio F.Y.** | Precio REAL de cierre de la acción en la fecha de inicio de su año fiscal, obtenido vía `yfinance.Ticker.history()` (histórico real de precios, no una aproximación ni un valor cacheado). |
| **P. real actual** | Precio de la acción en el momento de generar el informe. |
| **P. objetivo** | Precio objetivo medio de consenso de analistas — este sí es siempre el de HOY, porque Yahoo Finance no expone el objetivo que tenían los analistas en el pasado (a diferencia del precio de la acción, que sí tiene histórico real). |
| **Diferencia** | Cuánto por encima o por debajo queda el precio real respecto al objetivo. |

Las acciones se agrupan en las mismas 3 categorías que el informe principal
(Principales / Pequeña capitalización / Cesta temática "Trump trade"), cada
una en su propia hoja, con índice navegable y un glosario final. Usa la
misma función de renderizado de tablas (`render_table()` en `screener.py`)
y la misma portada (`draw_cover_page()`) que el informe diario, solo cambia
el título — para que ambos documentos se vean como parte del mismo sistema.

`price_history.json` (versionado en el repo) guarda una foto de precio +
objetivo cada vez que corre este script, pero **ya no es necesario para la
columna "Precio inicio F.Y."** (esa es histórico real desde el primer día);
se mantiene por si se añaden en el futuro métricas que sí dependan de una
foto propia en vez de historial de Yahoo.

## Estructura del proyecto

```
Stock-Screener/
├── screener.py             # Lógica principal: analiza, rankea y genera el PDF diario
├── monthly_report.py       # Informe mensual/anual "Tabla seguimiento acciones"
├── bot_listener.py         # Escucha Telegram para disparar el informe bajo demanda
├── watchlist.txt           # Lista de tickers a analizar
├── price_history.json     # Histórico de precio/objetivo (ver informe mensual/anual)
├── requirements.txt        # Dependencias de Python
└── .github/workflows/
    ├── screener.yml         # Ejecuta el screener 3 veces al día
    ├── monthly_report.yml   # Ejecuta el informe mensual/anual el día 1 de cada mes
    └── bot_listener.yml     # Comprueba el botón/comando de Telegram cada 10 min
```

## Automatización (GitHub Actions)

| Workflow | Frecuencia | Qué hace |
|---|---|---|
| `screener.yml` | 08:00, 14:00 y 21:00 (hora de Madrid) | Genera y envía el informe diario automáticamente |
| `monthly_report.yml` | Día 1 de cada mes, 08:00 (hora de Madrid); también el informe anual si es 1 de enero | Genera y envía la "Tabla seguimiento acciones" |
| `bot_listener.yml` | Cada 10 minutos | Comprueba si se pulsó "Generar informe ahora" o se envió `/informe`, y si es así dispara el informe |

Ambos workflows también se pueden lanzar manualmente desde la pestaña
**Actions** (`workflow_dispatch`).

## Glosario de métricas

| Métrica | Explicación |
|---|---|
| **Score** | Aciertos sobre criterios aplicables para ese ticker (ver [criterios del ranking](#criterios-del-ranking)). |
| **Precio** | Precio actual en el momento en que se generó ESE informe (no un valor fijo), en la divisa local del ticker (ver columna "País" para contexto: USD, EUR, KRW...). No convertido a una divisa común. |
| **P.Objetivo** | Precio objetivo medio de consenso de analistas (`targetMeanPrice`), mismas limitaciones de cobertura que "Crecim." y "Recomendación". **Horizonte: ~12 meses desde que CADA analista publicó su nota** (no desde hoy, ni fin de año calendario) — Yahoo agrega notas publicadas en fechas distintas, así que es una media de estimaciones a ~1 año desde momentos ligeramente distintos, nunca una proyección a varios años. |
| **Potencial** | Diferencia % entre "P.Objetivo" y "Precio", con el mismo horizonte aproximado de ~12 meses (ver aviso arriba). Positivo no garantiza subida real. |
| **P/E** | Precio / beneficio por acción (trailing). Se compara contra el promedio de su mismo sector, no un promedio global. Como referencia general: por debajo de 15 se suele considerar barato, entre 15 y 25 razonable, por encima de 25-30 caro / de alto crecimiento. |
| **PEG** | P/E dividido por el % de crecimiento esperado de beneficios. Por debajo de 1.5 sugiere que el precio no está sobrepagando ese crecimiento; por debajo de 1 se suele considerar barato. |
| **Crecim.** | Crecimiento interanual esperado del EPS. Consenso de analistas vía Yahoo Finance (`earningsGrowth`), ver la explicación completa de su origen [más arriba](#criterios-del-ranking). |
| **Insider buy** | Si algún directivo o accionista relevante compró acciones con su propio dinero en los últimos 90 días. Fuente primaria: SEC EDGAR (Form 4 oficial); si no encuentra el ticker, respaldo vía Yahoo Finance. N/D = ninguna de las dos fuentes tiene el dato (habitual fuera de EEUU); no cuenta ni a favor ni en contra. |
| **Calidad** | Versión simplificada del Piotroski F-Score (ROE, margen vs sector, deuda, liquidez). Ver [Factor de Calidad](#factor-de-calidad-desempate). |
| **ROE** | Return on Equity: beneficio neto / patrimonio neto. Por encima del 15% se considera bueno. |
| **Margen operativo** | Beneficio operativo / ingresos, comparado contra la media de su mismo sector. |
| **Deuda/Patrimonio** | Deuda total / patrimonio neto, en %. Por debajo de 100 se considera apalancamiento conservador. |
| **Liquidez** | Current ratio (activo corriente / pasivo corriente). Por encima de 1.5 se considera cómodo. |
| **Cap.** | Capitalización de mercado (`marketCap`). Determina si una acción entra en la sección de pequeña capitalización. |
| **# Analistas** | Número de analistas que cubren la acción según Yahoo Finance (`numberOfAnalystOpinions`). A menor cobertura, menos fiables son "Crecim." y "Recomendación". |
| **Recomendación** | Consenso agregado de analistas de bancos y brokers que cubren la acción. |
| **Bancos** | Firmas de análisis cuya nota más reciente sobre la acción fue de compra/sobreponderar. |
| **Sentimiento noticia** | Etiqueta automática por palabras clave sobre el titular+resumen de cada noticia. Es una heurística simple, no un análisis experto ni generado por IA. |
| **Cesta Trump trade** | Ver la sección [Cesta temática "Trump trade"](#cesta-temática-trump-trade) más arriba. |

Este mismo glosario está incluido, con hipervínculos desde la tabla, dentro
del PDF generado.
