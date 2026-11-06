"""Fragmentos de consulta compartidos por los servicios de eventos escolares.

Centraliza las dos condiciones que todo listado de eventos repite —la vigencia
del evento y la ventana de un mes— para que el portal del administrador y el del
médico no diverjan. Antes vivían duplicadas: ``_base_event_filter`` en
:mod:`app.services.school_admin_event_service` (que nadie llegó a invocar) y los
literales de fecha copiados en cada ``get_events_by_month``.
"""

from datetime import date


def apply_active_event_filter(query):
    """Restringe una consulta sobre ``event`` a los eventos vigentes.

    Vigente significa ni cancelado ni eliminado. Se aplica sobre la consulta en
    curso en lugar de devolver un diccionario de condiciones, para que el filtro
    no pueda quedar declarado y sin usar.

    Args:
        query: Consulta de supabase-py sobre la tabla ``event``.

    Returns:
        La misma consulta con los filtros de vigencia encadenados.
    """
    return query.eq("is_canceled", False).eq("is_deleted", False)


def month_window(year: int, month: int) -> tuple[str, str]:
    """Calcula la ventana semiabierta ``[inicio, siguiente)`` de un mes.

    El límite superior es el primer instante del mes siguiente, no el último
    segundo del mes consultado: una ventana cerrada en ``23:59:59`` deja fuera
    los eventos del último segundo del mes, que entonces no aparecen en ningún
    mes. El primero de enero cierra correctamente contra el año siguiente.

    Args:
        year: Año del período a consultar, ej. 2026.
        month: Mes del período a consultar, de 1 a 12.

    Returns:
        tuple[str, str]: ``(inicio, siguiente_inicio)`` en ISO 8601 con offset
        UTC explícito, listos para encadenar como ``.gte(inicio).lt(siguiente)``.

    Raises:
        ValueError: Si ``month`` queda fuera del rango 1-12.
    """
    start = date(year, month, 1)
    next_start = date(year + 1, 1, 1) if month == 12 else date(year, month + 1, 1)
    return (
        f"{start.isoformat()}T00:00:00+00:00",
        f"{next_start.isoformat()}T00:00:00+00:00",
    )
