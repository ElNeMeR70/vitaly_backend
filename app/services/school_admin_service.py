"""Servicio del rol administrador de escuela.

Concentra el perfil propio, estudiantes, salones, casos médicos, métricas y
médicos de una institución. Todos los métodos públicos reciben el ``user_id``
autenticado y resuelven el ``school_id`` en el servidor con
:meth:`SchoolAdminService._get_school_id_for_admin`: el cliente nunca elige su
tenant.

Es el archivo más grande del backend y está señalado para dividirse por dominio;
mientras tanto, el código nuevo se añade siguiendo las secciones existentes.
"""

from datetime import date, timedelta
from fastapi import HTTPException
from app.core.database import supabase
from app.services import profile_photo_service


_MONTH_LABELS = ["Ene", "Feb", "Mar", "Abr", "May", "Jun", "Jul", "Ago", "Sep", "Oct", "Nov", "Dic"]


def _first_day_months_ago(ref: date, n: int) -> date:
    """Devuelve el primer día del mes situado ``n`` meses antes de ``ref``.

    Auxiliar de las series temporales de métricas: normaliza el inicio de cada
    cubeta mensual cruzando correctamente el cambio de año.

    Args:
        ref: Fecha de referencia desde la que se cuenta hacia atrás.
        n: Cantidad de meses a retroceder. Con 0 devuelve el día 1 del mes de ``ref``.

    Returns:
        date: Día 1 del mes resultante.
    """
    month = ref.month - n
    year = ref.year
    while month <= 0:
        month += 12
        year -= 1
    return date(year, month, 1)


def _linear_trend(values: list) -> list:
    """Ajusta una recta de mínimos cuadrados a una serie y devuelve sus valores.

    Se usa para dibujar la línea de tendencia junto a la serie real en los
    gráficos de métricas.

    Args:
        values: Serie numérica ordenada cronológicamente.

    Returns:
        list: Valores de la recta ajustada, redondeados a un decimal y con la
        misma longitud que ``values``. Lista vacía si la serie viene vacía; el
        único valor, como float, si la serie tiene un solo punto.
    """
    n = len(values)
    if n == 0:
        return []
    if n == 1:
        return [float(values[0])]
    xs = list(range(1, n + 1))
    x_mean = sum(xs) / n
    y_mean = sum(values) / n
    num = sum((xs[i] - x_mean) * (values[i] - y_mean) for i in range(n))
    den = sum((xs[i] - x_mean) ** 2 for i in range(n))
    m = num / den if den != 0 else 0
    b = y_mean - m * x_mean
    return [round(m * xi + b, 1) for xi in xs]


class SchoolAdminService:
    """Operaciones del administrador de escuela, aisladas por ``school_id``."""

    def _get_school_id_for_admin(self, user_id: int) -> int:
        """Obtiene el school_id vinculado al usuario admin_escuela. Lanza 403 si no está vinculado."""
        sa_response = (
            supabase.table("school_administrator")
            .select("school_id")
            .eq("user_id", user_id)
            .execute()
        )
        if not sa_response.data or sa_response.data[0].get("school_id") is None:
            raise HTTPException(
                status_code=403,
                detail="Tu usuario no está vinculado a ninguna escuela."
            )
        return sa_response.data[0]["school_id"]

    def _calculate_bmi(self, weight_kg: float | None, height_cm: float | None) -> float | None:
        """
        Calcula el IMC (BMI) convirtiendo la altura de cm a metros.
        Retorna None si los datos son inválidos.
        """
        if not weight_kg or not height_cm or height_cm <= 0:
            return None
        # Conversión de cm a metros
        height_m = height_cm / 100.0
        return weight_kg / (height_m ** 2)

    # ──────────────────────────────────────────────────────────
    # GET - Mi Perfil
    # ──────────────────────────────────────────────────────────

    def get_my_profile(self, user_id: int) -> dict:
        """Devuelve el perfil del administrador junto con los datos de su escuela.

        Args:
            user_id: ID del administrador autenticado (claim ``sub`` del JWT).

        Returns:
            dict: ``user_info`` (datos personales), ``admin_info`` (cargo y escuela)
            y ``school_info`` (datos de la institución, con textos de reemplazo
            cuando algún campo está vacío).

        Raises:
            HTTPException: 403 si el usuario no está vinculado a ninguna escuela.
            HTTPException: 404 si no existe el perfil de administrador.
        """
        admin_res = (
            supabase.table("school_administrator")
            .select("*, user(*), school(*)")
            .eq("user_id", user_id)
            .execute()
        )
        if not admin_res.data:
            raise HTTPException(status_code=404, detail="Perfil de administrador no encontrado.")
            
        admin_data = admin_res.data[0]
        user_info = admin_data.pop("user", {}) or {}
        school_info = admin_data.pop("school", {}) or {}
        
        user_info.pop("password", None)
        user_info.pop("photo_file_id", None)

        # Manejo de nulos según requerimiento
        user_info["gender"] = user_info.get("gender") or "No especificado"
        user_info["address"] = user_info.get("address") or "Dirección desconocida"
        user_info["biography"] = user_info.get("biography") or "Sin biografía"
        user_info["identity_number"] = user_info.get("identity_number") or "N/A"
            
        return {
            "user_info": user_info,
            "admin_info": admin_data,
            "school_info": {
                "name": school_info.get("name") or "Nombre no disponible",
                "year_foundation": school_info.get("year_foundation") or "Desconocido",
                "address": school_info.get("address") or "Dirección no disponible"
            }
        }

    # ──────────────────────────────────────────────────────────
    # PUT - Actualizar Mi Perfil
    # ──────────────────────────────────────────────────────────

    def update_my_profile(self, user_id: int, payload: dict) -> dict:
        """
        Actualiza la información del usuario autenticado en la tabla 'user'.
        """
        if not payload:
            return self.get_my_profile(user_id)

        update_data = payload.copy()
        
        # Si viene birthday (date), convertir a string ISO
        if "birthday" in update_data and hasattr(update_data["birthday"], "isoformat"):
            update_data["birthday"] = update_data["birthday"].isoformat()

        # Actualizar en Supabase
        update_res = (
            supabase.table("user")
            .update(update_data)
            .eq("id", user_id)
            .execute()
        )

        if not update_res.data:
            raise HTTPException(status_code=400, detail="No se pudo actualizar el perfil.")

        return self.get_my_profile(user_id)

    # ──────────────────────────────────────────────────────────
    # POST - Subir Foto de Perfil a ImageKit
    # ──────────────────────────────────────────────────────────

    def upload_my_profile_photo(self, user_id: int, file_bytes: bytes, content_type: str) -> dict:
        """Sube la foto de perfil del usuario a ImageKit y actualiza photo_url en la BD.

        Delega la validación y el ciclo de vida ImageKit/BD en profile_photo_service.

        Returns:
            dict: Diccionario con la clave 'photo_url' y la URL generada.
        """
        return profile_photo_service.upload_profile_photo(
            user_id=user_id,
            file_bytes=file_bytes,
            content_type=content_type,
            folder=profile_photo_service.SCHOOL_ADMIN_PHOTO_FOLDER,
        )

    # ──────────────────────────────────────────────────────────
    # DELETE - Eliminar Foto de Perfil
    # ──────────────────────────────────────────────────────────

    def delete_my_profile_photo(self, user_id: int) -> dict:
        """Elimina la foto de perfil del usuario autenticado (BD e ImageKit).

        Returns:
            dict: Diccionario con la clave 'photo_url' en None.
        """
        return profile_photo_service.delete_profile_photo(user_id=user_id)


    # ──────────────────────────────────────────────────────────
    # GET - Students Resume (Métricas)
    # ──────────────────────────────────────────────────────────

    def get_students_resume(
        self,
        user_id: int,
        category: int | None = None,
        grade: int | None = None,
        section: str | None = None,
    ) -> dict:
        """Calcula el resumen de salud de los estudiantes de la escuela.

        Los filtros son jerárquicos: ``grade`` exige ``category``, y ``section``
        exige ambos. Solo considera el año académico vigente.

        Args:
            user_id: ID del administrador autenticado (claim ``sub`` del JWT).
            category: ID de la categoría (nivel educativo) por la que filtrar.
            grade: Grado dentro de la categoría. Requiere ``category``.
            section: Sección dentro del grado. Requiere ``category`` y ``grade``.

        Returns:
            dict: ``total_students``, ``overweight_students``,
            ``malnourished_students``, ``active_cases`` y ``optimal_students``.
            Todos en 0 si la escuela no tiene estudiantes que cumplan el filtro.

        Raises:
            HTTPException: 403 si el usuario no está vinculado a ninguna escuela.
            HTTPException: 400 si se envía ``grade`` sin ``category``, o ``section``
                sin ``category`` y ``grade``.
            HTTPException: 404 si no hay un año académico vigente configurado.
        """
        school_id = self._get_school_id_for_admin(user_id)

        # ── Validar dependencias entre filtros ────────────────
        if grade is not None and category is None:
            raise HTTPException(
                status_code=400,
                detail="El parámetro 'grade' requiere que 'category' también sea proporcionado."
            )
        if section is not None and (category is None or grade is None):
            raise HTTPException(
                status_code=400,
                detail="El parámetro 'section' requiere que 'category' y 'grade' también sean proporcionados."
            )

        # ── Obtener el año académico vigente ──────────────────
        ay_res = (
            supabase.table("academic_year")
            .select("id")
            .eq("is_current", True)
            .eq("is_deleted", False)
            .limit(1)
            .execute()
        )
        if not ay_res.data:
            raise HTTPException(
                status_code=404,
                detail="No hay un año académico vigente configurado en el sistema."
            )
        academic_year_id = ay_res.data[0]["id"]

        # ── Paso 1: Obtener classrooms de la escuela (con filtros opcionales) ──
        classrooms_query = (
            supabase.table("classroom")
            .select("id")
            .eq("school_id", school_id)
            .eq("academic_year_id", academic_year_id)
            .eq("is_deleted", False)
        )
        if category is not None:
            classrooms_query = classrooms_query.eq("category", category)
        if grade is not None:
            classrooms_query = classrooms_query.eq("level", grade)
        if section is not None:
            classrooms_query = classrooms_query.eq("section", section.upper())

        classrooms_res = classrooms_query.execute()
        classroom_ids = [c["id"] for c in classrooms_res.data] if classrooms_res.data else []

        empty_result = {
            "total_students": 0,
            "overweight_students": 0,
            "malnourished_students": 0,
            "active_cases": 0,
            "optimal_students": 0,
        }

        if not classroom_ids:
            return empty_result

        # ── Paso 2: Obtener student_ids únicos en esas aulas ──
        regs_res = (
            supabase.table("classroom_registration")
            .select("student_id")
            .in_("classroom_id", classroom_ids)
            .execute()
        )
        student_ids = list({r["student_id"] for r in regs_res.data}) if regs_res.data else []
        total_students = len(student_ids)

        if not student_ids:
            return empty_result

        # ── SOBREPESO Y DESNUTRICIÓN (Basado en IMC) ──────────
        metrics_res = (
            supabase.table("student_metrics")
            .select("student_id, height, weight")
            .in_("student_id", student_ids)
            .eq("is_current", True)
            .eq("is_deleted", False)
            .execute()
        )

        overweight_students = 0
        malnourished_students = 0

        if metrics_res.data:
            for m in metrics_res.data:
                bmi = self._calculate_bmi(m.get("weight"), m.get("height"))
                if bmi is not None:
                    if bmi < 18.5:
                        malnourished_students += 1
                    elif bmi > 24.9:
                        overweight_students += 1

        # ── CASOS ACTIVOS ─────────────────────────────────────
        cases_res = (
            supabase.table("medical_case")
            .select("id")
            .in_("student_id", student_ids)
            .is_("end_date", "null")
            .is_("final diagnosis", "null")
            .eq("is_deleted", False)
            .execute()
        )
        active_cases = len(cases_res.data) if cases_res.data else 0

        optimal_students = total_students - overweight_students - malnourished_students

        return {
            "total_students": total_students,
            "overweight_students": overweight_students,
            "malnourished_students": malnourished_students,
            "active_cases": active_cases,
            "optimal_students": optimal_students,
        }

    # ──────────────────────────────────────────────────────────
    # GET - Grados Académicos (Classrooms de la escuela)
    # ──────────────────────────────────────────────────────────

    def get_academic_grades(self, user_id: int, page: int, size: int) -> dict:
        """Lista de forma paginada los grados de la escuela en el año vigente.

        Args:
            user_id: ID del administrador autenticado (claim ``sub`` del JWT).
            page: Número de página, comenzando en 1.
            size: Cantidad de grados por página.

        Returns:
            dict: ``data`` (grados enriquecidos), ``total``, ``page``, ``size`` y
            ``academic_year`` con el período aplicado.

        Raises:
            HTTPException: 403 si el usuario no está vinculado a ninguna escuela.
            HTTPException: 404 si no hay un año académico vigente, o si la escuela
                no tiene grados registrados en él. Devolver 404 ante una colección
                vacía es comportamiento legado; se sigue en el issue #15.
        """
        school_id = self._get_school_id_for_admin(user_id)

        # Buscar el año académico vigente
        ay_res = (
            supabase.table("academic_year")
            .select("id, name")
            .eq("is_current", True)
            .eq("is_deleted", False)
            .limit(1)
            .execute()
        )
        if not ay_res.data:
            raise HTTPException(
                status_code=404,
                detail="No hay un año académico vigente configurado en el sistema."
            )
        current_academic_year = ay_res.data[0]
        academic_year_id = current_academic_year["id"]

        # Contar total de classrooms de esta escuela en el año vigente
        count_res = (
            supabase.table("classroom")
            .select("id", count="exact")
            .eq("school_id", school_id)
            .eq("academic_year_id", academic_year_id)
            .eq("is_deleted", False)
            .execute()
        )
        total = count_res.count or 0
        if total == 0:
            raise HTTPException(
                status_code=404,
                detail="Esta escuela no tiene grados registrados para el año académico vigente."
            )

        offset = (page - 1) * size
        rows = (
            supabase.table("classroom")
            .select("id, category, level, section, is_deleted")
            .eq("school_id", school_id)
            .eq("academic_year_id", academic_year_id)
            .eq("is_deleted", False)
            .order("category")
            .order("level")
            .range(offset, offset + size - 1)
            .execute()
        )

        # Enriquecer con el nombre del año académico
        enriched = []
        for c in rows.data:
            c["academic_year"] = current_academic_year
            enriched.append(c)

        return {
            "data": enriched,
            "total": total,
            "page": page,
            "size": size,
            "academic_year": current_academic_year,
        }

    # ──────────────────────────────────────────────────────────
    # GET - Estudiantes de un grado (classroom) específico
    # ──────────────────────────────────────────────────────────

    def get_students_by_classroom(self, user_id: int, classroom_id: int, page: int, size: int) -> dict:
        """Lista de forma paginada los estudiantes inscritos en un salón.

        Args:
            user_id: ID del administrador autenticado (claim ``sub`` del JWT).
            classroom_id: ID del salón a consultar.
            page: Número de página, comenzando en 1.
            size: Cantidad de estudiantes por página.

        Returns:
            dict: ``classroom`` (datos del salón), ``data`` (estudiantes),
            ``total``, ``page`` y ``size``.

        Raises:
            HTTPException: 403 si el usuario no está vinculado a ninguna escuela, o
                si el salón pertenece a otra escuela.
            HTTPException: 404 si el salón no existe, o si no tiene estudiantes
                registrados. Este segundo 404 es comportamiento legado; se sigue en
                el issue #15.
        """
        school_id = self._get_school_id_for_admin(user_id)

        # Verificar que el classroom pertenece a la misma escuela del admin
        classroom_res = (
            supabase.table("classroom")
            .select("id, school_id, category, level, section, academic_year_id")
            .eq("id", classroom_id)
            .eq("is_deleted", False)
            .execute()
        )
        if not classroom_res.data:
            raise HTTPException(status_code=404, detail=f"No existe ningún salón con ID {classroom_id}.")

        classroom = classroom_res.data[0]
        if classroom["school_id"] != school_id:
            raise HTTPException(
                status_code=403,
                detail="No tienes permiso para acceder a los estudiantes de este salón: pertenece a otra escuela."
            )

        # Obtener registros de classroom_registration para ese classroom
        count_res = (
            supabase.table("classroom_registration")
            .select("student_id", count="exact")
            .eq("classroom_id", classroom_id)
            .execute()
        )
        total = count_res.count or 0
        if total == 0:
            raise HTTPException(
                status_code=404,
                detail=f"El salón con ID {classroom_id} no tiene estudiantes registrados."
            )

        offset = (page - 1) * size
        regs_res = (
            supabase.table("classroom_registration")
            .select("student_id")
            .eq("classroom_id", classroom_id)
            .range(offset, offset + size - 1)
            .execute()
        )
        student_ids = [r["student_id"] for r in regs_res.data]

        # Traer los datos de los estudiantes
        students_res = (
            supabase.table("student")
            .select("id, name, lastname, birthday, gender, blood_type, identity_number, is_active, is_deleted")
            .in_("id", student_ids)
            .execute()
        )
        students = students_res.data or []

        # 1. Obtener casos médicos activos para comprobar has_active_medical_case
        cases_res = (
            supabase.table("medical_case")
            .select("student_id")
            .in_("student_id", student_ids)
            .is_("end_date", "null")
            .eq("is_deleted", False)
            .execute()
        )
        active_cases_students = {c["student_id"] for c in (cases_res.data or [])}

        # 2. Obtener métricas actuales para el BMI
        metrics_res = (
            supabase.table("student_metrics")
            .select("student_id, weight, height")
            .in_("student_id", student_ids)
            .eq("is_current", True)
            .eq("is_deleted", False)
            .execute()
        )
        metrics_map = {m["student_id"]: m for m in (metrics_res.data or [])}

        # 3. Enriquecer datos
        for s in students:
            sid = s["id"]
            
            # Caso médico activo
            s["has_active_medical_case"] = sid in active_cases_students

            # BMI status
            nutritional_status = "SIN DATOS"
            metric = metrics_map.get(sid)
            if metric:
                bmi = self._calculate_bmi(metric.get("weight"), metric.get("height"))
                if bmi:
                    if bmi < 18.5:
                        nutritional_status = "DESNUTRIDO"
                    elif bmi <= 24.9:
                        nutritional_status = "OPTIMO"
                    else:
                        nutritional_status = "SOBREPESO"
            s["bmi_status"] = nutritional_status

        return {
            "classroom": classroom,
            "data": students,
            "total": total,
            "page": page,
            "size": size,
        }

    # ──────────────────────────────────────────────────────────
    # GET - Detalle completo de un estudiante
    # ──────────────────────────────────────────────────────────

    def get_student_detail(self, user_id: int, student_id: int) -> dict:
        """Devuelve la ficha completa de un estudiante de la escuela.

        La pertenencia es indirecta: el estudiante debe estar inscrito, vía
        ``classroom_registration``, en un salón de la escuela del administrador.

        Args:
            user_id: ID del administrador autenticado (claim ``sub`` del JWT).
            student_id: ID del estudiante a consultar.

        Returns:
            dict: ``student`` (datos personales), ``representatives`` (``None`` si
            no tiene) y ``health_info`` (métricas y estado de salud).

        Raises:
            HTTPException: 403 si el usuario no está vinculado a ninguna escuela, si
                el estudiante no está inscrito en ningún salón, o si pertenece a
                otra escuela.
            HTTPException: 404 si no existe un estudiante activo con ese ID.
        """
        school_id = self._get_school_id_for_admin(user_id)

        # 1. Verificar que el estudiante existe y no está eliminado
        student_res = (
            supabase.table("student")
            .select("id, name, lastname, birthday, gender, blood_type, identity_number, is_active, is_deleted")
            .eq("id", student_id)
            .eq("is_deleted", False)
            .execute()
        )
        if not student_res.data:
            raise HTTPException(status_code=404, detail=f"No existe ningún estudiante activo con ID {student_id}.")
        student = student_res.data[0]

        # 2. Verificar aislamiento de tenant: el estudiante debe estar registrado en un salón de ESTA escuela
        reg_res = (
            supabase.table("classroom_registration")
            .select("classroom_id")
            .eq("student_id", student_id)
            .order("created_at", desc=True)
            .limit(1)
            .execute()
        )
        if not reg_res.data:
            raise HTTPException(
                status_code=403,
                detail="Este estudiante no está inscrito en ningún salón de tu escuela."
            )
        classroom_id = reg_res.data[0]["classroom_id"]
        cr_res = (
            supabase.table("classroom")
            .select("school_id, category, level, section")
            .eq("id", classroom_id)
            .execute()
        )
        if not cr_res.data or cr_res.data[0]["school_id"] != school_id:
            raise HTTPException(
                status_code=403,
                detail="No tienes permiso para visualizar a este estudiante: pertenece a otra escuela."
            )
        
        # Agregar información del salón al objeto del estudiante
        classroom = cr_res.data[0]
        student["classroom_category"] = classroom.get("category")
        student["classroom_level"] = classroom.get("level")
        student["section"] = classroom.get("section")

        # 3. Representantes: student_representative → parent → user
        sr_res = (
            supabase.table("student_representative")
            .select("parent_id")
            .eq("student_id", student_id)
            .eq("is_deleted", False)
            .execute()
        )
        representatives = []
        if sr_res.data:
            parent_ids = [r["parent_id"] for r in sr_res.data]
            for parent_id in parent_ids:
                parent_res = (
                    supabase.table("parent")
                    .select("id, user_id, occupation, type_representative, is_active")
                    .eq("id", parent_id)
                    .eq("is_deleted", False)
                    .execute()
                )
                if parent_res.data:
                    parent = parent_res.data[0]
                    # Traer datos básicos del user vinculado al representante
                    user_res = (
                        supabase.table("user")
                        .select("id, name, lastname, email, gender, address")
                        .eq("id", parent["user_id"])
                        .eq("is_deleted", False)
                        .execute()
                    )
                    parent["user_info"] = user_res.data[0] if user_res.data else None
                    representatives.append(parent)

        # 4. Información de salud (student_metrics)
        health_info = None
        metrics_res = (
            supabase.table("student_metrics")
            .select("height, weight, updated_at")
            .eq("student_id", student_id)
            .eq("is_deleted", False)
            .eq("is_current", True)
            .order("updated_at", desc=True)
            .limit(1)
            .execute()
        )
        if metrics_res.data:
            metric = metrics_res.data[0]
            weight_kg: float = metric["weight"]
            height_m: float = metric["height"]

            bmi = self._calculate_bmi(weight_kg, height_m)
            if bmi is not None:
                bmi = round(bmi, 2)
                if bmi < 18.5:
                    nutritional_status = "DESNUTRIDO"
                elif bmi <= 24.9:
                    nutritional_status = "OPTIMO"
                else:
                    nutritional_status = "OBESO"

                health_info = {
                    "weight_kg": weight_kg,
                    "height_m": height_m,
                    "bmi": bmi,
                    "nutritional_status": nutritional_status,
                    "measured_at": metric.get("updated_at"),
                }

        return {
            "student": student,
            "representatives": representatives if representatives else None,
            "health_info": health_info,
        }

    # ──────────────────────────────────────────────────────────
    # GET - Historial de métricas de un estudiante
    # ──────────────────────────────────────────────────────────

    def get_student_metrics_history(self, user_id: int, student_id: int, limit: int) -> dict | None:
        """Devuelve el histórico de peso y altura de un estudiante.

        Args:
            user_id: ID del administrador autenticado (claim ``sub`` del JWT).
            student_id: ID del estudiante cuyo histórico se consulta.
            limit: Número máximo de mediciones a devolver, de la más reciente
                hacia atrás.

        Returns:
            dict | None: ``{"peso": [...], "altura": [...]}`` con las series
            ordenadas, o ``None`` si el estudiante no tiene mediciones registradas.

        Raises:
            HTTPException: 403 si el usuario no está vinculado a ninguna escuela, si
                el estudiante no está inscrito en ningún salón, o si pertenece a
                otra escuela.
            HTTPException: 404 si no existe un estudiante activo con ese ID.
        """
        school_id = self._get_school_id_for_admin(user_id)

        # 1. Verificar que el estudiante existe y no está eliminado
        student_res = (
            supabase.table("student")
            .select("id")
            .eq("id", student_id)
            .eq("is_deleted", False)
            .execute()
        )
        if not student_res.data:
            raise HTTPException(status_code=404, detail=f"No existe ningún estudiante activo con ID {student_id}.")

        # 2. Verificar aislamiento de tenant: el estudiante pertenece a la escuela del admin
        reg_res = (
            supabase.table("classroom_registration")
            .select("classroom_id")
            .eq("student_id", student_id)
            .order("created_at", desc=True)
            .limit(1)
            .execute()
        )
        if not reg_res.data:
            raise HTTPException(
                status_code=403,
                detail="Este estudiante no está inscrito en ningún salón de tu escuela."
            )
        classroom_id = reg_res.data[0]["classroom_id"]
        cr_res = (
            supabase.table("classroom")
            .select("school_id")
            .eq("id", classroom_id)
            .execute()
        )
        if not cr_res.data or cr_res.data[0]["school_id"] != school_id:
            raise HTTPException(
                status_code=403,
                detail="No tienes permiso para visualizar a este estudiante: pertenece a otra escuela."
            )

        # 3. Obtener historial de métricas ordenadas por fecha de registro (más recientes primero)
        metrics_res = (
            supabase.table("student_metrics")
            .select("weight, height, updated_at")
            .eq("student_id", student_id)
            .eq("is_deleted", False)
            .order("updated_at", desc=True)
            .limit(limit)
            .execute()
        )

        if not metrics_res.data:
            return None

        # 4. Formatear la salida en series de tiempo separadas para peso y altura
        peso = []
        altura = []
        for m in metrics_res.data:
            fecha = m.get("updated_at")
            if m.get("weight") is not None:
                peso.append({"fecha": fecha, "valor": m["weight"]})
            if m.get("height") is not None:
                altura.append({"fecha": fecha, "valor": m["height"]})

        return {
            "peso": peso if peso else [],
            "altura": altura if altura else [],
        }


    # ──────────────────────────────────────────────────────────
    # GET - Búsqueda / Filtrado de casos médicos de la escuela
    # ──────────────────────────────────────────────────────────

    def search_medical_cases(
        self,
        user_id: int,
        page: int,
        size: int,
        status: str | None = None,
        type_of_case: str | None = None,
        date_from: date | None = None,
        date_to: date | None = None,
        q: str | None = None,
        search_by: str | None = None,
        current_year_only: bool = False,
    ) -> dict:
        """
        Búsqueda paginada de casos médicos con filtros opcionales y búsqueda por texto.

        Filtros:
        - status: 'activo' o 'resuelto'.
        - type_of_case: ALERGIA | LESION | ENFERMEDAD | EMERGENCIA
        - date_from / date_to: rango de fechas sobre init_date.
        - q: Texto a buscar.
        - search_by: 'student' (nombre/apellido) o 'case' (title, symptomatology, initial diagnosis).
        """
        school_id = self._get_school_id_for_admin(user_id)

        # 1. Obtener IDs de classrooms de la escuela
        classrooms_res = (
            supabase.table("classroom")
            .select("id")
            .eq("school_id", school_id)
            .eq("is_deleted", False)
            .execute()
        )
        if not classrooms_res.data:
            return {"data": [], "total": 0, "page": page, "size": size}

        classroom_ids = [c["id"] for c in classrooms_res.data]

        # 2. Obtener IDs únicos de estudiantes inscritos en esos classrooms
        regs_res = (
            supabase.table("classroom_registration")
            .select("student_id")
            .in_("classroom_id", classroom_ids)
            .execute()
        )
        if not regs_res.data:
            return {"data": [], "total": 0, "page": page, "size": size}

        student_ids = list({r["student_id"] for r in regs_res.data})

        # 3. Traer los datos de los estudiantes para la búsqueda por nombre y formateo
        students_res = (
            supabase.table("student")
            .select("id, name, lastname")
            .in_("id", student_ids)
            .execute()
        )
        student_map = {}
        if students_res.data:
            for s in students_res.data:
                student_map[s["id"]] = s

        # 4. Obtener fechas del año académico vigente si es necesario
        ay_init_date = None
        ay_end_date = None
        if current_year_only:
            ay_res = (
                supabase.table("academic_year")
                .select("init_date, end_date")
                .eq("is_current", True)
                .eq("is_deleted", False)
                .limit(1)
                .execute()
            )
            if ay_res.data:
                ay = ay_res.data[0]
                ay_init_date = ay.get("init_date")
                ay_end_date = ay.get("end_date")

        # 5. Construir query base con filtros directos en Supabase
        query = (
            supabase.table("medical_case")
            .select("id, type_of_case, title, symptomatology, \"initial diagnosis\", init_date, end_date, \"final diagnosis\", student_id")
            .in_("student_id", student_ids)
            .eq("is_deleted", False)
        )

        if type_of_case:
            query = query.eq("type_of_case", type_of_case.upper())
            
        effective_date_from = date_from.isoformat() if date_from else None
        if current_year_only and ay_init_date:
            effective_date_from = max(effective_date_from, ay_init_date) if effective_date_from else ay_init_date
            
        effective_date_to = date_to.isoformat() if date_to else None
        if current_year_only and ay_end_date:
            effective_date_to = min(effective_date_to, ay_end_date) if effective_date_to else ay_end_date
            
        if effective_date_from:
            query = query.gte("init_date", effective_date_from)
        if effective_date_to:
            query = query.lte("init_date", effective_date_to)

        all_res = query.order("init_date", desc=True).execute()
        all_cases = all_res.data if all_res.data else []

        # 6. Aplicar filtro de búsqueda por texto (q) y tipo de búsqueda (search_by) en Python
        if q:
            q_lower = q.lower()
            filtered_cases = []
            for c in all_cases:
                student = student_map.get(c["student_id"], {})
                student_full_name = f"{student.get('name', '')} {student.get('lastname', '')}".lower()
                
                match_student = q_lower in student_full_name
                
                case_title = (c.get("title") or "").lower()
                case_symp = (c.get("symptomatology") or "").lower()
                case_init_diag = (c.get("initial diagnosis") or "").lower()
                
                match_case = (q_lower in case_title) or (q_lower in case_symp) or (q_lower in case_init_diag)
                
                if search_by == "student":
                    if match_student:
                        filtered_cases.append(c)
                elif search_by == "case":
                    if match_case:
                        filtered_cases.append(c)
                else:
                    if match_student or match_case:
                        filtered_cases.append(c)
            all_cases = filtered_cases

        # 7. Filtrar por status en Python (resuelto = end_date AND final_diagnosis no nulos)
        if status:
            status_lower = status.lower()
            if status_lower == "resuelto":
                all_cases = [
                    c for c in all_cases
                    if c.get("end_date") is not None and c.get("final diagnosis") is not None
                ]
            elif status_lower == "activo":
                all_cases = [
                    c for c in all_cases
                    if c.get("end_date") is None or c.get("final diagnosis") is None
                ]

        total = len(all_cases)

        # 8. Paginación manual
        offset = (page - 1) * size
        paginated_cases = all_cases[offset: offset + size]

        if not paginated_cases:
            return {"data": [], "total": total, "page": page, "size": size}

        # 9. Formatear respuesta
        formatted_cases = []
        for case in paginated_cases:
            is_resolved = (
                case.get("end_date") is not None
                and case.get("final diagnosis") is not None
            )
            student = student_map.get(case["student_id"], {})
            student_name = f"{student.get('name', '')} {student.get('lastname', '')}".strip() or "Estudiante Desconocido"
            
            formatted_cases.append({
                "id": case["id"],
                "status": "resuelto" if is_resolved else "activo",
                "start_date": case.get("init_date"),
                "student_name": student_name,
                "type_of_case": case.get("type_of_case"),
                "description": case.get("symptomatology"),
            })

        return {
            "data": formatted_cases,
            "total": total,
            "page": page,
            "size": size,
        }

    # ──────────────────────────────────────────────────────────
    # GET - Casos médicos de la escuela (paginados)
    # ──────────────────────────────────────────────────────────

    def get_medical_cases(self, user_id: int, page: int, size: int, current_year_only: bool = False) -> dict:
        """Lista de forma paginada los casos médicos de la escuela.

        Args:
            user_id: ID del administrador autenticado (claim ``sub`` del JWT).
            page: Número de página, comenzando en 1.
            size: Cantidad de casos por página.
            current_year_only: Si es True, restringe el resultado al año académico
                vigente.

        Returns:
            dict: ``{"data": [...], "total": int, "page": int, "size": int}``. La
            lista viene vacía si la escuela no tiene casos médicos.

        Raises:
            HTTPException: 403 si el usuario no está vinculado a ninguna escuela.
        """
        school_id = self._get_school_id_for_admin(user_id)

        # 1. Obtener los IDs de los estudiantes que pertenecen a esta escuela
        classrooms_res = (
            supabase.table("classroom")
            .select("id")
            .eq("school_id", school_id)
            .eq("is_deleted", False)
            .execute()
        )
        if not classrooms_res.data:
            return {"data": [], "total": 0, "page": page, "size": size}

        classroom_ids = [c["id"] for c in classrooms_res.data]

        regs_res = (
            supabase.table("classroom_registration")
            .select("student_id")
            .in_("classroom_id", classroom_ids)
            .execute()
        )
        if not regs_res.data:
            return {"data": [], "total": 0, "page": page, "size": size}

        student_ids = list({r["student_id"] for r in regs_res.data})

        # 2. Contar los casos médicos pertenecientes a estos estudiantes
        count_query = (
            supabase.table("medical_case")
            .select("id", count="exact")
            .in_("student_id", student_ids)
            .eq("is_deleted", False)
        )
        
        data_query = (
            supabase.table("medical_case")
            .select("id, type_of_case, symptomatology, init_date, end_date, student_id")
            .in_("student_id", student_ids)
            .eq("is_deleted", False)
        )

        if current_year_only:
            ay_res = (
                supabase.table("academic_year")
                .select("init_date, end_date")
                .eq("is_current", True)
                .eq("is_deleted", False)
                .limit(1)
                .execute()
            )
            if ay_res.data:
                ay = ay_res.data[0]
                if ay.get("init_date"):
                    count_query = count_query.gte("init_date", ay["init_date"])
                    data_query = data_query.gte("init_date", ay["init_date"])
                if ay.get("end_date"):
                    count_query = count_query.lte("init_date", ay["end_date"])
                    data_query = data_query.lte("init_date", ay["end_date"])

        count_res = count_query.execute()
        total = count_res.count or 0
        if total == 0:
            return {"data": [], "total": 0, "page": page, "size": size}

        # 3. Obtener casos médicos paginados
        offset = (page - 1) * size
        cases_res = (
            data_query
            .order("init_date", desc=True)
            .range(offset, offset + size - 1)
            .execute()
        )
        cases = cases_res.data

        if not cases:
            return {"data": [], "total": total, "page": page, "size": size}

        # 4. Obtener la información de nombre/apellido para los casos correspondientes
        matched_student_ids = list({c["student_id"] for c in cases})
        students_res = (
            supabase.table("student")
            .select("id, name, lastname")
            .in_("id", matched_student_ids)
            .execute()
        )
        
        student_map = {}
        if students_res.data:
            student_map = {
                s["id"]: f"{s.get('name', '')} {s.get('lastname', '')}".strip()
                for s in students_res.data
            }

        # 5. Formatear la salida según las especificaciones
        formatted_cases = []
        for case in cases:
            status = "activo" if not case.get("end_date") else "resuelto"
            formatted_cases.append({
                "id": case["id"],
                "status": status,
                "start_date": case.get("init_date"),
                "student_name": student_map.get(case["student_id"], "Estudiante Desconocido"),
                "type_of_case": case.get("type_of_case"),
                "description": case.get("symptomatology")
            })

        return {
            "data": formatted_cases,
            "total": total,
            "page": page,
            "size": size,
        }

    # ──────────────────────────────────────────────────────────
    # GET - Médicos de la escuela (paginados)
    # ──────────────────────────────────────────────────────────

    def get_doctors(self, user_id: int, page: int, size: int) -> dict:
        """Lista de forma paginada los médicos no eliminados de la escuela.

        Args:
            user_id: ID del administrador autenticado (claim ``sub`` del JWT).
            page: Número de página, comenzando en 1.
            size: Cantidad de médicos por página.

        Returns:
            dict: ``{"data": [...], "total": int, "page": int, "size": int}``. La
            lista viene vacía si la escuela no tiene médicos asignados.

        Raises:
            HTTPException: 403 si el usuario no está vinculado a ninguna escuela.
        """
        school_id = self._get_school_id_for_admin(user_id)

        # 1. Contar doctores asignados a esta escuela
        count_res = (
            supabase.table("doctor_to_school")
            .select("id, doctor!inner(user!inner(id))", count="exact")
            .eq("school_id", school_id)
            .eq("doctor.user.is_deleted", False)
            .execute()
        )
        total = count_res.count or 0
        if total == 0:
            return {"data": [], "total": 0, "page": page, "size": size}

        # 2. Obtener la relación paginada
        offset = (page - 1) * size
        d2s_res = (
            supabase.table("doctor_to_school")
            .select("doctor_id, status, doctor!inner(user!inner(id))")
            .eq("school_id", school_id)
            .eq("doctor.user.is_deleted", False)
            .order("id")
            .range(offset, offset + size - 1)
            .execute()
        )
        
        if not d2s_res.data:
            return {"data": [], "total": total, "page": page, "size": size}

        doctor_ids = [d["doctor_id"] for d in d2s_res.data]
        status_ids = list({d["status"] for d in d2s_res.data if d.get("status") is not None})

        # 3. Obtener doctores
        docs_res = (
            supabase.table("doctor")
            .select("doc_id, user_id, doc_license_number, especially, experience_years")
            .in_("doc_id", doctor_ids)
            .execute()
        )
        doctors_map = {d["doc_id"]: d for d in docs_res.data}
        
        user_ids = list({d["user_id"] for d in docs_res.data if d.get("user_id") is not None})
        
        # 4. Obtener usuarios correspondientes a los doctores
        users_res = (
            supabase.table("user")
            .select("id, name, lastname, gender, photo_url")
            .in_("id", user_ids)
            .execute()
        )
        users_map = {u["id"]: u for u in users_res.data}

        # 5. Obtener los nombres de los estatus
        status_map = {}
        if status_ids:
            status_res = (
                supabase.table("system_status")
                .select("status_id, status_name")
                .in_("status_id", status_ids)
                .execute()
            )
            status_map = {str(s["status_id"]): s.get("status_name", "Desconocido") for s in status_res.data}

        # 6. Formatear la lista de doctores
        formatted_doctors = []
        for d2s in d2s_res.data:
            doctor = doctors_map.get(d2s["doctor_id"])
            if not doctor:
                continue
            
            user = users_map.get(doctor.get("user_id"))
            if not user:
                continue

            # Determinar prefijo según género (M/F o vacio)
            gender = user.get("gender")
            prefix = "Dr."
            if gender and str(gender).strip().upper() in ["F", "FEMALE", "FEMENINO", "MUJER"]:
                prefix = "Dra."
                
            full_name = f"{prefix} {user.get('name', '')} {user.get('lastname', '')}".strip()
            
            experience_years = doctor.get("experience_years")
            years_of_experience = 0
            if experience_years:
                current_year = date.today().year
                years_of_experience = max(0, current_year - experience_years)
                
            formatted_doctors.append({
                "id": d2s["doctor_id"],
                "fullname": full_name,
                "name": user.get("name"),
                "lastname": user.get("lastname"),
                "photo_url": user.get("photo_url"),
                "specialty": doctor.get("especially"),
                "medical_license": doctor.get("doc_license_number"),
                "status": status_map.get(str(d2s.get("status")), "Desconocido"),
                "experience_years": experience_years,
                "years_of_experience": years_of_experience
            })


        return {
            "data": formatted_doctors,
            "total": total,
            "page": page,
            "size": size,
        }

    # ──────────────────────────────────────────────────────────
    # GET - Detalle de un Doctor
    # ──────────────────────────────────────────────────────────

    def get_doctor_detail(self, user_id: int, doctor_id: int) -> dict:
        """
        Obtiene el detalle completo de un médico para el administrador de escuela.
        Incluye información profesional y personal del usuario vinculado.

        Args:
            user_id: ID del administrador de escuela autenticado (claim 'sub').
            doctor_id: ID del médico ('doctor.doc_id').

        Returns:
            dict: 'doctor_info' (datos profesionales y estatus en la escuela) y
            'user_info' (datos personales, 'photo_url' o None y teléfonos). Nunca
            incluye 'password' ni 'photo_file_id'.

        Raises:
            HTTPException: 403 si el administrador no está vinculado a una escuela o
                el médico no pertenece a su escuela; 404 si el médico no existe.
        """
        school_id = self._get_school_id_for_admin(user_id)

        # 1. Verificar que el doctor esté vinculado a esta escuela
        d2s_res = (
            supabase.table("doctor_to_school")
            .select("status")
            .eq("doctor_id", doctor_id)
            .eq("school_id", school_id)
            .execute()
        )
        if not d2s_res.data:
            raise HTTPException(
                status_code=403,
                detail="No tienes permiso para ver los detalles de este médico: no pertenece a tu escuela o no está vinculado."
            )
        d2s_data = d2s_res.data[0]

        # 2. Obtener información del doctor y su usuario vinculado
        doc_res = (
            supabase.table("doctor")
            .select("*, user(*)")
            .eq("doc_id", doctor_id)
            .execute()
        )
        if not doc_res.data:
            raise HTTPException(status_code=404, detail=f"No existe ningún médico con ID {doctor_id}.")
        
        doctor_data = doc_res.data[0]
        user_data = doctor_data.pop("user", {}) if doctor_data.get("user") else {}
        if user_data.get("is_deleted"):
            raise HTTPException(status_code=404, detail=f"No existe ningún médico con ID {doctor_id}.")

        # 3. Obtener el nombre del estatus en la escuela
        status_name = "Desconocido"
        if d2s_data.get("status"):
            st_res = (
                supabase.table("system_status")
                .select("status_name")
                .eq("status_id", d2s_data["status"])
                .execute()
            )
            if st_res.data:
                status_name = st_res.data[0].get("status_name", "Desconocido")
                print(f"DEBUG: Status found for doctor {doctor_id}: {status_name}")
            else:
                print(f"DEBUG: No status found in system_status for id {d2s_data['status']}")

        # 4. Obtener teléfonos del usuario
        phones = []
        if user_data.get("id"):
            phones_res = (
                supabase.table("user_phones")
                .select("phone_number, phone_category")
                .eq("user_id", user_data["id"])
                .execute()
            )
            phones = phones_res.data if phones_res.data else []

        # 5. Calcular años de experiencia
        experience_years = doctor_data.get("experience_years")
        years_of_experience = 0
        if experience_years:
            current_year = date.today().year
            years_of_experience = max(0, current_year - experience_years)

        # 6. Formatear respuesta final
        gender = user_data.get("gender")
        prefix = "Dr."
        if gender and str(gender).strip().upper() in ["F", "FEMALE", "FEMENINO", "MUJER"]:
            prefix = "Dra."
        
        full_name = f"{prefix} {user_data.get('name', '')} {user_data.get('lastname', '')}".strip()

        return {
            "doctor_info": {
                "id": doctor_id,
                "doc_license_number": doctor_data.get("doc_license_number"),
                "specialty": doctor_data.get("especially"),
                "experience_years": experience_years,
                "years_of_experience": years_of_experience,
                "school_status": status_name,
                "created_at": doctor_data.get("created_at"),
                "updated_at": doctor_data.get("updated_at")
            },
            "user_info": {
                "id": user_data.get("id"),
                "email": user_data.get("email"),
                "name": user_data.get("name"),
                "lastname": user_data.get("lastname"),
                "fullname": full_name,
                "gender": user_data.get("gender"),
                "birthday": user_data.get("birthday"),
                "address": user_data.get("address"),
                "identity_number": user_data.get("identity_number"),
                "biography": user_data.get("biography"),
                "photo_url": user_data.get("photo_url"),
                "phones": phones
            }
        }

    # ──────────────────────────────────────────────────────────
    # PATCH - Suspender / Activar Doctor en la Escuela
    # ──────────────────────────────────────────────────────────

    def suspend_doctor(self, user_id: int, doctor_id: int) -> dict:
        """
        Cambia el estado de un doctor a 'SUSPENDIDO' (status_id=1) en la relación con la escuela del admin.
        """
        school_id = self._get_school_id_for_admin(user_id)

        # Verificar existencia de la relación
        d2s_res = (
            supabase.table("doctor_to_school")
            .select("id")
            .eq("doctor_id", doctor_id)
            .eq("school_id", school_id)
            .execute()
        )
        if not d2s_res.data:
            raise HTTPException(
                status_code=404, 
                detail=f"El médico con ID {doctor_id} no está vinculado a tu escuela."
            )

        # Actualizar a SUSPENDIDO (1)
        update_res = (
            supabase.table("doctor_to_school")
            .update({"status": 1})
            .eq("doctor_id", doctor_id)
            .eq("school_id", school_id)
            .execute()
        )
        
        return {"message": "Las labores del doctor han sido suspendidas exitosamente."}

    def reactivate_doctor(self, user_id: int, doctor_id: int) -> dict:
        """
        Cambia el estado de un doctor a 'ACTIVO' (status_id=2) en la relación con la escuela del admin.
        """
        school_id = self._get_school_id_for_admin(user_id)

        # Verificar existencia de la relación
        d2s_res = (
            supabase.table("doctor_to_school")
            .select("id")
            .eq("doctor_id", doctor_id)
            .eq("school_id", school_id)
            .execute()
        )
        if not d2s_res.data:
            raise HTTPException(
                status_code=404, 
                detail=f"El médico con ID {doctor_id} no está vinculado a tu escuela."
            )

        # Actualizar a ACTIVO (2)
        update_res = (
            supabase.table("doctor_to_school")
            .update({"status": 2})
            .eq("doctor_id", doctor_id)
            .eq("school_id", school_id)
            .execute()
        )
        
        return {"message": "El médico ha sido activado exitosamente."}

    # ──────────────────────────────────────────────────────────
    # GET - Detalle de un caso médico específico
    # ──────────────────────────────────────────────────────────

    def get_medical_case_detail(self, user_id: int, case_id: int) -> dict:
        """Devuelve el detalle de un caso médico con todo su contexto.

        Args:
            user_id: ID del administrador autenticado (claim ``sub`` del JWT).
            case_id: ID del caso médico a consultar.

        Returns:
            dict: ``case_info``, ``student_info``, ``doctor_info``,
            ``representatives`` y ``health_context`` del estudiante afectado.

        Raises:
            HTTPException: 403 si el usuario no está vinculado a ninguna escuela, o
                si el estudiante del caso pertenece a otra escuela.
            HTTPException: 404 si no existe un caso médico activo con ese ID.
        """
        school_id = self._get_school_id_for_admin(user_id)

        # 1. Obtener el caso médico principal
        case_res = (
            supabase.table("medical_case")
            .select("*")
            .eq("id", case_id)
            .eq("is_deleted", False)
            .execute()
        )
        if not case_res.data:
            raise HTTPException(status_code=404, detail=f"No existe ningún caso médico activo con ID {case_id}.")
        medical_case = case_res.data[0]
        
        student_id = medical_case["student_id"]

        # 2. Utilizar el método existente para verificar permisos, obtener datos del estudiante, 
        # a los representantes, y de paso su información de salud para dar mayor contexto.
        # Si el estudiante no pertenece a la escuela del administrador, get_student_detail lanzará un 403.
        student_full_detail = self.get_student_detail(user_id=user_id, student_id=student_id)

        # 3. Obtener información del doctor
        doctor_info = None
        if medical_case.get("doctor_id"):
            doc_res = (
                supabase.table("doctor")
                .select("doc_id, user(id, name, lastname)")
                .eq("doc_id", medical_case["doctor_id"])
                .execute()
            )
            if doc_res.data:
                doc_data = doc_res.data[0]
                user_data = doc_data.get("user") or {}
                doctor_info = {
                    "id": doc_data["doc_id"],
                    "name": user_data.get("name"),
                    "lastname": user_data.get("lastname")
                }

        # 4. Limpiar case_info (quitar IDs)
        case_info = medical_case.copy()
        case_info.pop("student_id", None)
        case_info.pop("doctor_id", None)

        # 5. Empaquetar y retornar toda la información
        return {
            "case_info": case_info,
            "student_info": student_full_detail.get("student"),
            "doctor_info": doctor_info,
            "representatives": student_full_detail.get("representatives"),
            "health_context": student_full_detail.get("health_info")
        }

    # ──────────────────────────────────────────────────────────
    # GET - Detalle de un Representante y sus Hijos
    # ──────────────────────────────────────────────────────────

    def get_parent_detail(self, user_id: int, parent_id: int) -> dict:
        """Devuelve el detalle de un representante y de los hijos que tiene en la escuela.

        La pertenencia es indirecta: el representante solo es visible si al menos
        uno de sus estudiantes está inscrito en la escuela del administrador.

        Args:
            user_id: ID del administrador autenticado (claim ``sub`` del JWT).
            parent_id: ID del representante a consultar.

        Returns:
            dict: ``representative_info``, ``total_children`` y ``children``.

        Raises:
            HTTPException: 403 si el usuario no está vinculado a ninguna escuela, si
                el representante no tiene estudiantes asociados, o si ninguno de
                ellos pertenece a la escuela del administrador.
            HTTPException: 404 si no existe un representante activo con ese ID.
        """
        school_id = self._get_school_id_for_admin(user_id)

        # 1. Obtener la información del representante
        parent_res = (
            supabase.table("parent")
            .select("*, user(*)")
            .eq("id", parent_id)
            .eq("is_deleted", False)
            .execute()
        )
        if not parent_res.data:
            raise HTTPException(status_code=404, detail=f"No existe un representante activo con ID {parent_id}.")
        
        parent_data = parent_res.data[0]
        # Never expose credentials or internal storage ids of the nested user row
        if isinstance(parent_data.get("user"), dict):
            parent_data["user"].pop("password", None)
            parent_data["user"].pop("photo_file_id", None)

        # 2. Obtener la relación de hijos desde student_representative
        sr_res = (
            supabase.table("student_representative")
            .select("student_id")
            .eq("parent_id", parent_id)
            .eq("is_deleted", False)
            .execute()
        )
        student_ids = [r["student_id"] for r in sr_res.data] if sr_res.data else []

        if not student_ids:
            raise HTTPException(
                status_code=403, 
                detail="No tienes permiso para ver a este representante porque no tiene estudiantes asociados."
            )

        # 3. Validar permisos: Al menos un hijo debe pertenecer a la escuela del administrador
        # y cargar la información del classroom para todos.
        regs_res = (
            supabase.table("classroom_registration")
            .select("student_id, classroom_id, created_at, classroom(school_id, category, level, section)")
            .in_("student_id", student_ids)
            .order("created_at", desc=True)
            .execute()
        )

        # Determinar el último salón de cada estudiante y si alguno pertenece a la escuela
        student_latest_classroom = {}
        has_access = False
        
        for reg in regs_res.data:
            sid = reg["student_id"]
            if sid not in student_latest_classroom:
                classroom_info = reg.get("classroom")
                student_latest_classroom[sid] = classroom_info
                if classroom_info and classroom_info.get("school_id") == school_id:
                    has_access = True

        if not has_access:
            raise HTTPException(
                status_code=403,
                detail="No tienes permiso para ver a este representante: ninguno de sus hijos pertenece a tu escuela."
            )

        # 4. Obtener información de los estudiantes
        students_res = (
            supabase.table("student")
            .select("id, name, lastname, birthday")
            .in_("id", student_ids)
            .eq("is_deleted", False)
            .execute()
        )

        children_list = []
        if students_res.data:
            # Obtener casos médicos activos para comprobar el boolean has_active_medical_case
            cases_res = (
                supabase.table("medical_case")
                .select("student_id")
                .in_("student_id", student_ids)
                .is_("end_date", "null")
                .eq("is_deleted", False)
                .execute()
            )
            active_cases_students = {c["student_id"] for c in cases_res.data} if cases_res.data else set()

            # Obtener datos de salud para el IMC
            metrics_res = (
                supabase.table("student_metrics")
                .select("student_id, weight, height")
                .in_("student_id", student_ids)
                .eq("is_current", True)
                .eq("is_deleted", False)
                .execute()
            )
            # Solo la más reciente (ya filtrada por is_current=True, pero aseguramos de usar un dict)
            metrics_map = {m["student_id"]: m for m in metrics_res.data} if metrics_res.data else {}

            for student in students_res.data:
                sid = student["id"]
                
                # Nombre formateado
                full_name = f"{student.get('name', '')} {student.get('lastname', '')}".strip()

                # Grado y nivel actual
                cr = student_latest_classroom.get(sid)
                current_grade = f"{cr['category']} {cr['level']} {cr['section']}" if cr else "No asignado"

                # Estado según BMI
                nutritional_status = "SIN DATOS"
                metric = metrics_map.get(sid)
                if metric:
                    weight = metric.get("weight")
                    height = metric.get("height")
                    if weight and height and height > 0:
                        bmi = weight / (height ** 2)
                        if bmi < 18.5:
                            nutritional_status = "DESNUTRIDO"
                        elif bmi <= 24.9:
                            nutritional_status = "OPTIMO"
                        else:
                            nutritional_status = "OBESO"

                # Caso médico activo
                has_active = sid in active_cases_students

                children_list.append({
                    "id": sid,
                    "name": full_name,
                    "birthday": student.get("birthday"),
                    "current_grade": current_grade,
                    "bmi_status": nutritional_status,
                    "has_active_medical_case": has_active
                })

        return {
            "representative_info": parent_data,
            "total_children": len(children_list),
            "children": children_list
        }

    # ──────────────────────────────────────────────────────────
    # GET - Historial médico de un estudiante (Paginado)
    # ──────────────────────────────────────────────────────────

    def get_student_medical_history(self, user_id: int, student_id: int, page: int, size: int) -> dict:
        """Lista de forma paginada los casos médicos de un estudiante concreto.

        Args:
            user_id: ID del administrador autenticado (claim ``sub`` del JWT).
            student_id: ID del estudiante cuyo historial se consulta.
            page: Número de página, comenzando en 1.
            size: Cantidad de casos por página.

        Returns:
            dict: ``{"data": [...], "total": int, "page": int, "size": int}``. La
            lista viene vacía si el estudiante no tiene casos médicos.

        Raises:
            HTTPException: 403 si el usuario no está vinculado a ninguna escuela, o
                si el estudiante no pertenece a su escuela.
        """
        school_id = self._get_school_id_for_admin(user_id)

        # 1. Validar que el estudiante pertenezca a un classroom de esta escuela
        classrooms_res = (
            supabase.table("classroom")
            .select("id")
            .eq("school_id", school_id)
            .eq("is_deleted", False)
            .execute()
        )
        classroom_ids = [c["id"] for c in classrooms_res.data] if classrooms_res.data else []
        
        regs_res = (
            supabase.table("classroom_registration")
            .select("id")
            .eq("student_id", student_id)
            .in_("classroom_id", classroom_ids)
            .execute()
        )
        
        if not regs_res.data:
            raise HTTPException(
                status_code=403,
                detail=f"No tiene permisos para ver los casos médicos del estudiante {student_id} porque no está en su escuela."
            )

        # 2. Contar casos
        count_res = (
            supabase.table("medical_case")
            .select("id", count="exact")
            .eq("student_id", student_id)
            .eq("is_deleted", False)
            .execute()
        )
        total = count_res.count or 0
        if total == 0:
            return {"data": [], "total": 0, "page": page, "size": size}

        # 3. Obtener los casos médicos paginados
        offset = (page - 1) * size
        cases_res = (
            supabase.table("medical_case")
            .select("id, init_date, type_of_case, end_date, title, symptomatology")
            .eq("student_id", student_id)
            .eq("is_deleted", False)
            .order("init_date", desc=True)
            .range(offset, offset + size - 1)
            .execute()
        )
        
        # 4. Formatear
        formatted_cases = []
        for case in cases_res.data:
            formatted_cases.append({
                "id": case.get("id"),
                "start_date": case.get("init_date"),
                "type_of_case": case.get("type_of_case"),
                "is_active": case.get("end_date") is None,
                "title": case.get("title"),
                "description": case.get("symptomatology")
            })

        return {
            "data": formatted_cases,
            "total": total,
            "page": page,
            "size": size,
        }

    # ──────────────────────────────────────────────────────────
    # PUT - Actualizar perfil del estudiante (incluyendo representante)
    # ──────────────────────────────────────────────────────────

    def update_student_profile(self, user_id: int, student_id: int, payload: dict) -> dict:
        """Actualiza los datos de un estudiante de la escuela.

        Admite además reasignar el representante mediante ``representative_id``.

        Args:
            user_id: ID del administrador autenticado (claim ``sub`` del JWT).
            student_id: ID del estudiante a actualizar.
            payload: Campos a modificar. Los omitidos conservan su valor actual.

        Returns:
            dict: ``{"student_id": int, "updated": True}``.

        Raises:
            HTTPException: 403 si el usuario no está vinculado a ninguna escuela, o
                si el estudiante no pertenece a su escuela.
            HTTPException: 400 si el ``representative_id`` indicado no existe.
        """
        school_id = self._get_school_id_for_admin(user_id)
        
        # 1. Validar permiso (si el estudiante está en su escuela)
        classrooms_res = (
            supabase.table("classroom")
            .select("id")
            .eq("school_id", school_id)
            .eq("is_deleted", False)
            .execute()
        )
        classroom_ids = [c["id"] for c in classrooms_res.data] if classrooms_res.data else []
        
        regs_res = (
            supabase.table("classroom_registration")
            .select("id")
            .eq("student_id", student_id)
            .in_("classroom_id", classroom_ids)
            .execute()
        )
        if not regs_res.data:
            raise HTTPException(
                status_code=403,
                detail=f"No tiene permisos para modificar al estudiante {student_id} porque no está en su escuela."
            )

        # 2. Separar el id del representante del resto de los datos del estudiante
        rep_id = payload.pop("representative_id", None)
        
        # 3. Actualizar información del estudiante en la base de datos
        if payload:
            format_payload = {}
            for key, val in payload.items():
                if hasattr(val, 'value'):
                    # Caso de Enums (Gender, BloodType)
                    format_payload[key] = val.value
                elif key == "birthday" and val is not None:
                    # En caso de la fecha, parsear a string si viene como objeto date
                    format_payload[key] = val.isoformat() if hasattr(val, 'isoformat') else val
                elif val is not None:
                    format_payload[key] = val

            if format_payload:
                upd_res = (
                    supabase.table("student")
                    .update(format_payload)
                    .eq("id", student_id)
                    .execute()
                )

        # 4. Actualizar el representante del estudiante si se provee
        if rep_id is not None:
            parent_res = supabase.table("parent").select("id").eq("id", rep_id).execute()
            if not parent_res.data:
                raise HTTPException(status_code=400, detail="El identificador del representante(parent_id) provisto no existe.")
                
            curr_rep_res = (
                supabase.table("student_representative")
                .select("id")
                .eq("student_id", student_id)
                .eq("is_deleted", False)
                .execute()
            )
            
            if curr_rep_res.data:
                # Modificamos la relación actual
                (
                    supabase.table("student_representative")
                    .update({"parent_id": rep_id})
                    .eq("id", curr_rep_res.data[0]["id"])
                    .execute()
                )
            else:
                # Si no tenía representate, creamos una nueva
                (
                    supabase.table("student_representative")
                    .insert({"student_id": student_id, "parent_id": rep_id})
                    .execute()
                )

        return {"student_id": student_id, "updated": True}

    # ──────────────────────────────────────────────────────────
    # GET - Buscador de Representantes
    # ──────────────────────────────────────────────────────────

    def search_representatives(self, query: str, page: int, size: int) -> dict:
        """Busca representantes por nombre y apellido, de forma paginada.

        Con un solo término busca coincidencias en nombre o apellido; con varios,
        interpreta el primero como nombre y el resto como apellido.

        Nota: a diferencia del resto de métodos de este servicio, la búsqueda no
        recibe ``user_id`` y por tanto no se filtra por escuela: recorre todos los
        representantes del sistema. Es el comportamiento vigente, consumido por el
        endpoint de asignación de representante a un estudiante.

        Args:
            query: Texto de búsqueda. Si viene vacío o en blanco, el resultado es
                una lista vacía sin consultar la base de datos.
            page: Número de página, comenzando en 1.
            size: Cantidad de representantes por página.

        Returns:
            dict: ``{"data": [...], "total": int, "page": int, "size": int}``. La
            lista viene vacía si no hay coincidencias.
        """
        if not query or not query.strip():
            return {"data": [], "total": 0, "page": page, "size": size}

        # 1. Buscar de forma flexible en la tabla user
        u_query = supabase.table("user").select("id, name, lastname, email").eq("is_deleted", False)
        
        parts = query.strip().split()
        if len(parts) == 1:
            term = f"%{parts[0]}%"
            u_query = u_query.or_(f"name.ilike.{term},lastname.ilike.{term}")
        else:
            first = f"%{parts[0]}%"
            last = f"%{' '.join(parts[1:])}%"
            u_query = u_query.ilike("name", first).ilike("lastname", last)
            
        u_res = u_query.execute()
        
        if not u_res.data:
            return {"data": [], "total": 0, "page": page, "size": size}
            
        user_ids = [u["id"] for u in u_res.data]
        users_map = {u["id"]: u for u in u_res.data}

        # 2. Contar resultados reales que sí sean parent (representantes)
        count_res = (
            supabase.table("parent")
            .select("id", count="exact")
            .in_("user_id", user_ids)
            .eq("is_deleted", False)
            .execute()
        )
        total = count_res.count or 0
        if total == 0:
            return {"data": [], "total": 0, "page": page, "size": size}

        # 3. Obtener padres paginados
        offset = (page - 1) * size
        p_res = (
            supabase.table("parent")
            .select("id, ocupation, user_id")
            .in_("user_id", user_ids)
            .eq("is_deleted", False)
            .range(offset, offset + size - 1)
            .execute()
        )

        # 4. Formatear la lista solicitada
        formatted_list = []
        for parent in p_res.data:
            user_info = users_map.get(parent["user_id"], {})
            formatted_list.append({
                "representative_id": parent["id"],
                "user_id": parent["user_id"],
                "name": user_info.get("name", ""),
                "lastname": user_info.get("lastname", ""),
                "email": user_info.get("email", ""),
                "ocupation": parent.get("ocupation")
            })

        return {
            "data": formatted_list,
            "total": total,
            "page": page,
            "size": size,
        }

    # ──────────────────────────────────────────────────────────
    # PATCH - Activar / Desactivar Estudiante
    # ──────────────────────────────────────────────────────────

    def activate_student(self, user_id: int, student_id: int) -> dict:
        """Activa a un estudiante de la escuela, dejándolo en estado activo.

        Args:
            user_id: ID del administrador autenticado (claim ``sub`` del JWT).
            student_id: ID del estudiante cuyo estado se desea cambiar.

        Returns:
            dict: Fila del estudiante ya actualizada, o un dict vacío si la
            actualización no devolvió filas.

        Raises:
            HTTPException: 404 si el estudiante no existe.
            HTTPException: 400 si se intenta activar a un estudiante eliminado.
            HTTPException: 403 si el usuario no está vinculado a ninguna escuela, si
                el estudiante no está inscrito en ningún salón, o si pertenece a
                otra escuela.
        """
        school_id = self._get_school_id_for_admin(user_id)
        
        # 1. Verificar si existe el estudiante
        student_res = supabase.table("student").select("id, is_active, is_deleted").eq("id", student_id).execute()
        if not student_res.data:
            raise HTTPException(status_code=404, detail="Estudiante no encontrado.")
        if student_res.data[0].get("is_deleted"):
            raise HTTPException(status_code=400, detail="No se puede activar a un estudiante eliminado.")
            
        # 2. Verificar aislamiento de tenant
        reg_res = (
            supabase.table("classroom_registration")
            .select("classroom_id")
            .eq("student_id", student_id)
            .order("created_at", desc=True)
            .limit(1)
            .execute()
        )
        if not reg_res.data:
            raise HTTPException(
                status_code=403,
                detail="Este estudiante no está inscrito en ningún salón de tu escuela."
            )
        classroom_id = reg_res.data[0]["classroom_id"]
        cr_res = (
            supabase.table("classroom")
            .select("school_id")
            .eq("id", classroom_id)
            .execute()
        )
        if not cr_res.data or cr_res.data[0]["school_id"] != school_id:
            raise HTTPException(
                status_code=403,
                detail="No tienes permiso para modificar a este estudiante: pertenece a otra escuela."
            )
        
        # 3. Actualizar estado
        update_res = (
            supabase.table("student")
            .update({"is_active": True})
            .eq("id", student_id)
            .execute()
        )
        return update_res.data[0] if update_res.data else {}

    def deactivate_student(self, user_id: int, student_id: int) -> dict:
        """Desactiva a un estudiante de la escuela, dejándolo en estado inactivo.

        Args:
            user_id: ID del administrador autenticado (claim ``sub`` del JWT).
            student_id: ID del estudiante cuyo estado se desea cambiar.

        Returns:
            dict: Fila del estudiante ya actualizada, o un dict vacío si la
            actualización no devolvió filas.

        Raises:
            HTTPException: 404 si el estudiante no existe.
            HTTPException: 400 si se intenta modificar un estudiante eliminado.
            HTTPException: 403 si el usuario no está vinculado a ninguna escuela, si
                el estudiante no está inscrito en ningún salón, o si pertenece a
                otra escuela.
        """
        school_id = self._get_school_id_for_admin(user_id)
        
        # 1. Verificar si existe el estudiante
        student_res = supabase.table("student").select("id, is_active, is_deleted").eq("id", student_id).execute()
        if not student_res.data:
            raise HTTPException(status_code=404, detail="Estudiante no encontrado.")
        if student_res.data[0].get("is_deleted"):
            raise HTTPException(status_code=400, detail="No se puede modificar un estudiante eliminado.")
            
        # 2. Verificar aislamiento de tenant
        reg_res = (
            supabase.table("classroom_registration")
            .select("classroom_id")
            .eq("student_id", student_id)
            .order("created_at", desc=True)
            .limit(1)
            .execute()
        )
        if not reg_res.data:
            raise HTTPException(
                status_code=403,
                detail="Este estudiante no está inscrito en ningún salón de tu escuela."
            )
        classroom_id = reg_res.data[0]["classroom_id"]
        cr_res = (
            supabase.table("classroom")
            .select("school_id")
            .eq("id", classroom_id)
            .execute()
        )
        if not cr_res.data or cr_res.data[0]["school_id"] != school_id:
            raise HTTPException(
                status_code=403,
                detail="No tienes permiso para modificar a este estudiante: pertenece a otra escuela."
            )
        
        # 3. Actualizar estado
        update_res = (
            supabase.table("student")
            .update({"is_active": False})
            .eq("id", student_id)
            .execute()
        )
        return update_res.data[0] if update_res.data else {}

    # ──────────────────────────────────────────────────────────
    # GET - Resumen de categorías y niveles del año vigente
    # ──────────────────────────────────────────────────────────

    def get_classroom_categories_summary(self, user_id: int, category_id: int | None = None) -> dict:
        """Resume los salones de la escuela agrupados por categoría y grado.

        Args:
            user_id: ID del administrador autenticado (claim ``sub`` del JWT).
            category_id: Si se indica, restringe el resumen a esa categoría.

        Returns:
            dict: ``{"items": [...]}`` con una entrada por grupo. Lista vacía si la
            escuela no tiene salones en el año vigente.

        Raises:
            HTTPException: 403 si el usuario no está vinculado a ninguna escuela.
            HTTPException: 404 si no hay un año académico vigente configurado.
        """
        school_id = self._get_school_id_for_admin(user_id)

        ay_res = (
            supabase.table("academic_year")
            .select("id, name")
            .eq("is_current", True)
            .eq("is_deleted", False)
            .limit(1)
            .execute()
        )
        if not ay_res.data:
            raise HTTPException(
                status_code=404,
                detail="No hay un año académico vigente configurado en el sistema."
            )
        academic_year_id = ay_res.data[0]["id"]

        query = (
            supabase.table("classroom")
            .select("level, category, classroom_category(id, classroom_type_name)")
            .eq("school_id", school_id)
            .eq("academic_year_id", academic_year_id)
            .eq("is_deleted", False)
        )
        
        if category_id is not None:
            query = query.eq("category", category_id)
            
        rows_res = query.execute()

        if not rows_res.data:
            return {"items": []}

        seen: set = set()
        items = []
        for row in rows_res.data:
            level = row["level"]
            cat_id = row["category"]
            cat_info = row.get("classroom_category") or {}
            key = (level, cat_id)
            if key not in seen:
                seen.add(key)
                items.append({
                    "level": level,
                    "classroom_category_id": cat_id,
                    "classroom_type_name": cat_info.get("classroom_type_name"),
                })

        items.sort(key=lambda x: (x["classroom_category_id"], x["level"]))
        return {"items": items}

    # ──────────────────────────────────────────────────────────
    # GET - Categorías de grados disponibles del año vigente
    # ──────────────────────────────────────────────────────────

    def get_available_classroom_categories(self, user_id: int) -> dict:
        """Lista las categorías de salón que la escuela realmente usa este año.

        Pensado para poblar selectores en el frontend sin ofrecer categorías vacías.

        Args:
            user_id: ID del administrador autenticado (claim ``sub`` del JWT).

        Returns:
            dict: ``{"items": [...]}`` con las categorías en uso. Lista vacía si la
            escuela no tiene salones en el año vigente.

        Raises:
            HTTPException: 403 si el usuario no está vinculado a ninguna escuela.
            HTTPException: 404 si no hay un año académico vigente configurado.
        """
        school_id = self._get_school_id_for_admin(user_id)

        # 1. Obtener año académico vigente
        ay_res = (
            supabase.table("academic_year")
            .select("id")
            .eq("is_current", True)
            .eq("is_deleted", False)
            .limit(1)
            .execute()
        )
        if not ay_res.data:
            raise HTTPException(
                status_code=404,
                detail="No hay un año académico vigente configurado en el sistema."
            )
        academic_year_id = ay_res.data[0]["id"]

        # 2. Obtener categorías únicas de las aulas de la escuela en este año
        rows_res = (
            supabase.table("classroom")
            .select("category, classroom_category(id, classroom_type_name)")
            .eq("school_id", school_id)
            .eq("academic_year_id", academic_year_id)
            .eq("is_deleted", False)
            .execute()
        )

        if not rows_res.data:
            return {"items": []}

        seen_cats = set()
        items = []
        for row in rows_res.data:
            cat_id = row["category"]
            if cat_id and cat_id not in seen_cats:
                seen_cats.add(cat_id)
                cat_info = row.get("classroom_category") or {}
                items.append({
                    "classroom_category_id": cat_id,
                    "classroom_type_name": cat_info.get("classroom_type_name")
                })

        items.sort(key=lambda x: x["classroom_category_id"])
        return {"items": items}

    # ──────────────────────────────────────────────────────────
    # GET - Secciones disponibles por categoría y nivel
    # ──────────────────────────────────────────────────────────

    def get_sections_by_category_and_level(self, user_id: int, category_id: int, level: int, active: bool = False) -> dict:
        """Lista las secciones existentes para una categoría y grado concretos.

        Args:
            user_id: ID del administrador autenticado (claim ``sub`` del JWT).
            category_id: ID de la categoría (nivel educativo) a consultar.
            level: Grado dentro de la categoría.
            active: Si es True, devuelve solo las secciones con salones activos.

        Returns:
            dict: ``{"items": [...]}`` con las secciones encontradas. Lista vacía si
            no hay ninguna que cumpla el filtro.

        Raises:
            HTTPException: 403 si el usuario no está vinculado a ninguna escuela.
            HTTPException: 404 si no hay un año académico vigente configurado.
        """
        school_id = self._get_school_id_for_admin(user_id)

        query = (
            supabase.table("classroom")
            .select("id, section")
            .eq("school_id", school_id)
            .eq("category", category_id)
            .eq("level", level)
            .eq("is_deleted", False)
        )

        if active:
            ay_res = (
                supabase.table("academic_year")
                .select("id, name")
                .eq("is_current", True)
                .eq("is_deleted", False)
                .limit(1)
                .execute()
            )
            if not ay_res.data:
                raise HTTPException(
                    status_code=404,
                    detail="No hay un año académico vigente configurado en el sistema."
                )
            academic_year_id = ay_res.data[0]["id"]
            query = query.eq("academic_year_id", academic_year_id)

        rows_res = query.order("section").execute()

        items = [
            {
                "classroom_id": row["id"],
                "classroom_category_id": category_id,
                "level": level,
                "section": row["section"],
            }
            for row in (rows_res.data or [])
        ]

        return {"items": items}

    # ──────────────────────────────────────────────────────────
    # GET - Tendencia de casos médicos (mensual / semanal)
    # ──────────────────────────────────────────────────────────

    def get_medical_cases_tendency(
        self,
        user_id: int,
        mode: str,
        months: int,
        weeks: int,
    ) -> dict:
        """Calcula la tendencia de casos médicos de la escuela en el tiempo.

        Agrupa los casos en cubetas semanales o mensuales, compara el período
        actual con el inmediatamente anterior para obtener la tasa de crecimiento
        y acompaña la serie con una recta de tendencia.

        Args:
            user_id: ID del administrador autenticado (claim ``sub`` del JWT).
            mode: ``"weekly"`` para agrupar por semanas; cualquier otro valor
                agrupa por meses.
            months: Cantidad de meses a cubrir cuando ``mode`` no es ``"weekly"``.
            weeks: Cantidad de semanas a cubrir cuando ``mode`` es ``"weekly"``.

        Returns:
            dict: ``summary`` con ``total_incidents`` y ``growth_rate``, y ``data``
            con la serie por cubeta. Todo en cero y ``data`` vacío si la escuela no
            tiene salones o estudiantes.

        Raises:
            HTTPException: 403 si el usuario no está vinculado a ninguna escuela.
        """
        school_id = self._get_school_id_for_admin(user_id)

        classrooms_res = (
            supabase.table("classroom")
            .select("id")
            .eq("school_id", school_id)
            .eq("is_deleted", False)
            .execute()
        )
        classroom_ids = [c["id"] for c in (classrooms_res.data or [])]
        if not classroom_ids:
            return {"summary": {"total_incidents": 0, "growth_rate": 0.0}, "data": []}

        regs_res = (
            supabase.table("classroom_registration")
            .select("student_id")
            .in_("classroom_id", classroom_ids)
            .execute()
        )
        student_ids = list({r["student_id"] for r in (regs_res.data or [])})
        if not student_ids:
            return {"summary": {"total_incidents": 0, "growth_rate": 0.0}, "data": []}

        today = date.today()

        if mode == "weekly":
            current_start = today - timedelta(weeks=weeks)
            previous_start = current_start - timedelta(weeks=weeks)
            previous_end = current_start - timedelta(days=1)

            cases_res = (
                supabase.table("medical_case")
                .select("init_date")
                .in_("student_id", student_ids)
                .gte("init_date", current_start.isoformat())
                .lte("init_date", today.isoformat())
                .eq("is_deleted", False)
                .execute()
            )
            cases = cases_res.data or []

            week_order = []
            week_buckets: dict = {}
            for i in range(weeks):
                ws = current_start + timedelta(weeks=i)
                iso_year, iso_week, _ = ws.isocalendar()
                key = (iso_year, iso_week)
                if key not in week_buckets:
                    week_buckets[key] = {"label": f"Sem {iso_week}", "count": 0}
                    week_order.append(key)

            for case in cases:
                raw = (case.get("init_date") or "")[:10]
                if raw:
                    d = date.fromisoformat(raw)
                    iso_year, iso_week, _ = d.isocalendar()
                    key = (iso_year, iso_week)
                    if key in week_buckets:
                        week_buckets[key]["count"] += 1

            values = [week_buckets[k]["count"] for k in week_order]
            trends = _linear_trend(values)
            data = [
                {"label": week_buckets[week_order[i]]["label"], "value": values[i], "trend": trends[i]}
                for i in range(len(week_order))
            ]

        else:
            current_start = _first_day_months_ago(today, months)
            previous_start = _first_day_months_ago(current_start, months)
            previous_end = current_start - timedelta(days=1)

            cases_res = (
                supabase.table("medical_case")
                .select("init_date")
                .in_("student_id", student_ids)
                .gte("init_date", current_start.isoformat())
                .lte("init_date", today.isoformat())
                .eq("is_deleted", False)
                .execute()
            )
            cases = cases_res.data or []

            month_order = []
            month_buckets: dict = {}
            ref = current_start
            while ref <= today:
                key = (ref.year, ref.month)
                if key not in month_buckets:
                    month_buckets[key] = {"label": _MONTH_LABELS[ref.month - 1], "count": 0}
                    month_order.append(key)
                ref = date(ref.year + (ref.month // 12), ref.month % 12 + 1, 1)

            for case in cases:
                raw = (case.get("init_date") or "")[:10]
                if raw:
                    d = date.fromisoformat(raw)
                    key = (d.year, d.month)
                    if key in month_buckets:
                        month_buckets[key]["count"] += 1

            values = [month_buckets[k]["count"] for k in month_order]
            trends = _linear_trend(values)
            data = [
                {"label": month_buckets[month_order[i]]["label"], "value": values[i], "trend": trends[i]}
                for i in range(len(month_order))
            ]

        total_incidents = sum(values)

        prev_res = (
            supabase.table("medical_case")
            .select("id", count="exact")
            .in_("student_id", student_ids)
            .gte("init_date", previous_start.isoformat())
            .lte("init_date", previous_end.isoformat())
            .eq("is_deleted", False)
            .execute()
        )
        previous_total = prev_res.count or 0

        if previous_total > 0:
            growth_rate = round(((total_incidents - previous_total) / previous_total) * 100, 1)
        elif total_incidents > 0:
            growth_rate = 100.0
        else:
            growth_rate = 0.0

        return {
            "summary": {"total_incidents": total_incidents, "growth_rate": growth_rate},
            "data": data,
        }


#: Instancia compartida del servicio, importada por los endpoints de escuela.
school_admin_service = SchoolAdminService()
