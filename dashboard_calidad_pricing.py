#!/usr/bin/env python3
"""Dashboard interactivo de Calidad y Consistencia de Pricing - CO y MX
v3: Historico desde 2024, granularidad mes/trimestre/ano, filtros ciudad/zona/tipo
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

# ── Periodo: historico completo desde 2024 ──
HOY = date.today()
FECHA_INI = '2024-01-01'
FECHA_FIN_EXCL = (HOY + pd.Timedelta(days=1)).strftime('%Y-%m-%d')
_ultimo_mes = HOY
_meses = pd.date_range(start=FECHA_INI, end=_ultimo_mes, freq='MS')
MESES = [m.strftime('%Y-%m') for m in _meses]
MN = {'01':'Ene','02':'Feb','03':'Mar','04':'Abr','05':'May','06':'Jun',
      '07':'Jul','08':'Ago','09':'Sep','10':'Oct','11':'Nov','12':'Dic'}
ML = [f"{MN[m.split('-')[1]]} {m[2:4]}" for m in MESES]
PERIODO = f"Ene 2024 - {MN[MESES[-1].split('-')[1]]} {MESES[-1][:4]}"

print(f"Periodo: {FECHA_INI} a {FECHA_FIN_EXCL} ({len(MESES)} meses)")

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
SELECT FORMAT_DATE('%Y-%m', fecha) as mes, area_metropolitana as metro, ciudad,
  APPROX_QUANTILES(SAFE_DIVIDE(ask_price - ask_price_comite_post_remo, ask_price)*100, 100)[OFFSET(50)] as med_ask_habi,
  APPROX_QUANTILES(SAFE_DIVIDE(ask_price - precio_oferta_base_habi, ask_price)*100, 100)[OFFSET(50)] as med_precio_base,
  APPROX_QUANTILES(SAFE_DIVIDE(ask_price - ask_price_comite_pre_remo, ask_price)*100, 100)[OFFSET(50)] as med_pre_remo,
  COUNT(*) as vol
FROM `papyrus-delivery-data.idm_squad_3.funnel_sellers_co`
WHERE fecha >= '{FECHA_INI}' AND fecha < '{FECHA_FIN_EXCL}'
  AND ask_price > 0 AND ask_price_comite_post_remo > 0
  AND rechazos_general_auxiliar_v2 = 'Aprobado General'
GROUP BY 1, 2, 3 ORDER BY 1, 2"""

q_diff_mx = f"""
SELECT FORMAT_DATE('%Y-%m', fecha) as mes, area_metropolitana as metro, ciudad,
  APPROX_QUANTILES(SAFE_DIVIDE(ask_price - ask_price_comite_post_remo, ask_price)*100, 100)[OFFSET(50)] as med_ask_habi,
  APPROX_QUANTILES(SAFE_DIVIDE(ask_price - precio_oferta_base_habi, ask_price)*100, 100)[OFFSET(50)] as med_precio_base,
  APPROX_QUANTILES(SAFE_DIVIDE(ask_price - ask_price_comite_pre_remo, ask_price)*100, 100)[OFFSET(50)] as med_pre_remo,
  COUNT(*) as vol
FROM `papyrus-delivery-data.idm_squad_3.funnel_sellers_mx`
WHERE fecha >= '{FECHA_INI}' AND fecha < '{FECHA_FIN_EXCL}'
  AND ask_price > 0 AND ask_price_comite_post_remo > 0
  AND rechazos_general_auxiliar_v2 = 'Aprobado General'
GROUP BY 1, 2, 3 ORDER BY 1, 2"""

q_quality_co=f"""
WITH pd AS (
  SELECT FORMAT_DATE('%Y-%m',p.fecha) as mes, CAST(c.flag_portal_pricing AS INT64) as flag,
    {CO_METRO} as metro, ct.name as ciudad,
    zm.id as zona_mediana_id, zm.name as zona_mediana_name,
    i.tipo_inmueble,
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
SELECT mes, flag, metro, ciudad, zona_mediana_id, zona_mediana_name, tipo_inmueble, COUNT(*) as vol,
  AVG(num_comps) as avg_comps, APPROX_QUANTILES(cv_pct,100)[OFFSET(50)] as med_cv,
  AVG(comp_age) as avg_age, AVG(step_num) as avg_step
FROM pd GROUP BY 1,2,3,4,5,6,7 ORDER BY 1,2,3"""

q_quality_mx=f"""
WITH pd AS (
  SELECT FORMAT_DATE('%Y-%m',p.date_create) as mes, CAST(c.portal_pricing_flag AS INT64) as flag,
    {MX_METRO} as metro, ct.name as ciudad,
    mz.id as zona_mediana_id, mz.name as zona_mediana_name,
    pt.name as tipo_inmueble,
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
  JOIN `papyrus-data-mx.habi_wh.property_type` pt ON pr.property_type_id=pt.id
  JOIN `papyrus-data-mx.habi_wh_priority.property_location` loc ON pr.property_location_id=loc.id
  JOIN `papyrus-data-mx.habi_wh.median_zone` mz ON loc.median_zone_id=mz.id
  JOIN `papyrus-data-mx.habi_wh.big_zone` bz ON mz.big_zone_id=bz.id
  JOIN `papyrus-data-mx.habi_wh.city` ct ON loc.city_id=ct.id
  WHERE c.portal_pricing_flag IN (70,71) AND p.date_create>='{FECHA_INI}' AND p.date_create<'{FECHA_FIN_EXCL}' AND p.ask_price_habi>0
  QUALIFY ROW_NUMBER() OVER (PARTITION BY p.property_id, FORMAT_DATE('%Y-%m',p.date_create) ORDER BY p.date_create DESC) = 1)
SELECT mes, flag, metro, ciudad, zona_mediana_id, zona_mediana_name, tipo_inmueble, COUNT(*) as vol,
  AVG(num_comps) as avg_comps, APPROX_QUANTILES(cv_pct,100)[OFFSET(50)] as med_cv,
  AVG(comp_age) as avg_age, AVG(step_num) as avg_step
FROM pd GROUP BY 1,2,3,4,5,6,7 ORDER BY 1,2,3"""

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

# ── Ejecutar ──
print("="*50)
diff_co=rq(q_diff_co,"CO diff"); diff_mx=rq(q_diff_mx,"MX diff")
qual_co=rq(q_quality_co,"CO quality"); qual_mx=rq(q_quality_mx,"MX quality")
co_steps=rq(q_co_steps,"CO steps"); mx_steps=rq(q_mx_steps,"MX steps")
co_src=rq(q_co_src,"CO src"); mx_src=rq(q_mx_src,"MX src")
poly_all=rq(q_poly,"Polynator")

# ── JSON ──
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

def uniq(series): return sorted([str(v) for v in series.dropna().unique() if v and str(v)!='nan' and str(v)!='None'])

metros_co=uniq(qual_co['metro']); metros_mx=uniq(qual_mx['metro'])
ciudades_co=uniq(qual_co['ciudad']); ciudades_mx=uniq(qual_mx['ciudad'])
tipos_co=uniq(qual_co['tipo_inmueble']); tipos_mx=uniq(qual_mx['tipo_inmueble'])
sources_co=uniq(co_src['src']); sources_mx=uniq(mx_src['src'])

# Zonas: id - name
def zona_list(df):
    z = df[['zona_mediana_id','zona_mediana_name']].drop_duplicates().dropna()
    return sorted([f"{int(r.zona_mediana_id)} - {r.zona_mediana_name}" for _,r in z.iterrows()])
zonas_co=zona_list(qual_co); zonas_mx=zona_list(qual_mx)

meta = {
    'meses': MESES, 'meses_labels': ML, 'periodo': PERIODO,
    'metros_co':metros_co, 'metros_mx':metros_mx,
    'ciudades_co':ciudades_co, 'ciudades_mx':ciudades_mx,
    'zonas_co':zonas_co, 'zonas_mx':zonas_mx,
    'tipos_co':tipos_co, 'tipos_mx':tipos_mx,
    'sources_co':sources_co, 'sources_mx':sources_mx,
    'fecha_gen': HOY.strftime('%d/%m/%Y'),
}

datasets_json = json.dumps(datasets, ensure_ascii=False)
meta_json = json.dumps(meta, ensure_ascii=False)

print(f"\nJSON: {len(datasets_json)//1024}KB")
print("Generando HTML...")

html = f'''<!DOCTYPE html>
<html lang="es"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Dashboard Calidad Pricing - {PERIODO}</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4"></script>
<style>
*{{margin:0;padding:0;box-sizing:border-box}}
body{{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;background:#f0f2f5;color:#333;font-size:14px}}
.header{{background:linear-gradient(135deg,#0d3b4f,#3BA5B5);color:#fff;padding:20px 28px;display:flex;justify-content:space-between;align-items:center;box-shadow:0 2px 8px rgba(0,0,0,.15)}}
.header h1{{font-size:22px;font-weight:700}} .header .sub{{font-size:13px;opacity:.85}}
.container{{max-width:1500px;margin:0 auto;padding:20px}}
.country-tabs{{display:flex;gap:6px;margin-bottom:14px}}
.country-tab{{padding:10px 28px;border:2px solid transparent;background:#e0e0e0;cursor:pointer;font-size:14px;font-weight:700;border-radius:10px 10px 0 0;transition:.2s;color:#555}}
.country-tab.active{{background:#3BA5B5;color:#fff;border-color:#2d8a97}}
.country-tab:hover:not(.active){{background:#ccc}}
.filter-bar{{background:#fff;border-radius:12px;padding:14px 18px;margin-bottom:18px;display:flex;flex-wrap:wrap;gap:8px;align-items:center;box-shadow:0 2px 6px rgba(0,0,0,.06);border:1px solid #e8e8e8}}
.filter-label{{font-size:11px;font-weight:700;color:#888;text-transform:uppercase;letter-spacing:.5px}}
.filter-sep{{width:1px;height:28px;background:#e0e0e0;margin:0 4px}}
.dd-wrap{{position:relative;display:inline-block}}
.dd-trigger,.sel-ctrl{{padding:7px 32px 7px 12px;border:1.5px solid #d0d0d0;border-radius:8px;background:#fff;cursor:pointer;font-size:12px;min-width:130px;text-align:left;position:relative;transition:.2s;font-weight:500;appearance:none;-webkit-appearance:none}}
.dd-trigger::after,.sel-ctrl::after{{content:'\\25BC';position:absolute;right:10px;top:50%;transform:translateY(-50%);font-size:8px;color:#888}}
select.sel-ctrl{{padding-right:28px;background-image:url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='10' height='6'%3E%3Cpath d='M0 0l5 6 5-6z' fill='%23888'/%3E%3C/svg%3E");background-repeat:no-repeat;background-position:right 10px center}}
select.sel-ctrl::after{{display:none}}
.dd-trigger:hover,.sel-ctrl:hover{{border-color:#3BA5B5}}
.dd-trigger.active{{border-color:#3BA5B5;background:#e8f7f9;color:#0d3b4f}}
.dd-panel{{display:none;position:absolute;top:calc(100% + 4px);left:0;background:#fff;border:1px solid #ddd;border-radius:10px;box-shadow:0 8px 24px rgba(0,0,0,.12);z-index:100;min-width:220px;max-height:300px;overflow-y:auto;padding:8px 0}}
.dd-panel.open{{display:block}}
.dd-panel label{{display:flex;align-items:center;padding:5px 14px;cursor:pointer;font-size:12px;gap:8px;transition:.1s}}
.dd-panel label:hover{{background:#f0f7f8}}
.dd-panel input[type="checkbox"]{{accent-color:#3BA5B5;width:14px;height:14px}}
.dd-panel hr{{border:none;border-top:1px solid #eee;margin:4px 0}}
.btn{{padding:7px 18px;border:none;border-radius:8px;cursor:pointer;font-size:12px;font-weight:700;transition:.2s}}
.btn-primary{{background:#3BA5B5;color:#fff}} .btn-primary:hover{{background:#2d8a97}}
.btn-secondary{{background:#eee;color:#555}} .btn-secondary:hover{{background:#ddd}}
.btn-export{{background:transparent;color:#3BA5B5;border:1.5px solid #3BA5B5;padding:5px 12px;font-size:11px}} .btn-export:hover{{background:#e8f7f9}}
.kpi-row{{display:flex;gap:14px;margin-bottom:18px;flex-wrap:wrap}}
.kpi-card{{flex:1;min-width:200px;background:#fff;border-radius:12px;padding:18px 20px;text-align:center;box-shadow:0 2px 6px rgba(0,0,0,.06);border:1px solid #e8e8e8;position:relative}}
.kpi-card .value{{font-size:28px;font-weight:800;color:#0d3b4f}} .kpi-card .label{{font-size:11px;color:#888;margin-top:4px;font-weight:500}}
.kpi-card .delta{{font-size:11px;font-weight:700;margin-top:4px}}
.kpi-card .delta.up{{color:#2E7D32}} .kpi-card .delta.down{{color:#D32F2F}} .kpi-card .delta.flat{{color:#888}}
.kpi-card .info{{position:absolute;top:8px;right:10px;font-size:11px;color:#bbb;cursor:help}}
.section-tabs{{display:flex;gap:4px;margin-bottom:18px;flex-wrap:wrap}}
.section-tab{{padding:9px 18px;border:none;background:#e4e4e4;cursor:pointer;font-size:12px;font-weight:600;border-radius:8px;transition:.2s;color:#666}}
.section-tab.active{{background:#0d3b4f;color:#fff}}
.section-tab:hover:not(.active){{background:#d0d0d0}}
.section-content{{display:none}} .section-content.active{{display:block}}
.chart-box{{background:#fff;border-radius:12px;padding:20px 24px;margin-bottom:18px;box-shadow:0 2px 6px rgba(0,0,0,.06);border:1px solid #e8e8e8}}
.chart-box .chart-header{{display:flex;justify-content:space-between;align-items:center;margin-bottom:14px}}
.chart-box h3{{font-size:15px;font-weight:700;color:#0d3b4f;margin:0}}
.chart-row{{display:flex;gap:18px;flex-wrap:wrap}} .chart-half{{flex:1;min-width:420px}}
canvas{{max-height:400px}}
.compare-row{{display:flex;gap:18px;flex-wrap:wrap}} .compare-col{{flex:1;min-width:400px}}
.compare-label{{text-align:center;font-size:13px;font-weight:700;color:#3BA5B5;margin-bottom:8px;padding:6px;background:#e8f7f9;border-radius:6px}}
.tbl-wrap{{background:#fff;border-radius:12px;padding:16px 20px;margin-bottom:18px;box-shadow:0 2px 6px rgba(0,0,0,.06);border:1px solid #e8e8e8}}
.tbl-wrap .tbl-header{{display:flex;justify-content:space-between;align-items:center;margin-bottom:10px}}
.tbl-wrap .tbl-header h4{{font-size:13px;font-weight:700;color:#555}}
table{{width:100%;border-collapse:collapse;font-size:12px}}
th{{background:#f5f7f9;padding:8px;text-align:left;font-weight:700;color:#555;border-bottom:2px solid #e0e0e0;position:sticky;top:0}}
td{{padding:7px 8px;border-bottom:1px solid #f0f0f0}}
tr:hover td{{background:#f5f8fa}} td:first-child{{font-weight:600;color:#0d3b4f}}
/* Date picker */
.dp-popup{{display:none;position:absolute;top:calc(100% + 6px);left:0;background:#fff;border:1px solid #ddd;border-radius:12px;box-shadow:0 8px 30px rgba(0,0,0,.18);z-index:200;padding:20px;min-width:540px}}
.dp-popup.open{{display:block}}
.dp-row{{display:flex;gap:24px}}
.dp-cal{{flex:1;min-width:240px}}
.dp-title{{font-size:12px;font-weight:700;color:#555;text-align:center;margin-bottom:8px}}
.dp-nav{{display:flex;align-items:center;gap:4px;margin-bottom:8px;justify-content:center}}
.dp-nav button{{border:none;background:none;cursor:pointer;font-size:14px;color:#555;padding:4px 8px;border-radius:4px}}
.dp-nav button:hover{{background:#e8e8e8}}
.dp-sel{{border:1px solid #ddd;border-radius:6px;padding:4px 8px;font-size:12px;font-weight:600;cursor:pointer;background:#fff;color:#0d3b4f}}
.dp-grid{{display:grid;grid-template-columns:repeat(7,1fr);gap:2px;text-align:center}}
.dp-grid .dp-hdr{{font-size:10px;font-weight:700;color:#888;padding:4px 0}}
.dp-grid .dp-day{{font-size:12px;padding:6px 2px;border-radius:6px;cursor:pointer;transition:.15s;color:#333}}
.dp-grid .dp-day:hover{{background:#e8f7f9}}
.dp-grid .dp-day.sel{{background:#3BA5B5;color:#fff;font-weight:700}}
.dp-grid .dp-day.range{{background:#e0f2f4;color:#0d3b4f}}
.dp-grid .dp-day.other{{color:#ccc}}
.dp-grid .dp-day.today{{border:2px solid #3BA5B5}}
.dp-actions{{display:flex;justify-content:flex-end;gap:10px;margin-top:16px;padding-top:12px;border-top:1px solid #eee}}
.metric-desc{{background:#f5f8fa;border-left:3px solid #3BA5B5;padding:12px 16px;border-radius:0 8px 8px 0;margin-bottom:16px;font-size:12px;color:#555;line-height:1.6}}
.metric-desc code{{background:#e8e8e8;padding:1px 5px;border-radius:3px;font-size:11px}}
</style></head><body>
<div class="header">
  <div><h1>Dashboard de Calidad y Consistencia de Pricing</h1><div class="sub">{PERIODO} | Colombia y Mexico</div></div>
  <div class="sub">Generado: {meta['fecha_gen']}</div>
</div>
<div class="container">
  <div class="country-tabs">
    <button class="country-tab active" onclick="setCountry('co',this)">Colombia</button>
    <button class="country-tab" onclick="setCountry('mx',this)">Mexico</button>
    <button class="country-tab" onclick="setCountry('compare',this)" style="margin-left:auto;background:#f5f5f5;border:1.5px solid #3BA5B5;color:#3BA5B5">Comparar CO vs MX</button>
  </div>
  <div class="filter-bar">
    <span class="filter-label">Periodo:</span>
    <select id="sel-gran" class="sel-ctrl" style="min-width:110px" onchange="applyFilters()"><option value="mes" selected>Mes</option><option value="trimestre">Trimestre</option><option value="ano">Ano</option></select>
    <div class="dd-wrap" id="dd-daterange">
      <button class="dd-trigger" id="btn-daterange" onclick="toggleDatePicker()" style="min-width:220px"></button>
      <div class="dp-popup" id="dp-popup">
        <div class="dp-row">
          <div class="dp-cal">
            <div class="dp-title">Fecha de inicio</div>
            <div class="dp-nav"><select id="dp-m0" class="dp-sel" onchange="dpRender(0)"></select><button onclick="dpMove(0,-1)">&#9664;</button><button onclick="dpMove(0,1)">&#9654;</button></div>
            <div class="dp-grid" id="dp-grid0"></div>
          </div>
          <div class="dp-cal">
            <div class="dp-title">Fecha de finalizacion</div>
            <div class="dp-nav"><select id="dp-m1" class="dp-sel" onchange="dpRender(1)"></select><button onclick="dpMove(1,-1)">&#9664;</button><button onclick="dpMove(1,1)">&#9654;</button></div>
            <div class="dp-grid" id="dp-grid1"></div>
          </div>
        </div>
        <div class="dp-actions"><button class="btn btn-secondary" onclick="dpCancel()">Cancelar</button><button class="btn btn-primary" onclick="dpApply()">Aplicar</button></div>
      </div>
    </div>
    <div class="filter-sep"></div>
    <div class="dd-wrap" id="dd-metro"><button class="dd-trigger" onclick="toggleDD('metro')">Metro: Todas</button><div class="dd-panel" id="panel-metro"></div></div>
    <div class="dd-wrap" id="dd-ciudad"><button class="dd-trigger" onclick="toggleDD('ciudad')">Ciudad: Todas</button><div class="dd-panel" id="panel-ciudad"></div></div>
    <div class="dd-wrap" id="dd-zona"><button class="dd-trigger" onclick="toggleDD('zona')">Zona Med: Todas</button><div class="dd-panel" id="panel-zona"></div></div>
    <div class="dd-wrap" id="dd-tipo"><button class="dd-trigger" onclick="toggleDD('tipo')">Tipo: Todos</button><div class="dd-panel" id="panel-tipo"></div></div>
    <div class="dd-wrap" id="dd-comite"><button class="dd-trigger" onclick="toggleDD('comite')">Comite: Todos</button><div class="dd-panel" id="panel-comite"></div></div>
    <div class="dd-wrap" id="dd-source"><button class="dd-trigger" onclick="toggleDD('source')">Fuente: Todas</button><div class="dd-panel" id="panel-source"></div></div>
    <button class="btn btn-primary" onclick="applyFilters()">Aplicar</button>
    <button class="btn btn-secondary" onclick="clearFilters()">Limpiar</button>
  </div>
  <div class="kpi-row">
    <div class="kpi-card"><div class="info" title="Pricings unicos por inmueble/mes (F70+F71)">&#9432;</div><div class="value" id="kpi-vol">-</div><div class="label">Total Pricings</div><div class="delta flat" id="kpi-vol-d"></div></div>
    <div class="kpi-card"><div class="info" title="Mediana (ask_price - ask_price_comite_post_remo) / ask_price">&#9432;</div><div class="value" id="kpi-diff">-</div><div class="label">Med. Dif. Ask Habi</div><div class="delta flat" id="kpi-diff-d"></div></div>
    <div class="kpi-card"><div class="info" title="Mediana CV. Menor a 10% = bueno">&#9432;</div><div class="value" id="kpi-cv">-</div><div class="label">Mediana CV</div><div class="delta flat" id="kpi-cv-d"></div></div>
    <div class="kpi-card"><div class="info" title="Promedio comparables por pricing">&#9432;</div><div class="value" id="kpi-comps">-</div><div class="label">Prom. Comparables</div><div class="delta flat" id="kpi-comps-d"></div></div>
  </div>
  <div class="section-tabs">
    <button class="section-tab active" onclick="setSection('volumen',this)">1. Volumen</button>
    <button class="section-tab" onclick="setSection('diff',this)">2. Dif. Precio</button>
    <button class="section-tab" onclick="setSection('comps',this)">3. Comparables</button>
    <button class="section-tab" onclick="setSection('cv',this)">4. CV</button>
    <button class="section-tab" onclick="setSection('age',this)">5. Antiguedad</button>
    <button class="section-tab" onclick="setSection('step',this)">6. Escalera</button>
    <button class="section-tab" onclick="setSection('src',this)">7. Fuentes</button>
    <button class="section-tab" onclick="setSection('poly',this)">8. Polynator</button>
  </div>
  <div class="section-content active" id="sec-volumen"></div>
  <div class="section-content" id="sec-diff"></div>
  <div class="section-content" id="sec-comps"></div>
  <div class="section-content" id="sec-cv"></div>
  <div class="section-content" id="sec-age"></div>
  <div class="section-content" id="sec-step"></div>
  <div class="section-content" id="sec-src"></div>
  <div class="section-content" id="sec-poly"></div>
</div>
<script>
const DS={datasets_json};
const META={meta_json};
const C={{blue:'#1976D2',purple:'#7B1FA2',orange:'#EF6C00',green:'#2E7D32',red:'#D32F2F',gray:'#757575',teal:'#3BA5B5',dark:'#0d3b4f'}};
const PAL=['#1976D2','#EF6C00','#2E7D32','#7B1FA2','#D32F2F','#757575','#00897B','#C2185B','#FF8F00','#303F9F'];
const MC={{'Bogota':PAL[0],'Medellin':PAL[1],'Barranquilla':PAL[3],'Cali':PAL[2],'Otras':PAL[5],'CDMX':PAL[0],'Guadalajara':PAL[1],'Monterrey':PAL[3],'Queretaro':PAL[2]}};

const MDESC={{
  volumen:'Cantidad de pricings unicos (deduplicados por inmueble/mes). F70 = comite manual, F71 = comite automatico.<br><b>Fuente:</b> <code>tabla_historico_pricing_comparable_v2</code> (CO) / <code>habi_pricing_history_pricing_comparable</code> (MX)',
  diff:'Mediana de la diferencia porcentual entre el precio del cliente (ask_price) y el precio de Habi. Post-REMO = precio final con ajuste de remodelacion. Pre-REMO = sin ajuste. Base = precio oferta base.<br><b>Fuente:</b> <code>funnel_sellers_co</code> / <code>funnel_sellers_mx</code>',
  comps:'Promedio de comparables usados en cada pricing. Mas comparables = mayor confianza en la estimacion.<br><b>Fuente:</b> <code>tabla_historico_pricing_comparable_v2</code> (CO) / <code>habi_pricing_history_pricing_comparable</code> (MX)',
  cv:'Mediana del coeficiente de variacion (dispersion) de los comparables. Por debajo de 10% = buena homogeneidad.<br><b>Fuente:</b> <code>tabla_historico_pricing_comparable_v2</code> (CO) / <code>habi_pricing_history_pricing_comparable</code> (MX)',
  age:'Promedio en dias de la antiguedad de los comparables. Comparables mas recientes reflejan mejor el mercado actual.<br><b>Fuente:</b> <code>tabla_historico_pricing_comparable_v2</code> (CO) / <code>habi_pricing_history_pricing_comparable</code> (MX)',
  step:'Paso promedio de la escalera de pricing. Pasos bajos = filtros estrictos (comparables cercanos). Pasos altos = filtros relajados.<br><b>Fuente:</b> <code>tabla_historico_pricing_comparable_v2</code> (CO) / <code>habi_pricing_history_pricing_comparable</code> (MX)',
  src:'Composicion de fuentes de los comparables usados (WEB, CRM, etc.).<br><b>Fuente:</b> campo JSON <code>comparable</code> en <code>tabla_historico_pricing_comparable_v2</code> (CO) / <code>habi_pricing_history_pricing_comparable</code> (MX)',
  poly:'Porcentaje de pricings que usan poligonos de Polynator para delimitar la zona de busqueda.<br><b>Fuente:</b> <code>tabla_automatizacion_polinator</code>'
}};

let state={{country:'co',section:'volumen',metro:['__all__'],ciudad:['__all__'],zona:['__all__'],tipo:['__all__'],comite:['__all__'],source:['__all__'],gran:'mes',desde:META.meses[0],hasta:META.meses[META.meses.length-1]}};
let charts={{}};

// ── Dropdown ──
function toggleDD(n){{ document.querySelectorAll('.dd-panel').forEach(p=>{{if(p.id!=='panel-'+n)p.classList.remove('open');}}); document.getElementById('panel-'+n).classList.toggle('open'); }}
document.addEventListener('click',e=>{{if(!e.target.closest('.dd-wrap'))document.querySelectorAll('.dd-panel').forEach(p=>p.classList.remove('open'));}});
function buildDD(n,vals,al){{ const p=document.getElementById('panel-'+n); let h=`<label><input type="checkbox" value="__all__" checked onchange="handleCB('${{n}}')"> ${{al}}</label><hr>`; vals.forEach(v=>{{h+=`<label><input type="checkbox" value="${{v}}" onchange="handleCB('${{n}}')"> ${{v}}</label>`}}); p.innerHTML=h; }}
function handleCB(n){{ const p=document.getElementById('panel-'+n),ac=p.querySelector('input[value="__all__"]'),its=[...p.querySelectorAll('input:not([value="__all__"])')]; if(event.target.value==='__all__'){{its.forEach(c=>c.checked=false);ac.checked=true;}}else{{ac.checked=false;if(!its.some(c=>c.checked))ac.checked=true;}} updTrig(n); }}
function updTrig(n){{ const p=document.getElementById('panel-'+n),ac=p.querySelector('input[value="__all__"]'),its=[...p.querySelectorAll('input:not([value="__all__"])')],tr=p.parentElement.querySelector('.dd-trigger'); const lb={{'metro':'Metro','ciudad':'Ciudad','zona':'Zona Med','tipo':'Tipo','comite':'Comite','source':'Fuente'}}; if(ac.checked){{tr.textContent=lb[n]+': Todas';tr.classList.remove('active');}}else{{const s=its.filter(c=>c.checked).map(c=>c.value);tr.textContent=s.length<=2?lb[n]+': '+s.join(', '):lb[n]+': '+s.length+' sel.';tr.classList.add('active');}} }}
function readF(n){{ const p=document.getElementById('panel-'+n),ac=p.querySelector('input[value="__all__"]'); if(ac&&ac.checked)return['__all__']; return[...p.querySelectorAll('input:checked:not([value="__all__"])')].map(c=>c.value); }}

function initFilters(){{
  const co=state.country==='co'||state.country==='compare';
  buildDD('metro',co?META.metros_co:META.metros_mx,'Todas');
  buildDD('ciudad',co?META.ciudades_co:META.ciudades_mx,'Todas');
  buildDD('zona',co?META.zonas_co:META.zonas_mx,'Todas');
  buildDD('tipo',co?META.tipos_co:META.tipos_mx,'Todos');
  buildDD('comite',['70','71'],'Todos');
  buildDD('source',co?META.sources_co:META.sources_mx,'Todas');
  const cp=document.getElementById('panel-comite'); cp.querySelectorAll('label').forEach(l=>{{const cb=l.querySelector('input');if(cb.value==='70')l.childNodes[1].textContent=' F70 Manual';if(cb.value==='71')l.childNodes[1].textContent=' F71 Automatico';}});
}}

// ── State ──
function setCountry(c,el){{ state.country=c; document.querySelectorAll('.country-tab').forEach(t=>t.classList.remove('active'));el.classList.add('active'); clearFilters(); }}
function setSection(s,el){{
  state.section=s; document.querySelectorAll('.section-tab').forEach(t=>t.classList.remove('active'));el.classList.add('active');
  document.querySelectorAll('.section-content').forEach(d=>d.classList.remove('active'));document.getElementById('sec-'+s).classList.add('active');
  const cmp=state.country==='compare', geo=['volumen','diff','comps','cv','age'], qual=['volumen','comps','cv','age'];
  document.getElementById('dd-metro').style.display=geo.includes(s)&&!cmp?'':'none';
  document.getElementById('dd-ciudad').style.display=geo.includes(s)&&!cmp?'':'none';
  document.getElementById('dd-zona').style.display=qual.includes(s)&&!cmp?'':'none';
  document.getElementById('dd-tipo').style.display=qual.includes(s)&&!cmp?'':'none';
  document.getElementById('dd-comite').style.display=qual.includes(s)?'':'none';
  document.getElementById('dd-source').style.display=s==='src'?'':'none';
  renderSection();
}}
function applyFilters(){{ state.metro=readF('metro');state.ciudad=readF('ciudad');state.zona=readF('zona');state.tipo=readF('tipo');state.comite=readF('comite');state.source=readF('source'); state.gran=document.getElementById('sel-gran').value; renderKPIs();renderSection(); }}
function clearFilters(){{ state.metro=['__all__'];state.ciudad=['__all__'];state.zona=['__all__'];state.tipo=['__all__'];state.comite=['__all__'];state.source=['__all__'];state.gran='mes';state.desde=META.meses[0];state.hasta=META.meses[META.meses.length-1]; initFilters();document.getElementById('sel-gran').value='mes';dpUpdateBtn(); renderKPIs();renderSection(); }}

// ── Data ──
function fd(key,opts,ctry){{
  const c=ctry||state.country; let d=DS[key+'_'+c]||DS[key]||[];
  d=d.filter(r=>r.mes>=state.desde&&r.mes<=state.hasta);
  if(opts.metro&&!state.metro.includes('__all__'))d=d.filter(r=>state.metro.includes(r.metro));
  if(opts.ciudad&&!state.ciudad.includes('__all__'))d=d.filter(r=>state.ciudad.includes(r.ciudad));
  if(opts.zona&&!state.zona.includes('__all__'))d=d.filter(r=>state.zona.some(z=>String(r.zona_mediana_id)===z.split(' - ')[0]));
  if(opts.tipo&&!state.tipo.includes('__all__'))d=d.filter(r=>state.tipo.includes(r.tipo_inmueble));
  if(opts.flag&&!state.comite.includes('__all__'))d=d.filter(r=>state.comite.includes(String(r.flag)));
  if(opts.src&&!state.source.includes('__all__'))d=d.filter(r=>state.source.includes(r.src));
  return d;
}}
function wavg(rows,col,wCol){{let s=0,w=0;rows.forEach(r=>{{if(r[col]!=null&&r[wCol]!=null){{s+=r[col]*r[wCol];w+=r[wCol];}}}});return w>0?s/w:null;}}
function granKey(mes){{const g=state.gran;if(g==='mes')return mes;if(g==='trimestre'){{const[y,m]=mes.split('-');return y+'-Q'+Math.ceil(parseInt(m)/3);}}return mes.split('-')[0];}}
function granLabel(mes){{const g=state.gran,i=META.meses.indexOf(mes);if(g==='mes')return i>=0?META.meses_labels[i]:mes;if(g==='trimestre'){{const[y,m]=mes.split('-');return'Q'+Math.ceil(parseInt(m)/3)+' '+y.slice(2);}}return mes.split('-')[0];}}
function aggByGran(data,fn){{const g={{}};data.forEach(r=>{{const k=granKey(r.mes);if(!g[k])g[k]={{key:k,label:granLabel(r.mes),rows:[]}};g[k].rows.push(r);}});return Object.keys(g).sort().map(k=>({{gk:k,label:g[k].label,...fn(g[k].rows)}}));}}
function fmtN(v){{return v!=null?v.toLocaleString('es-CO',{{maximumFractionDigits:0}}):'-';}}
function fmtP(v){{return v!=null?v.toFixed(2)+'%':'-';}}
function fmtD(v){{return v!=null?v.toFixed(1):'-';}}

// ── Charts ──
function destroyChart(id){{if(charts[id]){{charts[id].destroy();delete charts[id];}}}}
function lineChart(cid,labels,datasets,yFmt,refLines){{
  destroyChart(cid);const ctx=document.getElementById(cid);if(!ctx)return;
  const plugins=[{{id:'dl',afterDatasetsDraw(chart){{const{{ctx:c}}=chart;chart.data.datasets.forEach((ds,di)=>{{const m=chart.getDatasetMeta(di);if(!m.hidden)m.data.forEach((pt,i)=>{{const v=ds.data[i];if(v==null)return;c.save();c.font='bold 10px sans-serif';c.fillStyle=ds.borderColor;c.textAlign='center';c.fillText(yFmt==='pct'?v.toFixed(1)+'%':yFmt==='int'?Math.round(v).toLocaleString():v.toFixed(1),pt.x,pt.y+(di%2===0?-12:14));c.restore();}});}});}}}}];
  if(refLines&&refLines.length)plugins.push({{id:'rl',afterDraw(chart){{const{{ctx:c,chartArea:{{left,right}},scales:{{y}}}}=chart;refLines.forEach(rl=>{{const yy=y.getPixelForValue(rl.value);c.save();c.beginPath();c.moveTo(left,yy);c.lineTo(right,yy);c.strokeStyle=rl.color;c.lineWidth=1.5;c.setLineDash(rl.dash||[6,4]);c.stroke();c.fillStyle=rl.color;c.font='bold 11px sans-serif';c.fillText(rl.label,right-70,yy-6);c.restore();}});}}}});
  charts[cid]=new Chart(ctx,{{type:'line',data:{{labels,datasets}},options:{{responsive:true,maintainAspectRatio:false,interaction:{{mode:'index',intersect:false}},plugins:{{legend:{{position:'bottom',labels:{{usePointStyle:true,font:{{size:11,weight:'bold'}},padding:16}}}},tooltip:{{backgroundColor:'rgba(0,0,0,.85)',padding:12,cornerRadius:8,callbacks:{{label(c){{const v=c.raw;if(v==null)return'';return' '+c.dataset.label+': '+(yFmt==='pct'?v.toFixed(2)+'%':yFmt==='int'?Math.round(v).toLocaleString():v.toFixed(1));}}}}}},datalabels:{{display:false}}}},scales:{{y:{{ticks:{{callback(v){{return yFmt==='pct'?v.toFixed(0)+'%':yFmt==='int'?v.toLocaleString():v.toFixed(1);}},font:{{size:11}}}}}},x:{{grid:{{display:false}},ticks:{{font:{{size:11,weight:'bold'}}}}}}}}}},plugins}});
  ctx.parentElement.style.height='380px';
}}
function stackedBarChart(cid,labels,datasets,yFmt){{
  destroyChart(cid);const ctx=document.getElementById(cid);if(!ctx)return;
  charts[cid]=new Chart(ctx,{{type:'bar',data:{{labels,datasets}},options:{{responsive:true,maintainAspectRatio:false,interaction:{{mode:'index',intersect:false}},plugins:{{legend:{{position:'bottom',labels:{{usePointStyle:true,font:{{size:11,weight:'bold'}},padding:16}}}},tooltip:{{backgroundColor:'rgba(0,0,0,.85)',padding:12,cornerRadius:8,callbacks:{{label(c){{return' '+c.dataset.label+': '+(yFmt==='pct'?c.raw.toFixed(0)+'%':Math.round(c.raw).toLocaleString());}},afterBody(items){{const t=items.reduce((s,i)=>s+(i.raw||0),0);return'  Total: '+(yFmt==='pct'?t.toFixed(0)+'%':Math.round(t).toLocaleString());}}}}}},datalabels:{{display:false}}}},scales:{{x:{{stacked:true,grid:{{display:false}},ticks:{{font:{{size:11,weight:'bold'}}}}}},y:{{stacked:true,ticks:{{callback(v){{return yFmt==='pct'?v+'%':v.toLocaleString();}},font:{{size:11}}}}}}}}}}}});
  ctx.parentElement.style.height='380px';
}}
function mkDS(l,d,c,o){{return{{label:l,data:d,borderColor:c,backgroundColor:c+(o?.fill?'33':''),pointBackgroundColor:c,borderWidth:2.5,pointRadius:5,pointHoverRadius:7,tension:.15,fill:!!o?.fill,...o}};}}
function mkBarDS(l,d,c){{return{{label:l,data:d,backgroundColor:c+'CC',borderColor:'#fff',borderWidth:1,borderRadius:2}};}}

// ── Table + CSV ──
function renderTable(cid,headers,rows,sn){{
  if(!sn)sn=cid.replace('tbl-','');
  let h=`<div class="tbl-wrap"><div class="tbl-header"><h4>Datos</h4><button class="btn btn-export" onclick="exportCSV('${{sn}}')">Exportar CSV</button></div><table id="tbl-data-${{sn}}"><thead><tr>`;
  headers.forEach(hd=>h+='<th>'+hd+'</th>');h+='</tr></thead><tbody>';
  rows.forEach(r=>{{h+='<tr>';r.forEach(v=>h+='<td>'+v+'</td>');h+='</tr>';}});h+='</tbody></table></div>';
  document.getElementById(cid).innerHTML=h;
}}
function exportCSV(n){{ const t=document.getElementById('tbl-data-'+n);if(!t)return; let csv='\\uFEFF'; t.querySelectorAll('tr').forEach(tr=>{{csv+=[...tr.querySelectorAll('th,td')].map(c=>'"'+c.textContent.replace(/"/g,'""')+'"').join(',')+('\\n');}});const b=new Blob([csv],{{type:'text/csv;charset=utf-8'}});const a=document.createElement('a');a.href=URL.createObjectURL(b);a.download=`calidad_pricing_${{n}}_${{state.country}}.csv`;a.click();}}

// ── KPIs ──
function renderKPIs(){{
  const q=fd('qual',{{metro:true,ciudad:true,zona:true,tipo:true,flag:true}});
  const d=fd('diff',{{metro:true,ciudad:true}});
  document.getElementById('kpi-vol').textContent=fmtN(q.reduce((s,r)=>s+(r.vol||0),0));
  const md=wavg(d,'med_ask_habi','vol'); document.getElementById('kpi-diff').textContent=md!=null?md.toFixed(2)+'%':'-';
  const mc=wavg(q,'med_cv','vol'); document.getElementById('kpi-cv').textContent=mc!=null?mc.toFixed(1)+'%':'-';
  const ac=wavg(q,'avg_comps','vol'); document.getElementById('kpi-comps').textContent=ac!=null?ac.toFixed(1):'-';
  // Deltas
  const meses=[...new Set(q.map(r=>r.mes))].sort();
  if(meses.length>=2){{const l=meses[meses.length-1],p=meses[meses.length-2]; const qL=q.filter(r=>r.mes===l),qP=q.filter(r=>r.mes===p),dL=d.filter(r=>r.mes===l),dP=d.filter(r=>r.mes===p);
    setD('kpi-vol-d',qL.reduce((s,r)=>s+r.vol,0),qP.reduce((s,r)=>s+r.vol,0));
    setD('kpi-diff-d',wavg(dL,'med_ask_habi','vol'),wavg(dP,'med_ask_habi','vol'));
    setD('kpi-cv-d',wavg(qL,'med_cv','vol'),wavg(qP,'med_cv','vol'),true);
    setD('kpi-comps-d',wavg(qL,'avg_comps','vol'),wavg(qP,'avg_comps','vol'));
  }}
}}
function setD(id,cur,prev,inv){{const el=document.getElementById(id);if(!el||cur==null||prev==null||prev===0){{if(el)el.textContent='';return;}};const p=((cur-prev)/Math.abs(prev))*100;const a=p>0.5?'&#9650;':p<-0.5?'&#9660;':'&#9644;';el.className='delta '+(p>0.5?(inv?'down':'up'):p<-0.5?(inv?'up':'down'):'flat');el.innerHTML=`${{a}} ${{Math.abs(p).toFixed(1)}}% vs mes ant.`;}}

// ── Sections ──
function renderSection(){{const fn={{volumen:renderVol,diff:renderDiff,comps:renderComps,cv:renderCV,age:renderAge,step:renderStep,src:renderSrc,poly:renderPoly}};if(fn[state.section])fn[state.section]();}}

function secHTML(id,chs){{
  const s=document.getElementById('sec-'+id);let h=`<div class="metric-desc">${{MDESC[id]||''}}</div>`;
  chs.forEach(cc=>{{
    if(cc.type==='row')h+=`<div class="chart-row">${{cc.items.map(it=>`<div class="chart-half"><div class="chart-box"><h3>${{it.title}}</h3><canvas id="${{it.id}}"></canvas></div></div>`).join('')}}</div>`;
    else if(cc.type==='compare')h+=`<div class="compare-row"><div class="compare-col"><div class="compare-label">Colombia</div><div class="chart-box"><canvas id="${{cc.id}}-co"></canvas></div></div><div class="compare-col"><div class="compare-label">Mexico</div><div class="chart-box"><canvas id="${{cc.id}}-mx"></canvas></div></div></div>`;
    else h+=`<div class="chart-box"><h3>${{cc.title}}</h3><canvas id="${{cc.id}}"></canvas></div>`;
  }});h+=`<div id="tbl-${{id}}"></div>`;s.innerHTML=h;
}}

function renderVol(){{
  const qOpts={{metro:true,ciudad:true,zona:true,tipo:true,flag:true}};
  if(state.country==='compare'){{
    secHTML('volumen',[{{type:'compare',id:'ch-vol'}},{{type:'compare',id:'ch-vol-m'}}]);
    ['co','mx'].forEach(ct=>{{
      const q=fd('qual',qOpts,ct),agg=aggByGran(q,rs=>({{v70:rs.filter(r=>r.flag===70).reduce((s,r)=>s+r.vol,0),v71:rs.filter(r=>r.flag===71).reduce((s,r)=>s+r.vol,0)}}));
      lineChart('ch-vol-'+ct,agg.map(r=>r.label),[mkDS('F70 Manual',agg.map(r=>r.v70),C.blue),mkDS('F71 Auto',agg.map(r=>r.v71),C.orange)],'int');
      const am=aggByGran(q,rs=>{{const g={{}};rs.forEach(r=>{{g[r.metro]=(g[r.metro]||0)+r.vol;}});return g;}});
      const metros=[...new Set(q.map(r=>r.metro))].filter(m=>m!=='Otras').sort();
      lineChart('ch-vol-m-'+ct,am.map(r=>r.label),metros.map((mt,i)=>mkDS(mt,am.map(r=>r[mt]||0),MC[mt]||PAL[i%PAL.length])),'int');
    }});return;
  }}
  secHTML('volumen',[{{id:'ch-vol',title:'Volumen de Pricings (Manual vs Automatico)'}},{{id:'ch-vol-m',title:'Volumen por Area Metropolitana'}}]);
  const q=fd('qual',qOpts);
  const agg=aggByGran(q,rs=>({{v70:rs.filter(r=>r.flag===70).reduce((s,r)=>s+r.vol,0),v71:rs.filter(r=>r.flag===71).reduce((s,r)=>s+r.vol,0)}}));
  lineChart('ch-vol',agg.map(r=>r.label),[mkDS('F70 Manual',agg.map(r=>r.v70),C.blue),mkDS('F71 Automatico',agg.map(r=>r.v71),C.orange)],'int');
  const am=aggByGran(q,rs=>{{const g={{}};rs.forEach(r=>{{g[r.metro]=(g[r.metro]||0)+r.vol;}});return g;}});
  const metros=[...new Set(q.map(r=>r.metro))].filter(m=>m!=='Otras').sort();
  lineChart('ch-vol-m',am.map(r=>r.label),metros.map((mt,i)=>mkDS(mt,am.map(r=>r[mt]||0),MC[mt]||PAL[i%PAL.length])),'int');
  renderTable('tbl-volumen',['Periodo','Manual F70','Auto F71','Total'],agg.map(r=>[r.label,fmtN(r.v70),fmtN(r.v71),fmtN(r.v70+r.v71)]),'volumen');
}}

function renderDiff(){{
  const dOpts={{metro:true,ciudad:true}};
  if(state.country==='compare'){{
    secHTML('diff',[{{type:'compare',id:'ch-diff'}}]);
    ['co','mx'].forEach(ct=>{{const d=fd('diff',dOpts,ct),agg=aggByGran(d,rs=>({{ah:wavg(rs,'med_ask_habi','vol'),pb:wavg(rs,'med_precio_base','vol'),pr:wavg(rs,'med_pre_remo','vol')}}));lineChart('ch-diff-'+ct,agg.map(r=>r.label),[mkDS('Ask Habi',agg.map(r=>r.ah),C.blue),mkDS('Precio Base',agg.map(r=>r.pb),C.purple),mkDS('Pre-REMO',agg.map(r=>r.pr),C.orange)],'pct');}});return;
  }}
  secHTML('diff',[{{id:'ch-diff',title:'Diferencia Precio Cliente vs Habi (Mediana %)'}},{{id:'ch-diff-m',title:'Dif. Ask Habi por Area Metropolitana'}}]);
  const d=fd('diff',dOpts),agg=aggByGran(d,rs=>({{ah:wavg(rs,'med_ask_habi','vol'),pb:wavg(rs,'med_precio_base','vol'),pr:wavg(rs,'med_pre_remo','vol'),vol:rs.reduce((s,r)=>s+(r.vol||0),0)}}));
  lineChart('ch-diff',agg.map(r=>r.label),[mkDS('Ask Habi (Post-REMO)',agg.map(r=>r.ah),C.blue),mkDS('Precio Base',agg.map(r=>r.pb),C.purple),mkDS('Pre-REMO',agg.map(r=>r.pr),C.orange)],'pct');
  const metros=[...new Set(d.map(r=>r.metro))].filter(m=>m&&m!=='None'&&m!=='not found').sort();
  const am=aggByGran(d,rs=>{{const g={{}};rs.forEach(r=>{{if(!g[r.metro])g[r.metro]=[];g[r.metro].push(r);}});const out={{}};Object.keys(g).forEach(mt=>{{out[mt]=wavg(g[mt],'med_ask_habi','vol');}});return out;}});
  lineChart('ch-diff-m',am.map(r=>r.label),metros.map((mt,i)=>mkDS(mt,am.map(r=>r[mt]||null),MC[mt]||PAL[i%PAL.length])),'pct');
  renderTable('tbl-diff',['Periodo','Ask Habi %','Precio Base %','Pre-REMO %','Vol'],agg.map(r=>[r.label,fmtP(r.ah),fmtP(r.pb),fmtP(r.pr),fmtN(r.vol)]),'diff');
}}

function renderQual(sid,chId,chMId,col,title,yFmt,refLines){{
  const qOpts={{metro:true,ciudad:true,zona:true,tipo:true,flag:true}};
  if(state.country==='compare'){{
    secHTML(sid,[{{type:'compare',id:chId}}]);
    ['co','mx'].forEach(ct=>{{const q=fd('qual',qOpts,ct),agg=aggByGran(q,rs=>({{val:wavg(rs,col,'vol')}}));lineChart(chId+'-'+ct,agg.map(r=>r.label),[mkDS(title,agg.map(r=>r.val),C.blue)],yFmt,refLines);}});return;
  }}
  secHTML(sid,[{{id:chId,title:title}},{{id:chMId,title:title+' por Area Metropolitana'}}]);
  const q=fd('qual',qOpts),agg=aggByGran(q,rs=>({{val:wavg(rs,col,'vol'),vol:rs.reduce((s,r)=>s+(r.vol||0),0)}}));
  lineChart(chId,agg.map(r=>r.label),[mkDS(title,agg.map(r=>r.val),C.blue)],yFmt,refLines);
  const metros=[...new Set(q.map(r=>r.metro))].filter(m=>m!=='Otras').sort();
  const am=aggByGran(q,rs=>{{const g={{}};rs.forEach(r=>{{if(!g[r.metro])g[r.metro]=[];g[r.metro].push(r);}});const out={{}};Object.keys(g).forEach(mt=>{{out[mt]=wavg(g[mt],col,'vol');}});return out;}});
  lineChart(chMId,am.map(r=>r.label),metros.map((mt,i)=>mkDS(mt,am.map(r=>r[mt]||null),MC[mt]||PAL[i%PAL.length])),yFmt,refLines);
  renderTable('tbl-'+sid,['Periodo',title,'Vol'],agg.map(r=>[r.label,yFmt==='pct'?fmtP(r.val):yFmt==='int'?fmtN(r.val):fmtD(r.val),fmtN(r.vol)]),sid);
}}
function renderComps(){{renderQual('comps','ch-comps','ch-comps-m','avg_comps','Prom. Comparables','dec');}}
function renderCV(){{renderQual('cv','ch-cv','ch-cv-m','med_cv','Mediana CV','pct',[{{value:10,label:'10% Umbral',color:'#D32F2F'}}]);}}
function renderAge(){{renderQual('age','ch-age','ch-age-m','avg_age','Prom. Antiguedad (dias)','int',[{{value:180,label:'6 meses',color:'#EF6C00',dash:[8,4]}},{{value:365,label:'12 meses',color:'#D32F2F',dash:[8,4]}}]);}}

function renderStep(){{
  secHTML('step',[{{id:'ch-step',title:'Paso Promedio de Escalera'}},{{id:'ch-step-d',title:'Distribucion de Pasos'}}]);
  const ct=state.country==='compare'?'co':state.country;
  const q=fd('qual',{{metro:true,ciudad:true,zona:true,tipo:true,flag:true}},ct);
  const agg=aggByGran(q,rs=>({{val:wavg(rs,'avg_step','vol'),vol:rs.reduce((s,r)=>s+(r.vol||0),0)}}));
  lineChart('ch-step',agg.map(r=>r.label),[mkDS('Paso Promedio',agg.map(r=>r.val),C.blue)],'dec');
  const s=fd('steps',{{}},ct),bkts=[...new Set(s.map(r=>r.step_bucket))].sort();
  const sa=aggByGran(s,rs=>{{const g={{}};rs.forEach(r=>{{g[r.step_bucket]=(g[r.step_bucket]||0)+r.n;}});const t=Object.values(g).reduce((a,b)=>a+b,0);const out={{}};Object.keys(g).forEach(b=>{{out[b]=t>0?g[b]/t*100:0;}});return out;}});
  stackedBarChart('ch-step-d',sa.map(r=>r.label),bkts.map((b,i)=>mkBarDS(b.includes('. ')?b.split('. ')[1]:b,sa.map(r=>r[b]||0),['#1B5E20','#388E3C','#66BB6A','#A5D6A7','#9E9E9E'][i%5])),'pct');
  renderTable('tbl-step',['Periodo','Paso Promedio','Vol'],agg.map(r=>[r.label,fmtD(r.val),fmtN(r.vol)]),'step');
}}

function renderSrc(){{
  secHTML('src',[{{type:'row',items:[{{id:'ch-src-q',title:'Fuentes - Cantidad'}},{{id:'ch-src-p',title:'Fuentes - Composicion %'}}]}}]);
  const ct=state.country==='compare'?'co':state.country;
  const s=fd('src',{{src:true}},ct);
  const srcT={{}};s.forEach(r=>{{srcT[r.src]=(srcT[r.src]||0)+r.n;}});const sorted=Object.entries(srcT).sort((a,b)=>b[1]-a[1]);const top5=sorted.slice(0,5).map(x=>x[0]);const cats=[...top5];if(sorted.length>5)cats.push('Otras');
  const getN=(cat,rs)=>cat==='Otras'?rs.filter(r=>!top5.includes(r.src)).reduce((a,r)=>a+r.n,0):rs.filter(r=>r.src===cat).reduce((a,r)=>a+r.n,0);
  const sa=aggByGran(s,rs=>{{const out={{}};cats.forEach(c=>out['q_'+c]=getN(c,rs));const t=cats.reduce((a,c)=>a+out['q_'+c],0);cats.forEach(c=>out['p_'+c]=t>0?out['q_'+c]/t*100:0);out._total=t;return out;}});
  stackedBarChart('ch-src-q',sa.map(r=>r.label),cats.map((c,i)=>mkBarDS(c,sa.map(r=>r['q_'+c]||0),PAL[i%PAL.length])),'int');
  stackedBarChart('ch-src-p',sa.map(r=>r.label),cats.map((c,i)=>mkBarDS(c,sa.map(r=>r['p_'+c]||0),PAL[i%PAL.length])),'pct');
  renderTable('tbl-src',['Periodo',...cats,'Total'],sa.map(r=>[r.label,...cats.map(c=>fmtN(r['q_'+c]||0)),fmtN(r._total||0)]),'src');
}}

function renderPoly(){{
  secHTML('poly',[{{id:'ch-poly',title:'% de Uso de Polynator por Pais'}}]);
  const p=DS.poly||[],fm=p.filter(r=>r.mes>=state.desde&&r.mes<=state.hasta);
  const agg=aggByGran(fm,rs=>{{const co=rs.filter(r=>r.pais==='Colombia'),mx=rs.filter(r=>r.pais==='Mexico');return{{pco:co.length?co.reduce((s,r)=>s+r.con_polynator,0)/co.reduce((s,r)=>s+r.total,0)*100:null,pmx:mx.length?mx.reduce((s,r)=>s+r.con_polynator,0)/mx.reduce((s,r)=>s+r.total,0)*100:null}};}});
  lineChart('ch-poly',agg.map(r=>r.label),[mkDS('Colombia',agg.map(r=>r.pco),C.blue),mkDS('Mexico',agg.map(r=>r.pmx),C.orange)],'pct');
  renderTable('tbl-poly',['Periodo','Colombia %','Mexico %'],agg.map(r=>[r.label,r.pco!=null?r.pco.toFixed(1)+'%':'-',r.pmx!=null?r.pmx.toFixed(1)+'%':'-']),'poly');
}}


// ── Date Picker ──
let dpState={{d0:null,d1:null,nav0:null,nav1:null}};
const MES_NAMES=['Ene','Feb','Mar','Abr','May','Jun','Jul','Ago','Sep','Oct','Nov','Dic'];
const DOW=['L','M','X','J','V','S','D'];

function toggleDatePicker(){{
  const pp=document.getElementById('dp-popup');
  if(pp.classList.contains('open')){{pp.classList.remove('open');return;}}
  // Init nav to current desde/hasta months
  const[y0,m0]=state.desde.split('-').map(Number);
  const[y1,m1]=state.hasta.split('-').map(Number);
  dpState.d0=new Date(y0,m0-1,1); dpState.d1=new Date(y1,m1-1,1);
  dpState.nav0=new Date(y0,m0-1,1); dpState.nav1=new Date(y1,m1-1,1);
  dpPopulateSelects(); dpRender(0); dpRender(1);
  pp.classList.add('open');
}}
function dpPopulateSelects(){{
  [0,1].forEach(idx=>{{
    const sel=document.getElementById('dp-m'+idx);
    let h='';
    for(let y=2024;y<=new Date().getFullYear();y++){{
      for(let m=0;m<12;m++){{
        const d=new Date(y,m,1);
        if(d>new Date())break;
        const val=y+'-'+String(m+1).padStart(2,'0');
        const lab=MES_NAMES[m]+' '+y;
        const nav=idx===0?dpState.nav0:dpState.nav1;
        h+=`<option value="${{val}}"${{nav.getFullYear()===y&&nav.getMonth()===m?' selected':''}}>${{lab}}</option>`;
      }}
    }}
    sel.innerHTML=h;
  }});
}}
function dpMove(idx,dir){{
  const nav=idx===0?dpState.nav0:dpState.nav1;
  nav.setMonth(nav.getMonth()+dir);
  dpPopulateSelects(); dpRender(idx);
}}
function dpRender(idx){{
  const sel=document.getElementById('dp-m'+idx);
  const[y,m]=sel.value.split('-').map(Number);
  if(idx===0)dpState.nav0=new Date(y,m-1,1); else dpState.nav1=new Date(y,m-1,1);
  const grid=document.getElementById('dp-grid'+idx);
  const selDate=idx===0?dpState.d0:dpState.d1;
  const first=new Date(y,m-1,1);
  let startDay=first.getDay(); if(startDay===0)startDay=7; // Mon=1
  const daysInMonth=new Date(y,m,0).getDate();
  const today=new Date(); today.setHours(0,0,0,0);
  let h=DOW.map(d=>`<div class="dp-hdr">${{d}}</div>`).join('');
  // Blanks for days before 1st
  for(let i=1;i<startDay;i++) h+=`<div class="dp-day other"></div>`;
  for(let d=1;d<=daysInMonth;d++){{
    const dt=new Date(y,m-1,d);
    let cls='dp-day';
    if(selDate&&dt.getFullYear()===selDate.getFullYear()&&dt.getMonth()===selDate.getMonth()&&dt.getDate()===selDate.getDate()) cls+=' sel';
    else if(dpState.d0&&dpState.d1&&dt>dpState.d0&&dt<dpState.d1) cls+=' range';
    if(dt.getTime()===today.getTime()) cls+=' today';
    h+=`<div class="${{cls}}" onclick="dpSelect(${{idx}},${{y}},${{m-1}},${{d}})">${{d}}</div>`;
  }}
  grid.innerHTML=h;
}}
function dpSelect(idx,y,m,d){{
  const dt=new Date(y,m,d);
  if(idx===0)dpState.d0=dt; else dpState.d1=dt;
  // Ensure d0 <= d1
  if(dpState.d0&&dpState.d1&&dpState.d0>dpState.d1){{
    if(idx===0)dpState.d1=new Date(dpState.d0); else dpState.d0=new Date(dpState.d1);
  }}
  dpRender(0);dpRender(1);
}}
function dpApply(){{
  if(dpState.d0&&dpState.d1){{
    state.desde=dpState.d0.getFullYear()+'-'+String(dpState.d0.getMonth()+1).padStart(2,'0');
    state.hasta=dpState.d1.getFullYear()+'-'+String(dpState.d1.getMonth()+1).padStart(2,'0');
    dpUpdateBtn();
  }}
  document.getElementById('dp-popup').classList.remove('open');
  applyFilters();
}}
function dpCancel(){{ document.getElementById('dp-popup').classList.remove('open'); }}
function dpUpdateBtn(){{
  const b=document.getElementById('btn-daterange');
  const i0=META.meses.indexOf(state.desde),i1=META.meses.indexOf(state.hasta);
  const l0=i0>=0?META.meses_labels[i0]:state.desde, l1=i1>=0?META.meses_labels[i1]:state.hasta;
  b.textContent=l0+' - '+l1;
  b.classList.toggle('active',state.desde!==META.meses[0]||state.hasta!==META.meses[META.meses.length-1]);
}}
// Close date picker on outside click
document.addEventListener('click',e=>{{if(!e.target.closest('#dd-daterange'))document.getElementById('dp-popup').classList.remove('open');}});

// ── Init ──
initFilters();dpUpdateBtn();renderKPIs();document.getElementById('dd-source').style.display='none';
document.getElementById('dd-zona').style.display='none';
document.getElementById('dd-tipo').style.display='none';
renderSection();
</script></body></html>'''

out_path = os.path.join(OUT, 'dashboard_calidad_pricing.html')
with open(out_path, 'w', encoding='utf-8') as f:
    f.write(html)
print(f"\nDashboard: {out_path}")
print("LISTO!")
