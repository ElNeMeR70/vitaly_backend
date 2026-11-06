"""Agenda de eventos escolares del rol médico.

El alcance se resuelve siempre en el servidor y exige dos condiciones a la vez:
el médico debe tener una participación activa en el evento (``doc_id`` derivado
del ``user_id`` autenticado) y, además, un vínculo **vigente** con la escuela que
lo convocó (``doctor_to_school.status``). La participación es un hecho histórico
que nada revoca; el vínculo con la escuela es la autorización viva, y suspender a
un médico es la única palanca de revocación del producto. El cliente nunca envía
un identificador de médico ni de escuela.
"""

import logging

from fastapi import HTTPException

from app.core.database import supabase
from app.services.event_queries import apply_active_event_filter, month_window

logger = logging.getLogger(__name__)

#: ``system_status`` que la escuela asigna a un médico vigente. Lo escribe
#: ``SchoolAdminService.reactivate_doctor``; ``suspend_doctor`` escribe 1.
#: Se usa como lista blanca: cualquier otro estado (suspendido, o uno futuro)
#: deja de ver los eventos de esa escuela.
ACTIVE_DOCTOR_SCHOOL_STATUS = 2


class DoctorEventService:
    """Consultas de eventos escolares con alcance limitado al médico autenticado."""

    # ──────────────────────────────────────────────────────────
    # Helpers internos
    # ──────────────────────────────────────────────────────────

    def _run(self, query, description: str):
        """Ejecuta una consulta supabase-py traduciendo los fallos al envelope.

        ``app/main.py`` solo registra handlers para ``HTTPException`` y los
        errores de validación: cualquier otra excepción (ej. ``APIError`` de
        supabase-py) saldría como un 500 en texto plano, sin el envelope que el
        frontend espera. Aquí se registra el fallo y se relanza como
        ``HTTPException(500)``.

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
            logger.exception("Fallo al consultar %s para la agenda del médico.", description)
            raise HTTPException(
                status_code=500,
                detail="Ocurrió un error al consultar la agenda de eventos."
            )

    def _get_doctor_id(self, user_id: int) -> int:
        """Resuelve el ``doc_id`` del usuario autenticado.

        Es el punto donde se deriva el alcance: ningún método acepta el
        identificador del médico desde el cliente.

        Args:
            user_id: ID del usuario autenticado (claim ``sub`` del JWT).

        Returns:
            int: ``doctor.doc_id`` del usuario.

        Raises:
            HTTPException: 403 si el usuario no tiene un perfil de médico asociado.
        """
        doc_res = self._run(
            supabase.table("doctor").select("doc_id").eq("user_id", user_id),
            "el perfil del médico",
        )
        if not doc_res.data:
            raise HTTPException(
                status_code=403,
                detail="Tu usuario no tiene un perfil de médico asociado."
            )
        return doc_res.data[0]["doc_id"]

    def _get_active_school_ids(self, doc_id: int) -> set[int]:
        """Resuelve las escuelas con las que el médico mantiene un vínculo vigente.

        Es la autorización viva del médico: ``suspend_doctor`` solo cambia este
        estado y no toca las participaciones, así que sin esta comprobación un
        médico suspendido seguiría leyendo la agenda de esa escuela.

        Args:
            doc_id: ID del médico ya resuelto en el servidor.

        Returns:
            set[int]: IDs de escuela donde el estado del vínculo es
            ``ACTIVE_DOCTOR_SCHOOL_STATUS``. Conjunto vacío si no hay ninguna.
        """
        res = self._run(
            supabase.table("doctor_to_school")
            .select("school_id")
            .eq("doctor_id", doc_id)
            .eq("status", ACTIVE_DOCTOR_SCHOOL_STATUS),
            "las escuelas vinculadas al médico",
        )
        return {row["school_id"] for row in (res.data or []) if row.get("school_id") is not None}

    def _resolve_schools_by_admin(self, sch_admin_ids: list[int]) -> dict[int, dict]:
        """Resuelve la escuela de cada administrador que convocó un evento.

        La escuela llega al evento de forma indirecta
        (``event_participation.id_school_admin`` → ``school_administrator`` →
        ``school``), porque la tabla ``event`` no almacena ``school_id``.

        Args:
            sch_admin_ids: IDs de administradores escolares a resolver.

        Returns:
            dict[int, dict]: Mapa ``sch_admin_id`` →
            ``{"school_id": int, "school_name": str | None}``. Los administradores
            sin fila asociada quedan fuera del mapa.
        """
        if not sch_admin_ids:
            return {}

        admins_res = self._run(
            supabase.table("school_administrator")
            .select("sch_admin_id, school_id")
            .in_("sch_admin_id", sch_admin_ids),
            "los administradores convocantes",
        )
        admins = admins_res.data or []
        if not admins:
            return {}

        school_ids = list({a["school_id"] for a in admins if a.get("school_id")})
        names: dict[int, str] = {}
        if school_ids:
            schools_res = self._run(
                supabase.table("school").select("sch_id, name").in_("sch_id", school_ids),
                "las escuelas de los eventos",
            )
            names = {s["sch_id"]: s.get("name") for s in (schools_res.data or [])}

        return {
            a["sch_admin_id"]: {
                "school_id": a.get("school_id"),
                "school_name": names.get(a.get("school_id")),
            }
            for a in admins
        }

    # ──────────────────────────────────────────────────────────
    # GET - Consultar Eventos por Mes (Paginación por fecha)
    # ──────────────────────────────────────────────────────────

    def get_events_by_month(self, user_id: int, year: int, month: int) -> dict:
        """Lista los eventos del médico que caen dentro de un mes concreto.

        Pensado para la vista de calendario: devuelve el mes completo sin paginar,
        ordenado cronológicamente. Un evento solo aparece si cumple las dos
        condiciones: el médico tiene una participación activa en él (de modo que
        los eventos de otros médicos quedan fuera por construcción) y la escuela
        que lo convocó mantiene un vínculo vigente con el médico. Quedan fuera
        además los eventos cancelados y los eliminados.

        Args:
            user_id: ID del médico autenticado (claim ``sub`` del JWT).
            year: Año del período a consultar, ej. 2026.
            month: Mes del período a consultar, de 1 a 12.

        Returns:
            dict: ``{"data": [...], "year": int, "month": int, "total": int}``. Cada
            evento incluye ``school_id`` y ``school_name``. Un mes sin eventos
            visibles devuelve ``data`` vacío y ``total`` en 0.

        Raises:
            HTTPException: 403 si el usuario no tiene un perfil de médico asociado.
            HTTPException: 500 si falla alguna de las consultas a la base de datos.
        """
        empty = {"data": [], "year": year, "month": month, "total": 0}

        doc_id = self._get_doctor_id(user_id)

        # Un médico sin ningún vínculo vigente no ve agenda, aunque conserve
        # participaciones históricas de una escuela que lo suspendió.
        active_school_ids = self._get_active_school_ids(doc_id)
        if not active_school_ids:
            return empty

        # Calcular rango del mes (ventana semiabierta [inicio, siguiente))
        start_date, next_month_start = month_window(year, month)

        # Participaciones activas del médico: única fuente del alcance.
        # No se filtra por 'event_participation.is_canceled' a propósito:
        # 'cancel_event' cancela siempre el evento junto con sus participaciones,
        # así que el filtro de vigencia sobre 'event' ya lo cubre, y 'create_event'
        # no fija la columna al insertar. Si no tuviera DEFAULT false, filtrarla
        # descartaría los NULL y vaciaría la agenda de todos los médicos.
        part_res = self._run(
            supabase.table("event_participation")
            .select("id_event, id_school_admin")
            .eq("id_doctor", doc_id)
            .eq("is_deleted", False),
            "las participaciones del médico",
        )
        if not part_res.data:
            return empty

        # Un evento puede tener varias participaciones del mismo médico; nos quedamos
        # con la primera que identifique al administrador convocante.
        admin_by_event: dict[int, int] = {}
        for part in part_res.data:
            event_id = part["id_event"]
            if part.get("id_school_admin") is not None and event_id not in admin_by_event:
                admin_by_event[event_id] = part["id_school_admin"]

        event_ids = list({p["id_event"] for p in part_res.data})

        events_res = self._run(
            apply_active_event_filter(
                supabase.table("event")
                .select("id, title, description, init_time, ent_time, created_at")
                .in_("id", event_ids)
            )
            .gte("init_time", start_date)
            .lt("init_time", next_month_start)
            .order("init_time", desc=False),
            "los eventos del mes",
        )
        events = events_res.data or []

        schools_by_admin = self._resolve_schools_by_admin(
            list({admin_by_event[e["id"]] for e in events if e["id"] in admin_by_event})
        )

        # Lista blanca: solo sobrevive el evento cuya escuela se pudo resolver y
        # sigue vinculada al médico. Un evento sin escuela resoluble se descarta,
        # porque no hay forma de comprobar que el médico siga autorizado en ella.
        visible = []
        for event in events:
            school = schools_by_admin.get(admin_by_event.get(event["id"]))
            if not school or school["school_id"] not in active_school_ids:
                continue
            event["school_id"] = school["school_id"]
            event["school_name"] = school["school_name"]
            visible.append(event)

        return {
            "data": visible,
            "year": year,
            "month": month,
            "total": len(visible),
        }


doctor_event_service = DoctorEventService()
