"""
Plataforma de seguimiento de rendimiento · Tutorías TIP AA
Ejecutar con:  streamlit run app.py
"""
from __future__ import annotations

import hashlib
import io
from pathlib import Path

import matplotlib.pyplot as plt
import pandas as pd
import streamlit as st

import logic as L

BASE = Path(__file__).parent
EJEMPLO = BASE / "planilla_ejemplo.xlsx"
TUTORES_CSV = BASE / "tutores.csv"

st.set_page_config(page_title="Seguimiento TIP AA", page_icon="📊", layout="wide")


# ---------------------------------------------------------------------------
# Carga y cálculos (cacheados)
# ---------------------------------------------------------------------------
@st.cache_data(show_spinner="Leyendo planilla…")
def cargar(contenido: bytes) -> L.Planilla:
    return L.leer_planilla(io.BytesIO(contenido))


@st.cache_data(show_spinner=False)
def preparar(contenido: bytes, clave: str, programas: tuple, tutores_csv: str):
    pl = cargar(contenido)
    df = L.datos_evaluacion(pl, clave, list(programas))
    lg = L.por_seccion(df)
    tut = L.tutores_dict(pd.read_csv(io.StringIO(tutores_csv), dtype=str).fillna(""))
    return df, lg, tut


@st.cache_data(show_spinner="Generando PDF…")
def pdf_seccion(contenido, clave, programas, tutores_csv, seccion, nombre_eval) -> bytes:
    df, lg, tut = preparar(contenido, clave, programas, tutores_csv)
    return L.pdf_seccion(lg, df, seccion, tut, nombre_eval)


@st.cache_data(show_spinner="Generando Excel…")
def excel_seccion(contenido, clave, programas, tutores_csv, seccion, nombre_eval) -> bytes:
    df, lg, tut = preparar(contenido, clave, programas, tutores_csv)
    return L.excel_seccion(lg, df, tut, nombre_eval, seccion)


@st.cache_data(show_spinner="Generando reporte general…")
def pdf_general(contenido, clave, programas, tutores_csv, nombre_eval) -> bytes:
    df, lg, tut = preparar(contenido, clave, programas, tutores_csv)
    return L.pdf_general(lg, df, tut, nombre_eval)


@st.cache_data(show_spinner="Generando Excel…")
def excel_general(contenido, clave, programas, tutores_csv, nombre_eval) -> bytes:
    df, lg, tut = preparar(contenido, clave, programas, tutores_csv)
    return L.excel_reporte(lg, df, tut, nombre_eval)


# ---------------------------------------------------------------------------
# Helpers de presentación
# ---------------------------------------------------------------------------
def chips(conteos: dict):
    partes = []
    for r, n in conteos.items():
        c = L.INFO_RANGO[r]["color"]
        partes.append(
            "<span style='display:inline-flex;align-items:center;gap:6px;margin:0 18px 6px 0;'>"
            f"<span style='width:12px;height:12px;border-radius:3px;background:{c};display:inline-block'></span>"
            f"{r}: <b>{n}</b></span>"
        )
    st.markdown("".join(partes), unsafe_allow_html=True)


def metricas(e: dict, ref: dict | None = None):
    c = st.columns(6)
    c[0].metric("Con nota", e["n_notas"], help=f"Sin nota (J, s/n, etc.): {e['n_sin_nota']}")
    delta = None
    if ref is not None and e["n_notas"] and ref["n_notas"]:
        delta = f"{e['media'] - ref['media']:+.2f} vs. general"
    c[1].metric("Media", L.fmt(e["media"], 2), delta=delta)
    c[2].metric("Mediana", L.fmt(e["mediana"], 2))
    c[3].metric("Mínimo", L.fmt(e["min"]))
    c[4].metric("Máximo", L.fmt(e["max"]))
    pct = "—" if e["n_notas"] == 0 else f"{e['pct_aprob']:.0f}%"
    c[5].metric("Con 4.0 o más", pct)


def tabla_alumnos(d: pd.DataFrame, con_seccion: bool):
    t = L._tabla_export(L.ordenar_alumnos(d), con_seccion)
    sty = (
        t.style
        .map(lambda v: f"background-color: {L.INFO_RANGO[v]['tinte']}; color: #0b0b0b"
             if v in L.INFO_RANGO else "", subset=["Rango"])
        .map(lambda v: f"background-color: {L.INFO_SEMAFORO[v]['tinte']}; color: #0b0b0b; font-weight: 600"
             if v in L.INFO_SEMAFORO else "", subset=["Semáforo"])
        .format({"Nota": lambda x: L.fmt(x)}, na_rep="—")
    )
    st.dataframe(sty, hide_index=True)


def tabla_resumen(res: pd.DataFrame):
    if res.empty:
        st.info("No hay datos para mostrar.")
        return
    fmt = {"Media": "{:.2f}", "Mediana": "{:.2f}", "Mín": "{:.1f}", "Máx": "{:.1f}", "% 4.0 o más": "{:.0f}%"}
    st.dataframe(res.style.format(fmt, na_rep="—"), hide_index=True)


def mostrar_fig(fig):
    st.pyplot(fig)
    plt.close(fig)


def descarga(etiqueta, datos, nombre, mime, clave):
    st.download_button(etiqueta, data=datos, file_name=nombre, mime=mime, key=clave)


MIME_PDF = "application/pdf"
MIME_XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

# ---------------------------------------------------------------------------
# Barra lateral: planilla, evaluación, filtros
# ---------------------------------------------------------------------------
with st.sidebar:
    st.header("1 · Planilla de notas")
    archivo = st.file_uploader("Sube la planilla (.xlsx)", type=["xlsx", "xlsm"])
    if archivo is not None:
        contenido, origen = archivo.getvalue(), archivo.name
    elif EJEMPLO.exists():
        contenido, origen = EJEMPLO.read_bytes(), "planilla de ejemplo"
        st.caption("Sin archivo subido: se usa la planilla de ejemplo.")
    else:
        st.info("Sube la planilla para comenzar.")
        st.stop()

    try:
        pl = cargar(contenido)
    except Exception as exc:  # noqa: BLE001
        st.error(f"No pude leer la planilla: {exc}")
        st.stop()

    st.header("2 · Evaluación")
    solo_con_notas = st.toggle("Solo evaluaciones con notas", value=True)
    cat = pl.evaluaciones[~pl.evaluaciones["auxiliar"]]
    if solo_con_notas:
        cat = cat[cat["n_notas"] > 0]
    if cat.empty:
        st.warning("La planilla no tiene evaluaciones con notas todavía.")
        st.stop()
    asignaturas = list(dict.fromkeys(cat["asignatura"]))
    asignatura = st.selectbox("Asignatura", asignaturas)
    sub = cat[cat["asignatura"] == asignatura]
    n_por_eval = dict(zip(sub["evaluacion"], sub["n_notas"]))
    evaluacion = st.selectbox(
        "Evaluación", list(n_por_eval), format_func=lambda e: f"{e}  ·  {n_por_eval[e]} notas"
    )
    clave = sub.loc[sub["evaluacion"] == evaluacion, "clave"].iloc[0]

    st.header("3 · Secciones TIP")
    progs = L.programas_presentes(pl)
    programas = st.multiselect(
        "Programas a incluir", progs, default=progs,
        format_func=lambda p: f"{p} · {L.PROGRAMAS[p]}" if p in L.PROGRAMAS else p,
        help=f"'{L.SIN_SECCION}' agrupa filas sin código de sección reconocible.",
    )
    st.divider()
    st.caption(
        "Rangos: **Bajo 3.0** · **3.0 a <3.5** · **3.5 a <4.0** · **4.0 o más**. "
        "Registros de texto (J, s/n…) se cuentan como **Sin nota**."
    )

nombre_eval = f"{asignatura} · {evaluacion}"

# ---------------------------------------------------------------------------
# Encabezado y pestañas
# ---------------------------------------------------------------------------
st.title("Seguimiento de rendimiento · Tutorías TIP AA")
st.caption(f"**{nombre_eval}**  ·  fuente: {origen}")
if pl.avisos:
    with st.expander(f"⚠️ {len(pl.avisos)} aviso(s) sobre la planilla"):
        for a in pl.avisos:
            st.write("• " + a)

tab_gen, tab_sec, tab_exp, tab_tut = st.tabs(
    ["📈 Resumen general", "👥 Por sección", "📦 Exportar reportes", "🧑‍🏫 Tutores"]
)

# Tutores primero (aunque la pestaña esté al final) para que sus cambios apliquen en todas.
if "tutores_base" not in st.session_state:
    if TUTORES_CSV.exists():
        st.session_state.tutores_base = pd.read_csv(TUTORES_CSV, dtype=str).fillna("")
    else:
        st.session_state.tutores_base = pd.DataFrame(columns=["Sección", "Tutor/a", "Horario"])

with tab_tut:
    st.subheader("Tutores por sección")
    st.write(
        "Edita la tabla para corregir nombres, horarios o agregar secciones (usa el formato "
        "`AA0001M-1`). Los cambios se aplican de inmediato a la vista y a los reportes. "
        "Para que queden guardados, descarga el CSV y reemplaza `tutores.csv` en la carpeta de la app."
    )
    tutores_df = st.data_editor(
        st.session_state.tutores_base, num_rows="dynamic", hide_index=True, key="editor_tutores"
    )
    tutores_csv = pd.DataFrame(tutores_df).fillna("").to_csv(index=False)
    st.download_button("⬇️ Descargar tutores.csv", tutores_csv.encode("utf-8-sig"),
                       "tutores.csv", "text/csv", key="dl_tutores")

prog_t = tuple(programas)
df, lg, tut = preparar(contenido, clave, prog_t, tutores_csv)
e_gen = L.estadisticas(df)
res = L.resumen_secciones(lg, tut)
secciones = L.secciones_presentes(lg) if len(lg) else []
sin_tutor = [s for s in secciones if s != L.SIN_SECCION and s not in tut]
if sin_tutor:
    with tab_tut:
        st.warning("Secciones con estudiantes pero sin tutor/a asignado: " + ", ".join(sin_tutor))

if df.empty:
    for t in (tab_gen, tab_sec, tab_exp):
        with t:
            st.info("No hay estudiantes con registro en esta evaluación para los programas seleccionados.")
    st.stop()

# ---------------------------------------------------------------------------
# Resumen general
# ---------------------------------------------------------------------------
with tab_gen:
    st.subheader("Estadísticas generales del control")
    st.caption("Cada estudiante se cuenta una vez, aunque esté inscrito en más de una sección.")
    metricas(e_gen)
    chips(e_gen["conteos"])

    c1, c2 = st.columns(2)
    with c1:
        mostrar_fig(L.fig_histograma(df["Nota"], "General del control"))
    with c2:
        mostrar_fig(L.fig_rangos_por_seccion(res))

    st.subheader("Resumen por sección")
    st.caption("Aquí un estudiante en dos secciones se cuenta en ambas.")
    tabla_resumen(res)

    with st.expander(f"Listado completo de estudiantes ({len(df)})"):
        filtro = st.multiselect("Filtrar por rango", L.ORDEN_RANGOS, default=L.ORDEN_RANGOS, key="f_gen")
        tabla_alumnos(df[df["Rango"].isin(filtro)], con_seccion=True)

# ---------------------------------------------------------------------------
# Por sección
# ---------------------------------------------------------------------------
with tab_sec:
    seccion = st.selectbox(
        "Sección", secciones,
        format_func=lambda s: s if s == L.SIN_SECCION else f"{s} · {L.nombre_tutor(s, tut)}",
    )
    g = lg[lg["Sección"] == seccion]
    e_sec = L.estadisticas(g)
    info = tut.get(seccion, {})
    linea = f"### {seccion}"
    if seccion != L.SIN_SECCION:
        linea += f" · {L.nombre_tutor(seccion, tut)}"
    st.markdown(linea)
    if info.get("horario"):
        st.caption(info["horario"])

    metricas(e_sec, ref=e_gen)
    chips(e_sec["conteos"])
    mostrar_fig(L.fig_histogramas_comparados(g["Nota"], df["Nota"], f"Sección {seccion}", "General del control"))

    st.markdown(f"**Estudiantes ({len(g)})**")
    filtro = st.multiselect("Filtrar por rango", L.ORDEN_RANGOS, default=L.ORDEN_RANGOS, key="f_sec")
    tabla_alumnos(g[g["Rango"].isin(filtro)], con_seccion=False)
    if (g["Otras secciones TIP"] != "").any():
        st.caption("Los estudiantes con «Otras secciones TIP» también aparecen en esas secciones.")

    tutor = L.nombre_tutor(seccion, tut)
    st.info(
        "**Lo que recibe el tutor/a:** el reporte descargable no muestra notas individuales ni "
        "intervalos. Cada estudiante aparece con su color del semáforo (🔴 rojo, 🟠 naranjo, "
        "🟢 verde); el resumen solo incluye media, mediana y cuántos están con 4.0 o más y bajo 4.0. "
        "Al final del reporte va la guía «¿Cómo interpretar el semáforo?»."
    )
    b1, b2, _ = st.columns([1, 1, 3])
    with b1:
        descarga("⬇️ Reporte PDF de la sección",
                 pdf_seccion(contenido, clave, prog_t, tutores_csv, seccion, nombre_eval),
                 L.nombre_archivo(nombre_eval, seccion, tutor, "pdf"), MIME_PDF, "dl_pdf_sec")
    with b2:
        descarga("⬇️ Excel de la sección",
                 excel_seccion(contenido, clave, prog_t, tutores_csv, seccion, nombre_eval),
                 L.nombre_archivo(nombre_eval, seccion, tutor, "xlsx"), MIME_XLSX, "dl_xlsx_sec")

# ---------------------------------------------------------------------------
# Exportar
# ---------------------------------------------------------------------------
with tab_exp:
    st.subheader("Reportes para coordinación")
    c1, c2 = st.columns(2)
    with c1:
        st.markdown("**Reporte general (PDF)**  \nEstadísticas del control, histograma, resumen por "
                    "sección y listado completo.")
        descarga("⬇️ Descargar PDF general", pdf_general(contenido, clave, prog_t, tutores_csv, nombre_eval),
                 f"General_{L.slug(nombre_eval)}.pdf", MIME_PDF, "dl_pdf_gen")
    with c2:
        st.markdown("**Planilla de resumen (Excel)**  \nHoja «Resumen», hoja «General» y una hoja por "
                    "sección con sus estudiantes.")
        descarga("⬇️ Descargar Excel", excel_general(contenido, clave, prog_t, tutores_csv, nombre_eval),
                 f"Resumen_{L.slug(nombre_eval)}.xlsx", MIME_XLSX, "dl_xlsx_gen")

    st.divider()
    st.subheader("Reportes para cada tutor/a")
    st.write(f"Se generará un PDF por sección ({len(secciones)}), más el reporte general y el Excel, "
             "todo en un ZIP. Los estudiantes inscritos en dos secciones aparecen en ambos reportes.")
    firma = hashlib.md5(
        b"|".join([contenido, clave.encode(), "|".join(prog_t).encode(), tutores_csv.encode()])
    ).hexdigest()
    if st.button("Generar ZIP con todos los reportes", type="primary"):
        barra = st.progress(0.0, text="Preparando…")
        datos_zip = L.zip_reportes(
            lg, df, tut, nombre_eval, secciones,
            progreso=lambda p, s: barra.progress(p, text=f"Generando {s}…"),
        )
        barra.empty()
        st.session_state.zip = (firma, datos_zip)
    guardado = st.session_state.get("zip")
    if guardado and guardado[0] == firma:
        descarga("⬇️ Descargar ZIP", guardado[1], f"Reportes_{L.slug(nombre_eval)}.zip",
                 "application/zip", "dl_zip")

    st.caption("Para descargar el reporte de una sola sección, usa la pestaña «Por sección».")
