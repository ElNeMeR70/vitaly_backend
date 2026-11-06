# [✨ Feat: GH-52] - Eventos del médico autenticado por mes

## Descripción

Añade `GET /api/v1/doctor/events/by-month`, la primera ruta con la que un médico puede leer los eventos que se le asignan. La asignación ya se guardaba en `event_participation.id_doctor` cuando el administrador de escuela creaba el evento, pero solo era legible desde el portal del administrador: el calendario del médico en el frontend no tenía de dónde leer.

Cambios concretos:

1. **Servicio nuevo `app/services/doctor_event_service.py`** (`DoctorEventService` + singleton `doctor_event_service`). `get_events_by_month` resuelve el `doc_id` desde el claim `sub` del JWT y exige **dos** condiciones para devolver un evento: que el médico tenga una participación activa en él, y que mantenga un vínculo vigente (`doctor_to_school.status`) con la escuela que lo convocó. La participación es un hecho histórico que nada revoca; el vínculo con la escuela es la autorización viva, y suspender a un médico es la única palanca de revocación del producto. Sin esa segunda condición, un médico suspendido habría seguido leyendo la agenda de esa escuela indefinidamente.
2. **Escuela por evento.** Cada evento se enriquece con `school_id` y `school_name`, resueltos de forma indirecta (`event_participation.id_school_admin` → `school_administrator` → `school`), porque la tabla `event` no almacena la escuela. Se resuelven en lote: una consulta a `school_administrator` y una a `school` para todo el mes. Un evento cuya escuela no se puede resolver **no se devuelve**, porque sin ella no hay forma de comprobar que el médico siga autorizado.
3. **Endpoint nuevo `app/api/v1/endpoints/doctor_events.py`**, protegido con `Depends(get_current_doctor)` en forma `Annotated` y montado en `app/api/v1/router.py` con prefijo `/doctor/events` y tag `Médico - Eventos`. Se usa un módulo propio en lugar de ampliar `doctor.py`, siguiendo la convención de `vitaly-module-scaffold` (`<role>_<feature>.py`), igual que `school_admin_events.py`.
4. **Esquemas tipados** `DoctorEventRead` y `DoctorEventMonthRead` en `app/schemas/event.py`. El contrato `{ data, year, month, total }` replica el `by-month` del administrador para que el frontend reutilice la forma que ya conoce, pero declarado como `APIResponse[DoctorEventMonthRead]` en lugar del `APIResponse[dict]` legado, así que la forma del evento queda documentada en `/docs`. `title` y `ent_time` se declaran opcionales a propósito: con ellos obligatorios, una fila heredada con esos campos a nulo degradaba a 500 **el mes completo** del médico, porque `ResponseValidationError` no pasa por los handlers globales de `app/main.py`.
5. **Helper compartido `app/services/event_queries.py`** (`month_window`, `apply_active_event_filter`), usado por los dos portales. Corrige un bug que ya existía en el servicio del administrador y que este PR habría duplicado: la ventana del mes cerraba en `23:59:59`, así que un evento que empezaba en el último segundo del mes quedaba fuera de ese mes **y** del siguiente, es decir, invisible en todo `by-month`. Ahora la ventana es semiabierta `[inicio, siguiente)`. Sustituye además a `_base_event_filter`, que llevaba desde su creación sin un solo call site.
6. **Errores inesperados envueltos.** Las consultas pasan por un helper que registra el fallo con `logger.exception` y lo relanza como `HTTPException(500)`, como exige `CLAUDE.md` §1. Antes un `APIError` de postgrest salía como 500 en texto plano, sin envelope.
7. **Tests** en `tests/test_doctor_events.py` (48 casos) sobre un falso Supabase que **aplica** los filtros en memoria, en lugar de un `MagicMock` que devuelve filas predefinidas. La primera versión de este PR usaba stubs y sobrevivía a mutaciones tan groseras como fijar la longitud del mes en 30 días, invertir el orden cronológico o eliminar el filtro por escuela; esas mutaciones ahora fallan.

Fuera de alcance: el endpoint de detalle `GET /doctor/events/{id}`, que queda para un issue aparte (decisión abierta registrada en el issue).

Sin dependencias nuevas, sin variables de entorno nuevas y sin cambios de esquema en base de datos.

Closes #52

### Tamaño de la PR: `size:exception` aceptada

`vitaly-chained-pr` corta en **400 líneas cambiadas**; esta PR tiene **1360** (453 en `app/`, el resto tests y documentación). El mantenedor acepta explícitamente la excepción de tamaño en lugar de trocear en PRs encadenadas.

Justificación: el refactor del helper compartido (`event_queries.py`) y la funcionalidad del médico se validan mutuamente. Separarlos dejaría una primera PR que cambia el comportamiento del `by-month` del administrador sin ningún consumidor nuevo que lo justifique, y una segunda que depende de la primera para no reintroducir el bug de la ventana. El grueso del diff además no es lógica: de las 1360 líneas, unas 900 son tests y documentación.

Para revisar por partes, los commits son unidades de trabajo independientes y se pueden leer en orden:

| Commit | Qué entrega |
|---|---|
| `003b6a5` | Borrador del issue (solo documentación) |
| `cce26a3` | Funcionalidad: esquemas, servicio, endpoint, router y tests |
| `c01ee81` | Borrador de PR inicial (solo documentación) |
| `62ecd8d` | Helper compartido y corrección de la ventana del mes en ambos portales |
| `7f1e3ae` | Correcciones de la revisión adversarial: escuela vigente, envelope en 500, esquema resiliente, suite reescrita |
| `44a7432` | Cobertura del helper compartido en el lado del administrador |
| `261e2d0` | Este borrador actualizado |

### Limitaciones conocidas (no introducidas por este PR)

- **Offsets horarios.** La ventana del mes ancla en UTC, pero `create_event` guarda `init_time` con el offset que envíe el cliente sin normalizar. Con clientes en husos distintos, un evento de la noche del último día puede caer en el mes contiguo. `app/services/event_schedule_policy.py` difiere explícitamente el saneamiento de offsets al **issue #9**; este PR no lo adelanta, pero lo hereda igual que el `by-month` del administrador.
- **El valor del estado vigente está deducido, no verificado.** `ACTIVE_DOCTOR_SCHOOL_STATUS = 2` sale de `suspend_doctor` (escribe `1`) y `reactivate_doctor` (escribe `2`). En contra: `app/services/auth_service.py:136` documenta el `2` como *"Estado 'Inactivo/Pendiente' usualmente"* sobre `school_administrator`, y el backend **nunca** inserta en `doctor_to_school`, así que el estado inicial de un vínculo no consta en el código. Es la suposición más consecuente de esta PR: si el `2` no significa vigente, todos los médicos reciben `200` con la agenda vacía y nada delata la causa. **Verificar antes de fusionar** con `SELECT status_id, status_name FROM system_status;` y `SELECT status, count(*) FROM doctor_to_school GROUP BY 1;`.
- **Convocar a un médico suspendido se acepta en silencio.** `_get_school_doctor_ids` no filtra por estado, así que un administrador puede convocar a un médico suspendido, recibir `200` y que el evento nunca le llegue. Se deja fuera de esta PR a propósito, con borrador de issue en `docs/issues/2026-11-06-convocar-medico-suspendido.md`.
- **Lista `IN` sin cota.** Se leen todas las participaciones históricas del médico y sus ids viajan como parámetro de URL a PostgREST. Para un médico con años de historial en varias escuelas la URL puede crecer hasta rozar el límite de la cabecera de petición. El patrón viene del servicio del administrador; acotarlo requiere mover el filtro de fechas al lado del servidor (función Postgres y migración), por lo que merece issue propio.

## Tipo de cambio

Marque con una x dentro de los `[]` los que apliquen

- [x] ✨ Nueva funcionalidad (cambio no-breaking que agrega funcionalidad)
- [x] 🛠️ Corrección de errores (cambio no-breaking que arregla un error)
- [ ] ❌ Cambio importante (arreglo o característica que haría que la funcionalidad existente no funcionara como se esperaba)
- [ ] 📝 Actualización de la documentación
- [x] 🧹 Code refactoring (modificación de la estructura interna del código sin modificar las APIs)
- [ ] ✅ Build configuration change (modificación de los archivos para hacer deploy)
- [ ] 🗑️ Chore (actividades que no modifican la interacción con la app, por ejemplo, modificar la configuración del linter)

> El `by-month` del administrador cambia de comportamiento en un caso: un evento que empieza en el último segundo del mes, antes invisible, ahora aparece. No se considera breaking porque corrige una pérdida de datos.

## ¿Cómo se ha probado esto?

Pruebas automatizadas (`pytest`): **454 pasan**, 48 de ellas en `tests/test_doctor_events.py`. `ruff check app/ tests/` limpio. La suite se validó por mutación: fijar la longitud del mes, invertir el orden, cerrar la ventana en `lte`, quitar el filtro `is_canceled`, quitar el filtro por `id_doctor` y quitar la lista blanca de escuela vigente hacen fallar tests.

Para la prueba manual hace falta un evento creado por un admin de escuela con `doctor_ids: [<doc_id>]` dentro del mes a consultar, y el token del médico de ese `doc_id`:

- [ ] **POST /api/v1/school-admin/events/** (rol: `admin_escuela`) → 200; crea el evento con `doctor_ids: [<doc_id>]` e `init_time` dentro del mes a consultar. Anota el `data.event_id`.
- [ ] **GET /api/v1/doctor/events/by-month?year=&month=** (rol: `doctor` convocado) → 200 con `data.total = 1`, el evento en `data.data[0]` y `school_id` / `school_name` de la escuela que lo convocó.
- [ ] **GET /api/v1/doctor/events/by-month?year=&month=** (rol: `doctor`, **otro** médico no convocado) → 200 con `data.data: []` y `data.total: 0`.
- [ ] **PATCH /api/v1/school-admin/doctors/{doctor_id}/suspend** (rol: `admin_escuela`) y repetir el `by-month` del médico convocado → 200 con `data.data: []` y `data.total: 0`: la suspensión corta la visibilidad. Reactivarlo la devuelve.
- [ ] **PATCH /api/v1/school-admin/events/{event_id}/cancel** (rol: `admin_escuela`) y repetir el `by-month` → 200 con `data.data: []` y `data.total: 0`.
- [ ] **GET /api/v1/doctor/events/by-month?year=&month=** con un mes sin eventos (rol: `doctor`) → 200 con `data.data: []` y `data.total: 0`, nunca 404.
- [ ] **GET /api/v1/school-admin/events/by-month?year=&month=** (rol: `admin_escuela`) → 200: regresión del helper compartido; el listado del administrador sigue devolviendo el mes completo.
- [ ] **GET /api/v1/doctor/events/by-month?year=2026&month=4** (rol: `admin_escuela`, `representante` o token del portal de administración) → 403 con envelope `success: false`.
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
- [x] He agregado pruebas que prueban que mi solución es efectiva o que mi función funciona *(validadas por mutación, ver arriba)*
- [x] Las pruebas unitarias nuevas y existentes pasan localmente con mis cambios *(454 passed)*

> Advertencia honesta para quien revise: los tests usan un falso Supabase en memoria. Prueban que los filtros se aplican y qué resultado producen, pero **no** prueban aislamiento real contra PostgreSQL. Verificar que un médico suspendido deja de ver la agenda contra una instancia real sigue siendo una comprobación manual (está en la lista de arriba).

## Nota para el frontend (#56)

El contrato queda congelado así; el consumo en el calendario del médico va en el issue de frontend #56, en repositorio aparte.

```
GET /api/v1/doctor/events/by-month?year=<int>&month=<int>
Authorization: Bearer <token de médico>
```

```ts
interface DoctorEvent {
  id: number;
  title: string | null;
  description: string | null;
  init_time: string;          // ISO 8601, UTC con sufijo "Z"
  ent_time: string | null;
  created_at: string | null;
  school_id: number;          // siempre presente
  school_name: string | null;
}

interface DoctorEventMonth {
  data: DoctorEvent[];        // ordenados por init_time ascendente
  year: number;
  month: number;
  total: number;
}
// La respuesta llega envuelta: APIResponse<DoctorEventMonth>
```
