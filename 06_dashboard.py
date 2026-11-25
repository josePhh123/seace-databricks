# Databricks notebook source
# /// script
# [tool.databricks.environment]
# environment_version = "6"
# ///
# MAGIC %md
# MAGIC # 06 · Dashboard como código
# MAGIC Crea (o actualiza) el dashboard **Obras públicas del Perú · SEACE** a partir de la capa gold.
# MAGIC Todo el diseño vive en este notebook: datasets, widgets y layout. Volver a correrlo deja el dashboard igual.

# COMMAND ----------

import json
from databricks.sdk import WorkspaceClient

w = WorkspaceClient()
NOMBRE = "Obras públicas del Perú · SEACE"
CARPETA = "/Users/" + w.current_user.me().user_name
WAREHOUSE_ID = next(iter(w.warehouses.list())).id

# --- Hallazgos calculados con los datos (se muestran en el panel de texto) ---
k = spark.sql("""
with base as (select proveedor, tipo_procedimiento, monto_adjudicado_pen m from oece.gold.obras_adjudicadas),
prov as (select proveedor, sum(m) m from base group by 1),
rk as (select m, row_number() over (order by m desc) rn from prov)
select
  (select sum(m) from base) total,
  (select count(*) from base) n,
  (select sum(m) from base where m >= 1e8) m_grandes,
  (select count(*) from base where m >= 1e8) n_grandes,
  (select sum(m) from base where m < 1e6) m_chicas,
  (select count(*) from base where m < 1e6) n_chicas,
  (select sum(m) from rk where rn <= 10) m_top10,
  (select count(*) from base where tipo_procedimiento = 'Licitación Pública Abreviada') n_lpa
""").first()
pct = lambda a, b: f"{float(a) / float(b) * 100:.0f} %".replace(".", ",")
miles = lambda x: f"{int(x):,}".replace(",", " ")
hallazgos = [
    "### Hallazgos",
    f"- **{k.n_grandes} obras** de S/ 100 M o más concentran el **{pct(k.m_grandes, k.total)}** del monto adjudicado.",
    f"- En el otro extremo, **{miles(k.n_chicas)} obras** menores a S/ 1 M suman solo el **{pct(k.m_chicas, k.total)}**.",
    f"- Los **10 mayores contratistas** se llevan el **{pct(k.m_top10, k.total)}** del monto.",
    f"- El **{pct(k.n_lpa, k.n)}** de las obras se adjudica por *Licitación Pública Abreviada*.",
]

# COMMAND ----------

# --- Datasets ---
datasets = [
    {"name": "obras", "displayName": "Obras adjudicadas", "queryLines": [
        "SELECT ocid, nomenclatura, tipo_procedimiento, entidad, departamento, proveedor,\n",
        "       fecha_adjudicacion, mes_adjudicacion,\n",
        "       CAST(monto_adjudicado_pen / 1e6 AS DOUBLE) AS monto_m,\n",
        "       CASE WHEN monto_adjudicado_pen < 1e6 THEN '1) Menos de 1 M'\n",
        "            WHEN monto_adjudicado_pen < 5e6 THEN '2) 1 a 5 M'\n",
        "            WHEN monto_adjudicado_pen < 2e7 THEN '3) 5 a 20 M'\n",
        "            WHEN monto_adjudicado_pen < 1e8 THEN '4) 20 a 100 M'\n",
        "            ELSE '5) 100 M o más' END AS rango_monto\n",
        "FROM oece.gold.obras_adjudicadas"]},
    {"name": "top_entidades", "displayName": "Top entidades", "queryLines": [
        "SELECT entidad, CAST(SUM(monto_adjudicado_pen) / 1e6 AS DOUBLE) AS monto_m, COUNT(*) AS obras\n",
        "FROM oece.gold.obras_adjudicadas GROUP BY entidad ORDER BY monto_m DESC LIMIT 10"]},
    {"name": "top_contratistas", "displayName": "Top contratistas", "queryLines": [
        "SELECT proveedor, CAST(SUM(monto_adjudicado_pen) / 1e6 AS DOUBLE) AS monto_m, COUNT(*) AS obras\n",
        "FROM oece.gold.obras_adjudicadas GROUP BY proveedor ORDER BY monto_m DESC LIMIT 10"]},
    {"name": "obras_mayores", "displayName": "Obras de mayor monto", "queryLines": [
        "SELECT nomenclatura, entidad, departamento, proveedor, fecha_adjudicacion,\n",
        "       ROUND(monto_adjudicado_pen / 1e6, 1) AS monto_m\n",
        "FROM oece.gold.obras_adjudicadas ORDER BY monto_adjudicado_pen DESC LIMIT 15"]},
]

# --- Helpers para widgets ---
FMT = {"type": "number-plain", "abbreviation": "none", "decimalPlaces": {"type": "exact", "places": 0}}

def q(ds, fields, disaggregated=False, name="main_query"):
    return [{"name": name, "query": {"datasetName": ds, "disaggregated": disaggregated,
             "fields": [{"name": n, "expression": e} for n, e in fields]}}]

def frame(t, desc=None):
    f = {"showTitle": True, "title": t}
    if desc:
        f.update({"showDescription": True, "description": desc})
    return f

def texto(name, lines):
    return {"name": name, "multilineTextboxSpec": {"lines": [l + "\n" for l in lines]}}

def contador(name, t, expr_name, expr, ds="obras"):
    return {"name": name, "queries": q(ds, [(expr_name, expr)]),
            "spec": {"version": 2, "widgetType": "counter", "frame": frame(t),
                     "encodings": {"value": {"fieldName": expr_name, "displayName": t, "format": FMT}}}}

def barra_h(name, t, ds, cat, val_name, val_expr, desc=None, color=None):
    enc = {"x": {"fieldName": val_name, "scale": {"type": "quantitative"}, "displayName": "S/ millones", "format": FMT},
           "y": {"fieldName": cat, "scale": {"type": "categorical", "sort": {"by": "x-reversed"}}, "displayName": ""},
           "label": {"show": True}}
    if color:
        enc["color"] = {"fieldName": val_name, "scale": {"type": "quantitative"}}
    return {"name": name, "queries": q(ds, [(cat, f"`{cat}`"), (val_name, val_expr)]),
            "spec": {"version": 3, "widgetType": "bar", "frame": frame(t, desc), "encodings": enc}}

def barra_v(name, t, ds, cat, val_name, val_expr, desc=None):
    return {"name": name, "queries": q(ds, [(cat, f"`{cat}`"), (val_name, val_expr)]),
            "spec": {"version": 3, "widgetType": "bar", "frame": frame(t, desc),
                     "encodings": {"x": {"fieldName": cat, "scale": {"type": "categorical", "sort": {"by": "natural-order"}}, "displayName": ""},
                                   "y": {"fieldName": val_name, "scale": {"type": "quantitative"}, "displayName": "S/ millones", "format": FMT},
                                   "label": {"show": True}}}}

def filtro(name, t, campo):
    qn = f"f_{campo}"
    return {"name": name, "queries": q("obras", [(campo, f"`{campo}`")], name=qn),
            "spec": {"version": 2, "widgetType": "filter-multi-select", "frame": frame(t),
                     "encodings": {"fields": [{"fieldName": campo, "displayName": t, "queryName": qn}]}}}

def col(field, title, i, typ="string"):
    c = {"fieldName": field, "displayName": title, "title": title, "visible": True, "order": i,
         "type": typ, "displayAs": "number" if typ == "float" else ("datetime" if typ == "date" else "string"),
         "alignContent": "right" if typ == "float" else "left", "booleanValues": ["false", "true"],
         "allowSearch": False, "highlightLinks": False, "useMonospaceFont": False, "preserveWhitespace": False,
         "imageUrlTemplate": "{{ @ }}", "imageTitleTemplate": "{{ @ }}", "imageWidth": "", "imageHeight": "",
         "linkUrlTemplate": "{{ @ }}", "linkTextTemplate": "{{ @ }}", "linkTitleTemplate": "{{ @ }}", "linkOpenInNewTab": True}
    if typ == "float":
        c["numberFormat"] = "0,0.0"
    if typ == "date":
        c["dateTimeFormat"] = "DD/MM/YYYY"
    return c

cols = [("monto_m", "S/ millones", "float"), ("nomenclatura", "Proceso", "string"), ("departamento", "Departamento", "string"),
        ("entidad", "Entidad", "string"), ("proveedor", "Contratista", "string"), ("fecha_adjudicacion", "Adjudicación", "date")]
tabla = {"name": "tabla_mayores",
         "queries": q("obras_mayores", [(f, f"`{f}`") for f, _, _ in cols], disaggregated=True),
         "spec": {"version": 1, "widgetType": "table", "frame": frame("Las 15 obras de mayor monto adjudicado"),
                  "encodings": {"columns": [col(f, t, i, ty) for i, (f, t, ty) in enumerate(cols)]},
                  "invisibleColumns": [], "allowHTMLByDefault": False, "itemsPerPage": 15,
                  "paginationSize": "default", "condensed": True, "withRowNumber": True}}

# COMMAND ----------

# --- Layout (grilla de 12 columnas) ---
def pos(x, y, wd, h):
    return {"x": x, "y": y, "width": wd, "height": h}

layout = [
    (texto("titulo", [
        "# Obras públicas adjudicadas en el Perú",
        "Contrataciones del Estado publicadas en el **SEACE** (datos abiertos OCDS del OECE) · adjudicaciones de julio a noviembre de 2026 (noviembre parcial) · montos en **soles**, convertidos desde USD/EUR cuando corresponde.",
    ]), pos(0, 0, 12, 3)),
    (contador("k_monto", "Monto adjudicado (S/ millones)", "sum(monto_m)", "SUM(`monto_m`)"), pos(0, 3, 3, 3)),
    (contador("k_obras", "Adjudicaciones de obra", "count(*)", "COUNT(*)"), pos(3, 3, 3, 3)),
    (contador("k_entidades", "Entidades contratantes", "countdistinct(entidad)", "COUNT(DISTINCT `entidad`)"), pos(6, 3, 3, 3)),
    (contador("k_contratistas", "Contratistas", "countdistinct(proveedor)", "COUNT(DISTINCT `proveedor`)"), pos(9, 3, 3, 3)),
    (filtro("f1", "Departamento", "departamento"), pos(0, 6, 4, 2)),
    (filtro("f2", "Tipo de procedimiento", "tipo_procedimiento"), pos(4, 6, 4, 2)),
    (filtro("f3", "Mes de adjudicación", "mes_adjudicacion"), pos(8, 6, 4, 2)),
    (barra_v("g_mes", "Monto adjudicado por mes", "obras", "mes_adjudicacion", "sum(monto_m)", "SUM(`monto_m`)",
             "S/ millones · julio tiene una sola obra; noviembre llega hasta el día 9"), pos(0, 8, 6, 6)),
    (barra_h("g_proc", "¿Cómo se adjudica? Monto por tipo de procedimiento", "obras", "tipo_procedimiento", "sum(monto_m)", "SUM(`monto_m`)",
             "S/ millones"), pos(6, 8, 6, 6)),
    (barra_h("g_depto", "Monto adjudicado por departamento", "obras", "departamento", "sum(monto_m)", "SUM(`monto_m`)",
             "S/ millones · departamento de la entidad contratante"), pos(0, 14, 6, 10)),
    (barra_v("g_rango", "Pocas obras grandes concentran el monto", "obras", "rango_monto", "sum(monto_m)", "SUM(`monto_m`)",
             "Monto total (S/ millones) según el tamaño de cada obra"), pos(6, 14, 6, 6)),
    (texto("hallazgos", hallazgos), pos(6, 20, 6, 4)),
    (barra_h("g_entidades", "Top 10 entidades que más adjudican", "top_entidades", "entidad", "sum(monto_m)", "SUM(`monto_m`)",
             "S/ millones · todo el periodo"), pos(0, 24, 6, 8)),
    (barra_h("g_contratistas", "Top 10 contratistas por monto adjudicado", "top_contratistas", "proveedor", "sum(monto_m)", "SUM(`monto_m`)",
             "S/ millones · todo el periodo"), pos(6, 24, 6, 8)),
    (tabla, pos(0, 32, 12, 9)),
]

dashboard = {
    "datasets": datasets,
    "pages": [{"name": "obras", "displayName": "Obras adjudicadas", "pageType": "PAGE_TYPE_CANVAS",
               "layout": [{"widget": wdg, "position": p} for wdg, p in layout]}],
}
serialized = json.dumps(dashboard, ensure_ascii=False)

# COMMAND ----------

# --- Crear o actualizar, y publicar ---
from databricks.sdk.service.dashboards import Dashboard

existente = next((d for d in w.lakeview.list() if d.display_name == NOMBRE), None)
spec = Dashboard(display_name=NOMBRE, parent_path=CARPETA, warehouse_id=WAREHOUSE_ID, serialized_dashboard=serialized)
if existente:
    d = w.lakeview.update(existente.dashboard_id, Dashboard(display_name=NOMBRE, warehouse_id=WAREHOUSE_ID, serialized_dashboard=serialized))
else:
    d = w.lakeview.create(spec)
w.lakeview.publish(d.dashboard_id, embed_credentials=True, warehouse_id=WAREHOUSE_ID)
print("DASHBOARD_ID", d.dashboard_id)
print("\n".join(hallazgos))