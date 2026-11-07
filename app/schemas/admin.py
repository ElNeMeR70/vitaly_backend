from pydantic import BaseModel, Field
from typing import List, Optional
from datetime import date


class UserAdminResponse(BaseModel):
    """Vista resumida de un usuario para los listados del administrador de sistema."""

    id: int = Field(description="Identificador único del usuario.")
    email: str = Field(description="Correo electrónico con el que el usuario inicia sesión.")
    name: str = Field(description="Nombre del usuario.")
    lastname: str = Field(description="Apellido del usuario.")
    birthday: date = Field(description="Fecha de nacimiento del usuario (YYYY-MM-DD).")
    is_active: bool = Field(
        description="True si la cuenta está aprobada y habilitada para iniciar sesión."
    )
    is_deleted: bool = Field(description="True si el usuario fue eliminado lógicamente (soft delete).")


class PaginatedUsersResponse(BaseModel):
    """Página de usuarios devuelta por el listado del administrador de sistema.

    Formato legado: la paginación viaja anidada dentro de ``APIResponse.data`` en
    lugar de ``APIResponse.meta``. No se migra sin coordinar con el frontend.
    """

    data: List[UserAdminResponse] = Field(description="Usuarios contenidos en la página actual.")
    total: int = Field(description="Total de usuarios que cumplen el filtro aplicado.")
    page: int = Field(description="Número de página actual, comenzando en 1.")
    size: int = Field(description="Cantidad de usuarios solicitados por página.")


class ApproveUserRequest(BaseModel):
    """Solicitud para aprobar la cuenta de un usuario pendiente."""

    user_id_to_approve: int = Field(description="ID del usuario cuya cuenta se desea aprobar.")
    admin_id: Optional[int] = Field(default=None, description="Obsoleto: se ignora; el servidor usa el claim sub del JWT.")


class BulkApproveUserRequest(BaseModel):
    """Solicitud para aprobar varias cuentas pendientes en una sola operación."""

    user_ids_to_approve: List[int] = Field(
        description="IDs de los usuarios cuyas cuentas se desean aprobar."
    )
    admin_id: Optional[int] = Field(default=None, description="Obsoleto: se ignora; el servidor usa el claim sub del JWT.")


class ToggleUserRequest(BaseModel):
    """Solicitud para alternar el estado activo/inactivo de un usuario."""

    user_id_to_toggle: Optional[int] = Field(default=None, description="Obsoleto: el objetivo se toma del path {user_id}.")
    admin_id: Optional[int] = Field(default=None, description="Obsoleto: se ignora; el servidor usa el claim sub del JWT.")


class UserDetailsResponse(BaseModel):
    """Detalle completo de un usuario, incluyendo los datos propios de su rol."""

    user: UserAdminResponse = Field(description="Datos base de la cuenta de usuario.")
    role: str = Field(
        description="Rol del usuario: admin_sistema, admin_escuela, doctor o representante."
    )
    details: dict = Field(
        description="Datos específicos del rol (ej. escuela asignada, cédula profesional)."
    )
