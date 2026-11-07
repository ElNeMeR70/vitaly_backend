"""Bitácora de auditoría de acciones sensibles.

Registra accesos y cambios sobre datos médicos/PHR, aprobaciones de usuarios,
acciones globales de ``admin_sistema`` y exportaciones, según la skill
``audit-logging``. Hasta el issue #66 el proyecto no tenía tabla de auditoría;
la crea la migración ``20261107120100_create_audit_log_table.sql``.

La tabla ``audit_log`` conserva RLS activo y sin políticas, igual que
``settings``: la ``anon key`` no puede leerla ni escribirla por la Data API, así
que el único camino de escritura es el cliente privilegiado ``supabase_admin``.

Minimización de PHR/PII: ``changes`` y ``metadata`` guardan nombres de campo,
banderas y filtros, nunca diagnósticos, signos vitales, documentos de identidad
ni credenciales. Lo que se registra es el **acceso**, no el contenido.
"""

import logging

from fastapi import HTTPException

from app.core.database import supabase_admin

logger = logging.getLogger(__name__)


class AuditService:
    """Escritura append-only sobre ``audit_log``."""

    def record(
        self,
        *,
        actor: dict,
        action: str,
        entity: str,
        entity_id: str | int | None = None,
        school_id: int | None = None,
        changes: dict | None = None,
        metadata: dict | None = None,
        critical: bool = False,
    ) -> None:
        """Registra una entrada de auditoría.

        Args:
            actor: Payload del JWT devuelto por el gate ``get_current_*``. Se leen
                ``sub`` y ``role``.
            action: Una de ``read``, ``create``, ``update``, ``delete``,
                ``approve``, ``reject``, ``export`` (lo valida un CHECK en la tabla).
            entity: Nombre de la tabla o recurso afectado, ej. ``student``.
            entity_id: Clave del registro afectado. ``None`` en acciones sobre un
                conjunto, como una búsqueda.
            school_id: Escuela en cuyo ámbito ocurrió la acción. ``None`` solo en
                acciones globales de ``admin_sistema``.
            changes: Nombres de campo y banderas de cambio. Nunca valores clínicos.
            metadata: Contexto no sensible: filtros aplicados, número de resultados.
            critical: Si es ``True``, un fallo al escribir aborta la operación con
                500. Por defecto ``False``: una lectura no debe fallar porque la
                bitácora esté caída, pero el fallo queda en el log del servidor.

        Raises:
            HTTPException: 500 solo si ``critical`` es ``True`` y la escritura falla.
                Se lanza como ``HTTPException`` —y no como excepción desnuda— porque
                ``app/main.py`` no registra un handler genérico y el error saldría
                sin el envelope ``APIResponse``.
        """
        row = {
            "actor_id": int(actor["sub"]),
            "actor_role": actor.get("role"),
            "school_id": school_id,
            "action": action,
            "entity": entity,
            "entity_id": str(entity_id) if entity_id is not None else None,
            "changes": changes or {},
            "metadata": metadata or {},
        }

        if supabase_admin is None:
            logger.error(
                "SUPABASE_SERVICE_KEY no está configurada: no se registró la acción "
                "'%s' sobre '%s' del actor %s.", action, entity, row["actor_id"],
            )
            if critical:
                raise HTTPException(
                    status_code=500,
                    detail="No se pudo registrar la operación en la bitácora de auditoría.",
                )
            return

        try:
            supabase_admin.table("audit_log").insert(row).execute()
        except Exception:
            logger.exception(
                "Fallo al registrar en la bitácora la acción '%s' sobre '%s' del actor %s.",
                action, entity, row["actor_id"],
            )
            if critical:
                raise HTTPException(
                    status_code=500,
                    detail="No se pudo registrar la operación en la bitácora de auditoría.",
                )


#: Instancia compartida del servicio, importada por los demás servicios.
audit_service = AuditService()
