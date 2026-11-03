"""Auditor de campañas: limpia los datos, calcula métricas y aplica reglas de auditoría.

Uso:
    python auditor.py                 # usa campanias.csv (o lo genera si no existe)
    python auditor.py mis_datos.csv   # audita otro archivo con las mismas columnas

Escribe informe.html y hallazgos.csv.
"""
import html
import os
import sys

import numpy as np
import pandas as pd

import datos

# Umbrales de las reglas. Se pueden pasar otros a auditar() sin tocar el código.
UMBRALES = {
    "gasto_min_sin_conv": 150_000,   # gasto acumulado a partir del cual "0 conversiones" es grave
    "roas_min": 1.0,                 # por debajo, la campaña pierde plata
    "roas_escalar": 3.0,             # por encima (y gastando todo el presupuesto) conviene escalar
    "ctr_min": 0.005,                # 0,5 %
    "impresiones_min_ctr": 50_000,   # sin volumen mínimo el CTR no dice nada
    "frecuencia_max": 4.0,           # impresiones por persona alcanzada
    "fatiga_cpa": 0.30,              # CPA 7 días vs 7 días anteriores
    "conv_min_ventana": 5,           # conversiones mínimas en cada ventana para comparar
    "ejecucion_baja": 0.70,
    "ejecucion_alta": 1.10,
}
SEVERIDAD_ORDEN = {"alta": 0, "media": 1, "baja": 2, "oportunidad": 3}
NUMERICAS = ["presupuesto_diario", "gasto", "impresiones", "alcance", "clics", "conversiones", "ingresos"]


# ---------------------------------------------------------------- limpieza
def limpiar(df):
    """Valida las filas. Devuelve (df_limpio, problemas) donde problemas es una lista de
    {"tipo", "filas"}. Las filas inválidas se descartan: mejor auditar menos datos que datos falsos."""
    faltan = [c for c in datos.COLUMNAS if c not in df.columns]
    if faltan:
        raise ValueError("Faltan columnas: " + ", ".join(faltan))
    df = df[datos.COLUMNAS].copy()
    df["fecha"] = pd.to_datetime(df["fecha"], errors="coerce")
    for c in NUMERICAS:
        df[c] = pd.to_numeric(df[c], errors="coerce")

    problemas = []
    malas = pd.Series(False, index=df.index)

    def marcar(mask, tipo):
        nonlocal malas
        mask = mask & ~malas            # una fila se cuenta una sola vez
        if mask.any():
            problemas.append({"tipo": tipo, "filas": int(mask.sum())})
            malas |= mask

    marcar(df["fecha"].isna() | df[NUMERICAS].isna().any(axis=1), "valores vacíos o ilegibles")
    marcar((df[NUMERICAS] < 0).any(axis=1), "valores negativos")
    marcar(df["clics"] > df["impresiones"], "más clics que impresiones")
    marcar(df["conversiones"] > df["clics"], "más conversiones que clics")
    # Los duplicados se buscan al final, entre las filas que no tienen otro error, para no culpar
    # al duplicado de un problema que en realidad es de la fila (y quedarnos con la buena).
    marcar(df.duplicated(["fecha", "campania_id"], keep="first") & ~malas, "filas duplicadas (misma fecha y campaña)")
    return df[~malas].reset_index(drop=True), problemas


# ---------------------------------------------------------------- métricas
def _dividir(a, b):
    """División que da NaN (no infinito ni error) cuando el denominador es 0."""
    return a / b.replace(0, np.nan)


def metricas(df):
    """Una fila por campaña con gasto, ingresos y los indicadores clásicos."""
    g = df.groupby(["campania_id", "campania", "plataforma", "objetivo"], as_index=False).agg(
        dias=("fecha", "nunique"), presupuesto_diario=("presupuesto_diario", "mean"),
        gasto=("gasto", "sum"), impresiones=("impresiones", "sum"), clics=("clics", "sum"),
        conversiones=("conversiones", "sum"), ingresos=("ingresos", "sum"))
    freq = (df.assign(f=_dividir(df["impresiones"], df["alcance"]))
              .groupby("campania_id")["f"].mean().rename("frecuencia"))
    g = g.merge(freq, on="campania_id")
    g["ctr"] = _dividir(g["clics"], g["impresiones"])
    g["cpc"] = _dividir(g["gasto"], g["clics"])
    g["cpm"] = _dividir(g["gasto"], g["impresiones"]) * 1000
    g["cpa"] = _dividir(g["gasto"], g["conversiones"])
    g["roas"] = _dividir(g["ingresos"], g["gasto"])
    g["ejecucion"] = g["gasto"] / (g["presupuesto_diario"] * g["dias"])
    return g.sort_values("gasto", ascending=False).reset_index(drop=True)


def cpa_por_ventana(df, dias=7):
    """CPA de los últimos `dias` días vs. los `dias` anteriores, por campaña."""
    fin = df["fecha"].max()
    corte = fin - pd.Timedelta(days=dias)
    corte0 = corte - pd.Timedelta(days=dias)
    rec = df[df["fecha"] > corte].groupby("campania_id")[["gasto", "conversiones"]].sum()
    ant = df[(df["fecha"] > corte0) & (df["fecha"] <= corte)].groupby("campania_id")[["gasto", "conversiones"]].sum()
    out = rec.join(ant, lsuffix="_rec", rsuffix="_ant", how="outer")
    out["cpa_rec"] = _dividir(out["gasto_rec"], out["conversiones_rec"])
    out["cpa_ant"] = _dividir(out["gasto_ant"], out["conversiones_ant"])
    out["cambio"] = out["cpa_rec"] / out["cpa_ant"] - 1
    return out


# ---------------------------------------------------------------- reglas
def pesos(n):
    return f"${n:,.0f}".replace(",", ".")


def num(x, dec=1):
    """Número con coma decimal (formato argentino)."""
    return f"{x:,.{dec}f}".replace(",", "_").replace(".", ",").replace("_", ".")


def pct(x, dec=1):
    return num(x * 100, dec) + " %"


def auditar(df, u=None):
    """Aplica las reglas y devuelve una lista de hallazgos (diccionarios) ordenada por prioridad."""
    u = {**UMBRALES, **(u or {})}
    m = metricas(df)
    v = cpa_por_ventana(df).to_dict("index")
    hall = []

    def add(r, sev, regla, detalle, accion):
        hall.append(dict(campania_id=int(r.campania_id), campania=r.campania, plataforma=r.plataforma,
                         severidad=sev, regla=regla, detalle=detalle, accion=accion))

    for r in m.itertuples():
        conv = r.objetivo == "Conversión"
        if conv and r.conversiones == 0 and r.gasto >= u["gasto_min_sin_conv"]:
            add(r, "alta", "Gasto sin conversiones",
                f"Gastó {pesos(r.gasto)} y no registró ninguna conversión.",
                "Revisar el seguimiento (píxel o etiqueta) antes de gastar más; si funciona, pausar.")
        elif conv and r.roas < u["roas_min"]:
            add(r, "alta", "ROAS por debajo de 1",
                f"Por cada $1 invertido devuelve ${num(r.roas, 2)}. CPA: {pesos(r.cpa)}.",
                "Pausar o recortar el presupuesto y revisar segmentación y palabras clave.")
        if r.impresiones >= u["impresiones_min_ctr"] and r.ctr < u["ctr_min"]:
            add(r, "media", "CTR muy bajo",
                f"CTR de {pct(r.ctr, 2)} sobre {num(r.impresiones, 0)} impresiones.",
                "Probar otras creatividades y mensajes; revisar si el público es el correcto.")
        if r.frecuencia > u["frecuencia_max"]:
            add(r, "media", "Frecuencia alta",
                f"Cada persona vio el anuncio {num(r.frecuencia)} veces en promedio.",
                "Renovar la creatividad o ampliar el público para evitar la fatiga.")
        w = v.get(r.campania_id, {})
        if (conv and w.get("conversiones_rec", 0) >= u["conv_min_ventana"]
                and w.get("conversiones_ant", 0) >= u["conv_min_ventana"]
                and w.get("cambio", 0) >= u["fatiga_cpa"]):
            add(r, "media", "CPA en aumento",
                f"El CPA de la última semana ({pesos(w['cpa_rec'])}) subió {pct(w['cambio'], 0)} "
                f"frente a la anterior ({pesos(w['cpa_ant'])}).",
                "Revisar la creatividad y la audiencia; puede estar agotándose.")
        if r.ejecucion < u["ejecucion_baja"]:
            add(r, "baja", "No gasta su presupuesto",
                f"Gastó el {pct(r.ejecucion, 0)} del presupuesto diario.",
                "Ampliar la segmentación o revisar la puja: se está perdiendo volumen.")
        elif r.ejecucion > u["ejecucion_alta"]:
            add(r, "media", "Se pasa del presupuesto",
                f"Gastó el {pct(r.ejecucion, 0)} del presupuesto diario.", "Revisar los límites de gasto.")
        if conv and r.roas >= u["roas_escalar"] and r.ejecucion >= 0.9:
            add(r, "oportunidad", "Candidata a escalar",
                f"ROAS de {num(r.roas)} y gasta todo su presupuesto.",
                "Subir el presupuesto de a poco (alrededor de 20 %) y vigilar que el ROAS se sostenga.")
    hall.sort(key=lambda h: (SEVERIDAD_ORDEN[h["severidad"]], h["campania_id"]))
    return hall


def resumen(df):
    """Totales de toda la cuenta (solo campañas de conversión para ROAS y CPA)."""
    c = df[df["objetivo"] == "Conversión"]
    gasto_c = c["gasto"].sum()
    return dict(gasto=float(df["gasto"].sum()), ingresos=float(df["ingresos"].sum()),
                conversiones=int(df["conversiones"].sum()),
                roas=float(c["ingresos"].sum() / gasto_c) if gasto_c else float("nan"),
                cpa=float(gasto_c / c["conversiones"].sum()) if c["conversiones"].sum() else float("nan"),
                desde=df["fecha"].min().date().isoformat(), hasta=df["fecha"].max().date().isoformat(),
                campanias=int(df["campania_id"].nunique()))


# ---------------------------------------------------------------- informe
def _linea(df):
    d = df.groupby("fecha")[["gasto", "ingresos"]].sum().reset_index()
    W, H, pad = 640, 200, 34
    top = max(d["ingresos"].max(), d["gasto"].max()) * 1.08
    n = max(1, len(d) - 1)

    def pts(col):
        return " ".join(f"{pad + i / n * (W - pad * 2):.1f},{H - pad - d[col].iloc[i] / top * (H - pad * 2):.1f}"
                        for i in range(len(d)))
    grid = "".join(f'<line x1="{pad}" x2="{W - pad}" y1="{H - pad - k / 4 * (H - pad * 2):.0f}" '
                   f'y2="{H - pad - k / 4 * (H - pad * 2):.0f}" class="g"/>' for k in range(5))
    f0, f1 = d["fecha"].iloc[0].strftime("%d/%m"), d["fecha"].iloc[-1].strftime("%d/%m")
    return (f'<svg viewBox="0 0 {W} {H}" role="img" aria-label="Gasto e ingresos por día">{grid}'
            f'<polyline points="{pts("ingresos")}" class="l-ing"/><polyline points="{pts("gasto")}" class="l-gas"/>'
            f'<text x="{pad}" y="14" class="t">máx. {pesos(top)} por día</text><text x="{pad}" y="{H - 10}" class="t">{f0}</text><text x="{W - pad}" y="{H - 10}" class="t" text-anchor="end">{f1}</text></svg>')


CSS = """
:root{--bg:#f3f4fb;--ink:#14122b;--mute:#6b6a85;--card:#fff;--line:#e4e4f2;--acc:#5a3df0;--lime:#c8f23a;--alta:#e5384f;--media:#f08a1d;--baja:#4b8bd6;--op:#12a36b}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif}
.w{max-width:1000px;margin:0 auto;padding:28px 18px 60px}
header{background:var(--ink);color:#fff;border-radius:18px;padding:26px 28px;position:relative;overflow:hidden}
header:after{content:"";position:absolute;right:-40px;top:-40px;width:180px;height:180px;background:var(--lime);border-radius:50%;opacity:.9}
header h1{margin:0 0 4px;font-size:28px;letter-spacing:-.02em}header p{margin:0;color:#b9b8d9;position:relative;z-index:1}
.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px;margin:16px 0}
.k{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:14px 16px}.k b{display:block;font-size:24px;letter-spacing:-.02em}.k span{color:var(--mute);font-size:12px;text-transform:uppercase;letter-spacing:.06em}
h2{font-size:18px;margin:30px 0 10px}.card{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:16px;overflow-x:auto}
.h{display:grid;grid-template-columns:112px 1fr;gap:4px 14px;padding:14px 0;border-bottom:1px solid var(--line)}.h:last-child{border:0}
.tag{text-align:center;font-size:11px;font-weight:700;text-transform:uppercase;letter-spacing:.06em;color:#fff;border-radius:6px;padding:3px 8px;height:fit-content;white-space:nowrap}
.tag.alta{background:var(--alta)}.tag.media{background:var(--media)}.tag.baja{background:var(--baja)}.tag.oportunidad{background:var(--op)}
.h b{display:block}.h small{color:var(--mute)}.h .a{grid-column:2;background:#f6f5ff;border-left:3px solid var(--acc);padding:6px 10px;border-radius:0 8px 8px 0;margin-top:4px}
table{border-collapse:collapse;width:100%;font-size:13px;min-width:640px}th{text-align:right;color:var(--mute);font-weight:600;font-size:11px;text-transform:uppercase;letter-spacing:.05em;padding:6px 8px;border-bottom:2px solid var(--ink)}
td{padding:8px;text-align:right;border-bottom:1px solid var(--line);font-variant-numeric:tabular-nums}th:first-child,td:first-child{text-align:left}td small{color:var(--mute)}
.bad{color:var(--alta);font-weight:700}.good{color:var(--op);font-weight:700}
svg{width:100%;height:auto}.g{stroke:var(--line)}.t{fill:var(--mute);font-size:11px}.l-ing{fill:none;stroke:var(--acc);stroke-width:2.5}.l-gas{fill:none;stroke:#f08a1d;stroke-width:2.5;stroke-dasharray:5 4}
.leg{font-size:12px;color:var(--mute)}.leg i{display:inline-block;width:14px;height:3px;margin:0 5px 3px 10px;background:var(--acc)}.leg i.o{background:#f08a1d}
.dq{background:#fff8e6;border:1px solid #f3dca0;border-radius:14px;padding:12px 16px;font-size:14px}.ok{background:#eafaf2;border-color:#b6e8cf}
footer{color:var(--mute);font-size:12px;margin-top:30px}
@media(max-width:560px){header{padding:20px}header h1{font-size:22px}.h{grid-template-columns:1fr}.h .a{grid-column:1}}
"""


def _ars(n):
    return "—" if pd.isna(n) else pesos(n)


def informe_html(df, problemas, hall, u=None):
    s, m = resumen(df), metricas(df)
    e = html.escape
    k = [("Gasto", pesos(s["gasto"])), ("Ingresos", pesos(s["ingresos"])), ("ROAS (conversión)", num(s['roas'], 2)),
         ("CPA (conversión)", _ars(s["cpa"])), ("Conversiones", f"{s['conversiones']:,}".replace(",", "."))]
    kpis = "".join(f'<div class="k"><span>{a}</span><b>{b}</b></div>' for a, b in k)
    hs = "".join(
        f'<div class="h"><span class="tag {h["severidad"]}">{h["severidad"]}</span>'
        f'<div><b>{e(h["campania"])} · {e(h["regla"])}</b><small>{e(h["plataforma"])}</small><br>{e(h["detalle"])}</div>'
        f'<div class="a">{e(h["accion"])}</div></div>' for h in hall) or "<p>Sin hallazgos.</p>"
    filas = []
    for r in m.itertuples():
        cls_roas = "bad" if r.objetivo == "Conversión" and r.roas < 1 else ("good" if r.objetivo == "Conversión" and r.roas >= 3 else "")
        filas.append(
            f"<tr><td>{e(r.campania)}<br><small>{e(r.plataforma)} · {e(r.objetivo)}</small></td><td>{pesos(r.gasto)}</td>"
            f"<td>{pct(r.ctr, 2)}</td><td>{_ars(r.cpc)}</td><td>{num(r.frecuencia)}</td><td>{int(r.conversiones)}</td>"
            f"<td>{_ars(r.cpa)}</td><td class='{cls_roas}'>{'—' if pd.isna(r.roas) else num(r.roas, 2)}</td><td>{pct(r.ejecucion, 0)}</td></tr>")
    tabla = ("<table><tr><th>Campaña</th><th>Gasto</th><th>CTR</th><th>CPC</th><th>Frec.</th><th>Conv.</th><th>CPA</th>"
             "<th>ROAS</th><th>Ejec.</th></tr>" + "".join(filas) + "</table>")
    if problemas:
        dq = '<div class="dq"><b>Calidad de los datos:</b> se descartaron filas antes de auditar — ' + "; ".join(
            f'{p["filas"]} con {e(p["tipo"])}' for p in problemas) + ".</div>"
    else:
        dq = '<div class="dq ok"><b>Calidad de los datos:</b> no se encontró ninguna fila con errores.</div>'
    return f"""<!doctype html><html lang="es"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Auditoría de campañas</title><style>{CSS}</style></head><body><div class="w">
<header><h1>Auditoría de campañas</h1><p>{s['campanias']} campañas · del {s['desde']} al {s['hasta']} · datos ficticios</p></header>
<div class="kpis">{kpis}</div>
<h2>Qué hay que mirar primero</h2><div class="card">{hs}</div>
<h2>Gasto e ingresos por día</h2><div class="card">{_linea(df)}<div class="leg"><i></i>Ingresos<i class="o"></i>Gasto</div></div>
<h2>Todas las campañas</h2><div class="card">{tabla}</div>
<h2>Datos</h2>{dq}
<footer>CTR = clics / impresiones · CPC = gasto / clics · CPA = gasto / conversiones · ROAS = ingresos / gasto.
ROAS y CPA solo se juzgan en campañas de conversión. La frecuencia es el promedio diario de impresiones por persona alcanzada.</footer>
</div></body></html>"""


def hallazgos_csv(hall, ruta):
    pd.DataFrame(hall, columns=["campania_id", "campania", "plataforma", "severidad", "regla", "detalle", "accion"]).to_csv(
        ruta, index=False, encoding="utf-8")


def main(argv):
    ruta = argv[1] if len(argv) > 1 else "campanias.csv"
    if not os.path.exists(ruta):
        print(f"No existe {ruta}: genero datos ficticios.")
        datos.escribir_csv(ruta, datos.generar())
    limpio, problemas = limpiar(pd.read_csv(ruta))
    hall = auditar(limpio)
    with open("informe.html", "w", encoding="utf-8") as f:
        f.write(informe_html(limpio, problemas, hall))
    hallazgos_csv(hall, "hallazgos.csv")
    cuenta = pd.Series([h["severidad"] for h in hall]).value_counts()
    print(f"{len(limpio)} filas auditadas, {sum(p['filas'] for p in problemas)} descartadas.")
    print("Hallazgos:", ", ".join(f"{n} {k}" for k, n in cuenta.items()) or "ninguno")
    print("Escribí informe.html y hallazgos.csv")


if __name__ == "__main__":
    main(sys.argv)
