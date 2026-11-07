"""Registro de usuarios por rol, autenticación y revocación de tokens."""

import logging

from fastapi import HTTPException, status
from app.core.database import supabase
from app.core.security import get_password_hash, verify_password, create_access_token
from app.schemas.user import SystemAdminRegister, SchoolAdminRegister, DoctorRegister, ParentRegister, LoginBase, PasswordChangeCreate, Token
from app.schemas.enums import SCOPE_ROLES, TokenScope, UserRole
import uuid
from datetime import datetime

logger = logging.getLogger(__name__)

class AuthService:
    """Servicio de autenticación y alta de cuentas para los cuatro roles."""

    def _create_base_user(self, user_in, role_enum: UserRole):
        """Crea la fila en ``user`` y su mapeo de rol en ``user_roles``.

        Paso común a los cuatro flujos de registro. La cuenta nace inactiva
        (``is_active=False``): requiere aprobación de un administrador antes de
        poder iniciar sesión.

        Args:
            user_in: Esquema de registro del rol correspondiente. Debe exponer
                ``correo``, ``contraseña``, ``nombre``, ``apellido`` y
                ``fecha_de_nacimiento``.
            role_enum: Rol que se asignará a la cuenta recién creada.

        Returns:
            int: ID del usuario creado en la tabla ``user``.

        Raises:
            HTTPException: 400 si el correo ya está registrado, o si falta el
                documento de identidad en el registro de un representante.
            HTTPException: 500 si la inserción en ``user`` no devuelve la fila.
        """
        # 1. Verificar si el usuario ya existe (usamos tabla 'user' singular)
        user_response = supabase.table("user").select("id").eq("email", user_in.correo).execute()
        
        if user_response.data:
            user_id = user_response.data[0]["id"]
            roles_response = supabase.table("user_roles").select("role").eq("user_id", user_id).execute()
            role = roles_response.data[0]["role"] if roles_response.data else "desconocido"
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"El usuario ya existe bajo el rol: {role}"
            )
        
        # 1.1 Validar documento de identidad obligatorio para representantes
        if role_enum == UserRole.REPRESENTANTE and not getattr(user_in, "identity_number", None):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="El número de identidad es obligatorio para completar el registro de representante."
            )
        
        # 2. Insertar en la tabla maestra 'user'
        hashed_password = get_password_hash(user_in.contraseña)
        user_data = {
            "email": user_in.correo,
            "password": hashed_password,
            "name": user_in.nombre,
            "lastname": user_in.apellido,
            "identity_number": getattr(user_in, "identity_number", None),
            "birthday": str(user_in.fecha_de_nacimiento),
            "is_active": False,
            "is_deleted": False
        }
        
        # INSERT ... RETURNING id
        user_result = supabase.table("user").insert(user_data).execute()
        if not user_result.data:
            raise HTTPException(status_code=500, detail="Error al crear el usuario base")
            
        new_user_id = user_result.data[0]["id"]
        
        # 3. Insertar el mapeo de rol en 'user_roles'
        role_data = {
            "user_id": new_user_id,
            "role": role_enum.value,
            "is_active": False
        }
        supabase.table("user_roles").insert(role_data).execute()
        
        return new_user_id

    def register_system_admin(self, user_in):
        """Registra un administrador de sistema y su perfil en ``system_administrator``.

        Le asigna el nivel de privilegio básico (1).

        Args:
            user_in: Datos de registro validados del administrador de sistema.

        Returns:
            str: Mensaje de confirmación indicando que la cuenta queda pendiente
            de verificación.

        Raises:
            HTTPException: 400 si el correo ya está registrado.
            HTTPException: 500 si falla la creación del usuario base.
        """
        new_user_id = self._create_base_user(user_in, UserRole.ADMIN_SISTEMA)
        
        supabase.table("system_administrator").insert({
            "admin_id": new_user_id,
            "level_privilege": 1 # Nivel básico por defecto
        }).execute()
        
        return "El administrador de sistema ha sido creado exitosamente, pero debe esperar a ser verificado para continuar."

    def register_school_admin(self, user_in):
        """Registra un administrador de escuela y su perfil en ``school_administrator``.

        Lo vincula a la escuela indicada en ``user_in.school_id``, que define su alcance
        de datos para el resto del sistema.

        Args:
            user_in: Datos de registro validados del administrador de escuela.

        Returns:
            str: Mensaje de confirmación indicando que la cuenta queda pendiente
            de verificación.

        Raises:
            HTTPException: 400 si el correo ya está registrado.
            HTTPException: 500 si falla la creación del usuario base.
        """
        new_user_id = self._create_base_user(user_in, UserRole.ADMIN_ESCUELA)
        
        supabase.table("school_administrator").insert({
            "user_id": new_user_id,
            "school_id": user_in.school_id,
            "administrative_position": user_in.administrative_position,
            "status": 2 # Estado 'Inactivo/Pendiente' usualmente
        }).execute()
        
        return "El administrador de escuela ha sido creado exitosamente, pero debe esperar a ser verificado para continuar."

    def register_doctor(self, user_in):
        """Registra un médico y su perfil en ``doctor``.

        Registra su número de licencia y especialidad.

        Args:
            user_in: Datos de registro validados del médico.

        Returns:
            str: Mensaje de confirmación indicando que la cuenta queda pendiente
            de verificación.

        Raises:
            HTTPException: 400 si el correo ya está registrado.
            HTTPException: 500 si falla la creación del usuario base.
        """
        new_user_id = self._create_base_user(user_in, UserRole.DOCTOR)
        
        supabase.table("doctor").insert({
            "user_id": new_user_id,
            "doc_license_number": user_in.doc_license_number,
            "especially": user_in.especially
        }).execute()
        
        return "El perfil de doctor ha sido creado exitosamente, pero debe esperar a ser verificado para continuar."

    def register_parent(self, user_in: ParentRegister):
        """Registra un representante y lo vincula con sus estudiantes autorizados.

        Tras crear el perfil en ``parent``, busca los estudiantes cuyo representante
        autorizado coincide con el documento de identidad recibido y los vincula
        automáticamente.

        Args:
            user_in: Datos de registro del representante. ``identity_number`` es
                obligatorio y es la clave de la vinculación automática.

        Returns:
            str: Mensaje de confirmación. Si hubo vinculación automática, indica
            cuántos estudiantes se asociaron.

        Raises:
            HTTPException: 400 si el correo ya está registrado o falta el documento
                de identidad.
            HTTPException: 500 si falla la creación del usuario base.
        """
        new_user_id = self._create_base_user(user_in, UserRole.REPRESENTANTE)
        
        supabase.table("parent").insert({
            "user_id": new_user_id,
            "occupation": user_in.occupation,
            "type_representative": user_in.type_representative,
            "is_active": False
        }).execute()

        # Vincular automáticamente con estudiantes autorizados
        from app.services.parent_service import parent_service
        link_result = parent_service.link_students_by_identity(
            user_id=new_user_id,
            identity_number=user_in.identity_number
        )
        
        linked_count = len(link_result.get("linked_students", []))
        msg = f"El perfil de representante ha sido creado exitosamente. Debe esperar la verificación del sistema."
        if linked_count > 0:
            msg += f" Se vincularon automáticamente {linked_count} estudiante(s)."
        
        return msg
        
    def authenticate_user(self, user_in: LoginBase, scope: TokenScope) -> Token:
        """Valida las credenciales y emite un JWT para el portal de login indicado.

        El rol del usuario debe pertenecer al portal (`SCOPE_ROLES[scope]`); si no, se
        rechaza antes de emitir el token. El token incluye el claim `scope` para que los
        gates de `app/api/deps.py` verifiquen su procedencia.

        Args:
            user_in: Credenciales (correo y contraseña) recibidas en el login.
            scope: Portal por el que se autentica (`TokenScope.ADMIN` o `TokenScope.CLIENT`).

        Returns:
            Token: JWT de acceso con los claims del usuario y su `scope`.

        Raises:
            HTTPException: 401 si las credenciales son incorrectas; 403 si el usuario está
                eliminado, inactivo o su rol no pertenece al portal solicitado; 500 si
                falla una consulta o cualquier otra operación inesperada del login.
        """
        try:
            user_response = supabase.table("user").select("*").eq("email", user_in.email).execute()
            if not user_response.data:
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Credenciales incorrectas"
                )

            user = user_response.data[0]

            if user.get("is_deleted", False):
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="El usuario ha sido eliminado"
                )

            if not user.get("is_active", False):
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="El usuario está inactivo, debe esperar verificación"
                )

            if not verify_password(user_in.password, user["password"]):
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Credenciales incorrectas"
                )

            # Obtener el rol del usuario
            role_response = supabase.table("user_roles").select("role").eq("user_id", user["id"]).execute()
            role = role_response.data[0]["role"] if role_response.data else "desconocido"

            # Cada portal solo emite tokens para sus roles (ej. admin_sistema no entra por el de clientes)
            if role not in {allowed.value for allowed in SCOPE_ROLES[scope]}:
                portal = "administración" if scope == TokenScope.ADMIN else "clientes"
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail=f"Esta cuenta no tiene acceso al portal de {portal}. Inicia sesión desde el portal correspondiente."
                )

            # Generar token
            token_data = {
                "sub": str(user["id"]),
                "email": str(user["email"]),
                "role": role,
                "scope": scope.value,
                "name": f"{user.get('name', '')} {user.get('lastname', '')}".strip(),
                "gender": user.get("gender") if user.get("gender") is not None else ""
            }

            if role == UserRole.DOCTOR.value:
                doctor_res = supabase.table("doctor").select("especially, doc_license_number").eq("user_id", user["id"]).execute()
                if doctor_res.data:
                    token_data["especially"] = doctor_res.data[0].get("especially")
                    token_data["doc_license_number"] = doctor_res.data[0].get("doc_license_number")
            elif role == UserRole.ADMIN_ESCUELA.value:
                admin_res = supabase.table("school_administrator").select("administrative_position, school_id").eq("user_id", user["id"]).execute()
                if admin_res.data:
                    token_data["administrative_position"] = admin_res.data[0].get("administrative_position")
                    school_id = admin_res.data[0].get("school_id")
                    if school_id:
                        school_res = supabase.table("school").select("name").eq("sch_id", school_id).execute()
                        if school_res.data:
                            token_data["school"] = school_res.data[0].get("name")
            elif role == UserRole.REPRESENTANTE.value:
                parent_res = supabase.table("parent").select("type_representative").eq("user_id", user["id"]).execute()
                if parent_res.data:
                    token_data["type_representative"] = parent_res.data[0].get("type_representative")

            access_token = create_access_token(data=token_data)

            return Token(access_token=access_token, token_type="bearer")
        except HTTPException:
            raise
        except Exception as exc:
            logger.exception("Error inesperado durante la autenticación")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="No se pudo iniciar sesión. Inténtalo de nuevo más tarde.",
            ) from exc

    def assert_account_available(self, user_id: int) -> None:
        """Rechaza sesiones cuyo usuario ya no existe o fue eliminado.

        Args:
            user_id: Identificador obtenido del claim ``sub`` validado.

        Returns:
            None: La cuenta existe y no está eliminada.

        Raises:
            HTTPException: 401 si la cuenta no existe o está eliminada;
                500 si no se puede consultar su estado.
        """
        try:
            response = (
                supabase.table("user").select("id, is_deleted")
                .eq("id", user_id).execute()
            )
        except Exception as exc:
            # No registrar la excepción del proveedor: puede incluir credenciales.
            logger.error("No se pudo consultar el estado de una cuenta autenticada")
            raise HTTPException(500, "No se pudo verificar el estado de la cuenta.") from exc
        if not response.data or response.data[0].get("is_deleted"):
            raise HTTPException(
                status_code=401,
                detail="La cuenta no está disponible. Por favor inicia sesión de nuevo.",
                headers={"WWW-Authenticate": "Bearer"},
            )

    def change_password(self, user_id: int, data: PasswordChangeCreate) -> str:
        """Cambia la contraseña del usuario autenticado tras verificar la vigente.

        La sesión actual se mantiene: el token con el que se hace la petición no se
        revoca. Las demás sesiones abiertas siguen siendo válidas hasta su expiración,
        porque el JWT no lleva ninguna marca ligada a la contraseña.

        Args:
            user_id: ID del usuario autenticado, tomado del claim ``sub`` del token.
            data: Contraseña vigente y nueva contraseña.

        Returns:
            str: Mensaje de confirmación del cambio.

        Raises:
            HTTPException: 400 si la contraseña vigente es incorrecta o la nueva
                coincide con ella (nunca 401: el cliente trata un 401 como sesión
                expirada); 403 si la cuenta está inactiva; 404 si el usuario no existe
                o fue eliminado; 500 si falla una consulta o cualquier otra operación
                inesperada.
        """
        try:
            user_response = (
                supabase.table("user")
                .select("id, password, is_active")
                .eq("id", user_id)
                .eq("is_deleted", False)
                .execute()
            )
            if not user_response.data:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="El usuario no existe.")

            user = user_response.data[0]

            if not user.get("is_active", False):
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="La cuenta está inactiva; no se puede cambiar la contraseña.",
                )

            if not verify_password(data.current_password, user["password"]):
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="La contraseña actual es incorrecta.",
                )

            if data.new_password == data.current_password:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="La nueva contraseña debe ser distinta de la actual.",
                )

            update_response = (
                supabase.table("user")
                .update({"password": get_password_hash(data.new_password)})
                .eq("id", user_id)
                .eq("is_deleted", False)
                .execute()
            )
            # La fila pudo eliminarse entre la lectura y la escritura.
            if not update_response.data:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="El usuario no existe.")

            return "Contraseña actualizada"
        except HTTPException:
            raise
        except Exception as exc:
            logger.exception("Error inesperado al cambiar la contraseña del usuario %s", user_id)
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="No se pudo cambiar la contraseña. Inténtalo de nuevo más tarde.",
            ) from exc

    def revoke_token(self, token: str, expires_at: int):
        """Añade un token a la lista negra para invalidarlo antes de su expiración.

        Es el mecanismo de logout: el token sigue siendo criptográficamente válido,
        pero :func:`~app.api.deps.get_current_user_token` lo rechaza con 401.

        Args:
            token: JWT completo a revocar.
            expires_at: Timestamp del claim ``exp``, usado para poder purgar la
                lista negra una vez que el token caduca por sí solo.

        Returns:
            None: Inserta la fila en ``token_blacklist`` como efecto secundario.
        """
        expires_at_dt = datetime.utcfromtimestamp(expires_at)
        supabase.table("token_blacklist").insert({
            "token": token,
            "expires_at": expires_at_dt.isoformat()
        }).execute()

    def is_token_revoked(self, token: str) -> bool:
        """Indica si un token está en la lista negra.

        Args:
            token: JWT completo a comprobar.

        Returns:
            bool: True si el token fue revocado por un logout previo.

        Raises:
            HTTPException: 500 si no se puede consultar la lista de revocados.
        """
        try:
            response = supabase.table("token_blacklist").select("id").eq("token", token).execute()
        except Exception as exc:
            logger.error("No se pudo consultar la revocación de una sesión")
            raise HTTPException(500, "No se pudo verificar la sesión.") from exc
        return len(response.data) > 0

#: Instancia compartida del servicio, importada por endpoints y dependencias.
auth_service = AuthService()
