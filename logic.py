"""
Lógica de la plataforma de seguimiento TIP AA.

Todo lo que no es interfaz vive acá: lectura de la planilla, clasificación de
notas por rango, estadísticas, gráficos y generación de reportes (PDF / Excel / ZIP).
Así se puede probar y reutilizar sin Streamlit.
"""
from __future__ import annotations

import datetime as dt
import io
import math
import os
import re
import unicodedata
import zipfile
from collections import Counter

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
from matplotlib.patches import Patch  # noqa: E402

# ---------------------------------------------------------------------------
# Configuración
# ---------------------------------------------------------------------------
SIN_SECCION = "Sin sección TIP"

# Programas TIP AA (código -> nombre). AA1002M, AA1100F y AA1102M son programas paralelos.
PROGRAMAS = {
    "AA0001M": "Matemática I",
    "AA0002M": "Matemática II",
    "AA1002M": "Cálculo Diferencial e Integral",
    "AA1100F": "Intro a la Física Moderna",
    "AA1102M": "Álgebra Lineal",
}

# Códigos mal escritos en la planilla que se corrigen al leer, p. ej. {"AA0O01M": "AA0001M"}.
ALIAS_CODIGOS: dict[str, str] = {}


def nombre_programa(cod_o_seccion: str) -> str:
    return PROGRAMAS.get((cod_o_seccion or "").split("-")[0], "")

# Rangos de rendimiento: [lo, hi)
RANGOS = [
    {"nombre": "Bajo 3.0", "lo": -math.inf, "hi": 3.0, "color": "#d03b3b", "tinte": "#f6d8d8", "tinta": "#ffffff"},
    {"nombre": "3.0 a <3.5", "lo": 3.0, "hi": 3.5, "color": "#ec835a", "tinte": "#fbe2d6", "tinta": "#0b0b0b"},
    {"nombre": "3.5 a <4.0", "lo": 3.5, "hi": 4.0, "color": "#fab219", "tinte": "#feefcb", "tinta": "#0b0b0b"},
    {"nombre": "4.0 o más", "lo": 4.0, "hi": math.inf, "color": "#0ca30c", "tinte": "#d6efd6", "tinta": "#ffffff"},
]
SIN_NOTA = {"nombre": "Sin nota", "color": "#9a9890", "tinte": "#ecebe8", "tinta": "#0b0b0b"}
ORDEN_RANGOS = [r["nombre"] for r in RANGOS] + [SIN_NOTA["nombre"]]
INFO_RANGO = {r["nombre"]: r for r in RANGOS} | {SIN_NOTA["nombre"]: SIN_NOTA}

# Semáforo: lo que ven los tutores en sus reportes (sin mostrar intervalos de nota).
SEMAFORO = [
    {"nombre": "Rojo", "lo": -math.inf, "hi": 3.0, "color": "#d03b3b", "tinte": "#f6d8d8", "tinta": "#ffffff"},
    {"nombre": "Naranjo", "lo": 3.0, "hi": 4.0, "color": "#f08a24", "tinte": "#fde4cc", "tinta": "#0b0b0b"},
    {"nombre": "Verde", "lo": 4.0, "hi": math.inf, "color": "#0ca30c", "tinte": "#d6efd6", "tinta": "#ffffff"},
]
SIN_REGISTRO = {"nombre": "Sin registro", "color": "#9a9890", "tinte": "#ecebe8", "tinta": "#0b0b0b"}
ORDEN_SEMAFORO = [r["nombre"] for r in SEMAFORO] + [SIN_REGISTRO["nombre"]]
INFO_SEMAFORO = {r["nombre"]: r for r in SEMAFORO} | {SIN_REGISTRO["nombre"]: SIN_REGISTRO}

# Confidencialidad: si es False, los reportes exportables (PDF y Excel) muestran solo el
# rango de cada estudiante, nunca su nota exacta. La app (vista de coordinación) sí la muestra.
MOSTRAR_NOTA_EN_REPORTES = False

TINTA = "#0b0b0b"
TINTA_2 = "#52514e"
GRILLA = "#e4e3df"
SUPERFICIE = "#fcfcfb"


# ---------------------------------------------------------------------------
# Utilidades
# ---------------------------------------------------------------------------
def _vacio(v) -> bool:
    if v is None:
        return True
    if isinstance(v, float) and math.isnan(v):
        return True
    if isinstance(v, str) and v.strip() == "":
        return True
    try:
        return bool(pd.isna(v))
    except (TypeError, ValueError):
        return False


def limpiar(v) -> str:
    """Texto de celda sin saltos de línea ni espacios repetidos."""
    if _vacio(v):
        return ""
    return re.sub(r"\s+", " ", str(v)).strip()


def slug(texto: str) -> str:
    t = unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode()
    t = re.sub(r"[^A-Za-z0-9]+", "_", t).strip("_")
    return t or "reporte"


def clave_orden_seccion(sec: str):
    """AA0001M-2 < AA0001M-10 (orden natural); 'Sin sección' al final."""
    m = re.match(r"([A-Z]{2}\d{4}[A-Z])-(\d+)$", sec or "")
    if m:
        return (0, m.group(1), int(m.group(2)))
    return (1, sec or "", 0)


def parse_secciones(texto) -> list[str]:
    """'AA0001M-5; AA1002M-2' -> ['AA0001M-5', 'AA0002M-2']"""
    s = limpiar(texto).upper()
    out = []
    for cod, num in re.findall(r"([A-Z]{2}\d{4}[A-Z])\s*-\s*(\d+)", s):
        cod = ALIAS_CODIGOS.get(cod, cod)
        sec = f"{cod}-{int(num)}"
        if sec not in out:
            out.append(sec)
    return out


def parse_nota(v) -> tuple[float, str]:
    """Devuelve (nota numérica o NaN, observación de texto)."""
    if _vacio(v):
        return (math.nan, "")
    x = None
    if isinstance(v, (int, float, np.integer, np.floating)) and not isinstance(v, bool):
        x = float(v)
    else:
        s = limpiar(v)
        try:
            x = float(s.replace(",", "."))
        except ValueError:
            return (math.nan, s)
    if 1.0 <= x <= 7.0:
        return (x, "")
    return (math.nan, "Fuera de escala")


def semaforo_de(nota: float) -> str:
    if nota is None or (isinstance(nota, float) and math.isnan(nota)):
        return SIN_REGISTRO["nombre"]
    for r in SEMAFORO:
        if r["lo"] <= nota < r["hi"]:
            return r["nombre"]
    return SIN_REGISTRO["nombre"]


def rango_de(nota: float) -> str:
    if nota is None or (isinstance(nota, float) and math.isnan(nota)):
        return SIN_NOTA["nombre"]
    for r in RANGOS:
        if r["lo"] <= nota < r["hi"]:
            return r["nombre"]
    return SIN_NOTA["nombre"]


# ---------------------------------------------------------------------------
# Lectura de la planilla
# ---------------------------------------------------------------------------
class Planilla:
    """Planilla leída: `alumnos` (una fila por estudiante) + `evaluaciones` (catálogo)."""

    def __init__(self, alumnos: pd.DataFrame, evaluaciones: pd.DataFrame, avisos: list[str]):
        self.alumnos = alumnos
        self.evaluaciones = evaluaciones
        self.avisos = avisos


def _buscar_col(nombres: dict[int, str], *patrones: str) -> int | None:
    for patron in patrones:
        for j, n in nombres.items():
            if re.search(patron, n, flags=re.I):
                return j
    return None


def leer_planilla(fuente) -> Planilla:
    raw = pd.read_excel(fuente, header=None, sheet_name=0, dtype=object)
    if raw.empty:
        raise ValueError("La planilla está vacía.")

    # Fila de encabezado: la que contiene 'RUT'
    fila_h = None
    for i in range(min(15, len(raw))):
        if any(limpiar(v).upper() == "RUT" for v in raw.iloc[i]):
            fila_h = i
            break
    if fila_h is None:
        raise ValueError("No encontré la fila de encabezados (con la columna 'RUT').")

    h1 = [limpiar(v) for v in raw.iloc[fila_h]]
    h2 = [limpiar(v) for v in raw.iloc[fila_h + 1]] if fila_h + 1 < len(raw) else [""] * len(h1)

    info: dict[int, str] = {}
    evals: list[dict] = []
    usados: Counter = Counter()
    grupo = None
    for j in range(raw.shape[1]):
        if h1[j].upper().startswith("NOTAS"):
            grupo = re.sub(r"^NOTAS\s*", "", h1[j], flags=re.I).strip() or h1[j]
        if grupo is None:
            nombre = h1[j] or f"Columna {j + 1}"
            usados[nombre] += 1
            if usados[nombre] > 1:
                nombre = f"{nombre} ({usados[nombre]})"
            info[j] = nombre
        elif h2[j]:
            evals.append({"col": j, "asignatura": grupo, "evaluacion": h2[j]})

    if not evals:
        raise ValueError("No encontré columnas de notas (encabezados que comiencen con 'NOTAS').")

    j_rut = _buscar_col(info, r"^RUT$")
    j_nom = _buscar_col(info, r"^Nombre")
    j_tip = _buscar_col(info, r"TIP AA \(cod\)", r"TIP.*cod")
    j_cat = _buscar_col(info, r"FCFM DIM \(cod\)", r"DIM \(cod\)")
    if j_tip is None:
        raise ValueError("No encontré la columna 'Sección asignada TIP AA (cod)'.")

    datos = raw.iloc[fila_h + 2:].copy()
    datos = datos[~datos.apply(lambda r: all(_vacio(v) for v in r), axis=1)]

    alumnos = pd.DataFrame(index=datos.index)
    alumnos["Fila"] = datos.index + 1  # fila en Excel (1-indexada)
    alumnos["RUT"] = datos[j_rut].map(limpiar) if j_rut is not None else ""
    alumnos["Nombre"] = datos[j_nom].map(limpiar) if j_nom is not None else ""
    alumnos["Sección TIP (planilla)"] = datos[j_tip].map(limpiar)
    alumnos["Secciones"] = datos[j_tip].map(parse_secciones)
    alumnos["Sección cátedra"] = datos[j_cat].map(limpiar) if j_cat is not None else ""

    filas_cat = []
    claves_usadas: Counter = Counter()
    for e in evals:
        clave = f"{e['asignatura']} · {e['evaluacion']}"
        claves_usadas[clave] += 1
        if claves_usadas[clave] > 1:
            clave = f"{clave} ({claves_usadas[clave]})"
        col = datos[e["col"]]
        notas = col.map(lambda v: parse_nota(v)[0])
        alumnos[clave] = col
        filas_cat.append({
            "clave": clave,
            "asignatura": e["asignatura"],
            "evaluacion": e["evaluacion"],
            "auxiliar": bool(re.search(r"c[oó]mo va|objetivo", e["evaluacion"], flags=re.I)),
            "n_registros": int((~col.map(_vacio)).sum()),
            "n_notas": int(notas.notna().sum()),
        })
    evaluaciones = pd.DataFrame(filas_cat)

    avisos = []
    sin_tip = alumnos[alumnos["Secciones"].map(len) == 0]
    if len(sin_tip):
        valores = sorted({v for v in sin_tip["Sección TIP (planilla)"] if v})
        avisos.append(
            f"{len(sin_tip)} fila(s) sin un código de sección TIP reconocible "
            f"(valores: {', '.join(valores) or 'vacío'}). Aparecen como '{SIN_SECCION}'."
        )
    if ALIAS_CODIGOS:
        corregidas = alumnos["Sección TIP (planilla)"].str.upper().str.contains(
            "|".join(map(re.escape, ALIAS_CODIGOS)), regex=True)
    else:
        corregidas = pd.Series(False, index=alumnos.index)
    if corregidas.any():
        pares = ", ".join(f"{a}→{b}" for a, b in ALIAS_CODIGOS.items())
        avisos.append(f"Se corrigieron códigos en {int(corregidas.sum())} fila(s): {pares}.")

    return Planilla(alumnos.reset_index(drop=True), evaluaciones, avisos)


# ---------------------------------------------------------------------------
# Datos de una evaluación
# ---------------------------------------------------------------------------
def datos_evaluacion(pl: Planilla, clave: str, programas: list[str] | None = None) -> pd.DataFrame:
    """Una fila por estudiante que tiene registro (nota o texto) en la evaluación."""
    a = pl.alumnos
    sub = a[~a[clave].map(_vacio)].copy()
    parsed = sub[clave].map(parse_nota)
    sub["Nota"] = [p[0] for p in parsed]
    sub["Observación"] = [p[1] for p in parsed]
    sub["Rango"] = sub["Nota"].map(rango_de)
    sub["Semáforo"] = sub["Nota"].map(semaforo_de)
    if programas is not None:
        sub["Secciones"] = sub["Secciones"].map(
            lambda ss: [s for s in ss if s.split("-")[0] in programas]
        )
        sub = sub[(sub["Secciones"].map(len) > 0) | (SIN_SECCION in programas)]
    cols = ["Fila", "RUT", "Nombre", "Secciones", "Sección TIP (planilla)", "Sección cátedra",
            "Nota", "Observación", "Rango", "Semáforo"]
    return sub[cols].reset_index(drop=True)


def por_seccion(df: pd.DataFrame) -> pd.DataFrame:
    """Expande: un estudiante en 2 secciones aparece en ambas."""
    d = df.copy()
    d["Sección"] = d["Secciones"].map(lambda ss: ss if ss else [SIN_SECCION])
    d = d.explode("Sección")
    d["Otras secciones TIP"] = [
        ", ".join(s for s in ss if s != sec) for ss, sec in zip(d["Secciones"], d["Sección"])
    ]
    return d.reset_index(drop=True)


def ordenar_alumnos(df: pd.DataFrame, por_nota: bool = True, semaforo: bool = False) -> pd.DataFrame:
    """Por rango o semáforo (peor primero). Dentro de cada grupo, por nota o, si se ocultan
    notas, por fila (así el orden tampoco revela quién tuvo más o menos nota)."""
    d = df.copy()
    if semaforo:
        d["_r"] = d["Semáforo"].map(ORDEN_SEMAFORO.index)
    else:
        d["_r"] = d["Rango"].map(ORDEN_RANGOS.index)
    orden = ["_r", "Nota", "Fila"] if por_nota else ["_r", "Fila"]
    d = d.sort_values(orden, na_position="last").drop(columns="_r")
    return d.reset_index(drop=True)


def secciones_presentes(df_long: pd.DataFrame) -> list[str]:
    return sorted(df_long["Sección"].unique(), key=clave_orden_seccion)


def programas_presentes(pl: Planilla) -> list[str]:
    progs = {s.split("-")[0] for ss in pl.alumnos["Secciones"] for s in ss}
    return sorted(progs) + [SIN_SECCION]


# ---------------------------------------------------------------------------
# Estadísticas
# ---------------------------------------------------------------------------
def estadisticas(df: pd.DataFrame) -> dict:
    notas = df["Nota"].dropna().astype(float)
    n = len(notas)
    conteos = {r: int((df["Rango"] == r).sum()) for r in ORDEN_RANGOS}
    sem = {r: int((df["Semáforo"] == r).sum()) for r in ORDEN_SEMAFORO}
    n_aprob = conteos["4.0 o más"]
    return {
        "n_aprob": n_aprob,
        "n_bajo4": n - n_aprob,
        "pct_bajo4": 100.0 * (n - n_aprob) / n if n else math.nan,
        "semaforo": sem,
        "n_registros": len(df),
        "n_notas": n,
        "n_sin_nota": len(df) - n,
        "media": float(notas.mean()) if n else math.nan,
        "mediana": float(notas.median()) if n else math.nan,
        "desv": float(notas.std(ddof=1)) if n > 1 else math.nan,
        "min": float(notas.min()) if n else math.nan,
        "max": float(notas.max()) if n else math.nan,
        "pct_aprob": 100.0 * conteos["4.0 o más"] / n if n else math.nan,
        "conteos": conteos,
    }


def tutores_dict(tutores: pd.DataFrame) -> dict[str, dict]:
    out = {}
    if tutores is None or tutores.empty:
        return out
    for _, r in tutores.iterrows():
        sec = parse_secciones(r.get("Sección", ""))
        if not sec:
            continue
        out[sec[0]] = {"tutor": limpiar(r.get("Tutor/a", "")), "horario": limpiar(r.get("Horario", ""))}
    return out


def nombre_tutor(sec: str, tut: dict) -> str:
    if sec == SIN_SECCION:
        return "—"
    return tut.get(sec, {}).get("tutor") or "(sin tutor/a asignado)"


def resumen_secciones(df_long: pd.DataFrame, tut: dict) -> pd.DataFrame:
    filas = []
    for sec in secciones_presentes(df_long):
        g = df_long[df_long["Sección"] == sec]
        e = estadisticas(g)
        fila = {
            "Sección": sec,
            "Tutor/a": nombre_tutor(sec, tut),
            "Con nota": e["n_notas"],
            "Sin nota": e["n_sin_nota"],
            "Media": e["media"],
            "Mediana": e["mediana"],
            "Mín": e["min"],
            "Máx": e["max"],
        }
        for r in RANGOS:
            fila[r["nombre"]] = e["conteos"][r["nombre"]]
        fila["% 4.0 o más"] = e["pct_aprob"]
        filas.append(fila)
    return pd.DataFrame(filas)


def resumen_reporte(df_long: pd.DataFrame, tut: dict) -> pd.DataFrame:
    """Resumen por sección para reportes: sin mín/máx/desv; dos grupos (4.0 o más / bajo 4.0)
    y conteo por color del semáforo."""
    filas = []
    for sec in secciones_presentes(df_long):
        g = df_long[df_long["Sección"] == sec]
        e = estadisticas(g)
        fila = {
            "Sección": sec,
            "Tutor/a": nombre_tutor(sec, tut),
            "Con nota": e["n_notas"],
            "Sin nota": e["n_sin_nota"],
            "Media": e["media"],
            "Mediana": e["mediana"],
            "N° 4.0 o más": e["n_aprob"],
            "% 4.0 o más": e["pct_aprob"],
            "N° bajo 4.0": e["n_bajo4"],
            "% bajo 4.0": e["pct_bajo4"],
        }
        for r in SEMAFORO:
            fila[r["nombre"]] = e["semaforo"][r["nombre"]]
        filas.append(fila)
    return pd.DataFrame(filas)


def n_pct(n: int, pct: float) -> str:
    return "—" if pct is None or (isinstance(pct, float) and math.isnan(pct)) else f"{n}  ({pct:.0f}%)"


def fmt(x, dec=1) -> str:
    if x is None or (isinstance(x, float) and math.isnan(x)):
        return "—"
    return f"{x:.{dec}f}"


# ---------------------------------------------------------------------------
# Gráficos (matplotlib, usados en la app y en los PDF)
# ---------------------------------------------------------------------------
BINS = np.arange(1.0, 7.01, 0.5)


def _estilo_ejes(ax):
    ax.set_facecolor(SUPERFICIE)
    for s in ("top", "right", "left"):
        ax.spines[s].set_visible(False)
    ax.spines["bottom"].set_color(TINTA_2)
    ax.tick_params(colors=TINTA_2, labelsize=8, length=0)
    ax.yaxis.grid(True, color=GRILLA, linewidth=0.8)
    ax.set_axisbelow(True)


COLOR_NEUTRO = "#2a78d6"


def _esquema(semaforo):
    """semaforo: False -> 4 rangos (app); True -> semáforo; "neutro" -> un solo color (reportes)."""
    if semaforo == "neutro":
        neutro = {"nombre": "Estudiantes", "color": COLOR_NEUTRO}
        return [neutro], {"Estudiantes": neutro}, (lambda _n: "Estudiantes")
    if semaforo:
        return SEMAFORO, INFO_SEMAFORO, semaforo_de
    return RANGOS, INFO_RANGO, rango_de


def dibujar_histograma(ax, notas, titulo: str, semaforo: bool = False):
    notas = pd.Series(notas, dtype=float).dropna()
    _estilo_ejes(ax)
    ax.set_xlim(1.0, 7.0)
    ax.set_xticks(np.arange(1, 7.01, 0.5))
    ax.set_xticklabels([f"{x:.1f}" if x == int(x) else "" for x in np.arange(1, 7.01, 0.5)])
    ax.set_xlabel("Nota", fontsize=8, color=TINTA_2)
    ax.set_ylabel("N° estudiantes", fontsize=8, color=TINTA_2)
    if notas.empty:
        ax.set_title(titulo, fontsize=9.5, color=TINTA, loc="left", pad=6)
        ax.text(4, 0.5, "Sin notas registradas", ha="center", va="center", color=TINTA_2, fontsize=9)
        ax.set_yticks([])
        return
    cuentas, _ = np.histogram(notas, bins=BINS)
    _, info, clasif = _esquema(semaforo)
    colores = [info[clasif(b + 1e-9)]["color"] for b in BINS[:-1]]
    ax.bar(BINS[:-1] + 0.25, cuentas, width=0.44, color=colores, edgecolor="none")
    ax.set_ylim(0, max(cuentas.max(), 1) * 1.08)
    ax.yaxis.set_major_locator(matplotlib.ticker.MaxNLocator(integer=True, nbins=5))
    media, mediana = notas.mean(), notas.median()
    ax.axvline(media, color=TINTA, linewidth=1.4)
    ax.axvline(mediana, color=TINTA, linewidth=1.4, linestyle=(0, (3, 2)))
    if semaforo == "neutro":
        ax.axvline(4.0, color=TINTA_2, linewidth=1.0, linestyle=(0, (1, 2)))
    ax.set_title(f"{titulo}   ·   media {media:.2f}   ·   mediana {mediana:.2f}   ·   n = {len(notas)}",
                 fontsize=9.5, color=TINTA, loc="left", pad=6)


def _leyenda_hist(fig, x0: float, semaforo: bool = False):
    h = [] if semaforo == "neutro" else [Patch(color=r["color"], label=r["nombre"]) for r in _esquema(semaforo)[0]]
    if semaforo == "neutro":
        h.append(plt.Line2D([0], [0], color=TINTA_2, lw=1.0, ls=(0, (1, 2)), label="Nota 4.0"))
    h += [
        plt.Line2D([0], [0], color=TINTA, lw=1.4, label="Media"),
        plt.Line2D([0], [0], color=TINTA, lw=1.4, ls=(0, (3, 2)), label="Mediana"),
    ]
    fig.legend(handles=h, fontsize=7.5, frameon=False, ncol=6, loc="upper left",
               bbox_to_anchor=(x0 - 0.01, 0.995), handlelength=1.4, handleheight=0.8,
               columnspacing=1.4, labelcolor=TINTA_2)


def fig_histograma(notas, titulo: str, ancho=7.5, alto=3.0, semaforo: bool = False):
    fig, ax = plt.subplots(figsize=(ancho, alto), dpi=150)
    fig.patch.set_facecolor(SUPERFICIE)
    fig.subplots_adjust(left=0.08, right=0.98, bottom=0.17, top=0.78)
    dibujar_histograma(ax, notas, titulo, semaforo)
    _leyenda_hist(fig, 0.08, semaforo)
    return fig


def fig_histogramas_comparados(notas_sec, notas_gen, titulo_sec: str, titulo_gen: str,
                               semaforo: bool = False):
    fig, axes = plt.subplots(2, 1, figsize=(8.6, 5.4), dpi=150, sharex=True)
    fig.patch.set_facecolor(SUPERFICIE)
    fig.subplots_adjust(left=0.07, right=0.99, bottom=0.09, top=0.88, hspace=0.32)
    dibujar_histograma(axes[0], notas_sec, titulo_sec, semaforo)
    dibujar_histograma(axes[1], notas_gen, titulo_gen, semaforo)
    axes[0].tick_params(labelbottom=True)
    axes[0].set_xlabel("")
    _leyenda_hist(fig, 0.07, semaforo)
    return fig


def fig_rangos_por_seccion(resumen: pd.DataFrame, semaforo: bool = False):
    """Barras horizontales apiladas: N° de estudiantes por rango (o color) en cada sección."""
    grupos = _esquema(semaforo)[0]
    cats = [r["nombre"] for r in grupos]
    d = resumen.copy()
    if d.empty:
        fig, ax = plt.subplots(figsize=(7, 1.5), dpi=150)
        ax.axis("off")
        ax.text(0.5, 0.5, "Sin datos", ha="center")
        return fig
    etiquetas = [
        f"{s}  ·  {t}" if t and t != "—" else s for s, t in zip(d["Sección"], d["Tutor/a"])
    ]
    alto = 0.9 + 0.36 * len(d)
    fig, ax = plt.subplots(figsize=(8.6, alto), dpi=150)
    fig.patch.set_facecolor(SUPERFICIE)
    _estilo_ejes(ax)
    ax.yaxis.grid(False)
    ax.xaxis.grid(True, color=GRILLA, linewidth=0.8)
    ax.spines["bottom"].set_visible(False)
    y = np.arange(len(d))[::-1]
    izq = np.zeros(len(d))
    total_max = max(d[cats].sum(axis=1).max(), 1)
    for r in grupos:
        v = d[r["nombre"]].to_numpy(dtype=float)
        ax.barh(y, v, left=izq, height=0.62, color=r["color"], edgecolor=SUPERFICIE, linewidth=1.2,
                label=r["nombre"])
        for yi, li, vi in zip(y, izq, v):
            if vi > 0 and vi >= total_max * 0.045:
                ax.text(li + vi / 2, yi, f"{int(vi)}", ha="center", va="center", fontsize=7, color=r["tinta"])
        izq += v
    ax.set_yticks(y)
    ax.set_yticklabels(etiquetas, fontsize=8, color=TINTA)
    ax.set_xlabel("N° estudiantes con nota", fontsize=8, color=TINTA_2)
    ax.xaxis.set_major_locator(matplotlib.ticker.MaxNLocator(integer=True))
    ax.legend(fontsize=7, frameon=False, ncol=4, loc="lower left", bbox_to_anchor=(0, 1.0),
              handlelength=1.0, handleheight=0.8, labelcolor=TINTA_2)
    fig.tight_layout()
    return fig


def fig_a_png(fig) -> bytes:
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=200, facecolor=fig.get_facecolor())
    plt.close(fig)
    return buf.getvalue()


# ---------------------------------------------------------------------------
# Guía del semáforo para tutores
# ---------------------------------------------------------------------------
GUIA_INTRO = (
    "El semáforo resume cómo le fue a cada estudiante en esta evaluación, como una referencia para "
    "orientar la atención que ya das en tus sesiones. No es una calificación ni una etiqueta, ni "
    "tampoco una lista de tareas adicionales: refleja un momento puntual y puede cambiar en la "
    "siguiente evaluación. Úsalo con discreción: no lo compartas con el grupo, no menciones el color "
    "a los estudiantes y no los compares entre sí."
)

GUIA_SEMAFORO = [
    {
        "color": "Rojo",
        "titulo": "Más acompañamiento",
        "lectura": "Hay contenidos que aún no se consolidan. Son quienes más se benefician de un "
                   "acompañamiento cercano, dentro de la dinámica habitual de la tutoría.",
        "acciones": [
            "Acércate de forma natural durante la sesión para preguntarle cómo le fue y qué le costó "
            "más, sin mencionar el color ni la nota.",
            "Cuando haya espacio, dale prioridad a sus preguntas y propón ejercicios guiados, paso a "
            "paso, partiendo desde lo más básico del tema.",
            "Fíjate en dónde podría estar la dificultad (contenidos previos, comprensión de "
            "enunciados, manejo del tiempo u organización del estudio): con identificarlo ya puedes "
            "orientar mejor tu ayuda.",
            "Si notas dificultades que van más allá de lo académico (carga, salud, situación "
            "personal), avisa a coordinación. No tienes que resolverlo tú: coordinación le conecta "
            "con los apoyos de la facultad.",
        ],
    },
    {
        "color": "Naranjo",
        "titulo": "Cerca del objetivo",
        "lectura": "Tiene una base, pero con brechas puntuales que, trabajadas a tiempo, pueden "
                   "marcar la diferencia en la próxima evaluación.",
        "acciones": [
            "Aprovecha la revisión de ejercicios en la sesión para darle retroalimentación sobre sus "
            "errores más frecuentes, distinguiendo los conceptuales de los de cálculo o descuido.",
            "Al proponer ejercicios, incluye algunos de los temas donde tuvo más problemas, "
            "idealmente del estilo de los controles.",
            "Mantente atento/a a cómo avanza en las sesiones: con un poco de apoyo focalizado puede "
            "dar el salto.",
            "Fomenta que trabaje en parejas o grupos pequeños con compañeros de la sección.",
        ],
    },
    {
        "color": "Verde",
        "titulo": "Buen avance",
        "lectura": "Logró los aprendizajes evaluados. El desafío es sostener el ritmo, porque los "
                   "contenidos que vienen suelen ser más exigentes.",
        "acciones": [
            "Reconoce su esfuerzo y mantén su motivación para las próximas evaluaciones.",
            "Ofrécele ejercicios de mayor dificultad o de preparación para los temas que vienen.",
            "Invítale (de forma voluntaria) a explicar ejercicios a sus compañeros: enseñar refuerza "
            "lo aprendido y fortalece el trabajo entre pares.",
            "Sigue considerándole en la sesión: un buen resultado no asegura el siguiente.",
        ],
    },
    {
        "color": "Sin registro",
        "titulo": "Sin nota en esta evaluación",
        "lectura": "No hay nota registrada (por ejemplo, inasistencia justificada o nota pendiente).",
        "acciones": [
            "Pregúntale de forma cercana cómo está y si rendirá una evaluación recuperativa.",
            "Si lo necesita, oriéntale sobre los contenidos de la evaluación que no rindió.",
        ],
    },
]

GUIA_CIERRE = (
    "Esto no es una carga extra. La idea es que el semáforo te ayude a orientar, de forma natural, "
    "la atención que ya das en tus sesiones: no necesitas atender a todos en una misma sesión ni "
    "preparar material adicional; basta con tenerlo presente al circular por la sala, al decidir a "
    "quién acercarte o qué ejercicios proponer. Si una parte importante de la sección queda en rojo "
    "o naranjo, probablemente hay una brecha común de contenidos: puedes reforzar ese tema con todo "
    "el grupo y comentarlo con coordinación, que te apoyará."
)


# ---------------------------------------------------------------------------
# PDF (reportlab)
# ---------------------------------------------------------------------------
MM = 2.8346


def _fuentes_pdf():
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont

    if "DejaVu" in pdfmetrics.getRegisteredFontNames():
        return "DejaVu", "DejaVu-Bold"
    base = os.path.join(matplotlib.get_data_path(), "fonts", "ttf")
    try:
        pdfmetrics.registerFont(TTFont("DejaVu", os.path.join(base, "DejaVuSans.ttf")))
        pdfmetrics.registerFont(TTFont("DejaVu-Bold", os.path.join(base, "DejaVuSans-Bold.ttf")))
        from reportlab.lib.fonts import addMapping

        addMapping("DejaVu", 0, 0, "DejaVu")
        addMapping("DejaVu", 1, 0, "DejaVu-Bold")
        return "DejaVu", "DejaVu-Bold"
    except Exception:  # pragma: no cover
        return "Helvetica", "Helvetica-Bold"


def _estilos():
    from reportlab.lib.styles import ParagraphStyle

    f, fb = _fuentes_pdf()
    return {
        "titulo": ParagraphStyle("t", fontName=fb, fontSize=15, leading=19, textColor=TINTA),
        "sub": ParagraphStyle("s", fontName=f, fontSize=9.5, leading=13, textColor=TINTA_2),
        "h2": ParagraphStyle("h2", fontName=fb, fontSize=11, leading=14, textColor=TINTA,
                             spaceBefore=10, spaceAfter=5),
        "cuerpo": ParagraphStyle("c", fontName=f, fontSize=8.5, leading=11.5, textColor=TINTA),
        "cuerpo_b": ParagraphStyle("cb", fontName=fb, fontSize=9, leading=12, textColor=TINTA),
        "vineta": ParagraphStyle("v", fontName=f, fontSize=8.3, leading=11, textColor=TINTA,
                                 leftIndent=9, bulletIndent=0, spaceAfter=1.5),
        "celda": ParagraphStyle("cel", fontName=f, fontSize=7.5, leading=9, textColor=TINTA),
        "celda_b": ParagraphStyle("celb", fontName=fb, fontSize=7.5, leading=9, textColor=TINTA),
        "nota": ParagraphStyle("n", fontName=f, fontSize=7.5, leading=10, textColor=TINTA_2),
        "_f": f, "_fb": fb,
    }


def _estilo_tabla_base(st):
    from reportlab.lib import colors

    return [
        ("FONT", (0, 0), (-1, -1), st["_f"], 8.5),
        ("TEXTCOLOR", (0, 0), (-1, -1), colors.HexColor(TINTA)),
        ("LINEBELOW", (0, 0), (-1, 0), 0.6, colors.HexColor(TINTA_2)),
        ("LINEBELOW", (0, 1), (-1, -1), 0.3, colors.HexColor(GRILLA)),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 3), ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
    ]


# Resumen estadístico: sin mínimo, máximo ni desviación; dos grupos (4.0 o más / bajo 4.0).
FILAS_STATS = [
    ("Estudiantes con nota", lambda e: str(e["n_notas"])),
    ("Sin nota registrada (J, s/n, etc.)", lambda e: str(e["n_sin_nota"])),
    ("Media", lambda e: fmt(e["media"], 2)),
    ("Mediana", lambda e: fmt(e["mediana"], 2)),
    ("Con 4.0 o más", lambda e: n_pct(e["n_aprob"], e["pct_aprob"])),
    ("Bajo 4.0", lambda e: n_pct(e["n_bajo4"], e["pct_bajo4"])),
]


def _tabla_stats(st, columnas: list[tuple[str, dict]]):
    from reportlab.lib import colors
    from reportlab.platypus import Table, TableStyle

    data = [[""] + [c[0] for c in columnas]]
    for etiqueta, fn in FILAS_STATS:
        data.append([etiqueta] + [fn(c[1]) for c in columnas])
    t = Table(data, hAlign="LEFT", colWidths=[58 * MM] + [40 * MM] * len(columnas))
    est = _estilo_tabla_base(st) + [
        ("FONT", (0, 0), (-1, 0), st["_fb"], 8.5),
        ("ALIGN", (1, 0), (-1, -1), "RIGHT"),
        # separa visualmente el bloque de los dos grupos
        ("LINEABOVE", (0, 5), (-1, 5), 0.6, colors.HexColor(TINTA_2)),
        ("FONT", (0, 5), (-1, 6), st["_fb"], 8.5),
    ]
    t.setStyle(TableStyle(est))
    return t


def _celda_semaforo(st, nombre: str):
    from reportlab.platypus import Paragraph

    c = INFO_SEMAFORO[nombre]["color"]
    return Paragraph(f'<font color="{c}">●</font>&nbsp;{nombre}', st["celda_b"])


def _tabla_alumnos(st, d: pd.DataFrame, con_seccion: bool = False):
    """Listado para reportes: semáforo (rojo/naranjo/verde) en vez de nota o intervalo."""
    from reportlab.lib import colors
    from reportlab.platypus import Paragraph, Table, TableStyle

    P = lambda t, s="celda": Paragraph(str(t) if t not in (None, "") else "—", st[s])  # noqa: E731
    con_nota = MOSTRAR_NOTA_EN_REPORTES
    col_secs = "Secciones TIP" if con_seccion else "Otras secc. TIP"
    if con_nota:
        enc = ["Fila", "RUT", "Nombre", "Nota", "Semáforo", "Obs.", col_secs, "Sección cátedra"]
        anchos = [11, 22, 34, 12, 26, 16, 30, 31]
    else:
        enc = ["Fila", "RUT", "Nombre", "Semáforo", "Obs.", col_secs, "Sección cátedra"]
        anchos = [11, 22, 46, 26, 16, 30, 31]
    c_sem = 4 if con_nota else 3
    data = [[P(e, "celda_b") for e in enc]]
    est = _estilo_tabla_base(st) + [
        ("TOPPADDING", (0, 0), (-1, -1), 2.5), ("BOTTOMPADDING", (0, 0), (-1, -1), 2.5),
        ("LEFTPADDING", (0, 0), (-1, -1), 3), ("RIGHTPADDING", (0, 0), (-1, -1), 3),
    ]
    for i, (_, r) in enumerate(d.iterrows(), start=1):
        secs = ", ".join(r["Secciones"]) if con_seccion else r.get("Otras secciones TIP", "")
        fila = [P(r["Fila"]), P(r["RUT"]), P(r["Nombre"])]
        if con_nota:
            fila.append(P(fmt(r["Nota"]), "celda_b"))
        fila += [_celda_semaforo(st, r["Semáforo"]), P(r["Observación"]), P(secs), P(r["Sección cátedra"])]
        data.append(fila)
        info = INFO_SEMAFORO[r["Semáforo"]]
        est.append(("BACKGROUND", (c_sem, i), (c_sem, i), colors.HexColor(info["tinte"])))
        est.append(("LINEBEFORE", (c_sem, i), (c_sem, i), 2.2, colors.HexColor(info["color"])))
    t = Table(data, colWidths=[a * MM for a in anchos], repeatRows=1, hAlign="LEFT")
    t.setStyle(TableStyle(est))
    return t


def _conteo_semaforo(st, e: dict):
    from reportlab.platypus import Paragraph

    partes = []
    for nombre in ORDEN_SEMAFORO:
        n = e["semaforo"][nombre]
        if nombre == SIN_REGISTRO["nombre"] and n == 0:
            continue
        c = INFO_SEMAFORO[nombre]["color"]
        partes.append(f'<font color="{c}">●</font> {nombre}: <b>{n}</b>')
    return Paragraph(" &nbsp;&nbsp;&nbsp; ".join(partes), st["cuerpo"])


def _guia_semaforo(st):
    """Sección final: ¿Cómo interpretar el semáforo?"""
    from reportlab.lib import colors
    from reportlab.platypus import KeepTogether, Paragraph, Spacer, Table, TableStyle

    h = [Paragraph("¿Cómo interpretar el semáforo?", st["h2"]), Paragraph(GUIA_INTRO, st["cuerpo"]),
         Spacer(1, 8)]
    for g in GUIA_SEMAFORO:
        info = INFO_SEMAFORO[g["color"]]
        cab = Paragraph(
            f'<font color="{info["color"]}">●</font>&nbsp; {g["color"]} · <font name="{st["_f"]}">'
            f'{g["titulo"]}</font>', st["cuerpo_b"])
        cuerpo = [Paragraph(g["lectura"], st["cuerpo"]), Spacer(1, 3)]
        cuerpo += [Paragraph(a, st["vineta"], bulletText="•") for a in g["acciones"]]
        t = Table([[cab], [cuerpo]], colWidths=[180 * MM], hAlign="LEFT")
        t.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (0, 0), colors.HexColor(info["tinte"])),
            ("LINEBEFORE", (0, 0), (0, -1), 3, colors.HexColor(info["color"])),
            ("LEFTPADDING", (0, 0), (-1, -1), 8), ("RIGHTPADDING", (0, 0), (-1, -1), 8),
            ("TOPPADDING", (0, 0), (-1, -1), 4), ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ]))
        h.append(KeepTogether([t, Spacer(1, 7)]))
    h.append(Spacer(1, 2))
    h.append(Paragraph(GUIA_CIERRE, st["cuerpo"]))
    return h


def _pie(canvas, doc, texto):
    from reportlab.lib import colors

    canvas.saveState()
    f, _ = _fuentes_pdf()
    canvas.setFont(f, 7)
    canvas.setFillColor(colors.HexColor(TINTA_2))
    canvas.drawString(doc.leftMargin, 10 * MM, texto + "  ·  Documento confidencial, uso exclusivo del equipo de tutorías")
    canvas.drawRightString(doc.pagesize[0] - doc.rightMargin, 10 * MM, f"Página {doc.page}")
    canvas.restoreState()


def _doc(buf, titulo):
    from reportlab.lib.pagesizes import A4
    from reportlab.platypus import SimpleDocTemplate

    return SimpleDocTemplate(buf, pagesize=A4, leftMargin=14 * MM, rightMargin=14 * MM,
                             topMargin=14 * MM, bottomMargin=18 * MM, title=titulo,
                             author="Coordinación Tutorías TIP AA")


def _img(png: bytes, ancho_mm: float):
    from PIL import Image as PILImage
    from reportlab.platypus import Image

    w, h = PILImage.open(io.BytesIO(png)).size
    return Image(io.BytesIO(png), width=ancho_mm * MM, height=ancho_mm * MM * h / w)


def pdf_seccion(df_long: pd.DataFrame, df_eval: pd.DataFrame, seccion: str, tut: dict,
                nombre_eval: str) -> bytes:
    from reportlab.platypus import KeepTogether, PageBreak, Paragraph, Spacer

    st = _estilos()
    g = ordenar_alumnos(df_long[df_long["Sección"] == seccion], por_nota=MOSTRAR_NOTA_EN_REPORTES,
                        semaforo=True)
    e_sec, e_gen = estadisticas(g), estadisticas(df_eval)
    tutor = nombre_tutor(seccion, tut)
    horario = tut.get(seccion, {}).get("horario", "")
    hoy = dt.date.today().strftime("%d-%m-%Y")

    buf = io.BytesIO()
    doc = _doc(buf, f"Reporte {nombre_eval} – {seccion}")
    h = [Paragraph(f"Reporte de rendimiento · {nombre_eval}", st["titulo"]), Spacer(1, 3)]
    prog = nombre_programa(seccion)
    linea = f"Sección <b>{seccion}</b>" + (f" ({prog})" if prog else "") + f" &nbsp;·&nbsp; Tutor/a: <b>{tutor}</b>"
    if horario:
        linea += f" &nbsp;·&nbsp; {horario}"
    h += [Paragraph(linea, st["sub"]), Paragraph(f"Generado el {hoy}", st["sub"]), Spacer(1, 8)]

    h.append(Paragraph("Resumen estadístico", st["h2"]))
    h.append(_tabla_stats(st, [("Sección", e_sec), ("General del control", e_gen)]))
    h.append(Spacer(1, 4))
    h.append(Paragraph(
        "Porcentajes calculados sobre los estudiantes con nota. «General del control» considera a "
        "todos los estudiantes TIP con registro en esta evaluación (cada uno una vez, aunque esté en "
        "más de una sección).", st["nota"]))

    png = fig_a_png(fig_histogramas_comparados(
        g["Nota"], df_eval["Nota"], f"Sección {seccion}", "General del control", semaforo="neutro"))
    h.append(KeepTogether([Paragraph("Distribución de notas", st["h2"]), _img(png, 170)]))

    h.append(Paragraph(f"Estudiantes de la sección ({len(g)})", st["h2"]))
    if len(g):
        h.append(_conteo_semaforo(st, e_sec))
        h.append(Spacer(1, 5))
        h.append(_tabla_alumnos(st, g))
        h.append(Spacer(1, 4))
        nota = "El significado de cada color y sugerencias de trabajo están en «¿Cómo interpretar el semáforo?», al final de este reporte."
        if (g["Otras secciones TIP"] != "").any():
            nota += " Los estudiantes con «Otras secc. TIP» también figuran en el reporte de esas secciones."
        h.append(Paragraph(nota, st["nota"]))
    else:
        h.append(Paragraph("No hay estudiantes de esta sección con registro en la evaluación.", st["cuerpo"]))

    h.append(PageBreak())
    h += _guia_semaforo(st)

    pie = f"{nombre_eval} · {seccion} · {tutor}"
    doc.build(h, onFirstPage=lambda c, d: _pie(c, d, pie), onLaterPages=lambda c, d: _pie(c, d, pie))
    return buf.getvalue()


def pdf_general(df_long: pd.DataFrame, df_eval: pd.DataFrame, tut: dict, nombre_eval: str) -> bytes:
    from reportlab.lib import colors
    from reportlab.platypus import KeepTogether, PageBreak, Paragraph, Spacer, Table, TableStyle

    st = _estilos()
    e_gen = estadisticas(df_eval)
    res = resumen_reporte(df_long, tut)
    hoy = dt.date.today().strftime("%d-%m-%Y")
    buf = io.BytesIO()
    doc = _doc(buf, f"Reporte general {nombre_eval}")
    h = [Paragraph(f"Reporte general · {nombre_eval}", st["titulo"]), Spacer(1, 3),
         Paragraph(f"Todas las secciones TIP AA &nbsp;·&nbsp; Generado el {hoy}", st["sub"]), Spacer(1, 8)]

    h.append(Paragraph("Resumen estadístico del control", st["h2"]))
    h.append(_tabla_stats(st, [("General", e_gen)]))
    h.append(Spacer(1, 5))
    h.append(_conteo_semaforo(st, e_gen))
    png = fig_a_png(fig_histograma(df_eval["Nota"], "General del control", semaforo="neutro"))
    h.append(KeepTogether([Paragraph("Distribución de notas", st["h2"]), _img(png, 175)]))

    h.append(Paragraph("Resumen por sección", st["h2"]))
    enc = ["Sección", "Tutor/a", "Con nota", "Sin nota", "Media", "Mediana",
           "4.0 o más", "Bajo 4.0", "Rojo", "Naranjo", "Verde"]
    data = [[Paragraph(x, st["celda_b"]) for x in enc]]
    for _, r in res.iterrows():
        data.append([r["Sección"], Paragraph(r["Tutor/a"], st["celda"]), r["Con nota"], r["Sin nota"],
                     fmt(r["Media"], 2), fmt(r["Mediana"], 2),
                     n_pct(r["N° 4.0 o más"], r["% 4.0 o más"]), n_pct(r["N° bajo 4.0"], r["% bajo 4.0"]),
                     r["Rojo"], r["Naranjo"], r["Verde"]])
    anchos = [20, 30, 12, 12, 12, 17, 19, 19, 13, 14, 13]
    t = Table(data, colWidths=[a * MM for a in anchos], repeatRows=1, hAlign="LEFT")
    est = _estilo_tabla_base(st) + [
        ("FONT", (0, 0), (-1, -1), st["_f"], 7.5),
        ("ALIGN", (2, 1), (-1, -1), "RIGHT"),
        ("LEFTPADDING", (0, 0), (-1, -1), 2.5), ("RIGHTPADDING", (0, 0), (-1, -1), 2.5),
    ]
    for k, r in enumerate(SEMAFORO):
        est.append(("BACKGROUND", (8 + k, 0), (8 + k, 0), colors.HexColor(r["tinte"])))
    t.setStyle(TableStyle(est))
    h.append(t)
    if len(res):
        png = fig_a_png(fig_rangos_por_seccion(res, semaforo=True))
        h.append(KeepTogether([Paragraph("Semáforo por sección", st["h2"]), _img(png, 182)]))
    h.append(Spacer(1, 4))
    h.append(Paragraph(
        "En el resumen por sección, un estudiante inscrito en dos secciones se cuenta en ambas.", st["nota"]))

    h.append(PageBreak())
    alumnos = ordenar_alumnos(df_eval, por_nota=MOSTRAR_NOTA_EN_REPORTES, semaforo=True)
    h.append(Paragraph(f"Listado completo ({len(alumnos)} estudiantes)", st["h2"]))
    h.append(_tabla_alumnos(st, alumnos, con_seccion=True))

    h.append(PageBreak())
    h += _guia_semaforo(st)

    pie = f"Reporte general · {nombre_eval}"
    doc.build(h, onFirstPage=lambda c, d: _pie(c, d, pie), onLaterPages=lambda c, d: _pie(c, d, pie))
    return buf.getvalue()


# ---------------------------------------------------------------------------
# Excel
# ---------------------------------------------------------------------------
def _tabla_export(d: pd.DataFrame, con_seccion: bool, mostrar_nota: bool = True,
                  mostrar_rango: bool = True) -> pd.DataFrame:
    out = pd.DataFrame({"Fila planilla": d["Fila"], "RUT": d["RUT"], "Nombre": d["Nombre"]})
    if mostrar_nota:
        out["Nota"] = d["Nota"]
    if mostrar_rango:
        out["Rango"] = d["Rango"]
    out["Semáforo"] = d["Semáforo"]
    out["Observación"] = d["Observación"]
    if con_seccion:
        out["Secciones TIP"] = d["Secciones"].map(", ".join)
    else:
        out["Otras secciones TIP"] = d["Otras secciones TIP"]
    out["Sección cátedra"] = d["Sección cátedra"]
    return out


def _linea_stats(e: dict) -> str:
    return (f"Con nota: {e['n_notas']} · Media {fmt(e['media'], 2)} · Mediana {fmt(e['mediana'], 2)} · "
            f"Con 4.0 o más: {n_pct(e['n_aprob'], e['pct_aprob'])} · Bajo 4.0: {n_pct(e['n_bajo4'], e['pct_bajo4'])}")


def excel_reporte(df_long: pd.DataFrame, df_eval: pd.DataFrame, tut: dict, nombre_eval: str,
                  secciones: list[str] | None = None) -> bytes:
    """Excel con hoja Resumen, General, una hoja por sección y la guía del semáforo."""
    secciones = secciones or secciones_presentes(df_long)
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="xlsxwriter") as xw:
        wb = xw.book
        f_tit = wb.add_format({"bold": True, "font_size": 13})
        f_sub = wb.add_format({"font_color": TINTA_2, "italic": True})
        f_head = wb.add_format({"bold": True, "bottom": 1, "text_wrap": True, "valign": "vcenter"})
        f_num = wb.add_format({"num_format": "0.0"})
        f_num2 = wb.add_format({"num_format": "0.00"})
        f_pct = wb.add_format({"num_format": '0"%"'})
        f_wrap = wb.add_format({"text_wrap": True, "valign": "top"})
        f_bold = wb.add_format({"bold": True})
        fmts_sem = {r: wb.add_format({"bg_color": INFO_SEMAFORO[r]["tinte"], "bold": True,
                                      "font_color": INFO_SEMAFORO[r]["color"] if r != "Sin registro" else TINTA_2})
                    for r in ORDEN_SEMAFORO}

        def hoja_alumnos(nombre_hoja, d, titulo, con_seccion):
            con_nota = MOSTRAR_NOTA_EN_REPORTES
            t = _tabla_export(ordenar_alumnos(d, por_nota=con_nota, semaforo=True), con_seccion,
                              mostrar_nota=con_nota, mostrar_rango=False)
            t.to_excel(xw, sheet_name=nombre_hoja, startrow=3, index=False)
            ws = xw.sheets[nombre_hoja]
            ws.write(0, 0, titulo, f_tit)
            ws.write(1, 0, _linea_stats(estadisticas(d)) + "  ·  Ver hoja «Cómo interpretar».", f_sub)
            for j, c in enumerate(t.columns):
                ws.write(3, j, c, f_head)
            ws.set_column(0, 0, 11)
            ws.set_column(1, 1, 13)
            ws.set_column(2, 2, 26)
            j_sem = list(t.columns).index("Semáforo")
            if con_nota:
                ws.set_column(3, 3, 7, f_num)
            ws.set_column(j_sem, j_sem, 13)
            ws.set_column(j_sem + 1, j_sem + 1, 14)
            ws.set_column(j_sem + 2, j_sem + 3, 22)
            letra = chr(ord("A") + j_sem)
            n = len(t)
            if n:
                for r in ORDEN_SEMAFORO:
                    ws.conditional_format(4, j_sem, 3 + n, j_sem, {
                        "type": "formula", "criteria": f'=${letra}5="{r}"', "format": fmts_sem[r]})
            ws.freeze_panes(4, 0)
            ws.autofilter(3, 0, 3 + max(n, 1), len(t.columns) - 1)

        # Resumen
        res = resumen_reporte(df_long, tut)
        res.to_excel(xw, sheet_name="Resumen", startrow=3, index=False)
        ws = xw.sheets["Resumen"]
        ws.write(0, 0, f"Resumen por sección · {nombre_eval}", f_tit)
        ws.write(1, 0, "General del control — " + _linea_stats(estadisticas(df_eval)), f_sub)
        for j, c in enumerate(res.columns):
            ws.write(3, j, c, f_head)
        ws.set_column(0, 0, 13)
        ws.set_column(1, 1, 24)
        ws.set_column(2, 3, 9)
        ws.set_column(4, 5, 9, f_num2)
        ws.set_column(6, 6, 10)
        ws.set_column(7, 7, 10, f_pct)
        ws.set_column(8, 8, 10)
        ws.set_column(9, 9, 10, f_pct)
        ws.set_column(10, 12, 9)
        for k, r in enumerate(SEMAFORO):
            ws.write(3, 10 + k, r["nombre"], wb.add_format(
                {"bold": True, "bottom": 1, "bg_color": r["tinte"], "valign": "vcenter"}))
        ws.set_row(3, 30)
        ws.insert_image(5 + len(res), 0, "hist.png", {
            "image_data": io.BytesIO(fig_a_png(fig_histograma(df_eval["Nota"], "Distribución general",
                                                              semaforo="neutro"))),
            "x_scale": 0.42, "y_scale": 0.42})

        hoja_alumnos("General", df_eval, f"Todos los estudiantes · {nombre_eval}", True)
        for sec in secciones:
            d = df_long[df_long["Sección"] == sec]
            hoja_alumnos(sec[:31], d, f"{sec} · {nombre_tutor(sec, tut)} · {nombre_eval}", False)

        # Guía del semáforo
        ws = wb.add_worksheet("Cómo interpretar")
        ws.set_column(0, 0, 16)
        ws.set_column(1, 1, 110, f_wrap)
        ws.write(0, 0, "¿Cómo interpretar el semáforo?", f_tit)
        ws.merge_range(1, 0, 1, 1, GUIA_INTRO, f_wrap)
        ws.set_row(1, 48)
        fila = 3
        for g in GUIA_SEMAFORO:
            ws.write(fila, 0, g["color"], fmts_sem[g["color"]])
            ws.write(fila, 1, f"{g['titulo']}. {g['lectura']}", f_bold)
            fila += 1
            for a in g["acciones"]:
                ws.write(fila, 1, "• " + a, f_wrap)
                fila += 1
            fila += 1
        ws.merge_range(fila, 0, fila, 1, GUIA_CIERRE, f_wrap)
        ws.set_row(fila, 48)
    return buf.getvalue()


def excel_seccion(df_long, df_eval, tut, nombre_eval, seccion) -> bytes:
    return excel_reporte(df_long, df_eval, tut, nombre_eval, secciones=[seccion])


# ---------------------------------------------------------------------------
# Paquete ZIP
# ---------------------------------------------------------------------------
def nombre_archivo(nombre_eval: str, seccion: str, tutor: str, ext: str) -> str:
    partes = [slug(nombre_eval), slug(seccion)]
    if tutor and not tutor.startswith("(") and tutor != "—":
        partes.append(slug(tutor))
    return "_".join(partes) + f".{ext}"


def zip_reportes(df_long, df_eval, tut, nombre_eval, secciones=None, progreso=None) -> bytes:
    secciones = secciones or secciones_presentes(df_long)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr(f"00_General_{slug(nombre_eval)}.pdf", pdf_general(df_long, df_eval, tut, nombre_eval))
        z.writestr(f"00_Resumen_{slug(nombre_eval)}.xlsx", excel_reporte(df_long, df_eval, tut, nombre_eval))
        for i, sec in enumerate(secciones, start=1):
            if progreso:
                progreso(i / len(secciones), sec)
            z.writestr(nombre_archivo(nombre_eval, sec, nombre_tutor(sec, tut), "pdf"),
                       pdf_seccion(df_long, df_eval, sec, tut, nombre_eval))
    return buf.getvalue()
