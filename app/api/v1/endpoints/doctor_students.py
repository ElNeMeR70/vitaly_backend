"""Búsqueda de estudiantes del médico autenticado, acotada a sus escuelas vigentes."""

from typing import Annotated, Optional

from fastapi import APIRouter, Depends, Query

from app.api.deps import get_current_doctor
from app.core.openapi_responses import BAD_REQUEST, TENANT_RESPONSES
from app.schemas.enums import BmiStatus
from app.schemas.responses import APIResponse, PaginationMeta
from app.schemas.student import StudentHealthRead
from app.services.audit_service import audit_service
from app.services.student_search_service import student_search_service

router = APIRouter()


# ──────────────────────────────────────────────────────────
# GET - Búsqueda global de estudiantes del médico
# ──────────────────────────────────────────────────────────
#
# NOTA CONOCIDA — divergencia asumida con GET /school-admin/students/resume.
# Las cifras de este buscador no cuadran con las tarjetas del resumen. El resumen
# cuenta filas de student_metrics en vez de estudiantes distintos
# (school_admin_service.py:333), absorbe los "SIN DATOS" dentro de optimal_students
# por resta (:353) y usa una definición más estrecha de caso activo (:346-347).
# Este endpoint reutiliza la clasificación del listado por salón (:554-565) y
# end_date IS NULL (:529), sin modificarlas. La unificación se aborda en #63, #64
# y #65; este endpoint expone la lógica vigente, no la corrige.

@router.get("/search", response_model=APIResponse[list[StudentHealthRead]],
    summary="Buscar estudiantes de las escuelas del médico",
    description=(
        "Búsqueda paginada entre los estudiantes de **todas** las escuelas con las que el "
        "médico mantiene un vínculo vigente, inscritos en el año académico actual. Con "
        "'school_id' puede acotarse a una sola de ellas. Los filtros son opcionales e "
        "independientes, salvo los de salón, que son jerárquicos: 'grade' exige 'category' y "
        "'section' exige ambos. Cada consulta queda registrada en la bitácora de auditoría."
    ),
    responses={**TENANT_RESPONSES, **BAD_REQUEST},
)
def search_students(
    doctor_payload: Annotated[dict, Depends(get_current_doctor)],
    school_id: Optional[int] = Query(
        None,
        description=(
            "Acota la búsqueda a una de las escuelas del médico. Si se omite, busca en todas "
            "sus escuelas vigentes. Una escuela con la que no mantiene vínculo responde 403."
        ),
    ),
    q: Optional[str] = Query(
        None,
        min_length=1,
        description="Texto a buscar en el nombre completo del estudiante. Coincidencia parcial, sin distinguir mayúsculas.",
    ),
    bmi_status: Optional[BmiStatus] = Query(
        None,
        description="Estado nutricional por IMC: DESNUTRIDO (<18.5), OPTIMO (18.5-24.9), SOBREPESO (>24.9) o 'SIN DATOS' (sin medición vigente).",
    ),
    has_active_medical_case: Optional[bool] = Query(
        None,
        description="True devuelve solo estudiantes con un caso médico sin cerrar; False solo los que no lo tienen. Si se omite, ambos.",
    ),
    category: Optional[int] = Query(None, description="ID de la categoría del salón (ej. 1=PREESCOLAR)."),
    grade: Optional[int] = Query(None, description="Nivel o grado del salón. Requiere 'category'."),
    section: Optional[str] = Query(None, description="Letra de la sección (ej. 'A'). Requiere 'category' y 'grade'."),
    page: int = Query(1, ge=1, description="Número de página (empieza en 1)."),
    size: int = Query(20, ge=1, le=100, description="Elementos por página (máximo 100)."),
):
    """Busca estudiantes en las escuelas vigentes del médico autenticado.

    Multi-tenancy:
        El alcance se resuelve en el servidor: del 'sub' del JWT se obtiene el
        'doc_id' y de ahí el conjunto de escuelas con vínculo vigente en
        'doctor_to_school'. El 'school_id' que el cliente puede enviar **no** define
        el alcance: solo acota dentro del conjunto ya resuelto, y se rechaza con 403
        si no pertenece. Suspender a un médico en una escuela le retira el acceso a
        sus estudiantes de inmediato.

    RBAC:
        Requiere rol doctor autenticado mediante Bearer token.

    Auditoría:
        Registra la consulta como acceso de lectura a datos de salud: una entrada por
        escuela consultada, con el médico, los filtros aplicados y el número de
        resultados. No se registran datos identificables de los estudiantes devueltos
        ni el término buscado.

    Raises adicionales:
        HTTPException(500): Si falta 'SUPABASE_SERVICE_KEY'. La vista
            'student_health_status' está denegada a la clave pública, de modo que solo
            el cliente privilegiado puede leerla.

    Raises:
        HTTPException(400): Si se envía 'grade' sin 'category', o 'section' sin
            'category' y 'grade'.
        HTTPException(401): Si el token es inválido, expiró o fue revocado.
        HTTPException(403): Si el rol no es doctor, si el usuario no tiene perfil de
            médico, o si pide una escuela con la que no mantiene vínculo vigente.
        HTTPException(500): Si la consulta a la vista falla.
    """
    user_id = int(doctor_payload["sub"])
    students, total, searched_school_ids = student_search_service.search_for_doctor(
        user_id=user_id,
        page=page,
        size=size,
        school_id=school_id,
        q=q,
        bmi_status=bmi_status,
        has_active_medical_case=has_active_medical_case,
        category=category,
        grade=grade,
        section=section,
    )

    # Acceso de lectura a datos de salud: se audita la consulta, no su contenido.
    # Una fila por escuela consultada, no una sola con el 'school_id' que mandó el
    # cliente: ese parámetro es opcional y viene vacío en el caso normal, y un NULL
    # en esa columna significa "acción global de admin_sistema" (ver el COMMENT de la
    # migración). Además deja utilizable idx_audit_log_school_created, que es el que
    # responde a "qué se consultó en la escuela X". La tabla es append-only: una fila
    # mal escrita no se corrige después.
    # `critical=False`: una bitácora caída no debe tumbar la búsqueda, pero el fallo
    # queda registrado en el log del servidor (ver app/services/audit_service.py).
    audit_metadata = {
        "searched_school_ids": searched_school_ids,
        "filters": {
            "q": bool(q),
            "bmi_status": bmi_status.value if bmi_status else None,
            "has_active_medical_case": has_active_medical_case,
            "category": category,
            "grade": grade,
            "section": section,
        },
        "page": page,
        "size": size,
        # Totales de la consulta completa, no de esta escuela: la búsqueda es una sola
        # y 'searched_school_ids' permite correlacionar las filas entre sí.
        "result_count": len(students),
        "total_matches": total,
    }
    for searched_school_id in searched_school_ids:
        audit_service.record(
            actor=doctor_payload,
            action="read",
            entity="student_health_status",
            school_id=searched_school_id,
            metadata=audit_metadata,
        )

    return APIResponse(
        data=students,
        message="Búsqueda de estudiantes completada exitosamente" if students
                else "No se encontraron estudiantes que cumplan los filtros",
        meta=PaginationMeta(
            page=page,
            size=size,
            total=total,
            total_pages=(total + size - 1) // size,
        ),
    )
