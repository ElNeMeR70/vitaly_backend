"""Búsqueda global de estudiantes para los roles de escuela y médico (issue #66).

Ambos endpoints leen la misma vista ``student_health_status`` y solo difieren en
cómo se resuelve el alcance:

* ``admin_escuela`` → su única escuela, vía
  :meth:`SchoolAdminService._get_school_id_for_admin`.
* ``doctor`` → el conjunto de escuelas con vínculo vigente, vía
  :meth:`DoctorEventService._get_doctor_id` y
  :meth:`DoctorEventService._get_active_school_ids`.

Los resolvedores se **reutilizan**, no se copian: ``.agents/rules/tenant-isolation.md``
lo exige explícitamente hasta que exista ``app/services/tenancy.py`` (issue #22).

Por qué una vista y no filtrado en Python: la clasificación por IMC es un dato
derivado, y filtrarla después de la consulta obliga a traer la escuela entera y
paginar en memoria, como hace ``SchoolAdminService.search_medical_cases``. Con los
volúmenes reales (escuela grande ≈ 2.660 estudiantes; médico con varias escuelas
≈ 10.000) eso produce un ``.in_()`` de decenas de KB —414— y expone el resultado
al truncado silencioso de ``max-rows``. La vista deja la paginación en la base de
datos: un único viaje con ``count="exact"`` + ``range``, constante respecto al
tamaño de la escuela.
"""

import logging

from fastapi import HTTPException

from app.core.database import supabase_admin
from app.schemas.enums import BmiStatus
from app.services.doctor_event_service import doctor_event_service
from app.services.school_admin_service import school_admin_service

logger = logging.getLogger(__name__)

#: Vista creada por la migración ``20261107120000_create_student_health_status_view.sql``.
#:
#: Se lee con el cliente privilegiado: la vista cruza datos de salud de todas las
#: escuelas, así que la migración se la deniega a ``anon`` —la clave que viaja al
#: frontend— igual que ``settings``. El aislamiento no se delega a la clave, lo
#: sigue aplicando el ``.in_("school_id", ...)`` resuelto en el servidor.
_VIEW = "student_health_status"

#: Columnas expuestas por la búsqueda. Se enumeran en lugar de usar ``*`` para no
#: filtrar peso y talla en crudo: una búsqueda masiva no los necesita.
_COLUMNS = (
    "student_id, school_id, name, lastname, full_name, birthday, gender, blood_type, "
    "identity_number, is_active, classroom_id, category, level, section, "
    "academic_year_id, bmi, bmi_status, has_active_medical_case"
)


def _escape_like(term: str) -> str:
    """Neutraliza los comodines de un término de búsqueda libre.

    PostgREST interpreta ``%`` y ``_`` como comodines de SQL, y además acepta
    ``*`` como alias de ``%``. Sin escaparlos, un usuario que escriba ``%``
    obtendría la escuela completa en lugar de una coincidencia literal.
    """
    for char in ("\\", "%", "_", "*"):
        term = term.replace(char, f"\\{char}")
    return term


class StudentSearchService:
    """Consultas de búsqueda de estudiantes con alcance resuelto en el servidor."""

    # ──────────────────────────────────────────────────────────
    # Helpers internos
    # ──────────────────────────────────────────────────────────

    def _run(self, query, description: str):
        """Ejecuta una consulta supabase-py traduciendo los fallos al envelope.

        ``app/main.py`` solo registra handlers para ``HTTPException`` y los errores
        de validación: un ``APIError`` de supabase-py saldría como 500 en texto
        plano, sin el envelope que el frontend espera.

        Args:
            query: Consulta de supabase-py lista para ejecutarse.
            description: Qué se estaba leyendo, para el registro del error.

        Returns:
            La respuesta de supabase-py.

        Raises:
            HTTPException: 500 si la consulta falla por cualquier motivo.
        """
        try:
            return query.execute()
        except Exception:
            logger.exception("Fallo al consultar %s en la búsqueda de estudiantes.", description)
            raise HTTPException(
                status_code=500,
                detail="Ocurrió un error al buscar estudiantes.",
            )

    def _validate_classroom_filters(
        self, category: int | None, grade: int | None, section: str | None
    ) -> None:
        """Valida la jerarquía de los filtros de salón.

        Replica la regla de ``SchoolAdminService.get_students_resume``: ``grade``
        exige ``category`` y ``section`` exige ambos, para que los dos puntos de
        entrada al mismo recorte de población se comporten igual.

        Raises:
            HTTPException: 400 si falta un filtro padre.
        """
        if grade is not None and category is None:
            raise HTTPException(
                status_code=400,
                detail="El parámetro 'grade' requiere que 'category' también sea proporcionado.",
            )
        if section is not None and (category is None or grade is None):
            raise HTTPException(
                status_code=400,
                detail="El parámetro 'section' requiere que 'category' y 'grade' también sean proporcionados.",
            )

    def _search(
        self,
        *,
        school_ids: list[int],
        page: int,
        size: int,
        q: str | None,
        bmi_status: BmiStatus | None,
        has_active_medical_case: bool | None,
        category: int | None,
        grade: int | None,
        section: str | None,
    ) -> tuple[list[dict], int]:
        """Ejecuta la búsqueda sobre la vista, acotada a las escuelas indicadas.

        Args:
            school_ids: Escuelas que el rol puede consultar, ya resueltas en el
                servidor. Una lista vacía devuelve un resultado vacío sin consultar.
            page: Número de página, comenzando en 1.
            size: Elementos por página.
            q: Texto libre sobre el nombre completo. Coincidencia parcial.
            bmi_status: Estado nutricional por el que filtrar.
            has_active_medical_case: Filtra por presencia de caso médico abierto.
            category: ID de la categoría del salón.
            grade: Nivel o grado del salón. Requiere ``category``.
            section: Sección del salón. Requiere ``category`` y ``grade``.

        Returns:
            tuple: La página de estudiantes y el total de coincidencias.

        Raises:
            HTTPException: 400 si la jerarquía de filtros de salón es inválida.
            HTTPException: 500 si falta ``SUPABASE_SERVICE_KEY`` o la consulta falla.
        """
        self._validate_classroom_filters(category, grade, section)

        if not school_ids:
            return [], 0

        if supabase_admin is None:
            logger.error(
                "SUPABASE_SERVICE_KEY no está configurada: la vista "
                "'student_health_status' solo es legible por el cliente privilegiado."
            )
            raise HTTPException(
                status_code=500,
                detail="La búsqueda de estudiantes no está disponible en este momento.",
            )

        query = (
            supabase_admin.table(_VIEW)
            .select(_COLUMNS, count="exact")
            .in_("school_id", school_ids)
        )

        if q:
            query = query.ilike("full_name", f"%{_escape_like(q)}%")
        if bmi_status is not None:
            query = query.eq("bmi_status", bmi_status.value)
        if has_active_medical_case is not None:
            query = query.eq("has_active_medical_case", has_active_medical_case)
        if category is not None:
            query = query.eq("category", category)
        if grade is not None:
            query = query.eq("level", grade)
        if section is not None:
            query = query.eq("section", section.upper())

        offset = (page - 1) * size
        # El count exacto viaja en la misma respuesta que la página (Content-Range),
        # así que filtros y total no pueden divergir: es una sola consulta.
        response = self._run(
            query.order("full_name").range(offset, offset + size - 1),
            "la vista de estado de salud de estudiantes",
        )
        return response.data or [], response.count or 0

    # ──────────────────────────────────────────────────────────
    # Puntos de entrada por rol
    # ──────────────────────────────────────────────────────────

    def search_for_school_admin(
        self,
        *,
        user_id: int,
        page: int,
        size: int,
        q: str | None = None,
        bmi_status: BmiStatus | None = None,
        has_active_medical_case: bool | None = None,
        category: int | None = None,
        grade: int | None = None,
        section: str | None = None,
    ) -> tuple[list[dict], int]:
        """Busca entre los estudiantes de la escuela del administrador autenticado.

        Args:
            user_id: ID del usuario autenticado (claim ``sub`` del JWT).
            page: Número de página, comenzando en 1.
            size: Elementos por página.
            q: Texto libre sobre el nombre completo.
            bmi_status: Estado nutricional por el que filtrar.
            has_active_medical_case: Filtra por presencia de caso médico abierto.
            category: ID de la categoría del salón.
            grade: Nivel o grado del salón. Requiere ``category``.
            section: Sección del salón. Requiere ``category`` y ``grade``.

        Returns:
            tuple: La página de estudiantes y el total de coincidencias.

        Raises:
            HTTPException: 400 si la jerarquía de filtros de salón es inválida.
            HTTPException: 403 si el usuario no está vinculado a ninguna escuela.
            HTTPException: 500 si la consulta falla.
        """
        # Reutiliza el resolvedor existente en lugar de repetir la consulta a
        # `school_administrator` (regla tenant-isolation, pendiente el helper del issue #22).
        school_id = school_admin_service._get_school_id_for_admin(user_id)
        return self._search(
            school_ids=[school_id],
            page=page,
            size=size,
            q=q,
            bmi_status=bmi_status,
            has_active_medical_case=has_active_medical_case,
            category=category,
            grade=grade,
            section=section,
        )

    def search_for_doctor(
        self,
        *,
        user_id: int,
        page: int,
        size: int,
        school_id: int | None = None,
        q: str | None = None,
        bmi_status: BmiStatus | None = None,
        has_active_medical_case: bool | None = None,
        category: int | None = None,
        grade: int | None = None,
        section: str | None = None,
    ) -> tuple[list[dict], int, list[int]]:
        """Busca entre los estudiantes de las escuelas vigentes del médico autenticado.

        El alcance son las escuelas con ``doctor_to_school.status`` vigente: suspender
        a un médico es la palanca de revocación del producto, de modo que deja de ver
        a los estudiantes de esa escuela de inmediato.

        Args:
            user_id: ID del usuario autenticado (claim ``sub`` del JWT).
            page: Número de página, comenzando en 1.
            size: Elementos por página.
            school_id: Acota la búsqueda a una de sus escuelas. Si se omite, busca
                en todas. El valor se valida contra el conjunto resuelto en el
                servidor: nunca se confía en él como alcance.
            q: Texto libre sobre el nombre completo.
            bmi_status: Estado nutricional por el que filtrar.
            has_active_medical_case: Filtra por presencia de caso médico abierto.
            category: ID de la categoría del salón.
            grade: Nivel o grado del salón. Requiere ``category``.
            section: Sección del salón. Requiere ``category`` y ``grade``.

        Returns:
            tuple: La página de estudiantes, el total de coincidencias y las
            escuelas efectivamente consultadas (para la bitácora de auditoría).

        Raises:
            HTTPException: 400 si la jerarquía de filtros de salón es inválida.
            HTTPException: 403 si el usuario no tiene perfil de médico, o si pide
                una escuela con la que no mantiene un vínculo vigente.
            HTTPException: 500 si la consulta falla.
        """
        doc_id = doctor_event_service._get_doctor_id(user_id)
        allowed = doctor_event_service._get_active_school_ids(doc_id)

        if school_id is not None:
            if school_id not in allowed:
                raise HTTPException(
                    status_code=403,
                    detail=(
                        "No tienes permiso para consultar los estudiantes de esta escuela: "
                        "no mantienes un vínculo vigente con ella."
                    ),
                )
            school_ids = [school_id]
        else:
            school_ids = sorted(allowed)

        rows, total = self._search(
            school_ids=school_ids,
            page=page,
            size=size,
            q=q,
            bmi_status=bmi_status,
            has_active_medical_case=has_active_medical_case,
            category=category,
            grade=grade,
            section=section,
        )
        return rows, total, school_ids


#: Instancia compartida del servicio, importada por los endpoints.
student_search_service = StudentSearchService()
