# Dashboard de Calidad y Consistencia de Pricing

Dashboard HTML interactivo con metricas de calidad del motor de pricing para Colombia y Mexico.

## Requisitos

```bash
pip install google-cloud-bigquery pandas numpy
```

Autenticacion con GCP:
```bash
gcloud auth application-default login
```

## Uso

```bash
python dashboard_calidad_pricing.py
open ~/Documentos/reportes/calidad-pricing/dashboard_calidad_pricing.html
```

## Funcionalidades

- **Tabs de pais:** Colombia / Mexico
- **Filtros interactivos:** Area Metropolitana, Mes, Comite (F70/F71), Fuente
- **KPI Cards:** Total Pricings, Dif. Ask Habi, Mediana CV, Prom. Comparables
- **Graficas Chart.js:** Lineas, stacked bars con tooltips interactivos
- **Tablas resumen:** Debajo de cada grafica

## Secciones

| # | Seccion | Tipo grafica |
|---|---------|-------------|
| 1 | Volumen de Pricings | Linea (Manual vs Auto + por metro) |
| 2 | Diferencia Precio Cliente vs Habi | Linea (3 metricas + por metro) |
| 3 | Comparables por Muestra | Linea + por metro |
| 4 | Coeficiente de Variacion | Linea + umbral 10% |
| 5 | Antiguedad de Comparables | Linea + refs 6/12 meses |
| 6 | Paso de Escalera | Linea + stacked bar distribucion |
| 7 | Fuentes de Comparables | Stacked bar cantidad + % |
| 8 | Uso de Polynator | Linea CO vs MX |

## Fuentes de datos (BigQuery)

- **Calidad:** `tabla_historico_pricing_comparable_v2` (CO) / `habi_pricing_history_pricing_comparable` (MX)
- **Diferencia precios:** `funnel_sellers_co/mx`
- **Polynator:** `tabla_automatizacion_polinator`

## Filtros aplicados

- Deduplicacion: 1 pricing por inmueble por mes (el ultimo)
- Solo meses completos
- Excluye Ventana/ventana-scraping en CO
- Diferencia de precios: solo `rechazos_general_auxiliar_v2 = 'Aprobado General'`
- Ask Habi usa `ask_price_comite_post_remo`
