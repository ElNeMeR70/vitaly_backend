"""Baja propia atómica; la identidad procede exclusivamente del JWT validado."""

import logging

from fastapi import HTTPException

from app.core.database import supabase_admin

logger = logging.getLogger(__name__)

_REJECTIONS = {
    "not_found": (401, "La cuenta no existe o ha sido eliminada."),
    "forbidden": (403, "Esta cuenta no puede eliminarse desde el portal de clientes."),
    "last_school_admin": (409, "No puedes eliminar tu cuenta: eres el único administrador activo de la escuela."),
    "active_medical_cases": (409, "No puedes eliminar tu cuenta mientras tengas casos clínicos activos."),
    "pending_reviews": (409, "No puedes eliminar tu cuenta mientras tengas revisiones pendientes."),
}


class AccountDeletionService:
    """Ejecuta las comprobaciones y escrituras en una única transacción Postgres."""

    def delete_own_account(self, user_id: int, token: str, expires_at: int) -> None:
        """Elimina lógicamente la cuenta, sus vínculos y revoca el token actual.

        Args:
            user_id: ID autenticado del claim sub; nunca un ID del cliente.
            token: Bearer token que se añadirá a la lista de revocados.
            expires_at: Expiración del JWT en segundos Unix.

        Returns:
            None: La transacción finalizó y las sesiones ya no pueden acceder.

        Raises:
            HTTPException: 401 si la cuenta no existe o está eliminada; 403 si
                su rol actual no pertenece al portal; 409 si quedan obligaciones;
                500 si falla la transacción. Ningún rechazo modifica datos.
        """
        if supabase_admin is None:
            raise HTTPException(status_code=500, detail="No se pudo eliminar la cuenta.")
        try:
            result = supabase_admin.rpc("delete_own_account", {
                "p_user_id": user_id,
                "p_token": token,
                "p_expires_at": expires_at,
            }).execute()
            if result.data in _REJECTIONS:
                code, message = _REJECTIONS[result.data]
                raise HTTPException(status_code=code, detail=message)
            if result.data != "ok":
                raise HTTPException(status_code=500, detail="No se pudo eliminar la cuenta.")
        except HTTPException:
            raise
        except Exception:
            # No registrar la excepción de PostgREST: puede incluir el JWT enviado.
            logger.error("Falló la transacción de eliminación de cuenta")
            raise HTTPException(status_code=500, detail="No se pudo eliminar la cuenta.") from None


account_deletion_service = AccountDeletionService()
