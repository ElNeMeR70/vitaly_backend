"""Estudiantes y representantes de la escuela del administrador autenticado."""

from typing import Annotated, Optional

from fastapi import APIRouter, Depends, Query

from app.api.deps import get_current_admin_escuela
from app.core.openapi_responses import BAD_REQUEST, TENANT_DETAIL_RESPONSES, TENANT_RESPONSES
from app.schemas.enums import BmiStatus
from app.schemas.responses import APIResponse, PaginationMeta
from app.schemas.student import StudentHealthRead, StudentUpdateSchoolAdmin
from app.services.school_admin_service import school_admin_service
from app.services.student_search_service import student_search_service

router = APIRouter()








# ──────────────────────────────────────────────────────────
# GET - Detalle completo de un estudiante
# ──────────────────────────────────────────────────────────

@router.get("/students/{student_id}", response_model=APIResponse[dict],
    summary="Obtener detalle de un estudiante",
    description=(
        "Ficha completa de un estudiante de la escuela: sus datos base, los representantes "
        "vinculados (null si no tiene) y sus métricas de salud más recientes con el estado "
        "nutricional OPTIMO, OBESO o DESNUTRIDO (null si no hay registros)."
    ),
    responses=TENANT_DETAIL_RESPONSES,
)
def get_student_detail(
    student_id: int,
    admin_payload: dict = Depends(get_current_admin_escuela)
):
    """Devuelve la ficha completa de un estudiante de la escuela.

    Multi-tenancy:
        El 'school_id' se resuelve en el servidor a partir del usuario autenticado
        (admin_payload['sub']); el cliente nunca lo envía. La pertenencia del estudiante es indirecta: debe estar inscrito, vía
        'classroom_registration', en un salón de la escuela del administrador.

    RBAC:
        Requiere rol admin_escuela autenticado mediante Bearer token.

    Raises:
        HTTPException(401): Si el token es inválido, expiró o fue revocado.
        HTTPException(403): Si el rol no es admin_escuela, o si el usuario no está
            vinculado a ninguna escuela. También 403 si el estudiante no está inscrito en ningún salón o
            pertenece a otra escuela.
        HTTPException(404): Si no existe un estudiante activo con ese ID.
    """
    user_id = int(admin_payload["sub"])
    result = school_admin_service.get_student_detail(user_id=user_id, student_id=student_id)
    return APIResponse(data=result, message="Detalle del estudiante obtenido exitosamente")


# ──────────────────────────────────────────────────────────
# GET - Detalle de un Representante y sus Hijos
# ──────────────────────────────────────────────────────────

@router.get("/representatives/{parent_id}", response_model=APIResponse[dict],
    summary="Obtener detalle de un representante",
    description=(
        "Datos de un representante junto con el conteo y la lista de sus hijos. Por cada hijo "
        "devuelve id, nombre completo, fecha de nacimiento, grado actual, estado nutricional "
        "(DESNUTRIDO, OPTIMO, OBESO o SIN DATOS) y si tiene algún caso médico activo."
    ),
    responses=TENANT_DETAIL_RESPONSES,
)
def get_parent_detail(
    parent_id: int,
    admin_payload: dict = Depends(get_current_admin_escuela)
):
    """Devuelve el detalle de un representante y de sus hijos en la escuela.

    Multi-tenancy:
        El 'school_id' se resuelve en el servidor a partir del usuario autenticado
        (admin_payload['sub']); el cliente nunca lo envía. El representante solo es visible si al menos uno de sus estudiantes
        pertenece a la escuela del administrador.

    RBAC:
        Requiere rol admin_escuela autenticado mediante Bearer token.

    Raises:
        HTTPException(401): Si el token es inválido, expiró o fue revocado.
        HTTPException(403): Si el rol no es admin_escuela, o si el usuario no está
            vinculado a ninguna escuela. También 403 si el representante no tiene estudiantes asociados, o si
            ninguno de ellos pertenece a la escuela del administrador.
        HTTPException(404): Si no existe un representante activo con ese ID.
    """
    user_id = int(admin_payload["sub"])
    result = school_admin_service.get_parent_detail(
        user_id=user_id,
        parent_id=parent_id
    )
    return APIResponse(data=result, message="Detalle del representante obtenido exitosamente")


# ──────────────────────────────────────────────────────────
# PUT - Actualizar perfil del estudiante (incluyendo representante)
# ──────────────────────────────────────────────────────────

@router.put("/students/{student_id}", response_model=APIResponse[dict],
    summary="Actualizar perfil de un estudiante",
    description=(
        "Modifica los campos enviados del estudiante: nombre, apellido, cédula, género, tipo "
        "de sangre y fecha de nacimiento. Además, 'representative_id' reemplaza al "
        "representante actual en 'student_representative'."
    ),
    responses={**TENANT_RESPONSES, **BAD_REQUEST},
)
def update_student_profile(
    student_id: int,
    payload: StudentUpdateSchoolAdmin,
    admin_payload: dict = Depends(get_current_admin_escuela)
):
    """Actualiza el perfil de un estudiante y, opcionalmente, su representante.

    Multi-tenancy:
        El 'school_id' se resuelve en el servidor a partir del usuario autenticado
        (admin_payload['sub']); el cliente nunca lo envía. La pertenencia del estudiante es indirecta: debe estar inscrito, vía
        'classroom_registration', en un salón de la escuela del administrador.

    RBAC:
        Requiere rol admin_escuela autenticado mediante Bearer token.

    Raises:
        HTTPException(401): Si el token es inválido, expiró o fue revocado.
        HTTPException(403): Si el rol no es admin_escuela, o si el usuario no está
            vinculado a ninguna escuela. También 403 si el estudiante no pertenece a la escuela del
            administrador.
        HTTPException(400): Si el 'representative_id' indicado no existe.
    """
    user_id = int(admin_payload["sub"])
    result = school_admin_service.update_student_profile(
        user_id=user_id,
        student_id=student_id,
        payload=payload.model_dump(exclude_unset=True)
    )
    return APIResponse(data=result, message="Perfil del estudiante y representante actualizados exitosamente")


# ──────────────────────────────────────────────────────────
# GET - Búsqueda global de Representantes (Paginado)
# ──────────────────────────────────────────────────────────

@router.get("/search/representatives", response_model=APIResponse[dict],
    summary="Buscar representantes",
    description=(
        "Búsqueda paginada de representantes por coincidencia parcial de nombre o apellido. "
        "Sirve para localizar el 'representative_id' que luego se usa al reasignar el "
        "representante de un estudiante. Devuelve email, nombre, apellido, representative_id, "
        "user_id y ocupación.  **Alcance:** la búsqueda recorre todos los representantes del "
        "sistema, no solo los de la escuela del administrador."
    ),
    responses=TENANT_RESPONSES,
)
def search_representatives(
    query: str = Query(..., min_length=1, description="Nombre o Apellido del representante"),
    page: int = Query(1, ge=1, description="Número de página"),
    size: int = Query(20, ge=1, le=100, description="Elementos por página"),
    admin_payload: dict = Depends(get_current_admin_escuela)
):
    """Busca representantes por nombre o apellido, de forma paginada.

    Multi-tenancy:
        Excepción al patrón del módulo: el servicio no recibe el 'user_id' y por
        tanto no filtra por escuela, sino que recorre todos los representantes del
        sistema. Es el comportamiento vigente, necesario para poder asignar a un
        estudiante un representante aún no vinculado a la escuela.

    RBAC:
        Requiere rol admin_escuela autenticado mediante Bearer token.

    Raises:
        HTTPException(401): Si el token es inválido, expiró o fue revocado.
        HTTPException(403): Si el rol no es admin_escuela, o si el usuario no está
            vinculado a ninguna escuela.
    """
    result = school_admin_service.search_representatives(
        query=query,
        page=page,
        size=size
    )
    return APIResponse(data=result, message="Búsqueda completada exitosamente")


# ──────────────────────────────────────────────────────────
# PATCH - Activar Estudiante
# ──────────────────────────────────────────────────────────

@router.patch("/students/{student_id}/activate", response_model=APIResponse[dict],
    summary="Activar estudiante",
    description=(
        "Reactiva a un estudiante previamente desactivado de la escuela. No puede aplicarse a "
        "un estudiante eliminado."
    ),
    responses={**TENANT_DETAIL_RESPONSES, **BAD_REQUEST},
)
def activate_student(
    student_id: int,
    admin_payload: dict = Depends(get_current_admin_escuela)
):
    """Activa a un estudiante de la escuela.

    Multi-tenancy:
        El 'school_id' se resuelve en el servidor a partir del usuario autenticado
        (admin_payload['sub']); el cliente nunca lo envía. La pertenencia del estudiante es indirecta: debe estar inscrito, vía
        'classroom_registration', en un salón de la escuela del administrador.

    RBAC:
        Requiere rol admin_escuela autenticado mediante Bearer token.

    Raises:
        HTTPException(401): Si el token es inválido, expiró o fue revocado.
        HTTPException(403): Si el rol no es admin_escuela, o si el usuario no está
            vinculado a ninguna escuela. También 403 si el estudiante no está inscrito en ningún salón o
            pertenece a otra escuela.
        HTTPException(404): Si el estudiante no existe.
        HTTPException(400): Si se intenta activar a un estudiante eliminado.
    """
    user_id = int(admin_payload["sub"])
    result = school_admin_service.activate_student(
        user_id=user_id,
        student_id=student_id
    )
    return APIResponse(data=result, message="Estudiante activado exitosamente")


# ──────────────────────────────────────────────────────────
# PATCH - Desactivar Estudiante
# ──────────────────────────────────────────────────────────

@router.patch("/students/{student_id}/deactivate", response_model=APIResponse[dict],
    summary="Desactivar estudiante",
    description=(
        "Desactiva a un estudiante activo de la escuela. No puede aplicarse a un estudiante "
        "eliminado."
    ),
    responses={**TENANT_DETAIL_RESPONSES, **BAD_REQUEST},
)
def deactivate_student(
    student_id: int,
    admin_payload: dict = Depends(get_current_admin_escuela)
):
    """Desactiva a un estudiante de la escuela.

    Multi-tenancy:
        El 'school_id' se resuelve en el servidor a partir del usuario autenticado
        (admin_payload['sub']); el cliente nunca lo envía. La pertenencia del estudiante es indirecta: debe estar inscrito, vía
        'classroom_registration', en un salón de la escuela del administrador.

    RBAC:
        Requiere rol admin_escuela autenticado mediante Bearer token.

    Raises:
        HTTPException(401): Si el token es inválido, expiró o fue revocado.
        HTTPException(403): Si el rol no es admin_escuela, o si el usuario no está
            vinculado a ninguna escuela. También 403 si el estudiante no está inscrito en ningún salón o
            pertenece a otra escuela.
        HTTPException(404): Si el estudiante no existe.
        HTTPException(400): Si se intenta modificar un estudiante eliminado.
    """
    user_id = int(admin_payload["sub"])
    result = school_admin_service.deactivate_student(
        user_id=user_id,
        student_id=student_id
    )
    return APIResponse(data=result, message="Estudiante desactivado exitosamente")


# ──────────────────────────────────────────────────────────
# GET - Búsqueda global de estudiantes de la escuela
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
#
# La ruta es /search/students y no /students/search porque /students/{student_id}
# ya existe en este módulo y capturaría "search" como id (422). Además mantiene la
# simetría con /search/representatives, que vive unas líneas más arriba.

@router.get("/search/students", response_model=APIResponse[list[StudentHealthRead]],
    summary="Buscar estudiantes de la escuela",
    description=(
        "Búsqueda paginada entre **todos** los estudiantes de la escuela inscritos en el año "
        "académico vigente, sin necesidad de elegir un salón. Los filtros son opcionales e "
        "independientes, salvo los de salón, que son jerárquicos: 'grade' exige 'category' y "
        "'section' exige ambos. Devuelve el estado nutricional por IMC y si el estudiante "
        "tiene algún caso médico abierto."
    ),
    responses={**TENANT_RESPONSES, **BAD_REQUEST},
)
def search_students(
    admin_payload: Annotated[dict, Depends(get_current_admin_escuela)],
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
    """Busca estudiantes de la escuela aplicando filtros opcionales.

    Multi-tenancy:
        El 'school_id' se resuelve en el servidor a partir del usuario autenticado
        (admin_payload['sub']); el cliente nunca lo envía. La vista
        'student_health_status' expone 'school_id' como columna, de modo que el
        filtro de escuela se aplica en la propia consulta.

    RBAC:
        Requiere rol admin_escuela autenticado mediante Bearer token.

    Raises:
        HTTPException(400): Si se envía 'grade' sin 'category', o 'section' sin
            'category' y 'grade'.
        HTTPException(401): Si el token es inválido, expiró o fue revocado.
        HTTPException(403): Si el rol no es admin_escuela, o si el usuario no está
            vinculado a ninguna escuela.
        HTTPException(500): Si falta 'SUPABASE_SERVICE_KEY' —la vista está denegada a
            la clave pública— o si la consulta a la vista falla.
    """
    user_id = int(admin_payload["sub"])
    students, total = student_search_service.search_for_school_admin(
        user_id=user_id,
        page=page,
        size=size,
        q=q,
        bmi_status=bmi_status,
        has_active_medical_case=has_active_medical_case,
        category=category,
        grade=grade,
        section=section,
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
