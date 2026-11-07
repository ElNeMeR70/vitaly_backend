"""Gates de autorización de FastAPI.

Este módulo es el único lugar del backend autorizado a comparar el rol del
usuario. Todo endpoint no público declara uno de estos gates mediante
``Depends(...)``; las rutas públicas se marcan con ``# PUBLIC_ROUTE``.
"""

from typing import Annotated

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from jose import jwt, JWTError
from app.core.config import get_settings
from app.schemas.enums import SCOPE_ROLES, TokenScope, UserRole
from app.services.auth_service import auth_service

settings = get_settings()
security = HTTPBearer()

ALGORITHM = "HS256"

def get_current_user_token(credentials: Annotated[HTTPAuthorizationCredentials, Depends(security)]) -> dict:
    """Decodifica y valida el JWT del header ``Authorization: Bearer``.

    Es la base de todos los demás gates: verifica la firma HS256, la expiración y
    que el token no haya sido revocado y que la cuenta siga existiendo sin borrado lógico.

    Args:
        credentials: Credenciales extraídas por ``HTTPBearer``. FastAPI responde
            401 por su cuenta si el header está ausente o malformado.

    Returns:
        dict: Payload decodificado del JWT. Contiene al menos ``sub`` (id de
        usuario como string) y ``role``. No incluye claim de escuela.

    Raises:
        HTTPException: 401 si el token es inválido, expiró, fue revocado o su
            cuenta fue eliminada; 500 si no puede comprobarse la cuenta.
    """
    try:
        payload = jwt.decode(credentials.credentials, settings.SECRET_KEY, algorithms=[ALGORITHM])
        subject = payload.get("sub")
        expires_at = payload.get("exp")
        if (
            not isinstance(subject, str) or not subject.isascii() or not subject.isdecimal()
            or int(subject) <= 0 or type(expires_at) is not int
        ):
            raise JWTError("Claims de sesión inválidos")
        
        # Verificar si el token ha sido revocado (logout)
        if auth_service.is_token_revoked(credentials.credentials):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="El token ha sido revocado. Por favor inicia sesión de nuevo.",
                headers={"WWW-Authenticate": "Bearer"},
            )
            
        auth_service.assert_account_available(int(subject))
        return payload
    except (JWTError, TypeError, ValueError):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Credenciales inválidas o token expirado",
            headers={"WWW-Authenticate": "Bearer"},
        )

def _require_scope(payload: dict, scope: TokenScope) -> dict:
    """Verifica que el token pertenece al portal y a uno de sus roles.

    Args:
        payload: Claims del JWT validado por get_current_user_token.
        scope: Portal requerido para acceder al recurso.

    Returns:
        dict: El mismo payload cuando su scope y rol corresponden al portal.

    Raises:
        HTTPException: 401 si falta scope y se requiere un nuevo login; 403 si
            el token procede de otro portal o su rol no pertenece a este.
    """
    token_scope = payload.get("scope")
    if token_scope is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="El token no indica su procedencia. Por favor inicia sesión de nuevo.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    allowed_roles = {role.value for role in SCOPE_ROLES[scope]}
    if token_scope != scope.value or payload.get("role") not in allowed_roles:
        portal = "administración" if scope == TokenScope.ADMIN else "clientes"
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Este recurso requiere un token emitido por el portal de {portal}."
        )
    return payload

def get_current_admin(payload: Annotated[dict, Depends(get_current_user_token)]) -> dict:
    """Exige un token del portal de administración, reservado a admin_sistema.

    Args:
        payload: Claims del JWT con firma, expiración y revocación verificadas.

    Returns:
        dict: Payload validado con scope=admin y rol admin_sistema.

    Raises:
        HTTPException: 401 si el token es inválido o no incluye scope; 403 si
            el scope o el rol no corresponden al portal de administración.
    """
    return _require_scope(payload, TokenScope.ADMIN)

def get_current_client(payload: Annotated[dict, Depends(get_current_user_token)]) -> dict:
    """Exige un token del portal de clientes.

    Admite admin_escuela, doctor y representante. El gate de rol posterior
    restringe el recurso; cada servicio resuelve la pertenencia desde sub.

    Args:
        payload: Claims del JWT con firma, expiración y revocación verificadas.

    Returns:
        dict: Payload validado con scope=client y un rol de este portal.

    Raises:
        HTTPException: 401 si el token es inválido o no incluye scope; 403 si
            el scope o el rol no corresponden al portal de clientes.
    """
    return _require_scope(payload, TokenScope.CLIENT)

def _require_role(payload: dict, role: UserRole) -> dict:
    """Exige un rol concreto sobre un payload validado por su portal.

    Args:
        payload: Claims del JWT cuyo scope ya ha sido comprobado.
        role: Rol requerido para ejecutar la operación.

    Returns:
        dict: El mismo payload cuando su rol coincide con el requerido.

    Raises:
        HTTPException: 403 si el usuario no tiene el rol requerido.
    """
    if payload.get("role") != role.value:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"No tienes los permisos suficientes para esta acción. Se requiere rol de {role.value}."
        )
    return payload

def get_current_admin_sistema(payload: Annotated[dict, Depends(get_current_admin)]) -> dict:
    """Exige que el usuario autenticado sea un administrador de sistema.

    Su alcance es global: puede auditar cualquier escuela, por lo que sus rutas sí
    admiten recibir un ``school_id`` por path.

    Args:
        payload: Payload del JWT validado por el gate del portal correspondiente.

    Returns:
        dict: El mismo payload, para que el handler lea ``int(payload["sub"])``.

    Raises:
        HTTPException: 401 si el token es inválido, expiró, fue revocado o no incluye scope.
        HTTPException: 403 si el scope no corresponde al portal o el rol del usuario no es ``admin_sistema``.
    """
    return _require_role(payload, UserRole.ADMIN_SISTEMA)

def get_current_admin_escuela(payload: Annotated[dict, Depends(get_current_client)]) -> dict:
    """Exige que el usuario autenticado sea un administrador de escuela.

    Su alcance se limita a la escuela vinculada en ``school_administrator``; el
    ``school_id`` se resuelve en el servidor a partir de ``sub``, nunca del cliente.

    Args:
        payload: Payload del JWT validado por el gate del portal correspondiente.

    Returns:
        dict: El mismo payload, para que el handler lea ``int(payload["sub"])``.

    Raises:
        HTTPException: 401 si el token es inválido, expiró, fue revocado o no incluye scope.
        HTTPException: 403 si el scope no corresponde al portal o el rol del usuario no es ``admin_escuela``.
    """
    return _require_role(payload, UserRole.ADMIN_ESCUELA)

def get_current_representante(payload: Annotated[dict, Depends(get_current_client)]) -> dict:
    """Exige que el usuario autenticado sea un representante.

    Su alcance se limita a los estudiantes vinculados a su registro en ``parent``.

    Args:
        payload: Payload del JWT validado por el gate del portal correspondiente.

    Returns:
        dict: El mismo payload, para que el handler lea ``int(payload["sub"])``.

    Raises:
        HTTPException: 401 si el token es inválido, expiró, fue revocado o no incluye scope.
        HTTPException: 403 si el scope no corresponde al portal o el rol del usuario no es ``representante``.
    """
    return _require_role(payload, UserRole.REPRESENTANTE)

def get_current_doctor(payload: Annotated[dict, Depends(get_current_client)]) -> dict:
    """Exige que el usuario autenticado sea un médico.

    Su alcance se limita a las escuelas vinculadas en ``doctor_to_school``.

    Args:
        payload: Payload del JWT validado por el gate del portal correspondiente.

    Returns:
        dict: El mismo payload, para que el handler lea ``int(payload["sub"])``.

    Raises:
        HTTPException: 401 si el token es inválido, expiró, fue revocado o no incluye scope.
        HTTPException: 403 si el scope no corresponde al portal o el rol del usuario no es ``doctor``.
    """
    return _require_role(payload, UserRole.DOCTOR)
