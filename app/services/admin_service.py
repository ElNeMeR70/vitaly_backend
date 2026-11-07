"""Operaciones globales del administrador de sistema.

A diferencia del resto de servicios, este opera sin alcance de escuela: el
``admin_sistema`` audita y administra todas las instituciones, por lo que sus
métodos reciben el ``school_id`` por parámetro en lugar de resolverlo del JWT.
"""

from fastapi import HTTPException, status
from app.core.database import supabase
from typing import List

from app.schemas.school import SchoolCreate, ClassroomCreate
from app.schemas.student import StudentCreate, StudentUpdate, ClassroomRegistrationCreate, BulkClassroomRegistrationCreate
from app.core.config import get_settings
import datetime

class AdminService:
    """Gestión global de usuarios, escuelas, salones y estudiantes."""

    def get_users(self, page: int = 1, size: int = 20, pending_only: bool = False):
        """Lista usuarios del sistema de forma paginada.

        Args:
            page: Número de página, comenzando en 1.
            size: Cantidad de usuarios por página.
            pending_only: Si es True, devuelve solo las cuentas sin aprobar
                (``is_active=False``).

        Returns:
            dict: ``{"data": [...], "total": int, "page": int, "size": int}``.
            La lista viene vacía si no hay usuarios que cumplan el filtro.
        """
        query = supabase.table("user").select("id, email, name, lastname, birthday, is_active, is_deleted", count="exact")
        
        if pending_only:
            query = query.eq("is_active", False).eq("is_deleted", False)
            
        # Pagination calculations
        start = (page - 1) * size
        end = start + size - 1
        
        response = query.range(start, end).execute()
        
        total = response.count if response.count is not None else 0
            
        return {
            "data": response.data,
            "total": total,
            "page": page,
            "size": size
        }

    def approve_user(self, user_id: int, admin_id: int):
        """Aprueba una cuenta pendiente, habilitándola para iniciar sesión.

        Args:
            user_id: ID del usuario a aprobar.
            admin_id: ID del administrador que registra la aprobación. Hoy solo se
                refleja en el mensaje de respuesta; queda pendiente su registro en
                la bitácora de auditoría.

        Returns:
            str: Mensaje de confirmación de la aprobación.

        Raises:
            HTTPException: 404 si el usuario no existe.
            HTTPException: 400 si el usuario está eliminado o ya estaba aprobado.
        """
        # We verify if the user exists and is inactive
        user_response = supabase.table("user").select("is_active, is_deleted").eq("id", user_id).execute()
        
        if not user_response.data:
            raise HTTPException(status_code=404, detail=f"Usuario con ID {user_id} no encontrado")
            
        user = user_response.data[0]
        
        if user["is_deleted"]:
            raise HTTPException(status_code=400, detail=f"No se puede aprobar al usuario {user_id} porque está eliminado.")
            
        if user["is_active"]:
            raise HTTPException(status_code=400, detail=f"El usuario {user_id} ya se encuentra aprobado.")
            
        # Update user to active
        supabase.table("user").update({"is_active": True}).eq("id", user_id).execute()
        
        # We could log this approval tracking `admin_id`, e.g., in an audit log in the future.
        
        return f"Usuario {user_id} aprobado exitosamente por el administrador (admin_id: {admin_id})."

    def approve_users_bulk(self, user_ids: List[int], admin_id: int):
        """Aprueba varias cuentas pendientes en una sola operación.

        Solo afecta a los usuarios que estén inactivos y no eliminados; los IDs
        inexistentes o ya aprobados se ignoran silenciosamente.

        Args:
            user_ids: IDs de los usuarios a aprobar.
            admin_id: ID del administrador que registra las aprobaciones.

        Returns:
            str: Mensaje indicando cuántas cuentas se aprobaron, o que no hubo
            ninguna por aprobar.
        """
        # We update active status for all provided IDs where is_active is false and is_deleted is false
        # The Supabase 'in_' method supports arrays
        results = (
            supabase.table("user")
            .update({"is_active": True})
            .in_("id", user_ids)
            .eq("is_active", False)
            .eq("is_deleted", False)
            .execute()
        )
        
        updated_count = len(results.data) if hasattr(results, 'data') and results.data else 0
        
        if updated_count == 0:
            return "No se ha aprobado ningún usuario (quizá ya estaban aprobados, eliminados, o los IDs no existen)."
            
        return f"Se han aprobado exitosamente {updated_count} usuarios por el administrador (admin_id: {admin_id})."

    def create_school(self, payload: SchoolCreate):
        """Registra una nueva escuela en el sistema.

        Valida que el año de fundación sea plausible y que no exista otra escuela
        con el mismo RIF ni con el mismo nombre dentro del mismo estado.

        Args:
            payload: Datos de la escuela a registrar.

        Returns:
            str: Mensaje de confirmación con el ID asignado a la escuela.

        Raises:
            HTTPException: 400 si el año de fundación es negativo o futuro, si el
                RIF ya existe, o si el par nombre+estado está duplicado.
            HTTPException: 500 si la inserción no devuelve la fila creada.
        """
        current_year = datetime.datetime.now().year
        
        if payload.year_foundation < 0:
            raise HTTPException(status_code=400, detail="El año de fundación no puede ser menor a 0")
            
        if payload.year_foundation > current_year:
            raise HTTPException(status_code=400, detail=f"El año de fundación no puede ser mayor al actual ({current_year})")
            
        # Validar RIF unico
        rif_check = supabase.table("school").select("sch_id").eq("rif", payload.rif).execute()
        if rif_check.data:
            raise HTTPException(status_code=400, detail="Ya existe una escuela registrada con este mismo RIF")
            
        # Validar Nombre + Estado duplicado
        name_state_check = supabase.table("school").select("sch_id").eq("name", payload.name).eq("state", payload.state).execute()
        if name_state_check.data:
            raise HTTPException(status_code=400, detail="Ya existe una escuela registrada con este mismo nombre en ese estado")
            
        school_data = {
            "name": payload.name,
            "year_foundation": payload.year_foundation,
            "state": payload.state,
            "rif": payload.rif,
            "address": payload.address,
            "school_type": payload.school_type,
            "is_active": True,
            "is_deleted": False
        }
        
        result = supabase.table("school").insert(school_data).execute()
        if not result.data:
            raise HTTPException(status_code=500, detail="Error al intentar registrar la escuela")
            
        return f"Escuela '{payload.name}' registrada exitosamente con el ID {result.data[0]['sch_id']}."

    def get_user_details(self, user_id: int):
        """Devuelve los datos de un usuario junto con el detalle propio de su rol.

        Según el rol, enriquece la respuesta con la fila de ``doctor``, ``parent``,
        ``school_administrator`` o ``system_administrator``.

        Args:
            user_id: ID del usuario a consultar.

        Returns:
            dict: ``{"user": dict, "role": str, "details": dict}``. ``details`` es
            un dict vacío si el rol no tiene perfil asociado.

        Raises:
            HTTPException: 404 si el usuario no existe.
        """
        user_response = supabase.table("user").select("id, email, name, lastname, birthday, is_active, is_deleted").eq("id", user_id).execute()
        if not user_response.data:
            raise HTTPException(status_code=404, detail="Usuario no encontrado")
            
        user_data = user_response.data[0]
        
        role_response = supabase.table("user_roles").select("role").eq("user_id", user_id).execute()
        role = role_response.data[0]["role"] if role_response.data else "desconocido"
        
        details = {}
        if role == "doctor":
            doc_res = supabase.table("doctor").select("*").eq("user_id", user_id).execute()
            if doc_res.data: details = doc_res.data[0]
        elif role == "representante":
            parent_res = supabase.table("parent").select("*").eq("user_id", user_id).execute()
            if parent_res.data: details = parent_res.data[0]
        elif role == "admin_escuela":
            sch_res = supabase.table("school_administrator").select("*").eq("user_id", user_id).execute()
            if sch_res.data: details = sch_res.data[0]
        elif role == "admin_sistema":
            sys_res = supabase.table("system_administrator").select("*").eq("admin_id", user_id).execute()
            if sys_res.data: details = sys_res.data[0]
            
        return {
            "user": user_data,
            "role": role,
            "details": details
        }

    def toggle_user_status(self, user_id: int, request_admin_id: int):
        """Alterna el estado activo/inactivo de un usuario.

        Incorpora dos salvaguardas: un administrador no puede deshabilitarse a sí
        mismo, ni alterar a otro ``admin_sistema`` de nivel de privilegio igual o
        superior al suyo.

        Args:
            user_id: ID del usuario cuyo estado se desea alternar.
            request_admin_id: ID del administrador que solicita el cambio.

        Returns:
            str: Mensaje indicando el nuevo estado del usuario.

        Raises:
            HTTPException: 400 si el administrador intenta deshabilitarse a sí mismo.
            HTTPException: 404 si el usuario objetivo no existe.
            HTTPException: 403 si el objetivo es un admin_sistema de nivel igual o
                superior al solicitante.
        """
        if user_id == request_admin_id:
            raise HTTPException(status_code=400, detail="No puedes deshabilitarte a ti mismo.")
            
        user_response = supabase.table("user").select("is_active, is_deleted").eq("id", user_id).execute()
        if not user_response.data:
            raise HTTPException(status_code=404, detail="Usuario a alterar no encontrado.")
            
        target_user = user_response.data[0]
        if target_user.get("is_deleted"):
            raise HTTPException(status_code=400, detail=f"No se puede alterar el estado del usuario {user_id} porque está eliminado.")
        
        # Check if the target is another system_admin
        target_role_res = supabase.table("user_roles").select("role").eq("user_id", user_id).execute()
        target_role = target_role_res.data[0]["role"] if target_role_res.data else None
        
        if target_role == "admin_sistema":
            req_sys_res = supabase.table("system_administrator").select("level_privilege").eq("admin_id", request_admin_id).execute()
            tgt_sys_res = supabase.table("system_administrator").select("level_privilege").eq("admin_id", user_id).execute()
            
            req_lvl = req_sys_res.data[0]["level_privilege"] if req_sys_res.data else 0
            tgt_lvl = tgt_sys_res.data[0]["level_privilege"] if tgt_sys_res.data else 0
            
            if tgt_lvl >= req_lvl:
                raise HTTPException(status_code=403, detail="No tienes suficientes privilegios para alterar el estado de este administrador de sistema, ya que tiene un nivel igual o superior al tuyo.")
                
        new_status = not target_user["is_active"]
        supabase.table("user").update({"is_active": new_status}).eq("id", user_id).execute()
        
        return f"El estado del usuario {user_id} ha sido cambiado a {'Activo' if new_status else 'Inactivo'}."

    def create_classroom(self, payload: ClassroomCreate):
        """Crea un salón para una escuela y un año académico concretos.

        Valida la categoría y el nivel contra ``CLASSROOM_LEVEL_LIMITS``, comprueba
        que existan la escuela y el año académico, y rechaza duplicados exactos
        (misma escuela, año, categoría, nivel y sección).

        Args:
            payload: Datos del salón a crear.

        Returns:
            str: Mensaje de confirmación con el ID asignado al salón.

        Raises:
            HTTPException: 400 si la categoría es desconocida, el nivel queda fuera
                del rango de su categoría, o el salón ya existe.
            HTTPException: 404 si la escuela o el año académico no existen.
            HTTPException: 500 si la inserción no devuelve la fila creada.
        """
        settings = get_settings()
        level_limits = settings.CLASSROOM_LEVEL_LIMITS

        # 1. Validar que la categoría es conocida
        if payload.category not in level_limits:
            valid = ", ".join(f"{k}" for k in level_limits)
            raise HTTPException(
                status_code=400,
                detail=f"Categoría inválida. Las categorías válidas son: {valid}."
            )

        # 2. Validar que el nivel esté dentro del rango permitido para la categoría
        max_level = level_limits[payload.category]
        if payload.level < 1 or payload.level > max_level:
            raise HTTPException(
                status_code=400,
                detail=f"Nivel inválido para la categoría {payload.category}. Debe estar entre 1 y {max_level}."
            )

        # 3. Verificar que la escuela existe
        school_check = supabase.table("school").select("sch_id").eq("sch_id", payload.school_id).execute()
        if not school_check.data:
            raise HTTPException(status_code=404, detail=f"No existe ninguna escuela con ID {payload.school_id}.")

        # 4. Verificar que el año académico existe
        year_check = supabase.table("academic_year").select("id").eq("id", payload.academic_year_id).execute()
        if not year_check.data:
            raise HTTPException(status_code=404, detail=f"No existe ningún año académico con ID {payload.academic_year_id}.")

        # 5. Verificar que no exista ya ese salón exacto (misma escuela, año, categoría, nivel y sección)
        duplicate_query = (
            supabase.table("classroom")
            .select("id")
            .eq("school_id", payload.school_id)
            .eq("academic_year_id", payload.academic_year_id)
            .eq("category", payload.category)
            .eq("level", payload.level)
        )
        if payload.section:
            duplicate_query = duplicate_query.eq("section", payload.section)
        
        duplicate_check = duplicate_query.execute()
        if duplicate_check.data:
            raise HTTPException(
                status_code=400,
                detail="Ya existe un salón de clase registrado con los mismos datos (escuela, año académico, categoría, nivel y sección)."
            )

        classroom_data = {
            "school_id": payload.school_id,
            "academic_year_id": payload.academic_year_id,
            "category": payload.category,
            "level": payload.level,
            "section": payload.section,
            "is_deleted": False
        }

        result = supabase.table("classroom").insert(classroom_data).execute()
        if not result.data:
            raise HTTPException(status_code=500, detail="Error al registrar el salón de clase.")

        new_id = result.data[0]["id"]
        section_str = f" - Sección {payload.section}" if payload.section else ""
        return f"Salón de clase creado exitosamente con ID {new_id} (Nivel {payload.level}{section_str})."

    def create_student(self, payload: StudentCreate):
        """Registra un estudiante en el sistema, sin inscribirlo aún en un salón.

        Args:
            payload: Datos del estudiante a registrar. ``identity_number`` es
                opcional, pero debe ser único entre los estudiantes no eliminados.

        Returns:
            str: Mensaje de confirmación con el ID asignado al estudiante.

        Raises:
            HTTPException: 400 si la cédula ya pertenece a otro estudiante activo.
            HTTPException: 500 si la inserción no devuelve la fila creada.
        """
        # Validar CI duplicada solo si fue proporcionada
        if payload.identity_number is not None:
            identity_number = payload.identity_number.strip()
            ci_check = supabase.table("student").select("id").eq("identity_number", identity_number).eq("is_deleted", False).execute()
            if ci_check.data:
                raise HTTPException(
                    status_code=400,
                    detail=f"Ya existe un estudiante registrado con la cédula de identidad '{identity_number}'."
                )

        student_data = {
            "name": payload.name.strip(),
            "lastname": payload.lastname.strip(),
            "birthday": payload.birthday.isoformat(),
            "gender": payload.gender.value,
            "blood_type": payload.blood_type.value if payload.blood_type else None,
            "identity_number": payload.identity_number.strip() if payload.identity_number else None,
            "is_active": True,
            "is_deleted": False
        }

        result = supabase.table("student").insert(student_data).execute()
        if not result.data:
            raise HTTPException(status_code=500, detail="Error al registrar al estudiante.")

        new_id = result.data[0]["id"]
        return f"Estudiante '{payload.name} {payload.lastname}' registrado exitosamente con ID {new_id}."

    def update_student(self, student_id: int, payload: StudentUpdate):
        """Actualiza los datos de un estudiante existente.

        Solo aplica los campos efectivamente enviados. ``is_active`` e
        ``is_deleted`` no son modificables por esta vía.

        Args:
            student_id: ID del estudiante a actualizar.
            payload: Campos a modificar; los omitidos conservan su valor.

        Returns:
            str: Mensaje de confirmación de la actualización.

        Raises:
            HTTPException: 404 si el estudiante no existe.
            HTTPException: 400 si el estudiante está eliminado, si la nueva cédula
                ya pertenece a otro estudiante activo, o si no se envió ningún campo.
        """
        # 1. Verificar que el estudiante existe y no está eliminado
        student_check = supabase.table("student").select("id, identity_number, is_deleted").eq("id", student_id).execute()
        if not student_check.data:
            raise HTTPException(status_code=404, detail=f"No existe ningún estudiante con ID {student_id}.")
        
        current = student_check.data[0]
        if current["is_deleted"]:
            raise HTTPException(status_code=400, detail="No se puede modificar un estudiante que ha sido eliminado del sistema.")

        # 2. Validar CI si viene y es diferente a la actual
        if payload.identity_number is not None:
            new_ci = payload.identity_number.strip()
            current_ci = current.get("identity_number")
            if new_ci != current_ci:
                ci_check = (
                    supabase.table("student")
                    .select("id")
                    .eq("identity_number", new_ci)
                    .eq("is_deleted", False)
                    .neq("id", student_id)
                    .execute()
                )
                if ci_check.data:
                    raise HTTPException(
                        status_code=400,
                        detail=f"Ya existe otro estudiante activo registrado con la cédula '{new_ci}'."
                    )

        # 3. Construir dict de cambios excluyendo is_active, is_deleted y campos None (no enviados)
        update_data = {}
        if payload.name is not None:
            update_data["name"] = payload.name.strip()
        if payload.lastname is not None:
            update_data["lastname"] = payload.lastname.strip()
        if payload.birthday is not None:
            update_data["birthday"] = payload.birthday.isoformat()
        if payload.gender is not None:
            update_data["gender"] = payload.gender.value
        if payload.blood_type is not None:
            update_data["blood_type"] = payload.blood_type.value
        if payload.identity_number is not None:
            update_data["identity_number"] = payload.identity_number.strip()

        if not update_data:
            raise HTTPException(status_code=400, detail="No se proporcionó ningún campo para actualizar.")

        supabase.table("student").update(update_data).eq("id", student_id).execute()

        return f"Estudiante con ID {student_id} actualizado exitosamente."

    def register_student_to_classroom(self, payload: ClassroomRegistrationCreate):
        """Inscribe un estudiante en un salón.

        Comprueba que el salón esté activo, que pertenezca al año académico
        indicado y que el estudiante no esté ya inscrito en él.

        Args:
            payload: Estudiante, salón, año académico y estado de la inscripción.

        Returns:
            str: Mensaje de confirmación de la vinculación.

        Raises:
            HTTPException: 404 si el salón no existe o está eliminado.
            HTTPException: 400 si el salón no pertenece al año académico indicado,
                o si el estudiante ya está inscrito en ese salón.
            HTTPException: 500 si la inserción no devuelve la fila creada.
        """
        # 1. Validar que la clase existe y pertenece al año académico solicitado
        classroom_check = supabase.table("classroom").select("id, academic_year_id").eq("id", payload.classroom_id).eq("is_deleted", False).execute()
        if not classroom_check.data:
            raise HTTPException(status_code=404, detail=f"No existe ningún salón de clase activo con ID {payload.classroom_id}.")
        
        classroom_data = classroom_check.data[0]
        if classroom_data["academic_year_id"] != payload.academic_year_id:
             raise HTTPException(
                 status_code=400, 
                 detail=f"El salón de clase {payload.classroom_id} no pertenece al año académico {payload.academic_year_id}."
             )
        
        # 2. Validar que el estudiante no esté ya registrado en este salón y año académico (evitar duplicados si aplica)
        # Nota: Dependiendo de reglas, un estudiante puede estar en un salón por año académico en teoría.
        reg_check = (
            supabase.table("classroom_registration")
            .select("id")
            .eq("student_id", payload.student_id)
            .eq("classroom_id", payload.classroom_id)
            .execute()
        )
        if reg_check.data:
            raise HTTPException(status_code=400, detail="El estudiante ya se encuentra registrado en este salón.")

        # 3. Insertar vinculación
        registration_data = {
            "student_id": payload.student_id,
            "classroom_id": payload.classroom_id,
            "status": payload.status_id if payload.status_id is not None else 2
        }

        result = supabase.table("classroom_registration").insert(registration_data).execute()
        if not result.data:
             raise HTTPException(status_code=500, detail="Error al vincular el estudiante al salón de clase.")

        return f"Estudiante {payload.student_id} vinculado exitosamente al salón {payload.classroom_id}."

    def bulk_register_students_to_classroom(self, payload: BulkClassroomRegistrationCreate):
        """Inscribe varios estudiantes en un mismo salón, omitiendo los ya inscritos.

        Args:
            payload: Lista de estudiantes, salón, año académico y estado.

        Returns:
            str: Mensaje indicando cuántos estudiantes se vincularon, o que no había
            vinculaciones nuevas por realizar.

        Raises:
            HTTPException: 404 si el salón no existe o está eliminado.
            HTTPException: 400 si el salón no pertenece al año académico indicado.
            HTTPException: 500 si la inserción masiva no devuelve filas.
        """
        # 1. Validar salón y año académico
        classroom_check = supabase.table("classroom").select("id, academic_year_id").eq("id", payload.classroom_id).eq("is_deleted", False).execute()
        if not classroom_check.data:
            raise HTTPException(status_code=404, detail=f"No existe ningún salón de clase activo con ID {payload.classroom_id}.")
        
        if classroom_check.data[0]["academic_year_id"] != payload.academic_year_id:
            raise HTTPException(
                 status_code=400, 
                 detail=f"El salón de clase {payload.classroom_id} no pertenece al año académico {payload.academic_year_id}."
             )

        # 2. Obtener registros actuales para este salón para evitar duplicados en el bulk
        existing_regs = (
            supabase.table("classroom_registration")
            .select("student_id")
            .eq("classroom_id", payload.classroom_id)
            .in_("student_id", payload.student_ids)
            .execute()
        )
        existing_student_ids = {r["student_id"] for r in existing_regs.data} if existing_regs.data else set()

        # 3. Preparar datos para inserción (solo los que no existen ya)
        registrations_to_insert = []
        for student_id in payload.student_ids:
            if student_id not in existing_student_ids:
                registrations_to_insert.append({
                    "student_id": student_id,
                    "classroom_id": payload.classroom_id,
                    "status": payload.status_id if payload.status_id is not None else 2
                })

        if not registrations_to_insert:
            return "No hay nuevas vinculaciones por realizar (los estudiantes ya estaban en este salón)."

        result = supabase.table("classroom_registration").insert(registrations_to_insert).execute()
        if not result.data:
            raise HTTPException(status_code=500, detail="Error al vincular masivamente a los estudiantes.")

        return f"Se han vinculado exitosamente {len(result.data)} estudiantes al salón {payload.classroom_id}."

    # ──────────────────────────────────────────────────────────
    # GET endpoints: Schools
    # ──────────────────────────────────────────────────────────

    def get_schools(self, page: int, size: int):
        """Lista las escuelas no eliminadas del sistema de forma paginada.

        Args:
            page: Número de página, comenzando en 1.
            size: Cantidad de escuelas por página.

        Returns:
            dict: ``{"data": [...], "total": int, "page": int, "size": int}``.

        Raises:
            HTTPException: 404 si no hay escuelas registrados. Comportamiento legado
                contrario al estándar REST del proyecto; su corrección se sigue en
                el issue #15.
        """
        offset = (page - 1) * size
        count_res = supabase.table("school").select("sch_id", count="exact").eq("is_deleted", False).execute()
        total = count_res.count or 0
        if total == 0:
            raise HTTPException(status_code=404, detail="No hay escuelas registradas en el sistema.")
        rows = (
            supabase.table("school")
            .select("sch_id, name, year_foundation, rif, address, state, school_type, is_active, is_deleted")
            .eq("is_deleted", False)
            .range(offset, offset + size - 1)
            .execute()
        )
        return {"data": rows.data, "total": total, "page": page, "size": size}

    def get_school_detail(self, school_id: int):
        """Devuelve los datos completos de una escuela.

        Args:
            school_id: ID de la escuela a consultar.

        Returns:
            dict: Fila de la escuela, incluidos ``is_active`` e ``is_deleted``.

        Raises:
            HTTPException: 404 si no existe ninguna escuela con ese ID.
        """
        rows = (
            supabase.table("school")
            .select("sch_id, name, year_foundation, rif, address, state, school_type, is_active, is_deleted")
            .eq("sch_id", school_id)
            .execute()
        )
        if not rows.data:
            raise HTTPException(status_code=404, detail=f"No existe ninguna escuela con ID {school_id}.")
        return rows.data[0]

    # ──────────────────────────────────────────────────────────
    # GET endpoints: Classrooms
    # ──────────────────────────────────────────────────────────

    def _enrich_classroom(self, classroom: dict) -> dict:
        """Añade el año académico y la escuela al dict de un salón.

        Args:
            classroom: Fila del salón. Debe traer ``academic_year_id`` y ``school_id``.

        Returns:
            dict: El mismo dict con las claves ``academic_year`` y ``school``
            añadidas, en ``None`` si la relación no se pudo resolver.
        """
        # Academic year
        ay_res = (
            supabase.table("academic_year")
            .select("id, name, init_date, end_date, is_current")
            .eq("id", classroom["academic_year_id"])
            .execute()
        )
        classroom["academic_year"] = ay_res.data[0] if ay_res.data else None

        # School
        sch_res = (
            supabase.table("school")
            .select("sch_id, name, year_foundation, state, rif, address, school_type, is_active")
            .eq("sch_id", classroom["school_id"])
            .execute()
        )
        classroom["school"] = sch_res.data[0] if sch_res.data else None
        return classroom

    def get_classrooms(self, page: int, size: int):
        """Lista los salones no eliminados del sistema de forma paginada.

        Args:
            page: Número de página, comenzando en 1.
            size: Cantidad de salones por página.

        Returns:
            dict: ``{"data": [...], "total": int, "page": int, "size": int}``. Cada salón viene
            enriquecido con su año académico y su escuela.

        Raises:
            HTTPException: 404 si no hay salones registrados. Comportamiento legado
                contrario al estándar REST del proyecto; su corrección se sigue en
                el issue #15.
        """
        offset = (page - 1) * size
        count_res = supabase.table("classroom").select("id", count="exact").eq("is_deleted", False).execute()
        total = count_res.count or 0
        if total == 0:
            raise HTTPException(status_code=404, detail="No hay salones de clase registrados en el sistema.")
        rows = (
            supabase.table("classroom")
            .select("id, school_id, academic_year_id, category, level, section, is_deleted")
            .eq("is_deleted", False)
            .range(offset, offset + size - 1)
            .execute()
        )
        enriched = [self._enrich_classroom(c) for c in rows.data]
        return {"data": enriched, "total": total, "page": page, "size": size}

    def get_classroom_detail(self, classroom_id: int):
        """Devuelve un salón con su año académico y su escuela.

        Args:
            classroom_id: ID del salón a consultar.

        Returns:
            dict: Fila del salón enriquecida con ``academic_year`` y ``school``.

        Raises:
            HTTPException: 404 si no existe ningún salón con ese ID.
        """
        rows = (
            supabase.table("classroom")
            .select("id, school_id, academic_year_id, category, level, section, is_deleted")
            .eq("id", classroom_id)
            .execute()
        )
        if not rows.data:
            raise HTTPException(status_code=404, detail=f"No existe ningún salón de clase con ID {classroom_id}.")
        return self._enrich_classroom(rows.data[0])

    # ──────────────────────────────────────────────────────────
    # GET endpoints: Students
    # ──────────────────────────────────────────────────────────

    def _enrich_student(self, student: dict) -> dict:
        """Añade la escuela y el salón al dict de un estudiante.

        La relación es indirecta: toma la inscripción más reciente del estudiante en
        ``classroom_registration`` y desde ella resuelve el salón y su escuela.

        Args:
            student: Fila del estudiante. Debe traer la clave ``id``.

        Returns:
            dict: El mismo dict con las claves ``school`` y ``classroom`` añadidas,
            en ``None`` si el estudiante no tiene inscripciones.
        """
        registration = (
            supabase.table("classroom_registration")
            .select("classroom_id")
            .eq("student_id", student["id"])
            .order("created_at", desc=True)
            .limit(1)
            .execute()
        )
        student["school"] = None
        student["classroom"] = None

        if registration.data:
            classroom_id = registration.data[0]["classroom_id"]
            cr = (
                supabase.table("classroom")
                .select("id, school_id, category, level, section")
                .eq("id", classroom_id)
                .execute()
            )
            if cr.data:
                classroom_row = cr.data[0]
                student["classroom"] = {
                    "id": classroom_row["id"],
                    "level": classroom_row["level"],
                    "category": classroom_row["category"],
                    "section": classroom_row.get("section"),
                }
                sch = (
                    supabase.table("school")
                    .select("sch_id, name")
                    .eq("sch_id", classroom_row["school_id"])
                    .execute()
                )
                if sch.data:
                    student["school"] = {"sch_id": sch.data[0]["sch_id"], "name": sch.data[0]["name"]}
        return student

    def get_students(self, page: int, size: int):
        """Lista los estudiantes no eliminados del sistema de forma paginada.

        Args:
            page: Número de página, comenzando en 1.
            size: Cantidad de estudiantes por página.

        Returns:
            dict: ``{"data": [...], "total": int, "page": int, "size": int}``. Cada estudiante viene
            enriquecido con su escuela y su salón actual.

        Raises:
            HTTPException: 404 si no hay estudiantes registrados. Comportamiento legado
                contrario al estándar REST del proyecto; su corrección se sigue en
                el issue #15.
        """
        offset = (page - 1) * size
        count_res = supabase.table("student").select("id", count="exact").eq("is_deleted", False).execute()
        total = count_res.count or 0
        if total == 0:
            raise HTTPException(status_code=404, detail="No hay estudiantes registrados en el sistema.")
        rows = (
            supabase.table("student")
            .select("id, name, lastname, birthday, gender, blood_type, identity_number, is_active, is_deleted")
            .eq("is_deleted", False)
            .range(offset, offset + size - 1)
            .execute()
        )
        enriched = [self._enrich_student(s) for s in rows.data]
        return {"data": enriched, "total": total, "page": page, "size": size}

    def get_student_detail(self, student_id: int):
        """Devuelve un estudiante con su escuela y su salón actual.

        Args:
            student_id: ID del estudiante a consultar.

        Returns:
            dict: Fila del estudiante enriquecida con ``school`` y ``classroom``.

        Raises:
            HTTPException: 404 si no existe ningún estudiante con ese ID.
        """
        rows = (
            supabase.table("student")
            .select("id, name, lastname, birthday, gender, blood_type, identity_number, is_active, is_deleted")
            .eq("id", student_id)
            .execute()
        )
        if not rows.data:
            raise HTTPException(status_code=404, detail=f"No existe ningún estudiante con ID {student_id}.")
        return self._enrich_student(rows.data[0])

#: Instancia compartida del servicio, importada por los endpoints de admin.
admin_service = AdminService()
