"""Generador de datos FICTICIOS de campañas publicitarias (reproducible).

Cada campaña tiene un "perfil" y algunas tienen un problema plantado a propósito
(seguimiento roto, frecuencia alta, rendimiento que cae...) para que el auditor
tenga algo que encontrar. Nada de esto viene de una cuenta publicitaria real.
"""
import csv
import datetime as dt
import random

COLUMNAS = ["fecha", "campania_id", "campania", "plataforma", "objetivo",
            "presupuesto_diario", "gasto", "impresiones", "alcance", "clics",
            "conversiones", "ingresos"]

# id, nombre, plataforma, objetivo, presupuesto diario, perfil
CAMPANIAS = [
    (1, "Retargeting carrito", "Meta", "Conversión", 80000,
     dict(cpm=6000, ctr=.024, cvr=.09, valor=22000, freq=2.1)),
    (2, "Lookalike compradores", "Meta", "Conversión", 120000,
     dict(cpm=7500, ctr=.014, cvr=.04, valor=22000, freq=1.8)),
    (3, "Búsqueda de marca", "Google", "Conversión", 50000,
     dict(cpm=9000, ctr=.09, cvr=.08, valor=20000, freq=1.3)),
    (4, "Búsqueda genérica", "Google", "Conversión", 140000,
     dict(cpm=15000, ctr=.022, cvr=.015, valor=20000, freq=1.4)),
    (5, "Display amplio", "Google", "Alcance", 60000,
     dict(cpm=2500, ctr=.0025, cvr=.003, valor=15000, freq=2.8)),
    (6, "Reels producto nuevo", "Meta", "Tráfico", 90000,
     dict(cpm=5500, ctr=.011, cvr=.01, valor=18000, freq=5.4)),
    (7, "Spark Ads liquidación", "TikTok", "Conversión", 70000,
     dict(cpm=4200, ctr=.015, cvr=0.0, valor=18000, freq=2.4)),   # seguimiento roto
    (8, "Shopping catálogo", "Google", "Conversión", 100000,
     dict(cpm=11000, ctr=.018, cvr=.05, valor=18000, freq=1.5)),
    (9, "Stories descuento", "Meta", "Conversión", 60000,
     dict(cpm=6500, ctr=.016, cvr=.035, valor=19000, freq=2.6, deriva=-.75)),  # cae con el tiempo
    (10, "TikTok in-feed", "TikTok", "Tráfico", 50000,
     dict(cpm=3800, ctr=.012, cvr=.02, valor=16000, freq=1.9)),
    (11, "YouTube video", "Google", "Alcance", 40000,
     dict(cpm=3000, ctr=.004, cvr=.002, valor=15000, freq=3.1)),
    (12, "Búsqueda competidores", "Google", "Conversión", 30000,
     dict(cpm=12000, ctr=.03, cvr=.06, valor=20000, freq=1.4, ejecucion=.4)),  # no gasta su presupuesto
]


def _ruido(r, s=0.12):
    return max(0.2, r.gauss(1.0, s))


def generar(hasta=dt.date(2026, 10, 31), dias=30, semilla=7, sucio=True):
    """Devuelve la lista de filas (diccionarios). Con sucio=True agrega 3 filas con errores."""
    r = random.Random(semilla)
    filas = []
    for d in range(dias):
        fecha = hasta - dt.timedelta(days=dias - 1 - d)
        avance = d / max(1, dias - 1)
        for cid, nombre, plat, obj, pres, p in CAMPANIAS:
            ejec = p.get("ejecucion", r.uniform(.93, 1.03))
            gasto = round(pres * ejec * _ruido(r, .05))
            imp = int(gasto / p["cpm"] * 1000 * _ruido(r))
            alcance = max(1, int(imp / (p["freq"] * _ruido(r, .06))))
            clics = int(imp * p["ctr"] * _ruido(r))
            cvr = p["cvr"] * (1 + p.get("deriva", 0) * avance) * _ruido(r, .18)
            conv = max(0, round(clics * cvr))
            ingresos = round(conv * p["valor"] * _ruido(r, .1))
            filas.append(dict(fecha=fecha.isoformat(), campania_id=cid, campania=nombre,
                              plataforma=plat, objetivo=obj, presupuesto_diario=pres,
                              gasto=gasto, impresiones=imp, alcance=alcance, clics=clics,
                              conversiones=conv, ingresos=ingresos))
    if sucio:
        f = filas
        f.append(dict(f[40]))                                   # fila duplicada
        malo = dict(f[100]); malo["clics"] = malo["impresiones"] + 500; f.append(malo)   # clics > impresiones
        neg = dict(f[200]); neg["gasto"] = -neg["gasto"]; f.append(neg)                  # gasto negativo
    return filas


def escribir_csv(ruta, filas):
    with open(ruta, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNAS)
        w.writeheader()
        w.writerows(filas)


if __name__ == "__main__":
    escribir_csv("campanias.csv", generar())
    print("campanias.csv escrito")
