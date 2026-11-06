# [✨ Feat: GH-52] - Eventos del médico autenticado por mes

## Descripción

Añade `GET /api/v1/doctor/events/by-month`, la primera ruta con la que un médico puede leer los eventos que se le asignan. La asignación ya se guardaba en `event_participation.id_doctor` cuando el administrador de escuela creaba el evento, pero solo era legible desde el portal del administrador: el calendario del médico en el frontend no tenía de dónde leer.

Cambios concretos:

1. **Servicio nuevo `app/services/doctor_event_service.py`** (`DoctorEventService` + singleton `doctor_event_service`). `get_events_by_month` resuelve el `doc_id` desde el claim `sub` del JWT y usa las participaciones activas de ese médico como única fuente del alcance, de modo que los eventos de otros médicos quedan fuera por construcción. Sobre los eventos resultantes aplica el filtro de vigencia (`is_canceled = False`, `is_deleted = False`) y la ventana del mes sobre `init_time`, ordenando ascendente.
2. **Escuela por evento.** Cada evento se enriquece con `school_id` y `school_name`, resueltos de forma indirecta (`event_participation.id_school_admin` → `school_administrator` → `school`), porque la tabla `event` no almacena la escuela. Se resuelven en lote: una consulta a `school_administrator` y una a `school` para todo el mes.
3. **Endpoint nuevo `app/api/v1/endpoints/doctor_events.py`**, protegido con `Depends(get_current_doctor)` y montado en `app/api/v1/router.py` con prefijo `/doctor/events` y tag `Médico - Eventos`. Se usa un módulo propio en lugar de ampliar `doctor.py`, siguiendo la convención de `vitaly-module-scaffold` (`<role>_<feature>.py`), igual que `school_admin_events.py`.
4. **Esquemas tipados** `DoctorEventRead` y `DoctorEventMonthRead` en `app/schemas/event.py`. El contrato `{ data, year, month, total }` replica el `by-month` del administrador para que el frontend reutilice la forma que ya conoce, pero declarado como `APIResponse[DoctorEventMonthRead]` en lugar del `APIResponse[dict]` legado, así que la forma del evento queda documentada en `/docs`.
5. **Tests** en `tests/test_doctor_events.py` (23 casos): contrato del endpoint, presencia en OpenAPI, alcance resuelto en servidor, exclusión de cancelados/eliminados/ajenos, mes vacío, matriz RBAC y validación de query params.

Fuera de alcance: el endpoint de detalle `GET /doctor/events/{id}`, que queda para un issue aparte (decisión abierta registrada en el issue).

Sin dependencias nuevas, sin variables de entorno nuevas y sin cambios de esquema en base de datos.

Closes #52

## Tipo de cambio

Marque con una x dentro de los `[]` los que apliquen

- [x] ✨ Nueva funcionalidad (cambio no-breaking que agrega funcionalidad)
- [ ] 🛠️ Corrección de errores (cambio no-breaking que arregla un error)
- [ ] ❌ Cambio importante (arreglo o característica que haría que la funcionalidad existente no funcionara como se esperaba)
- [ ] 📝 Actualización de la documentación
- [ ] 🧹 Code refactoring (modificación de la estructura interna del código sin modificar las APIs)
- [ ] ✅ Build configuration change (modificación de los archivos para hacer deploy)
- [ ] 🗑️ Chore (actividades que no modifican la interacción con la app, por ejemplo, modificar la configuración del linter)

## ¿Cómo se ha probado esto?

Pruebas automatizadas (`pytest`): 419 pasan, 23 de ellas nuevas en `tests/test_doctor_events.py`. `ruff check app/ tests/` limpio.

Para la prueba manual hace falta un evento creado por un admin de escuela con `doctor_ids: [<doc_id>]` dentro del mes a consultar, y el token del médico de ese `doc_id`:

- [ ] **POST /api/v1/school-admin/events/** (rol: `admin_escuela`) → 200; crea el evento con `doctor_ids: [<doc_id>]` e `init_time` dentro del mes a consultar. Anota el `data.event_id`.
- [ ] **GET /api/v1/doctor/events/by-month?year=&month=** (rol: `doctor`, el del `doc_id` convocado) → 200 con `data.total = 1`, el evento en `data.data[0]` y `school_id` / `school_name` de la escuela que lo convocó.
- [ ] **GET /api/v1/doctor/events/by-month?year=&month=** (rol: `doctor`, **otro** médico no convocado) → 200 con `data.data: []` y `data.total: 0`: no se filtra ningún evento ajeno.
- [ ] **PATCH /api/v1/school-admin/events/{event_id}/cancel** (rol: `admin_escuela`) y repetir el `by-month` del médico convocado → 200 con `data.data: []` y `data.total: 0`: el evento cancelado desaparece.
- [ ] **GET /api/v1/doctor/events/by-month?year=&month=** con un mes sin eventos (rol: `doctor`) → 200 con `data.data: []` y `data.total: 0`, nunca 404.
- [ ] **GET /api/v1/doctor/events/by-month?year=2026&month=4** (rol: `admin_escuela` o `representante`) → 403 con envelope `success: false`.
- [ ] **GET /api/v1/doctor/events/by-month?year=2026&month=4** sin cabecera `Authorization` → 401.
- [ ] **GET /api/v1/doctor/events/by-month?year=2015&month=13** (rol: `doctor`) → 422 con envelope y `errors` poblado.
- [ ] **GET /docs** → la ruta aparece bajo el tag `Médico - Eventos` con el modelo `APIResponse_DoctorEventMonthRead_` y los campos `school_id` / `school_name` documentados en `DoctorEventRead`.

**Configuración de prueba:**

- Servidor local: `uvicorn app.main:app --reload`
- Documentación interactiva: `http://localhost:8000/docs`
- Pruebas automatizadas: `pytest` (o `pytest tests/test_doctor_events.py -v`)

## Lista de Verificación

- [ ] He agregado las nuevas dependencias en la descripción *(no aplica: no hay dependencias nuevas)*
- [ ] He agregado las nuevas variables de entorno (solo los nombres y rutas) a la descripción *(no aplica: no hay variables nuevas)*
- [x] Mi código sigue las pautas de estilo de este proyecto
- [x] He realizado una auto-revisión de mi código
- [x] He comentado mi código, particularmente en áreas difíciles de entender
- [x] He realizado los cambios correspondientes a la documentación. *(metadatos FastAPI + docstrings Multi-tenancy/RBAC/Raises según Issue #8; borrador del issue versionado en `docs/issues/`)*
- [x] Mis cambios no generan nuevas advertencias
- [x] Mis cambios pasan el build y lint del proyecto localmente *(`ruff check app/ tests/` sin hallazgos)*
- [x] He agregado pruebas que prueban que mi solución es efectiva o que mi función funciona
- [x] Las pruebas unitarias nuevas y existentes pasan localmente con mis cambios *(419 passed)*

## Nota para el frontend (#56)

El contrato queda congelado así; el consumo en el calendario del médico va en el issue de frontend #56, en repositorio aparte.

```
GET /api/v1/doctor/events/by-month?year=<int>&month=<int>
Authorization: Bearer <token de médico>
```

```ts
interface DoctorEvent {
  id: number;
  title: string;
  description: string | null;
  init_time: string;        // ISO 8601
  ent_time: string;         // ISO 8601
  created_at: string | null;
  school_id: number | null;
  school_name: string | null;
}

interface DoctorEventMonth {
  data: DoctorEvent[];      // ordenados por init_time ascendente
  year: number;
  month: number;
  total: number;
}
// La respuesta llega envuelta: APIResponse<DoctorEventMonth>
```
