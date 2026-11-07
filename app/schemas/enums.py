from enum import Enum


class UserRole(str, Enum):
    """Roles del sistema. Determinan el gate `get_current_*` que protege cada ruta."""

    ADMIN_SISTEMA = "admin_sistema"
    ADMIN_ESCUELA = "admin_escuela"
    DOCTOR = "doctor"
    REPRESENTANTE = "representante"


class TokenScope(str, Enum):
    """Procedencia de un JWT: el portal de login que lo emitió (claim `scope`)."""
    ADMIN = "admin"
    CLIENT = "client"

# Roles que cada portal de login acepta. Un rol pertenece a un único scope.
SCOPE_ROLES: dict[TokenScope, frozenset[UserRole]] = {
    TokenScope.ADMIN: frozenset({UserRole.ADMIN_SISTEMA}),
    TokenScope.CLIENT: frozenset({UserRole.ADMIN_ESCUELA, UserRole.DOCTOR, UserRole.REPRESENTANTE}),
}

class Gender(str, Enum):
    """Género registrado de un estudiante."""

    MASCULINO = "MASCULINO"
    FEMENINO = "FEMENINO"


class BloodType(str, Enum):
    """Tipo de sangre del estudiante en notación ABO/Rh."""

    A_POS = "A+"
    A_NEG = "A-"
    B_POS = "B+"
    B_NEG = "B-"
    AB_POS = "AB+"
    AB_NEG = "AB-"
    O_POS = "O+"
    O_NEG = "O-"


class BmiStatus(str, Enum):
    """Estado nutricional de un estudiante según su IMC.

    Replica literalmente la clasificación que ya emite
    ``SchoolAdminService.get_students_by_classroom``
    (``app/services/school_admin_service.py:554-565``) y el ``CASE`` de la vista
    ``student_health_status`` (migración ``20261107120000``). Las tres deben
    moverse juntas; mientras tanto, este enum es la única fuente del vocabulario
    para los endpoints nuevos.

    Umbrales vigentes: ``< 18.5`` desnutrido, ``18.5-24.9`` óptimo, ``> 24.9``
    sobrepeso. El corte en 30 que separaría la obesidad se sigue en el issue #63.
    """

    DESNUTRIDO = "DESNUTRIDO"
    OPTIMO = "OPTIMO"
    SOBREPESO = "SOBREPESO"
    #: El estudiante no tiene una medición vigente en ``student_metrics``.
    #: El valor conserva el espacio por compatibilidad con el listado por salón.
    SIN_DATOS = "SIN DATOS"


class SettingKey(str, Enum):
    """Claves de la tabla `settings`: el tipo de configuración global del sistema.

    Toda clave leída o escrita por la API debe existir aquí; la tabla no admite
    claves ad-hoc desde el cliente.
    """

    #: Minutos mínimos de anticipación para programar el inicio de un evento.
    EVENT_MIN_LEAD_TIME_MINUTES = "event_min_lead_time_minutes"


class SettingValueType(str, Enum):
    """Tipo de dato declarado dentro del JSON `settings.value`.

    Replica el CHECK `settings_value_type_match` de la migración: el tipo
    declarado debe coincidir con el tipo JSON real del dato almacenado.
    """

    INTEGER = "integer"
    NUMBER = "number"
    STRING = "string"
    BOOLEAN = "boolean"
    JSON = "json"
