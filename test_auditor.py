"""Pruebas del auditor.  Se corren con:  python -m unittest -v   (o: python test_auditor.py)"""
import datetime as dt
import unittest

import pandas as pd

import auditor as a
import datos


def fila(dia=0, cid=1, objetivo="Conversión", **kw):
    base = dict(fecha=(dt.date(2026, 10, 1) + dt.timedelta(days=dia)).isoformat(), campania_id=cid,
                campania=f"C{cid}", plataforma="Meta", objetivo=objetivo, presupuesto_diario=1000,
                gasto=1000, impresiones=10000, alcance=5000, clics=200, conversiones=10, ingresos=5000)
    base.update(kw)
    return base


def df_de(filas):
    d, _ = a.limpiar(pd.DataFrame(filas))
    return d


class Limpieza(unittest.TestCase):
    def test_fila_correcta_pasa(self):
        d, p = a.limpiar(pd.DataFrame([fila()]))
        self.assertEqual(len(d), 1)
        self.assertEqual(p, [])

    def test_descarta_duplicados(self):
        d, p = a.limpiar(pd.DataFrame([fila(), fila()]))
        self.assertEqual(len(d), 1)
        self.assertIn("duplicadas", p[0]["tipo"])

    def test_un_error_no_se_confunde_con_duplicado(self):
        _, p = a.limpiar(pd.DataFrame([fila(), fila(clics=20000), fila(gasto=-1)]))
        self.assertEqual({x["tipo"] for x in p}, {"más clics que impresiones", "valores negativos"})

    def test_descarta_clics_mayores_que_impresiones(self):
        d, p = a.limpiar(pd.DataFrame([fila(clics=20000)]))
        self.assertEqual(len(d), 0)

    def test_descarta_conversiones_mayores_que_clics(self):
        d, _ = a.limpiar(pd.DataFrame([fila(conversiones=999)]))
        self.assertEqual(len(d), 0)

    def test_descarta_negativos(self):
        d, _ = a.limpiar(pd.DataFrame([fila(gasto=-5)]))
        self.assertEqual(len(d), 0)

    def test_descarta_vacios_y_texto_ilegible(self):
        d, p = a.limpiar(pd.DataFrame([fila(gasto=None), fila(dia=1, clics="abc"), fila(dia=2, fecha="no es fecha")]))
        self.assertEqual(len(d), 0)
        self.assertEqual(p[0]["filas"], 3)

    def test_una_fila_con_dos_errores_se_cuenta_una_vez(self):
        _, p = a.limpiar(pd.DataFrame([fila(gasto=-5, clics=20000)]))
        self.assertEqual(sum(x["filas"] for x in p), 1)

    def test_faltan_columnas(self):
        with self.assertRaises(ValueError):
            a.limpiar(pd.DataFrame([{"fecha": "2026-10-01"}]))

    def test_no_modifica_el_original(self):
        original = pd.DataFrame([fila(), fila()])
        a.limpiar(original)
        self.assertEqual(len(original), 2)


class Metricas(unittest.TestCase):
    def setUp(self):
        self.m = a.metricas(df_de([fila(dia=0), fila(dia=1)])).iloc[0]

    def test_formulas(self):
        self.assertEqual(self.m["gasto"], 2000)
        self.assertAlmostEqual(self.m["ctr"], 0.02)
        self.assertAlmostEqual(self.m["cpc"], 5)
        self.assertAlmostEqual(self.m["cpa"], 100)
        self.assertAlmostEqual(self.m["roas"], 5)
        self.assertAlmostEqual(self.m["cpm"], 100)
        self.assertAlmostEqual(self.m["frecuencia"], 2)
        self.assertAlmostEqual(self.m["ejecucion"], 1.0)

    def test_division_por_cero_da_nan_no_error(self):
        m = a.metricas(df_de([fila(clics=0, conversiones=0, gasto=0)])).iloc[0]
        self.assertTrue(pd.isna(m["cpa"]) and pd.isna(m["cpc"]) and pd.isna(m["roas"]))

    def test_una_fila_por_campania(self):
        m = a.metricas(df_de([fila(cid=1), fila(cid=2), fila(dia=1, cid=1)]))
        self.assertEqual(len(m), 2)

    def test_ventana_cpa(self):
        filas = [fila(dia=d, gasto=1000, conversiones=10) for d in range(14)]
        for f in filas[7:]:
            f["gasto"] = 2000                       # la última semana cuesta el doble por conversión
        v = a.cpa_por_ventana(df_de(filas))
        self.assertAlmostEqual(v.loc[1, "cambio"], 1.0)


class Reglas(unittest.TestCase):
    def reglas(self, filas, **u):
        return {h["regla"] for h in a.auditar(df_de(filas), u)}

    def test_sin_conversiones_con_gasto_alto(self):
        r = self.reglas([fila(dia=d, conversiones=0, ingresos=0, gasto=20000, presupuesto_diario=20000) for d in range(10)])
        self.assertIn("Gasto sin conversiones", r)

    def test_sin_conversiones_pero_poco_gasto_no_alarma(self):
        r = self.reglas([fila(conversiones=0, ingresos=0)])
        self.assertNotIn("Gasto sin conversiones", r)

    def test_roas_bajo(self):
        self.assertIn("ROAS por debajo de 1", self.reglas([fila(ingresos=500)]))

    def test_roas_bueno_no_alarma(self):
        self.assertNotIn("ROAS por debajo de 1", self.reglas([fila()]))

    def test_roas_no_se_juzga_en_trafico(self):
        r = self.reglas([fila(objetivo="Tráfico", ingresos=0, conversiones=0)])
        self.assertNotIn("ROAS por debajo de 1", r)
        self.assertNotIn("Gasto sin conversiones", r)

    def test_ctr_bajo_necesita_volumen(self):
        pocas = [fila(clics=1, conversiones=0, impresiones=1000)]
        self.assertNotIn("CTR muy bajo", self.reglas(pocas))
        muchas = [fila(dia=d, clics=10, conversiones=0, impresiones=20000) for d in range(5)]
        self.assertIn("CTR muy bajo", self.reglas(muchas))

    def test_frecuencia_alta(self):
        self.assertIn("Frecuencia alta", self.reglas([fila(impresiones=10000, alcance=1500)]))

    def test_cpa_en_aumento(self):
        filas = [fila(dia=d, gasto=1000, conversiones=10) for d in range(14)]
        for f in filas[7:]:
            f["conversiones"] = 5
        self.assertIn("CPA en aumento", self.reglas(filas))

    def test_cpa_en_aumento_pide_conversiones_minimas(self):
        filas = [fila(dia=d, gasto=1000, conversiones=0 if d < 7 else 1, ingresos=0) for d in range(14)]
        self.assertNotIn("CPA en aumento", self.reglas(filas))

    def test_subejecucion_y_sobreejecucion(self):
        self.assertIn("No gasta su presupuesto", self.reglas([fila(gasto=300)]))
        self.assertIn("Se pasa del presupuesto", self.reglas([fila(gasto=1300, ingresos=9000)]))

    def test_candidata_a_escalar(self):
        self.assertIn("Candidata a escalar", self.reglas([fila(ingresos=9000)]))

    def test_no_escalar_si_no_gasta_el_presupuesto(self):
        self.assertNotIn("Candidata a escalar", self.reglas([fila(gasto=500, ingresos=9000)]))

    def test_umbrales_configurables(self):
        self.assertNotIn("Candidata a escalar", self.reglas([fila(ingresos=9000)], roas_escalar=20))

    def test_orden_por_severidad(self):
        h = a.auditar(df_de([fila(cid=1, ingresos=9000), fila(cid=2, ingresos=100)]))
        orden = [a.SEVERIDAD_ORDEN[x["severidad"]] for x in h]
        self.assertEqual(orden, sorted(orden))

    def test_cuenta_sana_sin_alarmas(self):
        h = a.auditar(df_de([fila(dia=d) for d in range(10)]))
        self.assertTrue(all(x["severidad"] == "oportunidad" or x["severidad"] == "baja" for x in h) or h == [])


class DatosYFormato(unittest.TestCase):
    def test_generador_reproducible(self):
        self.assertEqual(datos.generar(), datos.generar())

    def test_datos_ficticios_encuentran_los_problemas_plantados(self):
        d, p = a.limpiar(pd.DataFrame(datos.generar()))
        self.assertEqual(sum(x["filas"] for x in p), 3)
        pares = {(h["campania"], h["regla"]) for h in a.auditar(d)}
        self.assertIn(("Spark Ads liquidación", "Gasto sin conversiones"), pares)
        self.assertIn(("Búsqueda genérica", "ROAS por debajo de 1"), pares)
        self.assertIn(("Reels producto nuevo", "Frecuencia alta"), pares)
        self.assertIn(("Stories descuento", "CPA en aumento"), pares)
        self.assertIn(("Búsqueda competidores", "No gasta su presupuesto"), pares)
        self.assertIn(("Retargeting carrito", "Candidata a escalar"), pares)

    def test_formato_argentino(self):
        self.assertEqual(a.pesos(1234567), "$1.234.567")
        self.assertEqual(a.num(1234.5, 2), "1.234,50")
        self.assertEqual(a.pct(0.0123, 1), "1,2 %")

    def test_texto_de_hallazgos_usa_coma_decimal(self):
        h = [x for x in a.auditar(df_de([fila(dia=d, clics=10, conversiones=0, impresiones=20000) for d in range(5)])) if x["regla"] == "CTR muy bajo"]
        self.assertIn("0,05 %", h[0]["detalle"])
        self.assertIn("100.000 impresiones", h[0]["detalle"])

    def test_informe_escapa_html(self):
        d = df_de([fila(campania="<script>x</script>")])
        self.assertNotIn("<script>x", a.informe_html(d, [], a.auditar(d)))

    def test_informe_se_genera_y_menciona_problemas_de_datos(self):
        d, p = a.limpiar(pd.DataFrame(datos.generar()))
        h = a.informe_html(d, p, a.auditar(d))
        self.assertIn("Auditoría de campañas", h)
        self.assertIn("se descartaron filas", h)


if __name__ == "__main__":
    unittest.main(verbosity=2)
