---
title: "[✨ Feature] Endpoint para que el usuario cambie su propia contraseña"
labels: [enhancement, P2, api, services, schemas]
issue: 57
url: https://github.com/Teixeira49/vitaly_backend/issues/57
---

## Descripción
El backend no ofrece ninguna forma de que un usuario autenticado cambie su contraseña. La contraseña solo se fija en el registro y no hay ruta de actualización. El frontend ya tiene la interfaz preparada en el perfil del administrador de escuela («Preferencias → Cambiar contraseña») y espera un `POST /api/v1/auth/change-password`.

## Evidencia
- `app/api/v1/endpoints/auth.py:38-276`: el router de autenticación expone registro, login y `logout`, pero ninguna ruta de cambio de contraseña.
- `app/services/auth_service.py:335`: la contraseña solo se hashea al crear la cuenta (`_create_base_user`); ningún servicio actualiza la columna `password` de `user`.
- `app/core/security.py:653-681`: `verify_password` y `get_password_hash` ya existen y se pueden reutilizar.
- `app/api/deps.py:703`: `get_current_user_token` admite cualquier sesión válida (ambos portales), como hace `logout`.
- Frontend: Teixeira49/vitaly-care-for-families#7 pide `changePassword` (`POST /api/v1/auth/change-password/`) con contraseña actual, nueva y confirmación.
- `../vitaly_frontend/vitaly-care-for-families/src/lib/api-client.ts:47`: el interceptor trata cualquier 401 como sesión expirada, así que una contraseña actual incorrecta no puede responder 401.
- Reproducción: `grep -rn "change-password\|change_password" app/` no devuelve resultados.

## Comportamiento actual vs esperado
- **Actual:** no existe la ruta; el usuario no puede cambiar su contraseña.
- **Esperado:** `POST /api/v1/auth/change-password` recibe `current_password` y `new_password`, verifica la actual contra el hash almacenado, guarda el hash bcrypt de la nueva y responde `200` con el envelope `APIResponse`.

## Impacto
- ¿Expone datos médicos/PHR o PII? No. La respuesta no devuelve el hash ni la contraseña.
- ¿Fuga entre escuelas (school_id)? No. La operación solo afecta a la fila de `user` del `sub` del token; no recibe ningún id de usuario del cliente.
- Roles afectados: todos (admin de sistema, admin de escuela, médico y representante).

## Propuesta de solución
1. Esquema `PasswordChangeCreate` en `app/schemas/user.py` con `current_password` y `new_password` (`min_length=8`), documentados con `Field(description=...)`. La confirmación se valida en el cliente.
2. Método `AuthService.change_password(user_id, data)` que lee la fila de `user` por `id` (404 si no existe o está eliminada), verifica la contraseña actual, rechaza que la nueva sea igual a la actual y actualiza `password` con `get_password_hash`. Los errores inesperados se relanzan como `HTTPException(500)`.
3. Endpoint `POST /api/v1/auth/change-password` en `app/api/v1/endpoints/auth.py`, con `get_current_user_token` (cualquier sesión válida), `response_model=APIResponse[str]`, `summary`, `description` y `responses` documentados.

Decisión abierta: ¿se revoca el token actual tras el cambio (obliga a iniciar sesión de nuevo) o se mantiene la sesión? En cualquier caso, las demás sesiones abiertas siguen siendo válidas hasta que expiran (7 días): invalidarlas requiere una columna nueva (ej. `password_changed_at`) y una migración, que quedan fuera de este issue.

## Criterios de aceptación
- [ ] `POST /api/v1/auth/change-password` con la contraseña actual correcta responde `200` y la nueva contraseña permite iniciar sesión.
- [ ] Contraseña actual incorrecta → `400` (no `401`), sin modificar la fila.
- [ ] Nueva contraseña igual a la actual → `400`.
- [ ] Nueva contraseña con menos de 8 caracteres → `422`.
- [ ] Sin token o con token inválido/revocado → `401`.
- [ ] Funciona con tokens de ambos portales (`scope=admin` y `scope=client`).
- [ ] El id del usuario sale del claim `sub`; el cuerpo no acepta ningún id.
- [ ] Tests en `tests/test_auth.py` que cubren los casos anteriores.
- [ ] `pytest` pasa.

## Referencias
- Frontend: Teixeira49/vitaly-care-for-families#7.
- `.agents/rules/rbac-enforcement.md`, `.agents/rules/standard-response.md`, `.agents/rules/schema-naming-convention.md`.
- Skills `vitaly-rbac`, `vitaly-feature-testing`, `vitaly-contracts-parity`.
