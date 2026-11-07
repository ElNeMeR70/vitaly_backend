# [🛠️ Fix: GH-61] - Etiquetas legibles de salones en revisiones

## Descripción

El detalle de una revisión mostraba `2 1 A` al concatenar el ID de categoría,
el nivel y la sección. Ahora `SchoolAdminEventService.get_event_detail` resuelve
los nombres en `classroom_category(id, classroom_type_name)` por lote y devuelve
`Primer grado primaria · Sección A` y `Segundo grado primaria · Sección B`.

- Primaria usa grados; preescolar, niveles; bachillerato, años. Se conservan las
  secciones para distinguir salones del mismo grado.
- Una sección nula, vacía o de espacios se omite sin separadores sobrantes.
- Una categoría ausente, inexistente o sin nombre devuelve, por ejemplo,
  `Primer nivel · Categoría no disponible · Sección A`, sin exponer su ID.
- Se mantiene el contrato `classrooms: { id: number, label: string }[]`.
- Una consulta recupera los salones y otra sus categorías deduplicadas; no hay
  consultas por salón. Si no hay categorías que resolver, se omite ese acceso.
- La consulta modificada de salones filtra por el `school_id` del administrador
  resuelto en el servidor. El catálogo es global y no lleva filtro de escuela.
- Los errores de consulta de salones/categorías se registran con `logger.exception`
  y se convierten en `HTTPException(500)`. Las pruebas comprueban ambos pasos.

Closes #61

Base propuesta: `development`. Rama: `fix/GH-61`, creada desde `7a9c611`
e integrada con `development` en `452bc21` antes de la validación final.
Una sola unidad revisable: corrección, pruebas y documentación. La autorización
explícita posterior del usuario habilita commit, push, PR, revisión y merge a
`development`, sustituyendo la parada en borrador de `triggerMode: manual`.
No requiere dependencias nuevas, variables de entorno ni migraciones.

## Tipo de cambio

- [ ] ✨ Nueva funcionalidad
- [x] 🛠️ Corrección de errores sin cambio de contrato
- [ ] ❌ Cambio importante
- [x] 📝 Actualización de la documentación
- [ ] 🧹 Code refactoring
- [ ] ✅ Build configuration change
- [ ] 🗑️ Chore

## ¿Cómo se ha probado esto?

- [x] `python -m pytest tests/ -q`: **548 passed**, 63 advertencias de deprecación
  de Pydantic/Starlette en código que esta corrección no modifica.
- [x] `python -m pytest tests/test_event_classroom_labels.py -q`: **27 passed**.
  Ejecuta el servicio desde filas numéricas con un cliente Supabase en memoria
  que aplica filtros y proyecciones. Cubre todos los niveles válidos de las tres
  categorías, secciones ausentes, fallback, secciones distintas, catálogo por
  lote (22 salones), catálogo sin consultas innecesarias y errores de consulta.
- [x] TestClient ejecuta el endpoint con el servicio real y datos sintéticos:
  comprueba envelope, contrato y etiquetas de primero A/B y segundo B.
- [x] `python -m ruff check app tests`: **All checks passed**.
- [x] `git diff --check`: sin errores.
- [ ] Verificación manual con API en ejecución: **GET
  /api/v1/school-admin/events/{event_id}**, rol `admin_escuela` → 200 con etiquetas
  legibles en `data.classrooms`, incluyendo dos secciones del mismo grado.
- [ ] Navegador en modo API: abrir `/admin/escuela/calendario`, consultar una
  revisión y confirmar «Secciones convocadas». No ejecutado en este encargo.

**Configuración de prueba:**

- Python 3.10.11, entorno virtual aislado y dependencias de `requirements-dev.txt`.
- `tests/conftest.py` desactiva lectura de `.env` y usa credenciales ficticias;
  pytest bloquea conexiones de red mediante pytest-socket. No se consultó ni
  modificó una base real. Los dobles no prueban PostgREST ni RLS en producción.
- Para verificación manual posterior: `uvicorn app.main:app --reload`, Swagger
  en `http://localhost:8000/docs` y datos autorizados de prueba.

**Consumo frontend verificado por lectura de código:**

La ruta relativa prescrita `../vitaly_frontend/vitaly-care-for-families` no está
presente en este worktree. Se inspeccionó el repositorio remoto, rama
`development`, commit `5034fa1116ce1adcd685d2f36b18ac3ff7dae641`:

- [CheckupDetailView.tsx, líneas 84–96](https://github.com/Teixeira49/vitaly-care-for-families/blob/5034fa1116ce1adcd685d2f36b18ac3ff7dae641/src/components/admin/schedule-checkup/CheckupDetailView.tsx#L84-L96)
  recorre `classrooms` y muestra directamente `classroom.label` dentro del Badge
  en «Secciones convocadas».
- [schoolEventsService.ts, líneas 55–59](https://github.com/Teixeira49/vitaly-care-for-families/blob/5034fa1116ce1adcd685d2f36b18ac3ff7dae641/src/services/schoolAdmin/schoolEventsService.ts#L55-L59)
  declara `{id: number, label: string}`. Su comentario conserva el ejemplo antiguo.
- [fetchSchoolEventDetail, líneas 253–273](https://github.com/Teixeira49/vitaly-care-for-families/blob/5034fa1116ce1adcd685d2f36b18ac3ff7dae641/src/services/schoolAdmin/schoolEventsService.ts#L253-L273)
  desempaqueta la respuesta API sin transformar las etiquetas. El modo mock usa
  etiquetas propias: no permite validar esta corrección backend.

Esta evidencia confirma compatibilidad y consumo directo por código; la
comprobación visual en navegador queda pendiente. No se modificó el frontend.

## Lista de Verificación

- [x] Dependencias documentadas: ninguna nueva.
- [x] Variables de entorno documentadas: ninguna nueva.
- [x] Mi código sigue las pautas de estilo de este proyecto.
- [x] He realizado una auto-revisión de mi código.
- [x] He comentado el código relevante y añadido docstrings al formateador.
- [x] He realizado los cambios correspondientes a la documentación.
- [x] Mis cambios no generan nuevas advertencias; las deprecaciones reportadas
  corresponden a archivos no modificados.
- [x] Mis cambios pasan las verificaciones Python y lint localmente.
- [x] He agregado pruebas que verifican la corrección desde filas numéricas.
- [x] Las pruebas unitarias nuevas y existentes pasan localmente.
