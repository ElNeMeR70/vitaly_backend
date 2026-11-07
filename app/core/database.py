"""Acceso a PostgreSQL/Supabase mediante el cliente síncrono supabase-py."""

from typing import Optional

from supabase import create_client, Client
from app.core.config import get_settings

settings = get_settings()

def get_supabase_client() -> Client:
    """Crea el cliente síncrono de supabase-py a partir de la configuración.

    Returns:
        Client: Cliente de Supabase listo para encadenar ``.table(...)``.

    Raises:
        Exception: Si la URL o la clave de Supabase son inválidas y la librería
            no puede construir el cliente.
    """
    return create_client(settings.SUPABASE_URL, settings.SUPABASE_KEY)


def get_supabase_admin_client() -> Optional[Client]:
    """Crea el cliente privilegiado (``service_role``), si está configurado.

    Returns:
        Client | None: Cliente con la ``service_role key``, o ``None`` si
        ``SUPABASE_SERVICE_KEY`` no está definida en el entorno.
    """
    if not settings.SUPABASE_SERVICE_KEY:
        return None
    return create_client(settings.SUPABASE_URL, settings.SUPABASE_SERVICE_KEY)

#: Cliente compartido por toda la aplicación. Es la única vía de acceso a datos
#: permitida: no se crean clientes ad-hoc ni se llama a la API REST directamente.
supabase = get_supabase_client()

#: Cliente privilegiado, reservado a los objetos que deben quedar **fuera** del
#: alcance de la ``anon key`` (``SUPABASE_KEY`` lleva ``role=anon`` y viaja al
#: frontend). Sus consumidores:
#:
#: - :mod:`app.services.settings_service` — ``settings`` conserva RLS activo y sin
#:   políticas, así que la Data API anónima no puede leerla ni modificarla y el único
#:   camino de escritura es el endpoint protegido por ``get_current_admin_sistema``.
#: - :mod:`app.services.audit_service` — la bitácora ``audit_log`` es append-only y
#:   sensible; solo ``service_role`` escribe en ella.
#: - :mod:`app.services.student_search_service` — la vista ``student_health_status``
#:   cruza datos de salud de **todas** las escuelas en un solo objeto, de modo que la
#:   migración se la deniega a ``anon``. Es la excepción consciente a la regla de
#:   abajo: el aislamiento no se delega a la clave, lo aplica el ``school_id``
#:   resuelto en el servidor, tal como describe la skill ``supabase-integration``.
#:
#: Fuera de esos casos, no lo uses para datos con alcance de escuela: ignora RLS y
#: anularía cualquier defensa en profundidad que se añada más adelante.
supabase_admin = get_supabase_admin_client()
