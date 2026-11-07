# [✨ Feat: GH-57] - Endpoint para que el usuario cambie su propia contraseña

## Descripción

Nuevo `POST /api/v1/auth/change-password` para que cualquier usuario autenticado cambie su contraseña. El frontend lo necesita para el diálogo «Cambiar contraseña» del perfil del administrador de escuela (Teixeira49/vitaly-care-for-families#7).

Closes #57

Cambios realizados:
1. `app/schemas/user.py`: esquema `PasswordChangeCreate` con `current_password` y `new_password` (mínimo 8 caracteres). Los dos campos se rechazan con `422` si superan los 72 bytes en UTF-8, porque bcrypt 5 lanza `ValueError` con contraseñas más largas y eso acabaría en un 500. La confirmación de la nueva contraseña se valida en el cliente.
2. `app/services/auth_service.py`: `AuthService.change_password` lee la fila de `user` por `id` (sin las eliminadas), comprueba que la cuenta esté activa, verifica la contraseña vigente, rechaza que la nueva sea igual y guarda el hash bcrypt. La escritura también filtra por `id` e `is_deleted`, y si no actualiza ninguna fila responde `404`. Los errores inesperados se registran con `logger.exception` y se relanzan como `HTTPException(500)`.
3. `app/api/v1/endpoints/auth.py`: la ruta usa `get_current_user_token`, así que vale cualquier sesión de los dos portales. El usuario sale del claim `sub` y el cuerpo no acepta ningún id. Lleva `summary`, `description`, docstring y `responses` con los códigos 400/401/403/404/422/500.
4. `tests/fake_supabase.py`: el doble en memoria ahora soporta `update`. Solo modifica las filas que pasan los filtros encadenados, así que un `.eq()` perdido en una escritura rompe el test.
5. `tests/test_auth.py`: 21 casos nuevos. Cubren el éxito con los cuatro roles y los dos portales, el login con la contraseña nueva (y el rechazo de la antigua), que la sesión se mantiene, contraseña actual incorrecta (400), nueva igual a la actual (400), cinco payloads inválidos (422), un `user_id` en el cuerpo que se ignora, token ausente, malformado o revocado (401), cuenta inactiva (403), cuenta eliminada (404), fallo de base de datos (500) y la fila eliminada entre la lectura y la escritura (404).

Decisiones:
- **Contraseña actual incorrecta → `400`, no `401`.** El interceptor de `api-client.ts` del frontend trata cualquier 401 como sesión expirada y cerraría la sesión.
- **Se mantiene la sesión.** El token de la petición no se revoca. Las demás sesiones abiertas siguen siendo válidas hasta que expiran (7 días). Invalidarlas requiere una columna nueva (ej. `password_changed_at`) y una migración, que quedan fuera de este issue.

Nota para el frontend: el issue #7 menciona la ruta con barra final (`/auth/change-password/`). La ruta es `/api/v1/auth/change-password`, sin barra final.

## Tipo de cambio

- [x] ✨ Nueva funcionalidad (cambio no-breaking que agrega funcionalidad)
- [ ] 🛠️ Corrección de errores (cambio no-breaking que arregla un error)
- [ ] ❌ Cambio importante (arreglo o característica que haría que la funcionalidad existente no funcionara como se esperaba)
- [ ] 📝 Actualización de la documentación
- [ ] 🧹 Code refactoring (modificación de la estructura interna del código sin modificar las APIs)
- [ ] ✅ Build configuration change (modificación de los archivos para hacer deploy)
- [ ] 🗑️ Chore (actividades que no modifican la interacción con la app, por ejemplo, modificar la configuración del linter)

## ¿Cómo se ha probado esto?

- [ ] **POST /api/v1/auth/change-password** (rol: `admin_escuela`, `doctor` o `representante`, token del portal de clientes) con la contraseña actual correcta → `200`, `data: "Contraseña actualizada"`.
- [ ] **POST /api/v1/auth/change-password** (rol: `admin_sistema`, token del portal de administración) → `200`.
- [ ] **POST /api/v1/auth/client/login** con la contraseña nueva → `200`; con la antigua → `401`.
- [ ] **POST /api/v1/auth/change-password** con la contraseña actual incorrecta → `400` «La contraseña actual es incorrecta.» (la sesión no se cierra).
- [ ] **POST /api/v1/auth/change-password** con `new_password` igual a la actual → `400`; con menos de 8 caracteres → `422`.
- [ ] **POST /api/v1/auth/change-password** sin token o tras `POST /auth/logout` → `401`.

**Configuración de prueba:**

- Servidor local: `uvicorn app.main:app --reload`
- Documentación interactiva: `http://localhost:8000/docs`
- Pruebas automatizadas: `pytest` (481 tests) y `ruff check` sobre los archivos tocados

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
