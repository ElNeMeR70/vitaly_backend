## Descripción

Añade `DELETE /api/v1/auth/me` para eliminar lógicamente la cuenta autenticada de administrador de escuela, médico o representante. Conserva email, foto y datos históricos; registra `deleted_at`, desactiva la cuenta y sus vínculos y revoca el token de la petición en una transacción PostgreSQL. Las peticiones posteriores de cualquiera de sus sesiones reciben 401.

Bloquea la baja del último administrador activo de una escuela, del médico con casos activos o revisiones futuras, del administrador con revisiones convocadas futuras y del representante con casos activos o revisiones futuras de hijos vinculados. Los conflictos devuelven 409 sin modificar datos. Un caso está activo si falta fecha de cierre o diagnóstico final. Una revisión está pendiente mientras su inicio no haya pasado; canceladas y eliminadas no bloquean. Para los hijos se usa su última inscripción en salón.

Los médicos eliminados dejan de aparecer en el listado y detalle escolar y no pueden recibir nuevas convocatorias. El listado filtra antes del conteo y paginación. El estado `school_administrator.status` se conserva: la inactividad efectiva se establece en `user.is_active=false`, sin inventar valores del catálogo de estados.

Dependencia de despliegue: aplicar primero `supabase/migrations/20261106120000_delete_own_account.sql`. Añade una columna nullable y una RPC ejecutable solo por `service_role`; se usa exclusivamente el cliente privilegiado compartido. Requiere la variable ya existente `SUPABASE_SERVICE_KEY` en el entorno del backend; su ausencia produce 500 saneado sin escrituras. No hay nuevas dependencias Python. SQL validado en PostgreSQL 15.15 local desechable, con autorización del usuario; no aplicado a Supabase.

El usuario autorizó explícitamente publicar la PR y la excepción `size:exception` para revisar la funcionalidad completa en una entrega. La configuración manual del repositorio queda sustituida por esa instrucción para esta tarea. Contrato: sin request body y `APIResponse[None]` (`data:null`); no cambia campos existentes del frontend. La conexión de las pantallas se sigue en Teixeira49/vitaly-care-for-families#7.

Closes #59

## Tipo de cambio

- [x] ✨ Nueva funcionalidad
- [x] 📝 Actualización de la documentación

## ¿Cómo se ha probado esto?

- [x] `pytest tests/`: 521 pruebas pasan; incluye contrato, permisos, claims, todas las sesiones y filtros de médicos.
- [x] `ruff check app tests` y `git diff --check`.
- [x] PostgreSQL desechable: aplicar migración, comprobar bloqueos de los tres roles, efectos, retención, rollback y permisos de RPC.
- [x] PostgreSQL desechable: dos bajas simultáneas de los últimos administradores dejan uno activo.
- [ ] Prueba manual: **DELETE /api/v1/auth/me** (roles: `admin_escuela`, `doctor`, `representante`) sin obligaciones → 200, `data:null`, «Cuenta eliminada correctamente».
- [ ] Prueba manual: misma ruta con obligaciones → 409 sin cambios; sin token → 401; `admin_sistema` → 403.
- [ ] Prueba manual: **POST /api/v1/auth/client/login** tras la baja → 403 «El usuario ha sido eliminado»; repetir una petición protegida con cualquiera de sus tokens → 401.

**Configuración de prueba:**

- Servidor local: `uvicorn app.main:app --reload`; documentación: `http://localhost:8000/docs`.
- Automatización Python: `pytest tests/` con Supabase simulado, sin credenciales reales.
- SQL aislado: `psql "$ACCOUNT_DELETION_TEST_DSN" -X -v ON_ERROR_STOP=1 -f tests/sql/account_deletion.sql`, seguido de `bash tests/sql/account_deletion_concurrency.sh`. Requiere PostgreSQL 15+ desechable y autorización de ejecución; la variable solo pertenece al harness.
- Las pruebas SQL usan un esquema sintético: verifican la función y concurrencia, no prueban paridad con el esquema remoto ni PostgREST.

## Lista de Verificación

- [x] Cambios y dependencia de migración documentados; sin nuevas dependencias Python; requiere `SUPABASE_SERVICE_KEY`, ya existente.
- [x] Endpoint síncrono, gate de clientes, identidad derivada de `sub` y envelope estándar.
- [x] Docstrings y metadatos OpenAPI incluidos.
- [x] Pruebas Python existentes y nuevas pasan; lint pasa.
- [x] SQL y concurrencia validados en PostgreSQL aislado.
- [ ] Revisión con contexto limpio completada sobre el commit publicado.
- [ ] Hallazgos, si existen, publicados y corregidos, con nueva revisión posterior.
