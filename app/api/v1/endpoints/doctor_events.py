"""Agenda de eventos escolares del médico autenticado."""

from fastapi import APIRouter, Depends, Query

from app.api.deps import get_current_doctor
from app.core.openapi_responses import PROTECTED_RESPONSES
from app.schemas.event import DoctorEventMonthRead
from app.schemas.responses import APIResponse
from app.services.doctor_event_service import doctor_event_service

router = APIRouter()


# ──────────────────────────────────────────────────────────
# GET - Consultar Eventos por Mes (Paginación por fecha)
# ──────────────────────────────────────────────────────────

@router.get("/by-month", response_model=APIResponse[DoctorEventMonthRead],
    summary="Listar los eventos del médico en un mes",
    description=(
        "Retorna los eventos vigentes del mes indicado en los que el médico autenticado "
        "tiene una participación activa, ordenados cronológicamente y con la escuela que "
        "los convocó. Excluye los eventos cancelados, los eliminados y los de otros "
        "médicos. A diferencia de un listado paginado, filtra por rango de fechas: está "
        "pensado para la vista de calendario."
    ),
    responses=PROTECTED_RESPONSES,
)
def get_my_events_by_month(
    year: int = Query(..., ge=2020, le=2100, description="Año a consultar"),
    month: int = Query(..., ge=1, le=12, description="Mes a consultar (1-12)"),
    doctor_payload: dict = Depends(get_current_doctor)
):
    """Lista los eventos del médico autenticado que caen dentro de un mes concreto.

    Multi-tenancy:
        El 'doc_id' se resuelve en el servidor a partir del usuario autenticado
        (doctor_payload['sub']); el cliente nunca lo envía. El alcance lo delimitan
        las participaciones activas de ese médico, así que solo se devuelven los
        eventos de las escuelas que lo convocaron.

    RBAC:
        Requiere rol doctor autenticado mediante Bearer token.

    Raises:
        HTTPException(401): Si el token es inválido, expiró o fue revocado.
        HTTPException(403): Si el rol no es doctor, o si el usuario no tiene un
            perfil de médico asociado.
    """
    user_id = int(doctor_payload["sub"])
    result = doctor_event_service.get_events_by_month(user_id=user_id, year=year, month=month)
    return APIResponse(data=result, message="Eventos del mes obtenidos exitosamente")
