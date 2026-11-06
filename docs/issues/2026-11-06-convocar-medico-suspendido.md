---
title: "[🛠️ Bug] Un administrador puede convocar a un médico suspendido y el evento le queda invisible"
labels: [bug, P2, services]
issue: null
url: null
---

## Descripción
Al crear un evento, el administrador de escuela puede incluir en `doctor_ids` a un médico cuyo vínculo con la escuela está **suspendido**. La petición responde `200` con `doctors_linked: 1`, se insertan las participaciones y el evento aparece en el detalle del administrador con ese médico convocado. Sin embargo, desde `GH-52` la agenda del médico (`GET /api/v1/doctor/events/by-month`) exige un vínculo vigente en `doctor_to_school`, así que ese evento no le llegará nunca. Ni el administrador ni el médico reciben señal alguna del problema.

## Evidencia
- `app/services/school_admin_event_service.py:78-83` — `_get_school_doctor_ids` consulta `doctor_to_school` filtrando solo por `school_id`; **no** filtra por `status`, así que devuelve también a los médicos suspendidos.
- `app/services/school_admin_event_service.py:98-105` — `create_event` valida `payload.doctor_ids` contra esa lista, de modo que un médico suspendido pasa la validación.
- `app/services/school_admin_service.py:1381-1410` — `suspend_doctor` escribe `doctor_to_school.status = 1` y no toca `event_participation`.
- `app/services/doctor_event_service.py:104-116` — `_get_active_school_ids` solo admite `status = ACTIVE_DOCTOR_SCHOOL_STATUS`, así que la escuela suspendida queda fuera de la lista blanca del médico.
- Reproducción: suspender al médico `<doc_id>` con `PATCH /api/v1/school-admin/doctors/<doc_id>/suspend`; crear después un evento con `doctor_ids: [<doc_id>]` → `200`; consultar `GET /api/v1/doctor/events/by-month` con el token de ese médico → `data: []`.

## Comportamiento actual vs esperado
- **Actual:** la convocatoria se acepta en silencio y no produce ningún efecto visible para el médico.
- **Esperado:** convocar a un médico sin vínculo vigente devuelve `400` con un mensaje que lo explique, igual que ya ocurre cuando el médico no pertenece a la escuela (`"El doctor con ID {id} no está asignado a tu escuela."`).

## Impacto
- ¿Expone datos médicos/PHR o PII? No. El riesgo es de integridad funcional, no de confidencialidad: el fallo es que se oculta de más, no de menos.
- ¿Fuga entre escuelas (`school_id`)? No.
- Roles afectados: administrador de escuela (cree haber convocado a alguien que nunca se enterará) y médico (no recibe una revisión que se le asignó).

## Propuesta de solución
Añadir el filtro de estado a `_get_school_doctor_ids`, reutilizando la constante `ACTIVE_DOCTOR_SCHOOL_STATUS` de `app/services/doctor_event_service.py` o moviéndola a un módulo compartido para que escritura y lectura no puedan divergir otra vez.

**Prerrequisito:** confirmar contra la base de datos qué valores admite `system_status` y cuál corresponde a un vínculo vigente. La constante se dedujo de `suspend_doctor` (escribe `1`) y `reactivate_doctor` (escribe `2`); `app/services/auth_service.py:136` documenta el `2` como *"Estado 'Inactivo/Pendiente' usualmente"* sobre `school_administrator`, lo que contradice esa lectura. Además, el backend **nunca** inserta en `doctor_to_school`, así que el estado inicial de un vínculo es desconocido desde el código. Implementar el filtro antes de esa verificación podría impedir convocar a cualquier médico.

## Criterios de aceptación
- [ ] `POST /api/v1/school-admin/events/` con un `doctor_id` cuyo vínculo no esté vigente devuelve `400` y no inserta ni el evento ni las participaciones.
- [ ] Convocar a un médico con vínculo vigente sigue funcionando igual.
- [ ] La constante del estado vigente vive en un único sitio compartido entre escritura y lectura.
- [ ] Tests en `tests/` cubren ambos casos.

## Referencias
- Detectado en la revisión adversarial de la PR de `GH-52`, que introdujo la comprobación del lado de la lectura.
- Relacionado: `GH-52` (agenda del médico por mes).
