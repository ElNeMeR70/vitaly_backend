# [✨ Feat: GH-51] - Incluir updated_at en el detalle de un evento escolar

## Descripción

`GET /api/v1/school-admin/events/{event_id}` ahora devuelve `data.event.updated_at`. El frontend lo usa en el nuevo diálogo de detalle de una revisión para mostrar «Actualizada hace…» (Teixeira49/vitaly-care-for-families#54, PRs #58–#61).

Closes #51

Cambios realizados:
1. `app/services/school_admin_event_service.py`: `get_event_detail` añade `updated_at` al diccionario `event`. La fila ya venía completa de `_validate_event_ownership` (`select("*")`), así que no hay consultas nuevas.
2. `cancel_event` y `update_event` guardaban `updated_at` con `datetime.utcnow().isoformat()`, un timestamp ingenuo. Al releerlo no se distingue un valor guardado como UTC de uno que PostgreSQL resolvió en la zona de la sesión, así que el cliente podía recibir un instante desfasado varias horas sin que nada lo delatara. Ahora se escribe con `datetime.now(timezone.utc).isoformat()`, igual que `settings_service`.
3. Helper `_with_utc_offset`: las filas escritas antes del punto anterior quedaron sin offset, así que el detalle les añade `+00:00` cuando no lo traen. Los valores que ya traen `Z` u offset no cambian, y `None` sigue siendo `None`.
4. `app/api/v1/endpoints/school_admin_events.py`: la descripción de OpenAPI del endpoint documenta `created_at` y `updated_at`.
5. Tests: 5 casos parametrizados para el detalle (offset `+00:00`, `Z`, `-04:00`, valor sin offset y `None`) y uno que falla si la edición vuelve a guardar un timestamp sin zona horaria.

Fuera de alcance: `GET /events/` y `GET /events/by-month` siguen sin `updated_at`.

Esta rama se fusionó con `development` para incorporar GH-52 (#54); el conflicto en `school_admin_event_service.py` era el helper nuevo junto a `_base_event_filter`, que `development` sustituyó por `apply_active_event_filter`.

## Tipo de cambio

- [x] ✨ Nueva funcionalidad (cambio no-breaking que agrega funcionalidad)
- [x] 🛠️ Corrección de errores (cambio no-breaking que arregla un error)
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
- Pruebas automatizadas: `pytest` (460 tests) y `ruff check` sobre los archivos tocados

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
