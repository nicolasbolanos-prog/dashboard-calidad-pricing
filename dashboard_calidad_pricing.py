#!/usr/bin/env python3
"""Dashboard interactivo de Calidad y Consistencia de Pricing - CO y MX
Genera un HTML autocontenido con Chart.js, filtros y tabs.
"""
import os,json,warnings
warnings.filterwarnings('ignore')
import pandas as pd
import numpy as np
from google.cloud import bigquery
from datetime import datetime, date

client = bigquery.Client(project='papyrus-data')
OUT = os.path.expanduser('~/Documentos/reportes/calidad-pricing')
os.makedirs(OUT, exist_ok=True)

# ── Periodo ──
HOY = date.today()
FECHA_INI = f'{HOY.year}-01-01'
FECHA_FIN_EXCL = HOY.replace(day=1).strftime('%Y-%m-%d')
_ultimo_mes = HOY.replace(day=1) - pd.Timedelta(days=1)
_meses = pd.date_range(start=FECHA_INI, end=_ultimo_mes, freq='MS')
MESES = [m.strftime('%Y-%m') for m in _meses]
MN = {'01':'Ene','02':'Feb','03':'Mar','04':'Abr','05':'May','06':'Jun',
      '07':'Jul','08':'Ago','09':'Sep','10':'Oct','11':'Nov','12':'Dic'}
ML = [f"{MN[m.split('-')[1]]} {m[2:4]}" for m in MESES]
PERIODO = f"Enero - {MN[MESES[-1].split('-')[1]]} {MESES[-1][:4]}"

print(f"Periodo: {FECHA_INI} a {FECHA_FIN_EXCL}")
print(f"Meses: {', '.join(ML)}")

def rq(q,l=""):
    print(f"  {l}..."); df=client.query(q).to_dataframe(); print(f"    -> {len(df)}"); return df

# ── Queries ──
CO_METRO="""CASE
  WHEN ct.name IN ('bogota','soacha','zipaquira','chia','cajica','mosquera','funza','cota','la_calera','madrid','facatativa') THEN 'Bogota'
  WHEN ct.name IN ('medellin','bello','itagui','la_estrella','envigado','sabaneta','copacabana','caldas','barbosa','girardota','rionegro') THEN 'Medellin'
  WHEN ct.name IN ('barranquilla','soledad','malambo','galapa','puerto_colombia') THEN 'Barranquilla'
  WHEN ct.name IN ('cali','palmira','yumbo','jamundi') THEN 'Cali'
  ELSE 'Otras' END"""
MX_METRO="""CASE
  WHEN ct.name IN ('ciudad_de_mexico','Ecatepec de Morelos','Tlalnepantla de Baz','Naucalpan de Juárez','Tecámac','Nezahualcóyotl','Huixquilucan','Cuautitlán Izcalli','Tultitlán','Atizapán de Zaragoza','Coacalco de Berriozábal','La Paz','Chimalhuacán','Ixtapaluca','Nicolás Romero','Chalco','Tultepec','Cuautitlán','Chicoloapan','Zumpango') THEN 'CDMX'
  WHEN ct.name IN ('Guadalajara','Zapopan','Tlajomulco de Zúñiga','Tonalá','Tlaquepaque','San Pedro Tlaquepaque','El Salto') THEN 'Guadalajara'
  WHEN ct.name IN ('Querétaro','Corregidora','El Marqués') THEN 'Queretaro'
  WHEN ct.name IN ('Monterrey','San Pedro Garza García','Apodaca','San Nicolás de los Garza','General Escobedo','Guadalupe','Santa Catarina','García','Juárez') THEN 'Monterrey'
  ELSE 'Otras' END"""

q_diff_co = f"""
SELECT FORMAT_DATE('%Y-%m', fecha) as mes, area_metropolitana as metro,
  APPROX_QUANTILES(SAFE_DIVIDE(ask_price - ask_price_comite_post_remo, ask_price)*100, 100)[OFFSET(50)] as med_ask_habi,
  APPROX_QUANTILES(SAFE_DIVIDE(ask_price - precio_oferta_base_habi, ask_price)*100, 100)[OFFSET(50)] as med_precio_base,
  APPROX_QUANTILES(SAFE_DIVIDE(ask_price - ask_price_comite_pre_remo, ask_price)*100, 100)[OFFSET(50)] as med_pre_remo,
  COUNT(*) as vol
FROM `papyrus-delivery-data.idm_squad_3.funnel_sellers_co`
WHERE fecha >= '{FECHA_INI}' AND fecha < '{FECHA_FIN_EXCL}'
  AND ask_price > 0 AND ask_price_comite_post_remo > 0
  AND rechazos_general_auxiliar_v2 = 'Aprobado General'
GROUP BY 1, 2 ORDER BY 1, 2"""

q_diff_mx = f"""
SELECT FORMAT_DATE('%Y-%m', fecha) as mes, area_metropolitana as metro,
  APPROX_QUANTILES(SAFE_DIVIDE(ask_price - ask_price_comite_post_remo, ask_price)*100, 100)[OFFSET(50)] as med_ask_habi,
  APPROX_QUANTILES(SAFE_DIVIDE(ask_price - precio_oferta_base_habi, ask_price)*100, 100)[OFFSET(50)] as med_precio_base,
  APPROX_QUANTILES(SAFE_DIVIDE(ask_price - ask_price_comite_pre_remo, ask_price)*100, 100)[OFFSET(50)] as med_pre_remo,
  COUNT(*) as vol
FROM `papyrus-delivery-data.idm_squad_3.funnel_sellers_mx`
WHERE fecha >= '{FECHA_INI}' AND fecha < '{FECHA_FIN_EXCL}'
  AND ask_price > 0 AND ask_price_comite_post_remo > 0
  AND rechazos_general_auxiliar_v2 = 'Aprobado General'
GROUP BY 1, 2 ORDER BY 1, 2"""

q_quality_co=f"""
WITH pd AS (
  SELECT FORMAT_DATE('%Y-%m',p.fecha) as mes, CAST(c.flag_portal_pricing AS INT64) as flag,
    {CO_METRO} as metro,
    COALESCE(NULLIF(ARRAY_LENGTH(REGEXP_EXTRACT_ALL(JSON_EXTRACT(c.comparable,'$.area'),r'"[0-9]+"')),0),
      ARRAY_LENGTH(REGEXP_EXTRACT_ALL(JSON_EXTRACT(c.comparable,'$.built_area'),r'"[0-9]+"'))) as num_comps,
    CASE WHEN SAFE_CAST(c.coefficient_variation AS FLOAT64)<1 THEN SAFE_CAST(c.coefficient_variation AS FLOAT64)*100
      ELSE SAFE_CAST(c.coefficient_variation AS FLOAT64) END as cv_pct,
    COALESCE(
      (SELECT AVG(SAFE_CAST(v AS FLOAT64)) FROM UNNEST(REGEXP_EXTRACT_ALL(JSON_EXTRACT(c.comparable,'$.dias_antiguedad'),r':([0-9]+)')) v),
      (SELECT AVG(DATE_DIFF(DATE(p.fecha),SAFE.PARSE_DATE('%Y-%m-%d',SUBSTR(d,1,10)),DAY))
       FROM UNNEST(REGEXP_EXTRACT_ALL(JSON_EXTRACT(c.comparable,'$.date_create'),r'"(\\d{{4}}-\\d{{2}}-\\d{{2}}[^"]*)"')) d)
    ) as comp_age,
    SAFE_CAST(REGEXP_EXTRACT(c.meta_filter_step,r'(\\d+)$') AS INT64) as step_num
  FROM `papyrus-data.habi_db.tabla_historico_pricing_comparable_v2` c
  JOIN `papyrus-data.habi_db.tabla_historico_pricing_v2` p ON c.historico_pricing_id=p.id
  JOIN `papyrus-data.habi_wh.tabla_inmueble_v2` i ON p.inmueble_id=i.id
  JOIN `papyrus-data.habi_wh.tabla_fuente` fu ON i.fuente_id=fu.id
  JOIN `papyrus-data.habi_wh.tabla_localizacion_inmueble_v2` loc ON COALESCE(i.localizacion_new_id,i.localizacion_id)=loc.id
  JOIN `papyrus-data.habi_wh.tabla_zona_mediana` zm ON loc.zona_mediana_id=zm.id
  JOIN `papyrus-data.habi_wh.tabla_zona_grande` zg ON zm.zona_grande_id=zg.id
  JOIN `papyrus-data.habi_wh.tabla_ciudad` ct ON zg.ciudad_id=ct.id
  WHERE c.flag_portal_pricing IN (70,71) AND p.fecha>='{FECHA_INI}' AND p.fecha<'{FECHA_FIN_EXCL}' AND p.ask_price_habi>0
    AND fu.fuente NOT IN ('Ventana','ventana-scraping')
  QUALIFY ROW_NUMBER() OVER (PARTITION BY p.inmueble_id, FORMAT_DATE('%Y-%m',p.fecha) ORDER BY p.fecha DESC) = 1)
SELECT mes, flag, metro, COUNT(*) as vol,
  AVG(num_comps) as avg_comps, APPROX_QUANTILES(cv_pct,100)[OFFSET(50)] as med_cv,
  AVG(comp_age) as avg_age, AVG(step_num) as avg_step
FROM pd GROUP BY 1,2,3 ORDER BY 1,2,3"""

q_quality_mx=f"""
WITH pd AS (
  SELECT FORMAT_DATE('%Y-%m',p.date_create) as mes, CAST(c.portal_pricing_flag AS INT64) as flag,
    {MX_METRO} as metro,
    COALESCE(NULLIF(ARRAY_LENGTH(REGEXP_EXTRACT_ALL(TO_JSON_STRING(JSON_QUERY(c.comparable,'$.area')),r'"[0-9]+"')),0),
      ARRAY_LENGTH(REGEXP_EXTRACT_ALL(TO_JSON_STRING(JSON_QUERY(c.comparable,'$.built_area')),r'"[0-9]+"'))) as num_comps,
    COALESCE(SAFE_CAST(c.coefficient_variation AS FLOAT64),
      (SELECT SAFE_DIVIDE(STDDEV(SAFE_CAST(v AS FLOAT64)),AVG(SAFE_CAST(v AS FLOAT64)))*100
       FROM UNNEST(REGEXP_EXTRACT_ALL(TO_JSON_STRING(JSON_QUERY(c.comparable,'$.price_per_m2')),r':"?([0-9]+\\.[0-9]+)"?')) v)) as cv_pct,
    (SELECT AVG(DATE_DIFF(DATE(p.date_create),SAFE.PARSE_DATE('%Y-%m-%d',SUBSTR(d,1,10)),DAY))
     FROM UNNEST(REGEXP_EXTRACT_ALL(TO_JSON_STRING(JSON_QUERY(c.comparable,'$.date_create')),r'"(\\d{{4}}-\\d{{2}}-\\d{{2}}[^"]*)"')) d) as comp_age,
    SAFE_CAST(REGEXP_EXTRACT(c.meta_filter_step,r'(\\d+)$') AS INT64) as step_num
  FROM `papyrus-data-mx.habi_wh_priority.habi_pricing_history_pricing_comparable` c
  JOIN `papyrus-data-mx.habi_wh.history_pricing` p ON c.history_pricing_id=p.id
  JOIN `papyrus-data-mx.habi_wh.property` pr ON p.property_id=pr.id
  JOIN `papyrus-data-mx.habi_wh_priority.property_location` loc ON pr.property_location_id=loc.id
  JOIN `papyrus-data-mx.habi_wh.median_zone` mz ON loc.median_zone_id=mz.id
  JOIN `papyrus-data-mx.habi_wh.big_zone` bz ON mz.big_zone_id=bz.id
  JOIN `papyrus-data-mx.habi_wh.city` ct ON loc.city_id=ct.id
  WHERE c.portal_pricing_flag IN (70,71) AND p.date_create>='{FECHA_INI}' AND p.date_create<'{FECHA_FIN_EXCL}' AND p.ask_price_habi>0
  QUALIFY ROW_NUMBER() OVER (PARTITION BY p.property_id, FORMAT_DATE('%Y-%m',p.date_create) ORDER BY p.date_create DESC) = 1)
SELECT mes, flag, metro, COUNT(*) as vol,
  AVG(num_comps) as avg_comps, APPROX_QUANTILES(cv_pct,100)[OFFSET(50)] as med_cv,
  AVG(comp_age) as avg_age, AVG(step_num) as avg_step
FROM pd GROUP BY 1,2,3 ORDER BY 1,2,3"""

q_co_steps=f"""WITH dedup AS (
  SELECT c.meta_filter_step, p.fecha as p_fecha, p.inmueble_id
  FROM `papyrus-data.habi_db.tabla_historico_pricing_comparable_v2` c
  JOIN `papyrus-data.habi_db.tabla_historico_pricing_v2` p ON c.historico_pricing_id=p.id
  JOIN `papyrus-data.habi_wh.tabla_inmueble_v2` i ON p.inmueble_id=i.id
  JOIN `papyrus-data.habi_wh.tabla_fuente` fu ON i.fuente_id=fu.id
  WHERE c.flag_portal_pricing IN (70,71) AND p.fecha>='{FECHA_INI}' AND p.fecha<'{FECHA_FIN_EXCL}' AND p.ask_price_habi>0
    AND fu.fuente NOT IN ('Ventana','ventana-scraping')
  QUALIFY ROW_NUMBER() OVER (PARTITION BY p.inmueble_id, FORMAT_DATE('%Y-%m',p.fecha) ORDER BY p.fecha DESC) = 1
)
SELECT FORMAT_DATE('%Y-%m',p_fecha) as mes,
  CASE WHEN SAFE_CAST(REGEXP_EXTRACT(meta_filter_step,r'(\\d+)$') AS INT64) BETWEEN 1 AND 3 THEN '01. Pasos 1-3'
    WHEN SAFE_CAST(REGEXP_EXTRACT(meta_filter_step,r'(\\d+)$') AS INT64) BETWEEN 4 AND 6 THEN '02. Pasos 4-6'
    WHEN SAFE_CAST(REGEXP_EXTRACT(meta_filter_step,r'(\\d+)$') AS INT64) BETWEEN 7 AND 9 THEN '03. Pasos 7-9'
    WHEN SAFE_CAST(REGEXP_EXTRACT(meta_filter_step,r'(\\d+)$') AS INT64) >= 10 THEN '04. Pasos 10+'
    ELSE '05. Sin paso' END as step_bucket, COUNT(*) as n
FROM dedup GROUP BY 1,2"""

q_mx_steps=f"""WITH dedup AS (
  SELECT c.meta_filter_step, p.date_create as p_date, p.property_id
  FROM `papyrus-data-mx.habi_wh_priority.habi_pricing_history_pricing_comparable` c
  JOIN `papyrus-data-mx.habi_wh.history_pricing` p ON c.history_pricing_id=p.id
  WHERE c.portal_pricing_flag IN (70,71) AND p.date_create>='{FECHA_INI}' AND p.date_create<'{FECHA_FIN_EXCL}' AND p.ask_price_habi>0
  QUALIFY ROW_NUMBER() OVER (PARTITION BY p.property_id, FORMAT_DATE('%Y-%m',p.date_create) ORDER BY p.date_create DESC) = 1
)
SELECT FORMAT_DATE('%Y-%m',p_date) as mes,
  CASE WHEN SAFE_CAST(REGEXP_EXTRACT(meta_filter_step,r'(\\d+)$') AS INT64) BETWEEN 1 AND 8 THEN '01. Pasos 1-8 (6m)'
    WHEN SAFE_CAST(REGEXP_EXTRACT(meta_filter_step,r'(\\d+)$') AS INT64) BETWEEN 9 AND 16 THEN '02. Pasos 9-16 (12m)'
    WHEN SAFE_CAST(REGEXP_EXTRACT(meta_filter_step,r'(\\d+)$') AS INT64) BETWEEN 17 AND 32 THEN '03. Pasos 17-32 (18-24m)'
    WHEN SAFE_CAST(REGEXP_EXTRACT(meta_filter_step,r'(\\d+)$') AS INT64) >= 33 THEN '04. Pasos 33+ (>24m)'
    ELSE '05. Sin paso' END as step_bucket, COUNT(*) as n
FROM dedup GROUP BY 1,2"""

q_co_src=f"""WITH dedup AS (
  SELECT c.comparable, p.fecha as p_fecha, p.inmueble_id
  FROM `papyrus-data.habi_db.tabla_historico_pricing_comparable_v2` c
  JOIN `papyrus-data.habi_db.tabla_historico_pricing_v2` p ON c.historico_pricing_id=p.id
  JOIN `papyrus-data.habi_wh.tabla_inmueble_v2` i ON p.inmueble_id=i.id
  JOIN `papyrus-data.habi_wh.tabla_fuente` fu ON i.fuente_id=fu.id
  WHERE c.flag_portal_pricing IN (70,71) AND p.fecha>='{FECHA_INI}' AND p.fecha<'{FECHA_FIN_EXCL}' AND p.ask_price_habi>0
    AND fu.fuente NOT IN ('Ventana','ventana-scraping')
  QUALIFY ROW_NUMBER() OVER (PARTITION BY p.inmueble_id, FORMAT_DATE('%Y-%m',p.fecha) ORDER BY p.fecha DESC) = 1
)
SELECT FORMAT_DATE('%Y-%m',p_fecha) as mes, src, COUNT(*) as n
FROM dedup
CROSS JOIN UNNEST(REGEXP_EXTRACT_ALL(COALESCE(JSON_EXTRACT(comparable,'$.fuente'),JSON_EXTRACT(comparable,'$.source_id')),r':"([^"]+)"')) as src
GROUP BY 1,2"""

q_mx_src=f"""WITH dedup AS (
  SELECT c.comparable, p.date_create as p_date, p.property_id
  FROM `papyrus-data-mx.habi_wh_priority.habi_pricing_history_pricing_comparable` c
  JOIN `papyrus-data-mx.habi_wh.history_pricing` p ON c.history_pricing_id=p.id
  WHERE c.portal_pricing_flag IN (70,71) AND p.date_create>='{FECHA_INI}' AND p.date_create<'{FECHA_FIN_EXCL}' AND p.ask_price_habi>0
  QUALIFY ROW_NUMBER() OVER (PARTITION BY p.property_id, FORMAT_DATE('%Y-%m',p.date_create) ORDER BY p.date_create DESC) = 1
)
SELECT FORMAT_DATE('%Y-%m',p_date) as mes, src, COUNT(*) as n
FROM dedup
CROSS JOIN UNNEST(REGEXP_EXTRACT_ALL(COALESCE(TO_JSON_STRING(JSON_QUERY(comparable,'$.source')),TO_JSON_STRING(JSON_QUERY(comparable,'$.source_id'))),r':"?([A-Za-z][^",}}]+)"?')) as src
GROUP BY 1,2"""

q_poly=f"""SELECT pais, FORMAT_DATE('%Y-%m', fecha) as mes,
  COUNTIF(polynator = 'Si') as con_polynator,
  COUNT(nid) as total,
  SAFE_DIVIDE(COUNTIF(polynator = 'Si'), COUNT(nid)) * 100 as pct_polynator
FROM `papyrus-delivery-data.idm_squad_precios.tabla_automatizacion_polinator`
WHERE fecha >= '{FECHA_INI}' AND fecha < '{FECHA_FIN_EXCL}'
GROUP BY 1, 2 ORDER BY 1, 2"""

# ── Ejecutar queries ──
print("="*50)
diff_co=rq(q_diff_co,"CO diff"); diff_mx=rq(q_diff_mx,"MX diff")
qual_co=rq(q_quality_co,"CO quality"); qual_mx=rq(q_quality_mx,"MX quality")
co_steps=rq(q_co_steps,"CO steps"); mx_steps=rq(q_mx_steps,"MX steps")
co_src=rq(q_co_src,"CO src"); mx_src=rq(q_mx_src,"MX src")
poly_all=rq(q_poly,"Polynator")

# ── Preparar JSON ──
def df_to_json(df):
    records = []
    for _, r in df.iterrows():
        row = {}
        for c in df.columns:
            v = r[c]
            if pd.isna(v): row[c] = None
            elif isinstance(v, (np.integer,int)): row[c] = int(v)
            elif isinstance(v, (np.floating,float)): row[c] = round(float(v),2)
            else: row[c] = str(v)
        records.append(row)
    return records

datasets = {
    'diff_co': df_to_json(diff_co), 'diff_mx': df_to_json(diff_mx),
    'qual_co': df_to_json(qual_co), 'qual_mx': df_to_json(qual_mx),
    'steps_co': df_to_json(co_steps), 'steps_mx': df_to_json(mx_steps),
    'src_co': df_to_json(co_src), 'src_mx': df_to_json(mx_src),
    'poly': df_to_json(poly_all),
}

metros_co = sorted([m for m in qual_co['metro'].unique() if m and str(m)!='nan'])
metros_mx = sorted([m for m in qual_mx['metro'].unique() if m and str(m)!='nan'])
sources_co = sorted([s for s in co_src['src'].unique() if s and str(s)!='nan'])
sources_mx = sorted([s for s in mx_src['src'].unique() if s and str(s)!='nan'])

meta = {
    'meses': MESES, 'meses_labels': ML, 'periodo': PERIODO,
    'metros_co': metros_co, 'metros_mx': metros_mx,
    'sources_co': sources_co, 'sources_mx': sources_mx,
    'fecha_gen': HOY.strftime('%d/%m/%Y'),
}

datasets_json = json.dumps(datasets, ensure_ascii=False)
meta_json = json.dumps(meta, ensure_ascii=False)

# ── Generar HTML ──
print("\nGenerando HTML...")

html = f'''<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Dashboard Calidad Pricing - {PERIODO}</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4"></script>
<style>
*{{margin:0;padding:0;box-sizing:border-box}}
body{{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;background:#f5f7fa;color:#333}}
.header{{background:linear-gradient(135deg,#1a237e,#3BA5B5);color:#fff;padding:16px 24px;display:flex;justify-content:space-between;align-items:center}}
.header h1{{font-size:20px;font-weight:600}} .header .sub{{font-size:13px;opacity:.8}}
.container{{max-width:1400px;margin:0 auto;padding:16px}}
.country-tabs{{display:flex;gap:4px;margin-bottom:12px}}
.country-tab{{padding:10px 24px;border:none;background:#e0e0e0;cursor:pointer;font-size:14px;font-weight:600;border-radius:8px 8px 0 0;transition:.2s}}
.country-tab.active{{background:#3BA5B5;color:#fff}}
.filter-bar{{background:#fff;border-radius:10px;padding:12px 16px;margin-bottom:16px;display:flex;flex-wrap:wrap;gap:10px;align-items:center;box-shadow:0 1px 3px rgba(0,0,0,.08)}}
.dd-wrap{{position:relative;display:inline-block}}
.dd-trigger{{padding:7px 32px 7px 12px;border:1px solid #ccc;border-radius:6px;background:#fff;cursor:pointer;font-size:13px;min-width:140px;text-align:left;position:relative}}
.dd-trigger::after{{content:'\\25BC';position:absolute;right:10px;top:50%;transform:translateY(-50%);font-size:9px;color:#888}}
.dd-trigger.active{{border-color:#3BA5B5;background:#e8f4f6}}
.dd-panel{{display:none;position:absolute;top:100%;left:0;background:#fff;border:1px solid #ddd;border-radius:6px;box-shadow:0 4px 12px rgba(0,0,0,.12);z-index:100;min-width:200px;max-height:280px;overflow-y:auto;padding:6px 0}}
.dd-panel.open{{display:block}}
.dd-panel label{{display:flex;align-items:center;padding:5px 12px;cursor:pointer;font-size:13px;gap:6px}}
.dd-panel label:hover{{background:#f0f0f0}}
.dd-panel hr{{border:none;border-top:1px solid #eee;margin:4px 0}}
.btn{{padding:7px 18px;border:none;border-radius:6px;cursor:pointer;font-size:13px;font-weight:600}}
.btn-primary{{background:#3BA5B5;color:#fff}} .btn-primary:hover{{background:#2d8a97}}
.btn-secondary{{background:#e0e0e0;color:#555}} .btn-secondary:hover{{background:#ccc}}
.kpi-row{{display:flex;gap:12px;margin-bottom:16px;flex-wrap:wrap}}
.kpi-card{{flex:1;min-width:180px;background:#fff;border-radius:10px;padding:16px;text-align:center;box-shadow:0 1px 3px rgba(0,0,0,.08)}}
.kpi-card .value{{font-size:28px;font-weight:700;color:#1a237e}} .kpi-card .label{{font-size:12px;color:#888;margin-top:4px}}
.section-tabs{{display:flex;gap:2px;margin-bottom:16px;flex-wrap:wrap}}
.section-tab{{padding:8px 16px;border:none;background:#e8e8e8;cursor:pointer;font-size:12px;font-weight:500;border-radius:6px;transition:.2s}}
.section-tab.active{{background:#1a237e;color:#fff}}
.section-content{{display:none}} .section-content.active{{display:block}}
.chart-box{{background:#fff;border-radius:10px;padding:20px;margin-bottom:16px;box-shadow:0 1px 3px rgba(0,0,0,.08)}}
.chart-box h3{{font-size:15px;font-weight:600;margin-bottom:12px;color:#333}}
.chart-row{{display:flex;gap:16px;flex-wrap:wrap}}
.chart-half{{flex:1;min-width:400px}}
canvas{{max-height:350px}}
table{{width:100%;border-collapse:collapse;font-size:12px;margin-top:12px}}
th{{background:#f0f0f0;padding:8px;text-align:left;font-weight:600;position:sticky;top:0}}
td{{padding:6px 8px;border-bottom:1px solid #eee}}
tr:hover td{{background:#f8f8f8}}
.note{{font-size:11px;color:#999;margin-top:8px;font-style:italic}}
</style>
</head>
<body>
<div class="header">
  <div><h1>Dashboard de Calidad y Consistencia de Pricing</h1><div class="sub">{PERIODO} | Colombia y Mexico</div></div>
  <div class="sub">Generado: {meta['fecha_gen']}</div>
</div>
<div class="container">
  <!-- Country tabs -->
  <div class="country-tabs">
    <button class="country-tab active" onclick="setCountry('co')">Colombia</button>
    <button class="country-tab" onclick="setCountry('mx')">Mexico</button>
  </div>
  <!-- Filter bar -->
  <div class="filter-bar">
    <div class="dd-wrap" id="dd-metro"><button class="dd-trigger" onclick="toggleDD('metro')">Area Metro: Todas</button><div class="dd-panel" id="panel-metro"></div></div>
    <div class="dd-wrap" id="dd-mes"><button class="dd-trigger" onclick="toggleDD('mes')">Mes: Todos</button><div class="dd-panel" id="panel-mes"></div></div>
    <div class="dd-wrap" id="dd-comite"><button class="dd-trigger" onclick="toggleDD('comite')">Comite: Todos</button><div class="dd-panel" id="panel-comite"></div></div>
    <div class="dd-wrap" id="dd-source"><button class="dd-trigger" onclick="toggleDD('source')">Fuente: Todas</button><div class="dd-panel" id="panel-source"></div></div>
    <button class="btn btn-primary" onclick="applyFilters()">Aplicar</button>
    <button class="btn btn-secondary" onclick="clearFilters()">Limpiar</button>
  </div>
  <!-- KPIs -->
  <div class="kpi-row">
    <div class="kpi-card"><div class="value" id="kpi-vol">-</div><div class="label">Total Pricings</div></div>
    <div class="kpi-card"><div class="value" id="kpi-diff">-</div><div class="label">Med. Dif. Ask Habi %</div></div>
    <div class="kpi-card"><div class="value" id="kpi-cv">-</div><div class="label">Mediana CV %</div></div>
    <div class="kpi-card"><div class="value" id="kpi-comps">-</div><div class="label">Prom. Comparables</div></div>
  </div>
  <!-- Section tabs -->
  <div class="section-tabs">
    <button class="section-tab active" onclick="setSection('volumen')">1. Volumen</button>
    <button class="section-tab" onclick="setSection('diff')">2. Dif. Precio</button>
    <button class="section-tab" onclick="setSection('comps')">3. Comparables</button>
    <button class="section-tab" onclick="setSection('cv')">4. CV</button>
    <button class="section-tab" onclick="setSection('age')">5. Antiguedad</button>
    <button class="section-tab" onclick="setSection('step')">6. Escalera</button>
    <button class="section-tab" onclick="setSection('src')">7. Fuentes</button>
    <button class="section-tab" onclick="setSection('poly')">8. Polynator</button>
  </div>
  <!-- Section contents -->
  <div class="section-content active" id="sec-volumen"><div class="chart-box"><h3>Volumen de Pricings</h3><canvas id="ch-vol"></canvas></div><div class="chart-box"><h3>Volumen por Area Metropolitana</h3><canvas id="ch-vol-metro"></canvas></div><div id="tbl-vol"></div></div>
  <div class="section-content" id="sec-diff"><div class="chart-box"><h3>Diferencia Precio Cliente vs Habi (Mediana %)</h3><canvas id="ch-diff"></canvas></div><div class="chart-box"><h3>Diferencia Ask Habi por Area Metropolitana</h3><canvas id="ch-diff-metro"></canvas></div><div id="tbl-diff"></div></div>
  <div class="section-content" id="sec-comps"><div class="chart-box"><h3>Comparables por Muestra (Promedio)</h3><canvas id="ch-comps"></canvas></div><div class="chart-box"><h3>Comparables por Area Metropolitana</h3><canvas id="ch-comps-metro"></canvas></div><div id="tbl-comps"></div></div>
  <div class="section-content" id="sec-cv"><div class="chart-box"><h3>Coeficiente de Variacion (Mediana %)</h3><canvas id="ch-cv"></canvas></div><div class="chart-box"><h3>CV por Area Metropolitana</h3><canvas id="ch-cv-metro"></canvas></div><div id="tbl-cv"></div></div>
  <div class="section-content" id="sec-age"><div class="chart-box"><h3>Antiguedad de Comparables (Promedio dias)</h3><canvas id="ch-age"></canvas></div><div class="chart-box"><h3>Antiguedad por Area Metropolitana</h3><canvas id="ch-age-metro"></canvas></div><div id="tbl-age"></div></div>
  <div class="section-content" id="sec-step"><div class="chart-box"><h3>Paso Promedio de Escalera</h3><canvas id="ch-step"></canvas></div><div class="chart-box"><h3>Distribucion de Pasos</h3><canvas id="ch-step-dist"></canvas></div><div id="tbl-step"></div></div>
  <div class="section-content" id="sec-src"><div class="chart-row"><div class="chart-half"><div class="chart-box"><h3>Fuentes - Cantidad</h3><canvas id="ch-src-qty"></canvas></div></div><div class="chart-half"><div class="chart-box"><h3>Fuentes - Composicion %</h3><canvas id="ch-src-pct"></canvas></div></div></div><div id="tbl-src"></div></div>
  <div class="section-content" id="sec-poly"><div class="chart-box"><h3>% de Uso de Polynator por Pais</h3><canvas id="ch-poly"></canvas></div><div id="tbl-poly"></div></div>
</div>

<script>
const DS = {datasets_json};
const META = {meta_json};
const COLORS = {{
  blue:'#1976D2',purple:'#7B1FA2',orange:'#EF6C00',green:'#2E7D32',red:'#D32F2F',gray:'#757575',
  teal:'#00897B',pink:'#C2185B',amber:'#FF8F00',indigo:'#303F9F'
}};
const PAL = ['#1976D2','#EF6C00','#2E7D32','#7B1FA2','#D32F2F','#757575','#00897B','#C2185B','#FF8F00','#303F9F'];
const METRO_C = {{'Bogota':PAL[0],'Medellin':PAL[1],'Barranquilla':PAL[3],'Cali':PAL[2],'Otras':PAL[5],
  'CDMX':PAL[0],'Guadalajara':PAL[1],'Monterrey':PAL[3],'Queretaro':PAL[2]}};

let state = {{country:'co', section:'volumen', metro:['__all__'], mes:['__all__'], comite:['__all__'], source:['__all__']}};
let charts = {{}};

// ── Dropdown helpers ──
function toggleDD(name) {{
  document.querySelectorAll('.dd-panel').forEach(p => {{ if(p.id !== 'panel-'+name) p.classList.remove('open'); }});
  document.getElementById('panel-'+name).classList.toggle('open');
}}
document.addEventListener('click', e => {{
  if(!e.target.closest('.dd-wrap')) document.querySelectorAll('.dd-panel').forEach(p=>p.classList.remove('open'));
}});

function buildDD(name, values, allLabel) {{
  const panel = document.getElementById('panel-'+name);
  let h = `<label><input type="checkbox" value="__all__" checked onchange="handleCB('${{name}}')"> ${{allLabel}}</label><hr>`;
  values.forEach(v => {{ h += `<label><input type="checkbox" value="${{v}}" onchange="handleCB('${{name}}')"> ${{v}}</label>`; }});
  panel.innerHTML = h;
}}
function handleCB(name) {{
  const panel = document.getElementById('panel-'+name);
  const allCb = panel.querySelector('input[value="__all__"]');
  const items = [...panel.querySelectorAll('input:not([value="__all__"])')];
  if(event.target.value === '__all__') {{
    items.forEach(cb => cb.checked = false);
    allCb.checked = true;
  }} else {{
    allCb.checked = false;
    if(!items.some(cb => cb.checked)) allCb.checked = true;
  }}
  updateTrigger(name);
}}
function updateTrigger(name) {{
  const panel = document.getElementById('panel-'+name);
  const allCb = panel.querySelector('input[value="__all__"]');
  const items = [...panel.querySelectorAll('input:not([value="__all__"])')];
  const trigger = panel.parentElement.querySelector('.dd-trigger');
  const labels = {{'metro':'Area Metro','mes':'Mes','comite':'Comite','source':'Fuente'}};
  if(allCb.checked) {{
    trigger.textContent = labels[name]+': Todas';
    trigger.classList.remove('active');
  }} else {{
    const sel = items.filter(cb=>cb.checked).map(cb=>cb.value);
    trigger.textContent = sel.length <= 2 ? labels[name]+': '+sel.join(', ') : labels[name]+': '+sel.length+' sel.';
    trigger.classList.add('active');
  }}
}}
function readFilter(name) {{
  const panel = document.getElementById('panel-'+name);
  const allCb = panel.querySelector('input[value="__all__"]');
  if(allCb && allCb.checked) return ['__all__'];
  return [...panel.querySelectorAll('input:checked:not([value="__all__"])')].map(cb=>cb.value);
}}

function initFilters() {{
  const metros = state.country==='co' ? META.metros_co : META.metros_mx;
  const sources = state.country==='co' ? META.sources_co : META.sources_mx;
  buildDD('metro', metros, 'Todas');
  buildDD('mes', META.meses.map((m,i) => m), 'Todos');
  buildDD('comite', ['70','71'], 'Todos');
  buildDD('source', sources, 'Todas');
  // Custom labels for mes
  const mesPanel = document.getElementById('panel-mes');
  const mesCbs = mesPanel.querySelectorAll('label');
  mesCbs.forEach((lbl,i) => {{
    if(i>0) {{ // skip "Todos"
      const cb = lbl.querySelector('input');
      lbl.childNodes[1].textContent = ' '+META.meses_labels[i-1];
    }}
  }});
  // Custom labels for comite
  const comPanel = document.getElementById('panel-comite');
  const comLabels = comPanel.querySelectorAll('label');
  comLabels.forEach(lbl => {{
    const cb = lbl.querySelector('input');
    if(cb.value==='70') lbl.childNodes[1].textContent = ' F70 Manual';
    if(cb.value==='71') lbl.childNodes[1].textContent = ' F71 Automatico';
  }});
}}

// ── Filter & State ──
function setCountry(c) {{
  state.country = c;
  document.querySelectorAll('.country-tab').forEach(t => t.classList.remove('active'));
  event.target.classList.add('active');
  clearFilters();
}}
function setSection(s) {{
  state.section = s;
  document.querySelectorAll('.section-tab').forEach(t => t.classList.remove('active'));
  event.target.classList.add('active');
  document.querySelectorAll('.section-content').forEach(d => d.classList.remove('active'));
  document.getElementById('sec-'+s).classList.add('active');
  // Show/hide filters based on section
  document.getElementById('dd-source').style.display = s==='src' ? '' : 'none';
  document.getElementById('dd-comite').style.display = ['volumen','comps','cv','age'].includes(s) ? '' : 'none';
  document.getElementById('dd-metro').style.display = ['volumen','diff','comps','cv','age'].includes(s) ? '' : 'none';
  renderSection();
}}
function applyFilters() {{
  state.metro = readFilter('metro');
  state.mes = readFilter('mes');
  state.comite = readFilter('comite');
  state.source = readFilter('source');
  renderKPIs();
  renderSection();
}}
function clearFilters() {{
  initFilters();
  state.metro=['__all__']; state.mes=['__all__']; state.comite=['__all__']; state.source=['__all__'];
  renderKPIs();
  renderSection();
}}

// ── Data helpers ──
function fd(key, opts) {{
  let data = DS[key+'_'+state.country] || DS[key] || [];
  if(opts.metro && !state.metro.includes('__all__')) data = data.filter(r => state.metro.includes(r.metro));
  if(opts.mes && !state.mes.includes('__all__')) data = data.filter(r => state.mes.includes(r.mes));
  if(opts.flag && !state.comite.includes('__all__')) data = data.filter(r => state.comite.includes(String(r.flag)));
  if(opts.src && !state.source.includes('__all__')) data = data.filter(r => state.source.includes(r.src));
  return data;
}}
function wavg(rows, col, wCol) {{
  let s=0,w=0; rows.forEach(r => {{ if(r[col]!=null && r[wCol]!=null) {{ s+=r[col]*r[wCol]; w+=r[wCol]; }} }});
  return w>0 ? s/w : null;
}}
function aggByMes(data, aggFn) {{
  const groups = {{}};
  data.forEach(r => {{ if(!groups[r.mes]) groups[r.mes]=[]; groups[r.mes].push(r); }});
  const meses = Object.keys(groups).sort();
  return meses.map(m => ({{mes:m, ...aggFn(groups[m])}}));
}}
function mesLabel(m) {{ const i = META.meses.indexOf(m); return i>=0 ? META.meses_labels[i] : m; }}
function fmtN(v) {{ return v!=null ? v.toLocaleString('es-CO',{{maximumFractionDigits:0}}) : '-'; }}
function fmtP(v) {{ return v!=null ? v.toFixed(2)+'%' : '-'; }}
function fmtD(v) {{ return v!=null ? v.toFixed(1) : '-'; }}

// ── Chart helpers ──
function destroyChart(id) {{ if(charts[id]) {{ charts[id].destroy(); delete charts[id]; }} }}
function lineChart(canvasId, labels, datasets, yFmt, refLines) {{
  destroyChart(canvasId);
  const ctx = document.getElementById(canvasId);
  if(!ctx) return;
  const plugins = [];
  if(refLines && refLines.length) {{
    plugins.push({{
      id:'refLines',
      afterDraw(chart) {{
        const {{ctx:c, chartArea:{{left,right}}, scales:{{y}}}} = chart;
        refLines.forEach(rl => {{
          const yy = y.getPixelForValue(rl.value);
          c.save(); c.beginPath(); c.moveTo(left,yy); c.lineTo(right,yy);
          c.strokeStyle=rl.color; c.lineWidth=1; c.setLineDash(rl.dash||[5,5]); c.stroke();
          c.fillStyle=rl.color; c.font='11px sans-serif'; c.fillText(rl.label,right-60,yy-5);
          c.restore();
        }});
      }}
    }});
  }}
  charts[canvasId] = new Chart(ctx, {{
    type:'line',
    data:{{labels, datasets}},
    options:{{
      responsive:true, maintainAspectRatio:false,
      interaction:{{mode:'index',intersect:false}},
      plugins:{{
        legend:{{position:'bottom',labels:{{usePointStyle:true,font:{{size:11}}}}}},
        tooltip:{{callbacks:{{
          label(c) {{ const v=c.raw; return v!=null ? c.dataset.label+': '+(yFmt==='pct'?v.toFixed(2)+'%':yFmt==='int'?Math.round(v).toLocaleString():v.toFixed(1)) : ''; }}
        }}}}
      }},
      scales:{{
        y:{{ticks:{{callback(v){{ return yFmt==='pct'?v.toFixed(0)+'%':yFmt==='int'?v.toLocaleString():v.toFixed(1); }}}}}},
        x:{{grid:{{display:false}}}}
      }}
    }},
    plugins
  }});
  ctx.parentElement.style.height = '320px';
}}
function stackedBarChart(canvasId, labels, datasets, yFmt) {{
  destroyChart(canvasId);
  const ctx = document.getElementById(canvasId);
  if(!ctx) return;
  charts[canvasId] = new Chart(ctx, {{
    type:'bar',
    data:{{labels, datasets}},
    options:{{
      responsive:true, maintainAspectRatio:false,
      interaction:{{mode:'index',intersect:false}},
      plugins:{{
        legend:{{position:'bottom',labels:{{usePointStyle:true,font:{{size:11}}}}}},
        tooltip:{{callbacks:{{
          label(c) {{ return c.dataset.label+': '+(yFmt==='pct'?c.raw.toFixed(0)+'%':Math.round(c.raw).toLocaleString()); }},
          afterBody(items) {{
            const total = items.reduce((s,i)=>s+(i.raw||0),0);
            return 'Total: '+(yFmt==='pct'?total.toFixed(0)+'%':Math.round(total).toLocaleString());
          }}
        }}}}
      }},
      scales:{{
        x:{{stacked:true,grid:{{display:false}}}},
        y:{{stacked:true,ticks:{{callback(v){{ return yFmt==='pct'?v+'%':v.toLocaleString(); }}}}}}
      }}
    }}
  }});
  ctx.parentElement.style.height = '320px';
}}
function mkDS(label, data, color, opts) {{
  return {{label, data, borderColor:color, backgroundColor:color+(opts?.fill?'33':''), pointBackgroundColor:color,
    borderWidth:2.5, pointRadius:4, tension:.1, fill:!!opts?.fill, ...opts}};
}}
function mkBarDS(label, data, color) {{
  return {{label, data, backgroundColor:color+'CC', borderColor:'#fff', borderWidth:1}};
}}

// ── Table helper ──
function renderTable(containerId, headers, rows) {{
  let h = '<div class="chart-box"><table><thead><tr>';
  headers.forEach(hd => h += '<th>'+hd+'</th>');
  h += '</tr></thead><tbody>';
  rows.forEach(r => {{ h += '<tr>'; r.forEach(v => h += '<td>'+v+'</td>'); h += '</tr>'; }});
  h += '</tbody></table></div>';
  document.getElementById(containerId).innerHTML = h;
}}

// ── KPIs ──
function renderKPIs() {{
  const q = fd('qual', {{metro:true, mes:true, flag:true}});
  const d = fd('diff', {{metro:true, mes:true}});
  const totalVol = q.reduce((s,r)=>s+(r.vol||0),0);
  const avgComps = wavg(q,'avg_comps','vol');
  const medCV = wavg(q,'med_cv','vol');
  const medDiff = wavg(d,'med_ask_habi','vol');
  document.getElementById('kpi-vol').textContent = fmtN(totalVol);
  document.getElementById('kpi-diff').textContent = medDiff!=null ? medDiff.toFixed(2)+'%' : '-';
  document.getElementById('kpi-cv').textContent = medCV!=null ? medCV.toFixed(1)+'%' : '-';
  document.getElementById('kpi-comps').textContent = avgComps!=null ? avgComps.toFixed(1) : '-';
}}

// ── Section renderers ──
function renderSection() {{
  const s = state.section;
  if(s==='volumen') renderVolumen();
  else if(s==='diff') renderDiff();
  else if(s==='comps') renderComps();
  else if(s==='cv') renderCV();
  else if(s==='age') renderAge();
  else if(s==='step') renderStep();
  else if(s==='src') renderSrc();
  else if(s==='poly') renderPoly();
}}

function renderVolumen() {{
  const q = fd('qual', {{metro:true, mes:true, flag:true}});
  // Aggregate by mes + flag
  const byMesFlag = {{}};
  q.forEach(r => {{ const k=r.mes+'|'+r.flag; if(!byMesFlag[k]) byMesFlag[k]={{mes:r.mes,flag:r.flag,vol:0}}; byMesFlag[k].vol+=r.vol; }});
  const meses = [...new Set(q.map(r=>r.mes))].sort();
  const labels = meses.map(mesLabel);
  const v70 = meses.map(m => {{ const r=Object.values(byMesFlag).find(x=>x.mes===m&&x.flag===70); return r?r.vol:0; }});
  const v71 = meses.map(m => {{ const r=Object.values(byMesFlag).find(x=>x.mes===m&&x.flag===71); return r?r.vol:0; }});
  lineChart('ch-vol', labels, [mkDS('F70 Manual',v70,COLORS.blue), mkDS('F71 Automatico',v71,COLORS.orange)], 'int');
  // By metro
  const byMesMetro = {{}};
  q.forEach(r => {{ const k=r.mes+'|'+r.metro; if(!byMesMetro[k]) byMesMetro[k]={{mes:r.mes,metro:r.metro,vol:0}}; byMesMetro[k].vol+=r.vol; }});
  const metros = [...new Set(q.map(r=>r.metro))].filter(m=>m!=='Otras').sort();
  const metroDS = metros.map((mt,i) => {{
    const vals = meses.map(m => {{ const r=Object.values(byMesMetro).find(x=>x.mes===m&&x.metro===mt); return r?r.vol:0; }});
    return mkDS(mt, vals, METRO_C[mt]||PAL[i%PAL.length]);
  }});
  lineChart('ch-vol-metro', labels, metroDS, 'int');
  // Table
  const rows = meses.map((m,i) => [labels[i], fmtN(v70[i]), fmtN(v71[i]), fmtN(v70[i]+v71[i])]);
  renderTable('tbl-vol', ['Mes','Manual F70','Auto F71','Total'], rows);
}}

function renderDiff() {{
  const d = fd('diff', {{metro:true, mes:true}});
  const agg = aggByMes(d, rows => ({{
    med_ask_habi: wavg(rows,'med_ask_habi','vol'),
    med_precio_base: wavg(rows,'med_precio_base','vol'),
    med_pre_remo: wavg(rows,'med_pre_remo','vol'),
    vol: rows.reduce((s,r)=>s+(r.vol||0),0)
  }}));
  const labels = agg.map(r=>mesLabel(r.mes));
  lineChart('ch-diff', labels, [
    mkDS('Ask Habi (Post-REMO)',agg.map(r=>r.med_ask_habi),COLORS.blue),
    mkDS('Precio Base',agg.map(r=>r.med_precio_base),COLORS.purple),
    mkDS('Pre-REMO',agg.map(r=>r.med_pre_remo),COLORS.orange)
  ], 'pct');
  // By metro
  const metros = [...new Set(d.map(r=>r.metro))].filter(m=>m&&m!=='None'&&m!=='not found').sort();
  const meses = [...new Set(d.map(r=>r.mes))].sort();
  const mlabels = meses.map(mesLabel);
  const mDS = metros.map((mt,i) => {{
    const vals = meses.map(m => {{ const r=d.find(x=>x.mes===m&&x.metro===mt); return r?r.med_ask_habi:null; }});
    return mkDS(mt, vals, METRO_C[mt]||PAL[i%PAL.length]);
  }});
  lineChart('ch-diff-metro', mlabels, mDS, 'pct');
  // Table
  const rows = agg.map(r => [mesLabel(r.mes), fmtP(r.med_ask_habi), fmtP(r.med_precio_base), fmtP(r.med_pre_remo), fmtN(r.vol)]);
  renderTable('tbl-diff', ['Mes','Ask Habi %','Precio Base %','Pre-REMO %','Vol'], rows);
}}

function renderQualMetric(chId, chMetroId, tblId, col, title, yFmt, refLines) {{
  const q = fd('qual', {{metro:true, mes:true, flag:true}});
  const agg = aggByMes(q, rows => ({{val: wavg(rows,col,'vol'), vol: rows.reduce((s,r)=>s+(r.vol||0),0)}}));
  const labels = agg.map(r=>mesLabel(r.mes));
  lineChart(chId, labels, [mkDS(title, agg.map(r=>r.val), COLORS.blue)], yFmt, refLines);
  // By metro
  const meses = [...new Set(q.map(r=>r.mes))].sort();
  const mlabels = meses.map(mesLabel);
  const byMM = {{}};
  q.forEach(r => {{ const k=r.mes+'|'+r.metro; if(!byMM[k]) byMM[k]=[]; byMM[k].push(r); }});
  const metros = [...new Set(q.map(r=>r.metro))].filter(m=>m!=='Otras').sort();
  const mDS = metros.map((mt,i) => {{
    const vals = meses.map(m => {{ const rows=byMM[m+'|'+mt]; return rows ? wavg(rows,col,'vol') : null; }});
    return mkDS(mt, vals, METRO_C[mt]||PAL[i%PAL.length]);
  }});
  lineChart(chMetroId, mlabels, mDS, yFmt, refLines);
  // Table
  const rows = agg.map(r => [mesLabel(r.mes), yFmt==='pct'?fmtP(r.val):yFmt==='int'?fmtN(r.val):fmtD(r.val), fmtN(r.vol)]);
  renderTable(tblId, ['Mes',title,'Vol'], rows);
}}

function renderComps() {{ renderQualMetric('ch-comps','ch-comps-metro','tbl-comps','avg_comps','Prom. Comparables','dec'); }}
function renderCV() {{ renderQualMetric('ch-cv','ch-cv-metro','tbl-cv','med_cv','Mediana CV','pct',[{{value:10,label:'10% Umbral',color:'#D32F2F'}}]); }}
function renderAge() {{ renderQualMetric('ch-age','ch-age-metro','tbl-age','avg_age','Prom. Antiguedad (dias)','int',[{{value:180,label:'6 meses',color:'#EF6C00',dash:[8,4]}},{{value:365,label:'12 meses',color:'#D32F2F',dash:[8,4]}}]); }}

function renderStep() {{
  // Avg step line
  const q = fd('qual', {{metro:true, mes:true, flag:true}});
  const agg = aggByMes(q, rows => ({{val: wavg(rows,'avg_step','vol'), vol: rows.reduce((s,r)=>s+(r.vol||0),0)}}));
  const labels = agg.map(r=>mesLabel(r.mes));
  lineChart('ch-step', labels, [mkDS('Paso Promedio', agg.map(r=>r.val), COLORS.blue)], 'dec');
  // Stacked dist
  const s = fd('steps', {{mes:true}});
  const meses = [...new Set(s.map(r=>r.mes))].sort();
  const mlabels = meses.map(mesLabel);
  const buckets = [...new Set(s.map(r=>r.step_bucket))].sort();
  const totals = {{}};
  meses.forEach(m => {{ totals[m] = s.filter(r=>r.mes===m).reduce((a,r)=>a+r.n,0); }});
  const bDS = buckets.map((b,i) => {{
    const lb = b.includes('. ') ? b.split('. ')[1] : b;
    const vals = meses.map(m => {{ const r=s.find(x=>x.mes===m&&x.step_bucket===b); const n=r?r.n:0; return totals[m]>0 ? n/totals[m]*100 : 0; }});
    return mkBarDS(lb, vals, ['#1B5E20','#388E3C','#66BB6A','#A5D6A7','#9E9E9E'][i%5]);
  }});
  stackedBarChart('ch-step-dist', mlabels, bDS, 'pct');
  // Table
  const rows = agg.map(r => [mesLabel(r.mes), fmtD(r.val), fmtN(r.vol)]);
  renderTable('tbl-step', ['Mes','Paso Promedio','Vol'], rows);
}}

function renderSrc() {{
  const s = fd('src', {{mes:true, src:true}});
  const meses = [...new Set(s.map(r=>r.mes))].sort();
  const labels = meses.map(mesLabel);
  // Top 5 sources
  const srcTotals = {{}};
  s.forEach(r => {{ srcTotals[r.src] = (srcTotals[r.src]||0)+r.n; }});
  const sorted = Object.entries(srcTotals).sort((a,b)=>b[1]-a[1]);
  const top5 = sorted.slice(0,5).map(x=>x[0]);
  const hasMisc = sorted.length > 5;
  const cats = [...top5]; if(hasMisc) cats.push('Otras');
  // Qty datasets
  const qtyDS = cats.map((cat,i) => {{
    const vals = meses.map(m => {{
      if(cat==='Otras') return s.filter(r=>r.mes===m&&!top5.includes(r.src)).reduce((a,r)=>a+r.n,0);
      const r=s.find(x=>x.mes===m&&x.src===cat); return r?r.n:0;
    }});
    return mkBarDS(cat, vals, PAL[i%PAL.length]);
  }});
  stackedBarChart('ch-src-qty', labels, qtyDS, 'int');
  // Pct datasets
  const totals = {{}};
  meses.forEach(m => {{ totals[m] = s.filter(r=>r.mes===m).reduce((a,r)=>a+r.n,0); }});
  const pctDS = cats.map((cat,i) => {{
    const vals = meses.map(m => {{
      let n;
      if(cat==='Otras') n = s.filter(r=>r.mes===m&&!top5.includes(r.src)).reduce((a,r)=>a+r.n,0);
      else {{ const r=s.find(x=>x.mes===m&&x.src===cat); n=r?r.n:0; }}
      return totals[m]>0 ? n/totals[m]*100 : 0;
    }});
    return mkBarDS(cat, vals, PAL[i%PAL.length]);
  }});
  stackedBarChart('ch-src-pct', labels, pctDS, 'pct');
  // Table
  const rows = meses.map((m,mi) => {{
    const total = totals[m]||0;
    const detail = cats.map(c => {{
      let n;
      if(c==='Otras') n=s.filter(r=>r.mes===m&&!top5.includes(r.src)).reduce((a,r)=>a+r.n,0);
      else {{ const r=s.find(x=>x.mes===m&&x.src===c); n=r?r.n:0; }}
      return fmtN(n);
    }});
    return [labels[mi], ...detail, fmtN(total)];
  }});
  renderTable('tbl-src', ['Mes', ...cats, 'Total'], rows);
}}

function renderPoly() {{
  const p = DS.poly || [];
  const meses = META.meses;
  const labels = meses.map(mesLabel);
  const co = meses.map(m => {{ const r=p.find(x=>x.mes===m&&x.pais==='Colombia'); return r?r.pct_polynator:null; }});
  const mx = meses.map(m => {{ const r=p.find(x=>x.mes===m&&x.pais==='Mexico'); return r?r.pct_polynator:null; }});
  // Filter by mes if active
  let fMeses = meses;
  let fLabels = labels;
  let fCo = co;
  let fMx = mx;
  if(!state.mes.includes('__all__')) {{
    const idx = meses.map((m,i) => state.mes.includes(m)?i:-1).filter(i=>i>=0);
    fMeses = idx.map(i=>meses[i]); fLabels = idx.map(i=>labels[i]);
    fCo = idx.map(i=>co[i]); fMx = idx.map(i=>mx[i]);
  }}
  lineChart('ch-poly', fLabels, [mkDS('Colombia',fCo,COLORS.blue), mkDS('Mexico',fMx,COLORS.orange)], 'pct');
  // Table
  const rows = fMeses.map((m,i) => [fLabels[i], fCo[i]!=null?fCo[i].toFixed(1)+'%':'-', fMx[i]!=null?fMx[i].toFixed(1)+'%':'-']);
  renderTable('tbl-poly', ['Mes','Colombia %','Mexico %'], rows);
}}

// ── Init ──
initFilters();
renderKPIs();
setSection('volumen');
// Fix section tab active state on init
document.querySelector('.section-tab').classList.add('active');
</script>
</body>
</html>'''

out_path = os.path.join(OUT, 'dashboard_calidad_pricing.html')
with open(out_path, 'w', encoding='utf-8') as f:
    f.write(html)
print(f"\nDashboard: {out_path}")
print("LISTO!")
