"""Eventos escolares de la escuela del administrador autenticado."""

from fastapi import APIRouter, Depends, Query

from app.api.deps import get_current_admin_escuela
from app.core.openapi_responses import (
    BAD_REQUEST,
    EVENT_SCHEDULE_UNPROCESSABLE,
    TENANT_DETAIL_RESPONSES,
    TENANT_RESPONSES,
)
from app.schemas.responses import APIResponse
from app.schemas.event import EventCreate, EventUpdate
from app.services.school_admin_event_service import school_admin_event_service

router = APIRouter()


# ──────────────────────────────────────────────────────────
# POST - Crear Evento
# ──────────────────────────────────────────────────────────

@router.post("/", response_model=APIResponse[dict],
    summary="Crear evento",
    description=(
        "Crea un evento en la escuela del administrador y genera una fila en "
        "'event_participation' por cada salón y cada médico convocado. Todos los salones y "
        "médicos indicados deben pertenecer a la escuela; la fecha de fin debe ser posterior "
        "a la de inicio y el inicio debe respetar la anticipación mínima configurada en el "
        "sistema, medida contra el reloj del servidor."
    ),
    responses={**TENANT_RESPONSES, **BAD_REQUEST, **EVENT_SCHEDULE_UNPROCESSABLE},
)
def create_event(
    payload: EventCreate,
    admin_payload: dict = Depends(get_current_admin_escuela)
):
    """Crea un evento y sus participaciones de salones y médicos.

    Multi-tenancy:
        El 'sch_admin_id' y el 'school_id' se resuelven en el servidor a partir del
        usuario autenticado (admin_payload['sub']); el cliente nunca los envía. Los 'classroom_ids' y 'doctor_ids' recibidos se validan contra la
        escuela resuelta antes de insertar nada.

    RBAC:
        Requiere rol admin_escuela autenticado mediante Bearer token.

    Raises:
        HTTPException(401): Si el token es inválido, expiró o fue revocado.
        HTTPException(403): Si el rol no es admin_escuela, o si el usuario no está
            vinculado a ninguna escuela.
        FieldValidationError(422): Si 'init_time' no respeta la anticipación mínima
            vigente. El error viaja en el envelope con errors[0].loc = ["body",
            "init_time"] y type = "event_schedule_unavailable"; el mensaje es
            genérico y no revela el margen ni el umbral calculado.
        HTTPException(400): Si algún salón o médico no pertenece a la escuela.
        HTTPException(500): Si la inserción del evento no devuelve la fila creada.
    """
    user_id = int(admin_payload["sub"])
    result = school_admin_event_service.create_event(user_id=user_id, payload=payload)
    return APIResponse(data=result, message="Evento creado exitosamente")


# ──────────────────────────────────────────────────────────
# GET - Consultar Eventos (Paginación normal)
# ──────────────────────────────────────────────────────────

@router.get("/", response_model=APIResponse[dict],
    summary="Listar eventos",
    description=(
        "Retorna de forma paginada los eventos vigentes (ni cancelados ni eliminados) de la "
        "escuela del administrador, ordenados por fecha de inicio descendente."
    ),
    responses=TENANT_RESPONSES,
)
def get_events(
    page: int = Query(1, ge=1, description="Número de página"),
    size: int = Query(20, ge=1, le=100, description="Elementos por página"),
    admin_payload: dict = Depends(get_current_admin_escuela)
):
    """Lista de forma paginada los eventos vigentes de la escuela.

    Multi-tenancy:
        El 'sch_admin_id' y el 'school_id' se resuelven en el servidor a partir del
        usuario autenticado (admin_payload['sub']); el cliente nunca los envía.

    RBAC:
        Requiere rol admin_escuela autenticado mediante Bearer token.

    Raises:
        HTTPException(401): Si el token es inválido, expiró o fue revocado.
        HTTPException(403): Si el rol no es admin_escuela, o si el usuario no está
            vinculado a ninguna escuela.
    """
    user_id = int(admin_payload["sub"])
    result = school_admin_event_service.get_events(user_id=user_id, page=page, size=size)
    return APIResponse(data=result, message="Eventos obtenidos exitosamente")


# ──────────────────────────────────────────────────────────
# GET - Consultar Eventos por Mes (Paginación por fecha)
# ──────────────────────────────────────────────────────────

@router.get("/by-month", response_model=APIResponse[dict],
    summary="Listar eventos de un mes",
    description=(
        "Retorna todos los eventos vigentes del mes indicado, ordenados cronológicamente. A "
        "diferencia del listado normal, filtra por rango de fechas en lugar de paginar por "
        "offset: está pensado para la vista de calendario."
    ),
    responses=TENANT_RESPONSES,
)
def get_events_by_month(
    year: int = Query(..., ge=2020, le=2100, description="Año a consultar"),
    month: int = Query(..., ge=1, le=12, description="Mes a consultar (1-12)"),
    admin_payload: dict = Depends(get_current_admin_escuela)
):
    """Lista los eventos de la escuela que caen dentro de un mes concreto.

    Multi-tenancy:
        El 'sch_admin_id' y el 'school_id' se resuelven en el servidor a partir del
        usuario autenticado (admin_payload['sub']); el cliente nunca los envía.

    RBAC:
        Requiere rol admin_escuela autenticado mediante Bearer token.

    Raises:
        HTTPException(401): Si el token es inválido, expiró o fue revocado.
        HTTPException(403): Si el rol no es admin_escuela, o si el usuario no está
            vinculado a ninguna escuela.
    """
    user_id = int(admin_payload["sub"])
    result = school_admin_event_service.get_events_by_month(user_id=user_id, year=year, month=month)
    return APIResponse(data=result, message="Eventos del mes obtenidos exitosamente")


# ──────────────────────────────────────────────────────────
# GET - Detalle de un Evento
# ──────────────────────────────────────────────────────────

@router.get("/{event_id}", response_model=APIResponse[dict],
    summary="Obtener detalle de un evento",
    description=(
        "Retorna un evento con sus datos base (incluidas `created_at` y `updated_at`, esta "
        "última en ISO 8601 con zona horaria o `null` si no tiene valor), los salones "
        "vinculados (grado, nivel y sección), los médicos vinculados (nombre y especialidad) "
        "y el administrador que lo creó. Solo expone eventos vigentes de la escuela del "
        "administrador."
    ),
    responses=TENANT_DETAIL_RESPONSES,
)
def get_event_detail(
    event_id: int,
    admin_payload: dict = Depends(get_current_admin_escuela)
):
    """Devuelve el detalle de un evento con sus participantes.

    Multi-tenancy:
        El 'sch_admin_id' y el 'school_id' se resuelven en el servidor a partir del
        usuario autenticado (admin_payload['sub']); el cliente nunca los envía. La pertenencia del evento es indirecta: debe tener al menos una
        participación registrada a nombre del administrador autenticado.

    RBAC:
        Requiere rol admin_escuela autenticado mediante Bearer token.

    Raises:
        HTTPException(401): Si el token es inválido, expiró o fue revocado.
        HTTPException(403): Si el rol no es admin_escuela, o si el usuario no está
            vinculado a ninguna escuela. También 403 si el evento pertenece a otra escuela.
        HTTPException(404): Si no existe un evento activo con ese ID.
    """
    user_id = int(admin_payload["sub"])
    result = school_admin_event_service.get_event_detail(user_id=user_id, event_id=event_id)
    return APIResponse(data=result, message="Detalle del evento obtenido exitosamente")


# ──────────────────────────────────────────────────────────
# PATCH - Cancelar Evento
# ──────────────────────────────────────────────────────────

@router.patch("/{event_id}/cancel", response_model=APIResponse[dict],
    summary="Cancelar evento",
    description=(
        "Marca el evento y todas sus participaciones como cancelados. Es una cancelación "
        "lógica: el registro se conserva, pero el evento deja de aparecer en los endpoints de "
        "consulta."
    ),
    responses=TENANT_DETAIL_RESPONSES,
)
def cancel_event(
    event_id: int,
    admin_payload: dict = Depends(get_current_admin_escuela)
):
    """Cancela un evento y, en cascada, sus participaciones.

    Multi-tenancy:
        El 'sch_admin_id' y el 'school_id' se resuelven en el servidor a partir del
        usuario autenticado (admin_payload['sub']); el cliente nunca los envía. La pertenencia del evento es indirecta: debe tener al menos una
        participación registrada a nombre del administrador autenticado.

    RBAC:
        Requiere rol admin_escuela autenticado mediante Bearer token.

    Raises:
        HTTPException(401): Si el token es inválido, expiró o fue revocado.
        HTTPException(403): Si el rol no es admin_escuela, o si el usuario no está
            vinculado a ninguna escuela. También 403 si el evento pertenece a otra escuela.
        HTTPException(404): Si no existe un evento activo con ese ID.
    """
    user_id = int(admin_payload["sub"])
    result = school_admin_event_service.cancel_event(user_id=user_id, event_id=event_id)
    return APIResponse(data=result, message="Evento cancelado exitosamente")


# ──────────────────────────────────────────────────────────
# PUT - Modificar datos base del Evento
# ──────────────────────────────────────────────────────────

@router.put("/{event_id}", response_model=APIResponse[dict],
    summary="Actualizar evento",
    description=(
        "Modifica los datos base del evento (título, descripción y fechas). No altera las "
        "participaciones de salones ni médicos. Solo se aplican los campos enviados y se "
        "revalida que la fecha de fin siga siendo posterior a la de inicio. Si el cuerpo "
        "trae 'init_time', el nuevo inicio debe respetar además la anticipación mínima "
        "configurada; las ediciones que no tocan el inicio no la revalidan."
    ),
    responses={**TENANT_DETAIL_RESPONSES, **BAD_REQUEST, **EVENT_SCHEDULE_UNPROCESSABLE},
)
def update_event(
    event_id: int,
    payload: EventUpdate,
    admin_payload: dict = Depends(get_current_admin_escuela)
):
    """Actualiza los datos base de un evento, sin tocar sus participaciones.

    Multi-tenancy:
        El 'sch_admin_id' y el 'school_id' se resuelven en el servidor a partir del
        usuario autenticado (admin_payload['sub']); el cliente nunca los envía. La pertenencia del evento es indirecta: debe tener al menos una
        participación registrada a nombre del administrador autenticado.

    RBAC:
        Requiere rol admin_escuela autenticado mediante Bearer token.

    Raises:
        HTTPException(401): Si el token es inválido, expiró o fue revocado.
        HTTPException(403): Si el rol no es admin_escuela, o si el usuario no está
            vinculado a ninguna escuela. También 403 si el evento pertenece a otra escuela.
        HTTPException(404): Si no existe un evento activo con ese ID.
        FieldValidationError(422): Si se envía un 'init_time' que no respeta la
            anticipación mínima vigente.
        HTTPException(400): Si no se envió ningún campo válido, o si la fecha de
            finalización no es posterior a la de inicio.
    """
    user_id = int(admin_payload["sub"])
    result = school_admin_event_service.update_event(
        user_id=user_id,
        event_id=event_id,
        payload=payload.model_dump(exclude_unset=True)
    )
    return APIResponse(data=result, message="Evento actualizado exitosamente")
