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

#: Cliente privilegiado, reservado a las tablas que deben quedar **fuera** del
#: alcance de la ``anon key``. Consumidores: settings_service y la RPC interna
#: de baja propia en account_deletion_service. La RPC opera sobre identidad
#: global y comprueba sus vínculos dentro de la transacción; nunca acepta una
#: identidad o escuela elegida por el cliente HTTP. La tabla settings conserva RLS
#: activo y sin políticas, de modo que la Data API anónima no puede leerla ni
#: modificarla, y el único camino de escritura es el endpoint protegido por
#: ``get_current_admin_sistema``. No lo uses para datos con alcance de escuela:
#: ignora RLS y anularía cualquier defensa en profundidad.
supabase_admin = get_supabase_admin_client()
