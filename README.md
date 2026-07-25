# 📈 Stock Screener

Screener automático de oportunidades de compra en bolsa. Analiza una watchlist de
tickers, los rankea por fundamentales (P/E, PEG, crecimiento de beneficios e
insider buying), genera un **informe en PDF** con el Top 10 y lo envía por
**Telegram** — todo corriendo gratis en GitHub Actions, sin servidor propio.

> ⚠️ **Aviso**: este informe se genera automáticamente a partir de datos públicos
> (Yahoo Finance). No constituye asesoramiento financiero ni recomendación de
> inversión personalizada.

## Índice

- [¿Qué hace?](#qué-hace)
- [Criterios del ranking](#criterios-del-ranking)
- [Configuración](#configuración)
  - [1. Crear el bot de Telegram](#1-crear-el-bot-de-telegram)
  - [2. Configurar los secrets en GitHub](#2-configurar-los-secrets-en-github)
  - [3. Ejecutar](#3-ejecutar)
- [Personalizar la watchlist](#personalizar-la-watchlist)
- [Estructura del proyecto](#estructura-del-proyecto)
- [Automatización (GitHub Actions)](#automatización-github-actions)
- [Glosario de métricas](#glosario-de-métricas)

## ¿Qué hace?

Para cada ticker de la [watchlist](watchlist.txt), `screener.py`:

1. Descarga fundamentales vía [yfinance](https://pypi.org/project/yfinance/)
   (P/E, capitalización de mercado, cobertura de analistas, crecimiento de
   beneficios, compras de insiders, recomendación de analistas).
2. Calcula un **score** según los [criterios del ranking](#criterios-del-ranking),
   comparando el P/E contra la media de su propio sector.
3. Se queda con el **Top 10** general y, por separado, un **Top 5 de empresas
   de pequeña capitalización** (menos de 2.000 millones de USD), y enriquece
   ambos con noticias recientes traducidas al español, sentimiento heurístico
   de esas noticias y bancos con nota de compra fuerte.
4. Genera un **PDF** (`informe.pdf`) con portada, índice navegable, tabla
   resumen, sección de pequeña capitalización, descripción detallada por
   acción, noticias y un glosario con hipervínculos.
5. Envía el PDF como documento a un chat/canal de **Telegram**.

Además, `bot_listener.py` escucha el botón *"Generar informe ahora"* (o el
comando `/informe`) en Telegram para disparar el informe bajo demanda, fuera
del horario programado.

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

El Top 10 (general) y el Top 5 (pequeña capitalización) con mayor score son
los que se incluyen en el informe.

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

Para desarrollo local, crea un archivo `.env` en la raíz del proyecto (no se
sube al repo) con las mismas variables:

```env
TELEGRAM_BOT_TOKEN=tu_token
TELEGRAM_CHAT_ID=tu_chat_id
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

## Estructura del proyecto

```
Stock-Screener/
├── screener.py             # Lógica principal: analiza, rankea y genera el PDF
├── bot_listener.py         # Escucha Telegram para disparar el informe bajo demanda
├── watchlist.txt           # Lista de tickers a analizar
├── requirements.txt        # Dependencias de Python
└── .github/workflows/
    ├── screener.yml        # Ejecuta el screener 3 veces al día
    └── bot_listener.yml    # Comprueba el botón/comando de Telegram cada 10 min
```

## Automatización (GitHub Actions)

| Workflow | Frecuencia | Qué hace |
|---|---|---|
| `screener.yml` | 08:00, 14:00 y 21:00 (hora de Madrid) | Genera y envía el informe automáticamente |
| `bot_listener.yml` | Cada 10 minutos | Comprueba si se pulsó "Generar informe ahora" o se envió `/informe`, y si es así dispara el informe |

Ambos workflows también se pueden lanzar manualmente desde la pestaña
**Actions** (`workflow_dispatch`).

## Glosario de métricas

| Métrica | Explicación |
|---|---|
| **Score** | Aciertos sobre criterios aplicables para ese ticker (ver [criterios del ranking](#criterios-del-ranking)). |
| **P/E** | Precio / beneficio por acción (trailing). Se compara contra el promedio de su mismo sector, no un promedio global. Como referencia general: por debajo de 15 se suele considerar barato, entre 15 y 25 razonable, por encima de 25-30 caro / de alto crecimiento. |
| **PEG** | P/E dividido por el % de crecimiento esperado de beneficios. Por debajo de 1.5 sugiere que el precio no está sobrepagando ese crecimiento; por debajo de 1 se suele considerar barato. |
| **Crecim.** | Crecimiento interanual esperado del EPS. Consenso de analistas vía Yahoo Finance (`earningsGrowth`), ver la explicación completa de su origen [más arriba](#criterios-del-ranking). |
| **Insider buy** | Si algún directivo o accionista relevante compró acciones con su propio dinero en los últimos 90 días (dato de comunicados SEC Form 4, vía Yahoo Finance). N/D = Yahoo no publica este dato para ese ticker (habitual fuera de EEUU); no cuenta ni a favor ni en contra. |
| **Cap.** | Capitalización de mercado (`marketCap`). Determina si una acción entra en la sección de pequeña capitalización. |
| **# Analistas** | Número de analistas que cubren la acción según Yahoo Finance (`numberOfAnalystOpinions`). A menor cobertura, menos fiables son "Crecim." y "Recomendación". |
| **Recomendación** | Consenso agregado de analistas de bancos y brokers que cubren la acción. |
| **Bancos** | Firmas de análisis cuya nota más reciente sobre la acción fue de compra/sobreponderar. |
| **Sentimiento noticia** | Etiqueta automática por palabras clave sobre el titular+resumen de cada noticia. Es una heurística simple, no un análisis experto ni generado por IA. |

Este mismo glosario está incluido, con hipervínculos desde la tabla, dentro
del PDF generado.
