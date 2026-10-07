# Seguimiento de rendimiento · Tutorías TIP AA

App en Streamlit para revisar las notas de cada evaluación por sección de tutoría y
generar los reportes que se entregan a cada tutor/a.

## Cómo ejecutarla

```bash
pip install -r requirements.txt
streamlit run app.py
```

Se abre en el navegador (http://localhost:8501). Sube la planilla desde la barra lateral;
si no subes nada, usa `planilla_ejemplo.xlsx`.

## Archivos

| Archivo | Qué es |
|---|---|
| `app.py` | Interfaz Streamlit |
| `logic.py` | Lectura de la planilla, estadísticas, gráficos y generación de PDF/Excel/ZIP |
| `tutores.csv` | Sección → tutor/a → horario. Se puede editar desde la pestaña «Tutores» o directamente |
| `planilla_ejemplo.xlsx` | Planilla de ejemplo (formato anonimizado) |

## Uso

1. **Barra lateral**: sube la planilla, elige asignatura y evaluación (por defecto solo
   aparecen las que tienen notas) y los programas TIP a incluir (AA0001M / AA0002M).
2. **Resumen general**: media, mediana, mín/máx, % con 4.0 o más, histograma, rangos por
   sección y tabla resumen.
3. **Por sección**: estadísticas de la sección comparadas con el total, histogramas y
   listado de estudiantes coloreado por rango. Botones para descargar el PDF y el Excel de esa sección.
4. **Exportar reportes**: PDF general, Excel con una hoja por sección y un ZIP con un PDF
   por tutor/a.

## Reglas que usa

- **Rangos**: Bajo 3.0 · 3.0 a <3.5 · 3.5 a <4.0 · 4.0 o más (un 3.5 queda en «3.5 a <4.0»
  y un 4.0 en «4.0 o más»).
- **Estudiantes en más de una sección** (p. ej. `AA0001M-5; AA0002M-2`) aparecen en el
  reporte de **cada** sección, con la columna «Otras secc. TIP». En las estadísticas
  generales se cuentan **una sola vez**.
- **Registros de texto** en la celda de nota (J, s/n, …) cuentan como «Sin nota»: aparecen
  en el listado con su observación, pero no entran en la media, la mediana ni el histograma.
  Las notas fuera de 1.0–7.0 también se tratan como «Sin nota».
- Un estudiante entra en una evaluación solo si su celda tiene algo (nota o texto).
- El código `AA1002M` se corrige automáticamente a `AA0002M` (ver `ALIAS_CODIGOS` en `logic.py`).
- Las filas sin código de sección válido (p. ej. «TOPE») quedan en «Sin sección TIP».
- Se ignoran las columnas «Cómo va (nota objetivo…)», porque no son evaluaciones.
- Para cambiar los cortes o los colores, edita `RANGOS` en `logic.py`.

## Formato de planilla esperado

Hoja 1, con dos filas de encabezado: la primera con `RUT`, `Nombre`, …,
`Sección asignada TIP AA (cod)` y los bloques `NOTAS <asignatura>`; la segunda con el
nombre de cada evaluación (C1, C2, Ex1, …). Si se agrega una asignatura o una evaluación
nueva con ese mismo formato, la app la detecta sola.
