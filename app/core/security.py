"""Primitivas de seguridad: firma de JWT y hashing de contraseñas con bcrypt."""

from datetime import datetime, timedelta
from typing import Optional
from jose import JWTError, jwt
import bcrypt
from app.core.config import get_settings

settings = get_settings()

ALGORITHM = "HS256"

def create_access_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
    """Firma un JWT de acceso con los claims indicados.

    Args:
        data: Claims a incluir, típicamente ``{"sub": str(user_id), "role": rol}``.
            No debe contener el ``school_id``: el tenant se resuelve en el servidor.
        expires_delta: Vigencia del token. Si se omite, usa
            ``ACCESS_TOKEN_EXPIRE_MINUTES`` de la configuración.

    Returns:
        str: Token JWT firmado con HS256 y la clave ``SECRET_KEY``.
    """
    to_encode = data.copy()
    if expires_delta:
        expire = datetime.utcnow() + expires_delta
    else:
        expire = datetime.utcnow() + timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(to_encode, settings.SECRET_KEY, algorithm=ALGORITHM)
    return encoded_jwt

def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Compara una contraseña en texto plano contra su hash bcrypt.

    Args:
        plain_password: Contraseña tal como la envió el usuario.
        hashed_password: Hash bcrypt almacenado en la base de datos.

    Returns:
        bool: True si la contraseña coincide con el hash.

    Raises:
        ValueError: Si ``hashed_password`` no es un hash bcrypt válido.
    """
    plain_password_bytes = plain_password.encode('utf-8')
    if len(plain_password_bytes) > 72:
        return False
    hashed_password_bytes = hashed_password.encode('utf-8')
    try:
        return bcrypt.checkpw(plain_password_bytes, hashed_password_bytes)
    except ValueError:
        if not hashed_password.startswith(('$2a$', '$2b$', '$2y$')):
            raise
        return False

def get_password_hash(password: str) -> str:
    """Genera el hash bcrypt de una contraseña, con salt aleatorio.

    Args:
        password: Contraseña en texto plano a proteger.

    Returns:
        str: Hash bcrypt listo para almacenarse en la columna de contraseña.
    """
    password_bytes = password.encode('utf-8')   
    salt = bcrypt.gensalt()
    return bcrypt.hashpw(password_bytes, salt).decode('utf-8')
