# [✨ Feat: GH-51] - Incluir updated_at en el detalle de un evento escolar

## Descripción

`GET /api/v1/school-admin/events/{event_id}` ahora devuelve `data.event.updated_at`. El frontend lo usa en el nuevo diálogo de detalle de una revisión para mostrar «Actualizada hace…» (Teixeira49/vitaly-care-for-families#54, PRs #58–#61).

Closes #51

Cambios realizados:
1. `app/services/school_admin_event_service.py`: `get_event_detail` añade `updated_at` al diccionario `event`. La fila ya venía completa de `_validate_event_ownership` (`select("*")`), así que no hay consultas nuevas.
2. Helper `_with_utc_offset`: `update_event` escribe `updated_at` con `datetime.utcnow().isoformat()`, sin zona horaria. Si la columna lo devuelve así, el cliente lo interpretaría como hora local, así que el detalle le añade `+00:00` cuando no trae offset. Los valores que ya traen `Z` u offset no cambian, y `None` sigue siendo `None`.
3. `app/api/v1/endpoints/school_admin_events.py`: la descripción de OpenAPI del endpoint documenta `created_at` y `updated_at`.
4. Tests: 5 casos parametrizados en `tests/test_school_admin_events.py` (offset `+00:00`, `Z`, `-04:00`, valor sin offset y `None`).

Fuera de alcance: `GET /events/` y `GET /events/by-month` siguen sin `updated_at`.

## Tipo de cambio

- [x] ✨ Nueva funcionalidad (cambio no-breaking que agrega funcionalidad)
- [ ] 🛠️ Corrección de errores (cambio no-breaking que arregla un error)
- [ ] ❌ Cambio importante (arreglo o característica que haría que la funcionalidad existente no funcionara como se esperaba)
- [ ] 📝 Actualización de la documentación
- [ ] 🧹 Code refactoring (modificación de la estructura interna del código sin modificar las APIs)
- [ ] ✅ Build configuration change (modificación de los archivos para hacer deploy)
- [ ] 🗑️ Chore (actividades que no modifican la interacción con la app, por ejemplo, modificar la configuración del linter)

## ¿Cómo se ha probado esto?

- [ ] **GET /api/v1/school-admin/events/{event_id}** (rol: `admin_escuela`) → `200`; `data.event` incluye `updated_at` en ISO 8601 con zona horaria (o `null` si la fila no tiene valor).
- [ ] **PUT /api/v1/school-admin/events/{event_id}** (rol: `admin_escuela`) y luego **GET** del mismo evento → `data.event.updated_at` refleja la edición y termina en `+00:00` o `Z`.
- [ ] **GET /api/v1/school-admin/events/{event_id}** de un evento de otra escuela (rol: `admin_escuela`) → sigue respondiendo `403`.

**Configuración de prueba:**

- Servidor local: `uvicorn app.main:app --reload`
- Documentación interactiva: `http://localhost:8000/docs`
- Pruebas automatizadas: `pytest` (401 tests) y `ruff check` sobre los archivos tocados

## Lista de Verificación

- [ ] He agregado las nuevas dependencias en la descripción *(no aplica)*
- [ ] He agregado las nuevas variables de entorno (solo los nombres y rutas) a la descripción *(no aplica)*
- [x] Mi código sigue las pautas de estilo de este proyecto
- [x] He realizado una auto-revisión de mi código
- [x] He comentado mi código, particularmente en áreas difíciles de entender
- [x] He realizado los cambios correspondientes a la documentación.
- [x] Mis cambios no generan nuevas advertencias
- [x] Mis cambios pasan el build y lint del proyecto localmente
- [x] He agregado pruebas que prueban que mi solución es efectiva o que mi función funciona
- [x] Las pruebas unitarias nuevas y existentes pasan localmente con mis cambios

🤖 Generated with [Claude Code](https://claude.com/claude-code)
