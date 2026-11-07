"""Gestión de eventos escolares para el rol administrador de escuela.

El alcance se resuelve siempre en el servidor: el ``sch_admin_id`` y el
``school_id`` salen del ``user_id`` autenticado, y un evento solo es accesible si
tiene una participación registrada a nombre de ese administrador.
"""

import logging
import re

from fastapi import HTTPException
from app.core.database import supabase
from app.services.event_schedule_policy import validate_lead_time
from app.services.event_queries import apply_active_event_filter, month_window
from datetime import datetime, date, timezone

# Sufijo de zona horaria al final de un timestamp ISO 8601: ``Z``, ``+00:00``, ``-0400`` o ``+05``.
_TZ_SUFFIX = re.compile(r"(Z|[+-]\d{2}(:?\d{2})?)$")
logger = logging.getLogger(__name__)


class SchoolAdminEventService:
    """Operaciones de eventos escolares con aislamiento por escuela."""

    # ──────────────────────────────────────────────────────────
    # Helpers internos
    # ──────────────────────────────────────────────────────────

    def _get_admin_record(self, user_id: int) -> dict:
        """Resuelve el registro de ``school_administrator`` del usuario autenticado.

        Es el punto donde se deriva el tenant: ningún método acepta el
        ``school_id`` desde el cliente.

        Args:
            user_id: ID del usuario autenticado (claim ``sub`` del JWT).

        Returns:
            dict: ``{"sch_admin_id": int, "school_id": int}``.

        Raises:
            HTTPException: 403 si el usuario no está vinculado a ninguna escuela.
        """
        sa_res = (
            supabase.table("school_administrator")
            .select("sch_admin_id, school_id")
            .eq("user_id", user_id)
            .execute()
        )
        if not sa_res.data:
            raise HTTPException(
                status_code=403,
                detail="Tu usuario no está vinculado a ninguna escuela."
            )
        return sa_res.data[0]

    def _get_school_classroom_ids(self, school_id: int) -> list[int]:
        """Retorna los IDs de todos los salones activos de una escuela.

        Args:
            school_id: ID de la escuela, ya resuelto en el servidor.

        Returns:
            list[int]: IDs de los salones no eliminados. Lista vacía si no hay.
        """
        res = (
            supabase.table("classroom")
            .select("id")
            .eq("school_id", school_id)
            .eq("is_deleted", False)
            .execute()
        )
        return [c["id"] for c in res.data] if res.data else []

    def _get_school_doctor_ids(self, school_id: int) -> list[int]:
        """Retorna los IDs de médicos disponibles para nuevas convocatorias.

        Args:
            school_id: ID de la escuela, ya resuelto en el servidor.

        Returns:
            list[int]: IDs de médicos de la escuela con cuenta activa y no
            eliminada. Lista vacía si no hay ninguno disponible.
        """
        res = (
            supabase.table("doctor_to_school")
            .select("doctor_id, doctor!inner(user!inner(id))")
            .eq("school_id", school_id)
            .eq("doctor.user.is_deleted", False)
            .eq("doctor.user.is_active", True)
            .execute()
        )
        return [d["doctor_id"] for d in res.data] if res.data else []

    @staticmethod
    def _with_utc_offset(value: str | None) -> str | None:
        """Garantiza que un timestamp ISO 8601 lleve zona horaria.

        Las ediciones nuevas guardan ``updated_at`` con offset, pero las filas
        escritas antes de ese cambio quedaron sin él; si la columna devuelve uno
        de esos valores, el cliente lo leería como hora local.

        Args:
            value: Timestamp tal como lo devuelve la base de datos, o ``None``.

        Returns:
            str | None: El mismo valor si ya trae offset, con ``+00:00`` añadido si no,
            o ``None`` si no hay valor.
        """
        if not value:
            return None
        # Se mira solo la parte de la hora para no confundir el día (``-05``) con un offset
        return value if _TZ_SUFFIX.search(value[10:]) else f"{value}+00:00"

    def _validate_event_ownership(self, event_id: int, sch_admin_id: int) -> dict:
        """Comprueba que un evento existe y pertenece al administrador indicado.

        La pertenencia es indirecta: se exige que el evento tenga al menos una
        participación registrada a nombre de ``sch_admin_id``.

        Args:
            event_id: ID del evento a validar.
            sch_admin_id: ID del administrador escolar resuelto en el servidor.

        Returns:
            dict: Fila completa del evento.

        Raises:
            HTTPException: 404 si no existe un evento activo con ese ID.
            HTTPException: 403 si el evento pertenece a otra escuela.
        """
        event_res = (
            apply_active_event_filter(
                supabase.table("event").select("*").eq("id", event_id)
            )
            .execute()
        )
        if not event_res.data:
            raise HTTPException(
                status_code=404,
                detail=f"No existe un evento activo con ID {event_id}."
            )
        event = event_res.data[0]

        # Verificar que al menos una participación del evento pertenece al admin
        participation_res = (
            supabase.table("event_participation")
            .select("id")
            .eq("id_event", event_id)
            .eq("id_school_admin", sch_admin_id)
            .eq("is_deleted", False)
            .limit(1)
            .execute()
        )
        if not participation_res.data:
            raise HTTPException(
                status_code=403,
                detail="No tienes permiso para acceder a este evento."
            )
        return event

    # ──────────────────────────────────────────────────────────
    # POST - Crear Evento
    # ──────────────────────────────────────────────────────────

    def create_event(self, user_id: int, payload) -> dict:
        """Crea un evento y sus participaciones de salones y médicos.

        Valida que el inicio respete la anticipación mínima configurada y que
        todos los salones y médicos indicados pertenezcan a la escuela del
        administrador antes de insertar nada.

        Args:
            user_id: ID del administrador autenticado (claim ``sub`` del JWT).
            payload: :class:`~app.schemas.event.EventCreate` con los datos del
                evento y los IDs de salones y médicos convocados.

        Returns:
            dict: ``event_id``, ``title``, ``classrooms_linked`` y ``doctors_linked``.

        Raises:
            HTTPException: 403 si el usuario no está vinculado a ninguna escuela.
            FieldValidationError: 422 si el inicio no respeta la anticipación
                mínima vigente (ver :mod:`app.services.event_schedule_policy`).
            HTTPException: 400 si algún salón o médico no pertenece a la escuela.
            HTTPException: 500 si la inserción del evento no devuelve la fila.
        """
        admin = self._get_admin_record(user_id)
        sch_admin_id = admin["sch_admin_id"]
        school_id = admin["school_id"]

        # Anticipación mínima: se valida antes de cualquier escritura para que un
        # horario rechazado no deje evento ni participaciones creados.
        validate_lead_time(payload.init_time)

        # Validar que los classrooms pertenecen a la escuela
        valid_classroom_ids = self._get_school_classroom_ids(school_id)
        for cid in payload.classroom_ids:
            if cid not in valid_classroom_ids:
                raise HTTPException(
                    status_code=400,
                    detail=f"El salón con ID {cid} no pertenece a tu escuela."
                )

        # Validar que los doctores están asignados a la escuela
        valid_doctor_ids = self._get_school_doctor_ids(school_id)
        for did in payload.doctor_ids:
            if did not in valid_doctor_ids:
                raise HTTPException(
                    status_code=400,
                    detail=f"El doctor con ID {did} no está asignado a tu escuela."
                )

        # 1. Insertar el evento
        event_data = {
            "title": payload.title,
            "description": payload.description,
            "init_time": payload.init_time.isoformat(),
            "ent_time": payload.ent_time.isoformat(),
        }
        event_res = supabase.table("event").insert(event_data).execute()
        if not event_res.data:
            raise HTTPException(status_code=500, detail="Error al crear el evento.")
        event_id = event_res.data[0]["id"]

        # 2. Crear participaciones para cada classroom
        participation_rows = []
        for cid in payload.classroom_ids:
            participation_rows.append({
                "id_event": event_id,
                "id_classroom": cid,
                "id_school_admin": sch_admin_id,
            })

        # 3. Crear participaciones para cada doctor
        for did in payload.doctor_ids:
            participation_rows.append({
                "id_event": event_id,
                "id_doctor": did,
                "id_school_admin": sch_admin_id,
            })

        supabase.table("event_participation").insert(participation_rows).execute()

        return {
            "event_id": event_id,
            "title": payload.title,
            "classrooms_linked": len(payload.classroom_ids),
            "doctors_linked": len(payload.doctor_ids),
        }

    # ──────────────────────────────────────────────────────────
    # GET - Consultar Eventos (Paginación normal)
    # ──────────────────────────────────────────────────────────

    def get_events(self, user_id: int, page: int, size: int) -> dict:
        """Lista de forma paginada los eventos de la escuela del administrador.

        Solo incluye eventos vigentes (ni cancelados ni eliminados), ordenados del
        más reciente al más antiguo.

        Args:
            user_id: ID del administrador autenticado (claim ``sub`` del JWT).
            page: Número de página, comenzando en 1.
            size: Cantidad de eventos por página.

        Returns:
            dict: ``{"data": [...], "total": int, "page": int, "size": int}``. Si el
            administrador no tiene eventos, ``data`` viene vacío y ``total`` en 0.

        Raises:
            HTTPException: 403 si el usuario no está vinculado a ninguna escuela.
        """
        admin = self._get_admin_record(user_id)
        sch_admin_id = admin["sch_admin_id"]

        # Obtener los IDs de eventos donde participa este admin
        part_res = (
            supabase.table("event_participation")
            .select("id_event")
            .eq("id_school_admin", sch_admin_id)
            .eq("is_deleted", False)
            .execute()
        )
        if not part_res.data:
            return {"data": [], "total": 0, "page": page, "size": size}

        event_ids = list({p["id_event"] for p in part_res.data})

        # Contar total de eventos activos
        count_res = (
            apply_active_event_filter(
                supabase.table("event").select("id", count="exact").in_("id", event_ids)
            )
            .execute()
        )
        total = count_res.count or 0
        if total == 0:
            return {"data": [], "total": 0, "page": page, "size": size}

        # Paginación
        offset = (page - 1) * size
        events_res = (
            apply_active_event_filter(
                supabase.table("event")
                .select("id, title, description, init_time, ent_time, created_at")
                .in_("id", event_ids)
            )
            .order("init_time", desc=True)
            .range(offset, offset + size - 1)
            .execute()
        )

        return {
            "data": events_res.data,
            "total": total,
            "page": page,
            "size": size,
        }

    # ──────────────────────────────────────────────────────────
    # GET - Consultar Eventos por Fecha (Paginación por mes)
    # ──────────────────────────────────────────────────────────

    def get_events_by_month(self, user_id: int, year: int, month: int) -> dict:
        """Lista los eventos del administrador que caen dentro de un mes concreto.

        Pensado para la vista de calendario: devuelve el mes completo sin paginar,
        ordenado cronológicamente.

        Args:
            user_id: ID del administrador autenticado (claim ``sub`` del JWT).
            year: Año del período a consultar, ej. 2026.
            month: Mes del período a consultar, de 1 a 12.

        Returns:
            dict: ``{"data": [...], "year": int, "month": int, "total": int}``.

        Raises:
            HTTPException: 403 si el usuario no está vinculado a ninguna escuela.
            ValueError: Si ``month`` queda fuera del rango 1-12.
        """
        admin = self._get_admin_record(user_id)
        sch_admin_id = admin["sch_admin_id"]

        # Calcular rango del mes (ventana semiabierta [inicio, siguiente))
        start_date, next_month_start = month_window(year, month)

        # Obtener IDs de eventos del admin
        part_res = (
            supabase.table("event_participation")
            .select("id_event")
            .eq("id_school_admin", sch_admin_id)
            .eq("is_deleted", False)
            .execute()
        )
        if not part_res.data:
            return {"data": [], "year": year, "month": month, "total": 0}

        event_ids = list({p["id_event"] for p in part_res.data})

        # Filtrar eventos por rango de fechas
        events_res = (
            apply_active_event_filter(
                supabase.table("event")
                .select("id, title, description, init_time, ent_time, created_at")
                .in_("id", event_ids)
            )
            .gte("init_time", start_date)
            .lt("init_time", next_month_start)
            .order("init_time", desc=False)
            .execute()
        )

        return {
            "data": events_res.data if events_res.data else [],
            "year": year,
            "month": month,
            "total": len(events_res.data) if events_res.data else 0,
        }

    # ──────────────────────────────────────────────────────────
    # GET - Detalle de un Evento
    # ──────────────────────────────────────────────────────────

    @staticmethod
    def _classroom_label(classroom: dict, category_name: str | None) -> str:
        """Construye una etiqueta sin exponer identificadores de categoría.

        Args:
            classroom: Fila con nivel y sección del salón.
            category_name: Nombre resuelto del catálogo, o None si falta.

        Returns:
            str: Grado, nivel o año, categoría y sección cuando esté presente.
        """
        category = (category_name or "").strip().lower()
        unit = {"primaria": "grado", "preescolar": "nivel", "bachillerato": "año"}.get(category, "nivel")
        ordinals = {1: "Primer", 2: "Segundo", 3: "Tercer", 4: "Cuarto", 5: "Quinto", 6: "Sexto"}
        level = classroom.get("level")
        if level in ordinals:
            label = f"{ordinals[level]} {unit} {category}".strip()
        elif level is not None:
            label = f"{unit.capitalize()} {level} {category}".strip()
        else:
            label = category.capitalize()
        if not category:
            label = " · ".join(filter(None, [label, "Categoría no disponible"]))
        section = str(classroom.get("section") or "").strip()
        return f"{label} · Sección {section}" if section else label

    def get_event_detail(self, user_id: int, event_id: int) -> dict:
        """Devuelve un evento con el detalle de sus salones y médicos convocados.

        Args:
            user_id: ID del administrador autenticado (claim ``sub`` del JWT).
            event_id: ID del evento a consultar.

        Returns:
            dict: Datos del evento junto con sus participaciones resueltas.

        Raises:
            HTTPException: 403 si el usuario no está vinculado a ninguna escuela, o
                si el evento pertenece a otra escuela.
            HTTPException: 404 si no existe un evento activo con ese ID.
            HTTPException: 500 si falla la consulta de salones o categorías.
        """
        admin = self._get_admin_record(user_id)
        sch_admin_id = admin["sch_admin_id"]

        event = self._validate_event_ownership(event_id, sch_admin_id)

        # Obtener participaciones del evento
        parts_res = (
            supabase.table("event_participation")
            .select("id_classroom, id_doctor, id_school_admin")
            .eq("id_event", event_id)
            .eq("is_deleted", False)
            .execute()
        )

        classroom_ids = list({p["id_classroom"] for p in parts_res.data if p.get("id_classroom")})
        doctor_ids = list({p["id_doctor"] for p in parts_res.data if p.get("id_doctor")})
        admin_ids = list({p["id_school_admin"] for p in parts_res.data if p.get("id_school_admin")})

        # Obtener info de salones
        classrooms_info = []
        if classroom_ids:
            try:
                cr_res = (
                    supabase.table("classroom")
                    .select("id, category, level, section")
                    .eq("school_id", admin["school_id"])
                    .in_("id", classroom_ids)
                    .execute()
                )
                category_ids = sorted({c["category"] for c in cr_res.data if c.get("category") is not None})
                categories = {}
                if category_ids:
                    # Catálogo global: una consulta por lote, sin filtro de escuela.
                    category_res = (
                        supabase.table("classroom_category")
                        .select("id, classroom_type_name")
                        .in_("id", category_ids)
                        .execute()
                    )
                    categories = {c["id"]: c.get("classroom_type_name") for c in category_res.data}
            except Exception as exc:
                logger.exception("Error al consultar los salones o categorías del evento")
                raise HTTPException(status_code=500, detail="No se pudieron obtener los salones del evento.") from exc
            classrooms_info = [
                {
                    "id": c["id"],
                    "label": self._classroom_label(c, categories.get(c.get("category"))),
                }
                for c in cr_res.data
            ] if cr_res.data else []

        # Obtener info de doctores
        doctors_info = []
        if doctor_ids:
            docs_res = (
                supabase.table("doctor")
                .select("doc_id, user_id, especially")
                .in_("doc_id", doctor_ids)
                .execute()
            )
            if docs_res.data:
                doc_user_ids = [d["user_id"] for d in docs_res.data if d.get("user_id")]
                users_res = (
                    supabase.table("user")
                    .select("id, name, lastname, gender")
                    .in_("id", doc_user_ids)
                    .execute()
                )
                users_map = {u["id"]: u for u in users_res.data} if users_res.data else {}

                for doc in docs_res.data:
                    user = users_map.get(doc.get("user_id"), {})
                    gender = user.get("gender", "")
                    prefix = "Dra." if str(gender).strip().upper() in ["F", "FEMALE", "FEMENINO", "MUJER"] else "Dr."
                    full_name = f"{prefix} {user.get('name', '')} {user.get('lastname', '')}".strip()
                    doctors_info.append({
                        "doc_id": doc["doc_id"],
                        "name": full_name,
                        "specialty": doc.get("especially"),
                    })

        # Obtener info del admin creador
        admin_info = None
        if admin_ids:
            # Usamos el primer admin registrado como creador
            creator_id = admin_ids[0]
            admin_res = (
                supabase.table("school_administrator")
                .select("sch_admin_id, user_id, administrative_position")
                .eq("sch_admin_id", creator_id)
                .execute()
            )
            if admin_res.data:
                admin_user_id = admin_res.data[0].get("user_id")
                user_res = (
                    supabase.table("user")
                    .select("id, name, lastname, email")
                    .eq("id", admin_user_id)
                    .execute()
                )
                if user_res.data:
                    u = user_res.data[0]
                    admin_info = {
                        "sch_admin_id": creator_id,
                        "name": f"{u.get('name', '')} {u.get('lastname', '')}".strip(),
                        "email": u.get("email"),
                        "position": admin_res.data[0].get("administrative_position"),
                    }

        return {
            "event": {
                "id": event["id"],
                "title": event["title"],
                "description": event.get("description"),
                "init_time": event["init_time"],
                "ent_time": event["ent_time"],
                "created_at": event.get("created_at"),
                "updated_at": self._with_utc_offset(event.get("updated_at")),
            },
            "classrooms": classrooms_info,
            "doctors": doctors_info,
            "created_by": admin_info,
        }

    # ──────────────────────────────────────────────────────────
    # PATCH - Cancelar Evento
    # ──────────────────────────────────────────────────────────

    def cancel_event(self, user_id: int, event_id: int) -> dict:
        """Cancela un evento y, en cascada, todas sus participaciones.

        Es una cancelación lógica (``is_canceled=True``): el evento deja de
        aparecer en los listados pero su registro se conserva.

        Args:
            user_id: ID del administrador autenticado (claim ``sub`` del JWT).
            event_id: ID del evento a cancelar.

        Returns:
            dict: ``{"event_id": int, "status": "canceled"}``.

        Raises:
            HTTPException: 403 si el usuario no está vinculado a ninguna escuela, o
                si el evento pertenece a otra escuela.
            HTTPException: 404 si no existe un evento activo con ese ID.
        """
        admin = self._get_admin_record(user_id)
        sch_admin_id = admin["sch_admin_id"]

        self._validate_event_ownership(event_id, sch_admin_id)

        # Cancelar el evento
        supabase.table("event").update({
            "is_canceled": True,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }).eq("id", event_id).execute()

        # Cancelar las participaciones asociadas
        supabase.table("event_participation").update({
            "is_canceled": True,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }).eq("id_event", event_id).execute()

        return {"event_id": event_id, "status": "canceled"}

    # ──────────────────────────────────────────────────────────
    # PUT - Modificar datos base del Evento
    # ──────────────────────────────────────────────────────────

    def update_event(self, user_id: int, event_id: int, payload: dict) -> dict:
        """Actualiza los datos base de un evento, sin tocar sus participaciones.

        Solo aplica los campos enviados de entre ``title``, ``description``,
        ``init_time`` y ``ent_time``, y revalida que la fecha de fin siga siendo
        posterior a la de inicio con los valores resultantes.

        Reprogramar cuenta como programar: si el cuerpo trae ``init_time``, el
        nuevo inicio debe respetar la anticipación mínima vigente. Las ediciones
        que no tocan el inicio (ej. corregir el título de un evento ya ocurrido)
        no pasan por esa validación.

        Args:
            user_id: ID del administrador autenticado (claim ``sub`` del JWT).
            event_id: ID del evento a actualizar.
            payload: Campos a modificar. Los omitidos conservan su valor actual.

        Returns:
            dict: Fila del evento ya actualizada.

        Raises:
            HTTPException: 403 si el usuario no está vinculado a ninguna escuela, o
                si el evento pertenece a otra escuela.
            HTTPException: 404 si no existe un evento activo con ese ID.
            FieldValidationError: 422 si se envía un ``init_time`` que no respeta
                la anticipación mínima vigente.
            HTTPException: 400 si no se envió ningún campo válido, o si la fecha de
                finalización no es posterior a la de inicio.
        """
        admin = self._get_admin_record(user_id)
        sch_admin_id = admin["sch_admin_id"]

        event = self._validate_event_ownership(event_id, sch_admin_id)

        # Solo se revalida la anticipación cuando se reprograma el inicio.
        if payload.get("init_time") is not None:
            validate_lead_time(payload["init_time"])

        # Construir campos a actualizar (solo los que se enviaron)
        update_data = {}
        allowed_fields = ["title", "description", "init_time", "ent_time"]
        for field in allowed_fields:
            if field in payload and payload[field] is not None:
                value = payload[field]
                # Convertir datetimes a ISO string
                if isinstance(value, datetime):
                    value = value.isoformat()
                update_data[field] = value

        if not update_data:
            raise HTTPException(
                status_code=400,
                detail="No se proporcionaron campos válidos para actualizar."
            )

        # Validar que ent_time > init_time si se modifican las fechas
        new_init = update_data.get("init_time", event["init_time"])
        new_ent = update_data.get("ent_time", event["ent_time"])
        if new_ent <= new_init:
            raise HTTPException(
                status_code=400,
                detail="La fecha de finalización debe ser posterior a la de inicio."
            )

        update_data["updated_at"] = datetime.now(timezone.utc).isoformat()

        result = (
            supabase.table("event")
            .update(update_data)
            .eq("id", event_id)
            .execute()
        )

        return result.data[0] if result.data else {"event_id": event_id, "status": "updated"}


#: Instancia compartida del servicio, importada por los endpoints de eventos.
school_admin_event_service = SchoolAdminEventService()
