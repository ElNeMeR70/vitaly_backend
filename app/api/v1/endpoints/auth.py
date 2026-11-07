"""Autenticación: registro por rol, login, cambio de contraseña y revocación de sesión."""

from typing import Annotated

from fastapi import APIRouter, Depends
from fastapi.security import HTTPAuthorizationCredentials

from app.api.deps import get_current_client, get_current_user_token, security
from app.core.openapi_responses import BAD_REQUEST, PROTECTED_RESPONSES, PUBLIC_RESPONSES, UNAUTHORIZED, UNPROCESSABLE, error_response
from app.schemas.user import SystemAdminRegister, SchoolAdminRegister, DoctorRegister, ParentRegister, ClientLoginCreate, AdminLoginCreate, PasswordChangeCreate, Token
from app.schemas.enums import TokenScope
from app.schemas.responses import APIResponse
from app.services.auth_service import auth_service
from app.services.account_deletion_service import account_deletion_service

router = APIRouter()
# Portales de login: montados en /auth/client y /auth/admin, cada uno con su sección en Swagger/ReDoc.
client_router = APIRouter()
admin_router = APIRouter()


@router.delete(
    "/me",
    response_model=APIResponse[None],
    summary="Eliminar la cuenta propia",
    description=(
        "Borrado lógico de la cuenta y desactivación de sus vínculos. Conserva "
        "el email y los datos históricos, registra deleted_at e invalida todas "
        "las sesiones. No requiere cuerpo ni contraseña adicional."
    ),
    responses={
        **PROTECTED_RESPONSES,
        **error_response(409, "La cuenta conserva obligaciones pendientes.",
                         "No puedes eliminar tu cuenta mientras tengas revisiones pendientes."),
    },
)
def delete_own_account(
    payload: Annotated[dict, Depends(get_current_client)],
    credentials: Annotated[HTTPAuthorizationCredentials, Depends(security)],
) -> APIResponse[None]:
    """Elimina únicamente la cuenta autenticada.

    Multi-tenancy:
        Usuario y vínculos se resuelven desde sub; no acepta school_id ni IDs.
    RBAC:
        admin_escuela, doctor y representante del portal de clientes.
    Raises:
        HTTPException: 401 por token inválido o cuenta eliminada; 403 por portal
            o rol incompatible; 409 por obligaciones pendientes; 500 si falla
            la transacción, que revierte todas sus escrituras.
    """
    account_deletion_service.delete_own_account(
        int(payload["sub"]), credentials.credentials, int(payload["exp"]),
    )
    return APIResponse(data=None, message="Cuenta eliminada correctamente")

# Errores comunes de ambos portales, con ejemplos del envelope estándar.
LOGIN_ERROR_RESPONSES = {
    **PUBLIC_RESPONSES,
    **error_response(401, "Unauthorized: correo o contraseña incorrectos.", "Credenciales incorrectas"),
    **error_response(
        403,
        "Forbidden: cuenta eliminada, inactiva o con un rol ajeno a este portal. No se emite ningún token.",
        "Esta cuenta no tiene acceso al portal solicitado.",
    ),
    **error_response(
        500,
        "Internal Server Error: no se pudo completar el login. Inténtalo de nuevo más tarde.",
        "No se pudo iniciar sesión. Inténtalo de nuevo más tarde.",
    ),
}

#: Registro: cualquiera puede solicitarlo; el 400 cubre el correo ya existente.
_REGISTER_RESPONSES = {**PUBLIC_RESPONSES, **BAD_REQUEST}

#: Cambio de contraseña: la contraseña vigente incorrecta es un 400, no un 401, para que el
#: cliente no la confunda con una sesión expirada.
_CHANGE_PASSWORD_RESPONSES = {
    **error_response(
        400,
        "Bad Request: la contraseña actual es incorrecta o la nueva coincide con ella.",
        "La contraseña actual es incorrecta.",
    ),
    **UNAUTHORIZED,
    **error_response(
        403,
        "Forbidden: la cuenta está inactiva.",
        "La cuenta está inactiva; no se puede cambiar la contraseña.",
    ),
    **error_response(404, "Not Found: el usuario del token no existe o fue eliminado.", "El usuario no existe."),
    **UNPROCESSABLE,
    **error_response(
        500,
        "Internal Server Error: no se pudo cambiar la contraseña.",
        "No se pudo cambiar la contraseña. Inténtalo de nuevo más tarde.",
    ),
}


# PUBLIC_ROUTE
@router.post(
    "/register-system-admin",
    response_model=APIResponse[str],
    summary="Registrar administrador de sistema",
    description=(
        "Crea una cuenta de administrador de sistema. La cuenta nace inactiva "
        "(is_active=False) y requiere la aprobación de otro administrador antes "
        "de poder iniciar sesión."
    ),
    responses=_REGISTER_RESPONSES,
)
def register_system_admin(user_in: SystemAdminRegister):
    """Registra un administrador de sistema, pendiente de verificación.

    Multi-tenancy:
        Rol de alcance global: la cuenta no queda vinculada a ninguna escuela.

    RBAC:
        Ruta pública: no requiere token.

    Raises:
        HTTPException(400): Si el correo ya está registrado bajo algún rol.
        HTTPException(500): Si falla la creación del usuario base.
    """
    msg = auth_service.register_system_admin(user_in)
    return APIResponse(data=msg, message="Registro completado para administrador de sistema")


# PUBLIC_ROUTE
@router.post(
    "/register-school-admin",
    response_model=APIResponse[str],
    summary="Registrar administrador de escuela",
    description=(
        "Crea una cuenta de administrador de escuela vinculada a la institución "
        "indicada en 'school_id'. La cuenta nace inactiva y requiere aprobación. "
        "El 'school_id' enviado aquí define el alcance de datos del usuario para "
        "el resto del sistema; a partir del login se resuelve siempre en el servidor."
    ),
    responses=_REGISTER_RESPONSES,
)
def register_school_admin(user_in: SchoolAdminRegister):
    """Registra un administrador de escuela, pendiente de verificación.

    Multi-tenancy:
        Es el único punto donde el cliente aporta un 'school_id': fija el vínculo
        inicial en 'school_administrator'. Después nunca se vuelve a aceptar del
        cliente, sino que se deriva del usuario autenticado.

    RBAC:
        Ruta pública: no requiere token.

    Raises:
        HTTPException(400): Si el correo ya está registrado bajo algún rol.
        HTTPException(500): Si falla la creación del usuario base.
    """
    msg = auth_service.register_school_admin(user_in)
    return APIResponse(data=msg, message="Registro completado para administrador de escuela")


# PUBLIC_ROUTE
@router.post(
    "/register-doctor",
    response_model=APIResponse[str],
    summary="Registrar médico",
    description=(
        "Crea una cuenta de médico escolar con su número de licencia y "
        "especialidad. La cuenta nace inactiva y requiere aprobación. La "
        "asignación a escuelas se gestiona aparte, en 'doctor_to_school'."
    ),
    responses=_REGISTER_RESPONSES,
)
def register_doctor(user_in: DoctorRegister):
    """Registra un médico, pendiente de verificación.

    Multi-tenancy:
        La cuenta nace sin escuela asignada; el alcance se otorga después vía
        'doctor_to_school'.

    RBAC:
        Ruta pública: no requiere token.

    Raises:
        HTTPException(400): Si el correo ya está registrado bajo algún rol.
        HTTPException(500): Si falla la creación del usuario base.
    """
    msg = auth_service.register_doctor(user_in)
    return APIResponse(data=msg, message="Registro completado para doctor")


# PUBLIC_ROUTE
@router.post(
    "/register-parent",
    response_model=APIResponse[str],
    summary="Registrar representante",
    description=(
        "Crea una cuenta de representante (padre, madre o tutor). El "
        "'identity_number' es obligatorio: con él se buscan y vinculan "
        "automáticamente los estudiantes que lo declararon como autorizado. La "
        "cuenta nace inactiva y requiere aprobación."
    ),
    responses=_REGISTER_RESPONSES,
)
def register_parent(user_in: ParentRegister):
    """Registra un representante y lo vincula con sus estudiantes autorizados.

    Multi-tenancy:
        El alcance se deriva de los estudiantes vinculados, no de un 'school_id'
        enviado por el cliente.

    RBAC:
        Ruta pública: no requiere token.

    Raises:
        HTTPException(400): Si el correo ya está registrado, o si falta el
            documento de identidad.
        HTTPException(500): Si falla la creación del usuario base.
    """
    msg = auth_service.register_parent(user_in)
    return APIResponse(data=msg, message="Registro completado para representante")


# PUBLIC_ROUTE
@router.post(
    "/login",
    response_model=APIResponse[Token],
    summary="Login del portal de clientes (obsoleto)",
    description="Alias obsoleto de `POST /auth/client/login`, con el mismo comportamiento. "
    "Se retirará cuando el frontend consuma la nueva ruta.",
    responses=LOGIN_ERROR_RESPONSES,
    deprecated=True,
)
def login(user_in: ClientLoginCreate):
    """Alias obsoleto de POST /api/v1/auth/client/login; delega en `client_login`.

    Multi-tenancy:
        El token no incluye school_id; cada servicio lo resuelve desde el claim sub.

    RBAC:
        Ruta pública. Admite admin_escuela, doctor y representante; admin_sistema
        debe utilizar POST /api/v1/auth/admin/login.

    Raises:
        HTTPException(401): Si las credenciales son incorrectas.
        HTTPException(403): Si la cuenta está eliminada, inactiva o su rol no
            pertenece al portal de clientes.
        HTTPException(500): Si falla una operación interna del login.
    """
    return client_login(user_in)


@router.post(
    "/logout",
    response_model=APIResponse[str],
    summary="Cerrar sesión",
    description=(
        "Revoca el token de acceso actual añadiéndolo a la lista negra. El token "
        "sigue siendo criptográficamente válido hasta su expiración, pero las "
        "siguientes peticiones que lo usen reciben un 401."
    ),
    responses=PROTECTED_RESPONSES,
)
def logout(
    payload: dict = Depends(get_current_user_token),
    credentials: HTTPAuthorizationCredentials = Depends(security)
):
    """Revoca el token con el que se realiza la petición.

    Multi-tenancy:
        Afecta únicamente al token del usuario autenticado.

    RBAC:
        Requiere cualquier rol autenticado mediante Bearer token.

    Raises:
        HTTPException(401): Si el token es inválido, expiró o ya fue revocado.
    """
    token = credentials.credentials
    expires_at = payload.get("exp")
    auth_service.revoke_token(token, expires_at)
    return APIResponse(data="Logout exitoso", message="Sesión cerrada correctamente")


@router.post(
    "/change-password",
    response_model=APIResponse[str],
    summary="Cambiar la contraseña propia",
    description=(
        "Cambia la contraseña del usuario autenticado tras verificar la contraseña "
        "actual. Admite tokens de ambos portales (cualquier rol). La sesión actual "
        "se mantiene; las demás sesiones abiertas siguen siendo válidas hasta que "
        "expiran. Una contraseña actual incorrecta responde 400, no 401."
    ),
    responses=_CHANGE_PASSWORD_RESPONSES,
)
def change_password(
    payload: Annotated[dict, Depends(get_current_user_token)],
    data: PasswordChangeCreate,
):
    """Cambia la contraseña de la cuenta identificada por el claim sub del token.

    Multi-tenancy:
        No aplica: solo modifica la fila de 'user' del usuario autenticado. El cuerpo
        no acepta ningún id de usuario.

    RBAC:
        Requiere cualquier rol autenticado mediante Bearer token, de cualquier portal.

    Raises:
        HTTPException(400): Si la contraseña actual es incorrecta o la nueva coincide con ella.
        HTTPException(401): Si el token es inválido, expiró o fue revocado.
        HTTPException(403): Si la cuenta está inactiva.
        HTTPException(404): Si el usuario no existe o fue eliminado.
        HTTPException(500): Si falla una operación interna del cambio.
    """
    msg = auth_service.change_password(user_id=int(payload["sub"]), data=data)
    return APIResponse(data=msg, message="Contraseña actualizada correctamente")


# PUBLIC_ROUTE
@client_router.post(
    "/login",
    response_model=APIResponse[Token],
    summary="Login del portal de clientes",
    description="Autentica a administradores de escuela, doctores y representantes. "
    "Un admin_sistema es rechazado con 403 y debe usar `POST /auth/admin/login`.",
    responses=LOGIN_ERROR_RESPONSES,
)
def client_login(user_in: ClientLoginCreate):
    """Autentica al usuario del portal de clientes y emite un JWT con scope=client.

    Multi-tenancy:
        El token no incluye school_id; cada servicio lo resuelve desde el claim sub.

    RBAC:
        Ruta pública. Admite admin_escuela, doctor y representante; admin_sistema
        debe utilizar POST /api/v1/auth/admin/login.

    Raises:
        HTTPException(401): Si las credenciales son incorrectas.
        HTTPException(403): Si la cuenta está eliminada, inactiva o su rol no
            pertenece al portal de clientes.
        HTTPException(500): Si falla una operación interna del login.
    """
    token_response = auth_service.authenticate_user(user_in, TokenScope.CLIENT)
    return APIResponse(data=token_response, message="Login exitoso")

# PUBLIC_ROUTE
@admin_router.post(
    "/login",
    response_model=APIResponse[Token],
    summary="Login del portal de administración",
    description="Autentica exclusivamente a administradores de sistema. Cualquier otro rol "
    "es rechazado con 403 antes de emitir el token y debe usar `POST /auth/client/login`.",
    responses=LOGIN_ERROR_RESPONSES,
)
def admin_login(user_in: AdminLoginCreate):
    """Autentica al administrador de sistema y emite un JWT con scope=admin.

    Multi-tenancy:
        No aplica: admin_sistema tiene alcance global sobre las escuelas.

    RBAC:
        Ruta pública. Solo admite admin_sistema; los demás roles utilizan
        POST /api/v1/auth/client/login y son rechazados aquí antes de emitir el JWT.

    Raises:
        HTTPException(401): Si las credenciales son incorrectas.
        HTTPException(403): Si la cuenta está eliminada, inactiva o su rol no
            pertenece al portal de administración.
        HTTPException(500): Si falla una operación interna del login.
    """
    token_response = auth_service.authenticate_user(user_in, TokenScope.ADMIN)
    return APIResponse(data=token_response, message="Login exitoso")
