"""Administración global de usuarios por parte del administrador de sistema."""

from typing import Optional

from fastapi import APIRouter, Body, Depends, Query

from app.api.deps import get_current_admin_sistema
from app.core.openapi_responses import (
    BAD_REQUEST,
    PROTECTED_DETAIL_RESPONSES,
    PROTECTED_RESPONSES,
)
from app.schemas.responses import APIResponse
from app.schemas.admin import PaginatedUsersResponse, ApproveUserRequest, BulkApproveUserRequest, UserDetailsResponse, ToggleUserRequest
from app.services.admin_service import admin_service

router = APIRouter()


@router.get(
    "/users",
    response_model=APIResponse[PaginatedUsersResponse],
    summary="Listar todos los usuarios",
    description=(
        "Retorna todos los usuarios de la plataforma de forma paginada, sin "
        "distinguir rol ni escuela. La paginación viaja anidada dentro de 'data' "
        "(formato legado)."
    ),
    responses=PROTECTED_RESPONSES,
)
def get_all_users(
    page: int = Query(1, ge=1, description="Número de página para la paginación"),
    size: int = Query(20, ge=1, le=100, description="Cantidad de usuarios a mostrar por página"),
    admin_payload: dict = Depends(get_current_admin_sistema)
):
    """Lista de forma paginada todos los usuarios registrados.

    Multi-tenancy:
        Vista global, sin filtro por escuela: es una ruta de auditoría del
        administrador de sistema.

    RBAC:
        Requiere rol admin_sistema autenticado mediante Bearer token.

    Raises:
        HTTPException(401): Si el token es inválido, expiró o fue revocado.
        HTTPException(403): Si el rol no es admin_sistema.
    """
    result = admin_service.get_users(page=page, size=size, pending_only=False)
    return APIResponse(data=result, message="Lista de todos los usuarios obtenida exitosamente")


@router.get(
    "/users/pending",
    response_model=APIResponse[PaginatedUsersResponse],
    summary="Listar usuarios pendientes de aprobación",
    description=(
        "Retorna de forma paginada las cuentas que aún no han sido aprobadas "
        "(is_active=False) y por tanto no pueden iniciar sesión."
    ),
    responses=PROTECTED_RESPONSES,
)
def get_pending_users(
    page: int = Query(1, ge=1, description="Número de página para la paginación"),
    size: int = Query(20, ge=1, le=100, description="Cantidad de usuarios a mostrar por página"),
    admin_payload: dict = Depends(get_current_admin_sistema)
):
    """Lista de forma paginada las cuentas pendientes de aprobación.

    Multi-tenancy:
        Vista global, sin filtro por escuela.

    RBAC:
        Requiere rol admin_sistema autenticado mediante Bearer token.

    Raises:
        HTTPException(401): Si el token es inválido, expiró o fue revocado.
        HTTPException(403): Si el rol no es admin_sistema.
    """
    result = admin_service.get_users(page=page, size=size, pending_only=True)
    return APIResponse(data=result, message="Usuarios pendientes obtenidos exitosamente")


@router.patch(
    "/users/approve",
    response_model=APIResponse[str],
    summary="Aprobar un usuario",
    description=(
        "Aprueba la cuenta indicada en el cuerpo, pasando su 'is_active' a True. "
        "El usuario debe existir, no estar eliminado y no estar ya aprobado."
    ),
    responses={**PROTECTED_DETAIL_RESPONSES, **BAD_REQUEST},
)
def approve_user(
    body: ApproveUserRequest,
    admin_payload: dict = Depends(get_current_admin_sistema)
):
    """Aprueba una cuenta pendiente, habilitándola para iniciar sesión.

    Multi-tenancy:
        Opera sobre cualquier usuario del sistema, sin filtro por escuela.

    RBAC:
        Requiere rol admin_sistema autenticado mediante Bearer token.

    Raises:
        HTTPException(401): Si el token es inválido, expiró o fue revocado.
        HTTPException(403): Si el rol no es admin_sistema.
        HTTPException(404): Si el usuario a aprobar no existe.
        HTTPException(400): Si el usuario está eliminado o ya estaba aprobado.
    """
    msg = admin_service.approve_user(user_id=body.user_id_to_approve, admin_id=int(admin_payload["sub"]))
    return APIResponse(data=msg, message="Aprobación completada")


@router.patch(
    "/users/approve/bulk",
    response_model=APIResponse[str],
    summary="Aprobar usuarios en lote",
    description=(
        "Aprueba en una sola operación todas las cuentas de la lista que estén "
        "inactivas y no eliminadas. Los IDs inexistentes o ya aprobados se "
        "ignoran sin error; la respuesta indica cuántas cuentas se aprobaron."
    ),
    responses=PROTECTED_RESPONSES,
)
def approve_users_bulk(
    body: BulkApproveUserRequest,
    admin_payload: dict = Depends(get_current_admin_sistema)
):
    """Aprueba varias cuentas pendientes en una sola operación.

    Multi-tenancy:
        Opera sobre cualquier usuario del sistema, sin filtro por escuela.

    RBAC:
        Requiere rol admin_sistema autenticado mediante Bearer token.

    Raises:
        HTTPException(401): Si el token es inválido, expiró o fue revocado.
        HTTPException(403): Si el rol no es admin_sistema.
    """
    msg = admin_service.approve_users_bulk(user_ids=body.user_ids_to_approve, admin_id=int(admin_payload["sub"]))
    return APIResponse(data=msg, message="Aprobación masiva completada")


@router.get(
    "/users/{user_id}/details",
    response_model=APIResponse[UserDetailsResponse],
    summary="Obtener detalle de un usuario",
    description=(
        "Retorna los datos de un usuario junto con el detalle propio de su rol: "
        "según sea doctor, representante, admin_escuela o admin_sistema, anexa la "
        "fila de la tabla relacionada."
    ),
    responses=PROTECTED_DETAIL_RESPONSES,
)
def get_user_details(
    user_id: int,
    admin_payload: dict = Depends(get_current_admin_sistema)
):
    """Devuelve un usuario con el detalle específico de su rol.

    Multi-tenancy:
        Vista global: puede consultar usuarios de cualquier escuela.

    RBAC:
        Requiere rol admin_sistema autenticado mediante Bearer token.

    Raises:
        HTTPException(401): Si el token es inválido, expiró o fue revocado.
        HTTPException(403): Si el rol no es admin_sistema.
        HTTPException(404): Si el usuario no existe.
    """
    result = admin_service.get_user_details(user_id=user_id)
    return APIResponse(data=result, message="Detalle del usuario obtenido exitosamente.")


@router.patch(
    "/users/{user_id}/toggle-active",
    response_model=APIResponse[str],
    summary="Alternar el estado activo de un usuario",
    description=(
        "Invierte el campo 'is_active' del usuario indicado. Dos salvaguardas: un "
        "administrador no puede deshabilitarse a sí mismo, ni alterar a otro "
        "admin_sistema cuyo 'level_privilege' sea igual o superior al suyo."
    ),
    responses={**PROTECTED_DETAIL_RESPONSES, **BAD_REQUEST},
)
def toggle_user_active(
    user_id: int,
    body: Optional[ToggleUserRequest] = Body(default=None),
    admin_payload: dict = Depends(get_current_admin_sistema)
):
    """Alterna el estado activo/inactivo de un usuario.

    Multi-tenancy:
        Opera sobre cualquier usuario del sistema, sin filtro por escuela.

    RBAC:
        Requiere rol admin_sistema autenticado mediante Bearer token.

    Raises:
        HTTPException(401): Si el token es inválido, expiró o fue revocado.
        HTTPException(403): Si el rol no es admin_sistema, o si el usuario objetivo
            es un admin_sistema de nivel de privilegio igual o superior.
        HTTPException(404): Si el usuario objetivo no existe.
        HTTPException(400): Si el administrador intenta deshabilitarse a sí mismo.
    """
    msg = admin_service.toggle_user_status(user_id=user_id, request_admin_id=int(admin_payload["sub"]))
    return APIResponse(data=msg, message="Estado activo de usuario alternado")
