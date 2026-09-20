# Validación de la entrega

- **49 pruebas automatizadas: correctas**, ejecutadas con Python 3.12, también con las dependencias reales instaladas.
- Cobertura: números no finitos, datos ausentes, pérdidas, fechas, divisas y subunidades, capitalización, estimaciones, comparación sin autorreferencia, XML Form 4, aislamiento de errores, protección contra fórmulas CSV, demo sin red/envíos y orden determinista.
- Sección adicional: histórico de caja/beneficios, dilución, ROIC faltante, límites del crecimiento extrapolado, descuento, margen de seguridad, conversión a peniques y precio demasiado alto.
- **Prueba real de Yahoo con AAPL y MSFT:** información y estimaciones recibidas; tres ejercicios comunes y tres cálculos de ROIC disponibles por empresa; sin incidencias registradas en esa ejecución. Al ser un universo de solo dos empresas, no hay comparables suficientes y no se fuerza una valoración. Esta prueba no constituye una selección de inversión.
- **Demo completa:** genera CSV, JSON, snapshot y PDF. Todas las empresas DEMO y sus cifras son ficticias. El PDF de prueba tenía 17 páginas y se ha inspeccionado visualmente, incluida la sección 6 y el índice actualizado.
- Se verificó generación de PDF con cero y diez candidatas durante la revisión del motor general, antes de incorporar la sección adicional. La última versión completa se verificó con las ocho empresas sintéticas de la demo. No se atribuyen a esos escenarios antiguos verificaciones de la sección nueva.
- No se han validado credenciales FMP ni envío de Telegram. SEC se probó mediante fixtures y controles de identidad/XML, no mediante una consulta live con la identidad del usuario.
- No se ha hecho backtest de rentabilidad. Las pruebas verifican comportamiento del software, no demuestran capacidad predictiva ni rentabilidad futura.

El acceso a proveedores puede cambiar. Un informe con incidencias o datos insuficientes exige revisarlos, aunque otras empresas se hayan procesado correctamente.

Por instrucción del usuario se ha retirado toda generación de HTML y los informes de ejemplo del paquete final. Se entregan código, pruebas y documentación.

Comprobación ampliada de Yahoo: seis de seis tickers recuperados (AAPL, MSFT, SAP.DE, SHEL.L, 7203.T y 005930.KS), sin excepciones; detalle y límites en PROVEEDORES.md. EDGAR permanece pendiente de identificación real para prueba live.
