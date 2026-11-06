---
title: "[✨ Feature] Consultar por mes los eventos asignados al médico autenticado"
labels: [enhancement, P1, api, services]
issue: 52
url: https://github.com/Teixeira49/vitaly_backend/issues/52
---

## Descripción
El administrador de escuela puede asignar médicos a un evento (revisión) y la asignación se guarda en `event_participation.id_doctor`, pero no existe ningún endpoint con el que el propio médico pueda leer esos eventos. El router del rol médico solo expone su perfil y su foto, y todos los endpoints de eventos exigen el rol `admin_escuela`. Por eso el calendario del médico en el frontend no puede mostrar las revisiones que le asignan.

## Evidencia
- `app/services/school_admin_event_service.py:217-225` — `create_event` inserta una fila en `event_participation` por cada `doctor_id` (`id_event`, `id_doctor`, `id_school_admin`): el dato de la asignación existe.
- `app/api/v1/endpoints/doctor.py:24`, `:55`, `:90`, `:135` — las únicas rutas del rol médico son `GET/PUT /profile` y `POST/DELETE /profile/photo`.
- `app/api/v1/router.py:18` — los eventos solo se montan en `/school-admin/events`.
- `app/api/v1/endpoints/school_admin_events.py:104-138` — `GET /school-admin/events/by-month` depende de `get_current_admin_escuela`, así que un token de médico recibe 403.
- `app/services/school_admin_event_service.py:309-366` — `get_events_by_month` filtra `event_participation` por `id_school_admin`; no hay una variante que filtre por `id_doctor`.
- Reproducción: como admin de escuela, `POST /api/v1/school-admin/events/` con `doctor_ids: [<doc_id>]`; luego, con el token de ese médico, no hay ninguna ruta `/api/v1/doctor/...` que devuelva el evento.

## Comportamiento actual vs esperado
- **Actual:** el médico asignado no tiene forma de consultar los eventos donde participa.
- **Esperado:** `GET /api/v1/doctor/events/by-month?year=&month=` devuelve, envuelto en `APIResponse`, los eventos vigentes (ni cancelados ni eliminados) del mes en los que el médico autenticado tiene una participación activa, ordenados por `init_time`.

## Impacto
- ¿Expone datos médicos/PHR o PII? No; el endpoint nuevo solo devuelve datos del evento (título, descripción, horario, escuela). Debe evitar devolver eventos de otros médicos.
- ¿Fuga entre escuelas (school_id)? El alcance se deriva del token (`doctor_payload["sub"]` → `doc_id`) y de `event_participation.id_doctor`; nunca de un parámetro del cliente.
- Roles afectados: médico (no ve su agenda); admin de escuela (la asignación no tiene efecto visible para el médico).

## Propuesta de solución
1. `DoctorEventService.get_events_by_month(user_id, year, month)`: resolver `doc_id` desde `user_id`, leer `event_participation` con `id_doctor = doc_id` e `is_deleted = False`, y filtrar `event` con `_base_event_filter()` y el rango del mes (mismo cálculo que `get_events_by_month` del admin).
2. Ruta `GET /api/v1/doctor/events/by-month` en `app/api/v1/endpoints/doctor.py`, protegida con `get_current_doctor`, mismo contrato que el `by-month` del admin (`{ data, year, month, total }`).
3. Cada evento incluye la escuela (`school_id`, `school_name`), resuelta vía `id_school_admin` → `school_administrator`, para que el médico pueda filtrar por escuela.

Decisión abierta: ¿añadir también `GET /api/v1/doctor/events/{event_id}` (detalle con salones) en este mismo issue, o dejarlo para uno aparte?

## Criterios de aceptación
- [ ] Con token de médico, `GET /api/v1/doctor/events/by-month?year=Y&month=M` devuelve 200 con los eventos donde tiene participación activa en ese mes.
- [ ] No devuelve eventos cancelados, eliminados ni de otros médicos.
- [ ] Un token de otro rol recibe 403; sin token, 401.
- [ ] Un mes sin eventos devuelve 200 con `data: []` y `total: 0`.
- [ ] Cada evento incluye `school_id` y `school_name`.
- [ ] Tests en `tests/` cubren los casos anteriores y el contrato aparece en OpenAPI.

## Referencias
- Issue de frontend vinculado: Teixeira49/vitaly-care-for-families (calendario del médico).
- `app/api/v1/endpoints/school_admin_events.py` (contrato `by-month` a replicar).
- Skills `vitaly-rbac`, `vitaly-tenant-isolation`, `standard-api-responses`.
