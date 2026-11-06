"""Agenda de eventos escolares del rol médico.

El alcance se resuelve siempre en el servidor: el ``doc_id`` sale del ``user_id``
autenticado y un evento solo es visible si existe una participación activa a
nombre de ese médico. El cliente nunca envía un identificador de médico ni de
escuela.
"""

import logging

from fastapi import HTTPException

from app.core.database import supabase
from app.services.event_queries import apply_active_event_filter, month_window

logger = logging.getLogger(__name__)


class DoctorEventService:
    """Consultas de eventos escolares con alcance limitado al médico autenticado."""

    # ──────────────────────────────────────────────────────────
    # Helpers internos
    # ──────────────────────────────────────────────────────────

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
        doc_res = (
            supabase.table("doctor")
            .select("doc_id")
            .eq("user_id", user_id)
            .execute()
        )
        if not doc_res.data:
            raise HTTPException(
                status_code=403,
                detail="Tu usuario no tiene un perfil de médico asociado."
            )
        return doc_res.data[0]["doc_id"]

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

        admins_res = (
            supabase.table("school_administrator")
            .select("sch_admin_id, school_id")
            .in_("sch_admin_id", sch_admin_ids)
            .execute()
        )
        admins = admins_res.data or []
        if not admins:
            return {}

        school_ids = list({a["school_id"] for a in admins if a.get("school_id")})
        names: dict[int, str] = {}
        if school_ids:
            schools_res = (
                supabase.table("school")
                .select("sch_id, name")
                .in_("sch_id", school_ids)
                .execute()
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
        ordenado cronológicamente. Solo incluye eventos vigentes (ni cancelados ni
        eliminados) con una participación activa del médico autenticado, de modo
        que los eventos de otros médicos quedan fuera por construcción.

        Args:
            user_id: ID del médico autenticado (claim ``sub`` del JWT).
            year: Año del período a consultar, ej. 2026.
            month: Mes del período a consultar, de 1 a 12.

        Returns:
            dict: ``{"data": [...], "year": int, "month": int, "total": int}``. Cada
            evento incluye ``school_id`` y ``school_name``. Un mes sin eventos
            devuelve ``data`` vacío y ``total`` en 0.

        Raises:
            HTTPException: 403 si el usuario no tiene un perfil de médico asociado.
        """
        doc_id = self._get_doctor_id(user_id)

        # Calcular rango del mes (ventana semiabierta [inicio, siguiente))
        start_date, next_month_start = month_window(year, month)

        # Participaciones activas del médico: única fuente del alcance
        part_res = (
            supabase.table("event_participation")
            .select("id_event, id_school_admin")
            .eq("id_doctor", doc_id)
            .eq("is_deleted", False)
            .execute()
        )
        if not part_res.data:
            return {"data": [], "year": year, "month": month, "total": 0}

        # Un evento puede tener varias participaciones del mismo médico; nos quedamos
        # con la primera que identifique al administrador convocante.
        admin_by_event: dict[int, int] = {}
        for part in part_res.data:
            event_id = part["id_event"]
            if part.get("id_school_admin") and event_id not in admin_by_event:
                admin_by_event[event_id] = part["id_school_admin"]

        event_ids = list({p["id_event"] for p in part_res.data})

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
        events = events_res.data or []
        if not events:
            return {"data": [], "year": year, "month": month, "total": 0}

        schools_by_admin = self._resolve_schools_by_admin(
            list({admin_by_event[e["id"]] for e in events if e["id"] in admin_by_event})
        )

        for event in events:
            school = schools_by_admin.get(admin_by_event.get(event["id"]), {})
            event["school_id"] = school.get("school_id")
            event["school_name"] = school.get("school_name")

        return {
            "data": events,
            "year": year,
            "month": month,
            "total": len(events),
        }


doctor_event_service = DoctorEventService()
