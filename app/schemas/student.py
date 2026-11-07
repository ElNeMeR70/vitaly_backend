from pydantic import BaseModel, Field, field_validator
from typing import List, Optional
from datetime import date
from app.schemas.enums import Gender, BloodType, BmiStatus

class StudentCreate(BaseModel):
    """Datos para registrar un estudiante en el sistema."""

    name: str = Field(description="Nombre del estudiante.")
    lastname: str = Field(description="Apellido del estudiante.")
    birthday: date = Field(
        description="Fecha de nacimiento (YYYY-MM-DD). No puede ser futura ni implicar más de 21 años."
    )
    gender: Gender = Field(description="Género del estudiante.")
    blood_type: Optional[BloodType] = Field(
        default=None, description="Tipo de sangre del estudiante, si se conoce."
    )
    identity_number: Optional[str] = Field(
        default=None, description="Documento de identidad del estudiante (cédula escolar), si aplica."
    )

    @field_validator("birthday")
    @classmethod
    def validate_birthday(cls, v: date) -> date:
        today = date.today()
        age = today.year - v.year - ((today.month, today.day) < (v.month, v.day))
        if age > 21:
            raise ValueError("El estudiante no puede ser mayor a 21 años.")
        if v > today:
            raise ValueError("La fecha de nacimiento no puede ser una fecha futura.")
        return v

class StudentUpdate(BaseModel):
    """Campos opcionales para actualizar un estudiante.

    Los campos omitidos conservan su valor actual. ``is_active`` e ``is_deleted``
    no son modificables desde este esquema.
    """

    name: Optional[str] = Field(default=None, description="Nuevo nombre del estudiante.")
    lastname: Optional[str] = Field(default=None, description="Nuevo apellido del estudiante.")
    birthday: Optional[date] = Field(
        default=None,
        description="Nueva fecha de nacimiento (YYYY-MM-DD). No puede ser futura ni implicar más de 21 años.",
    )
    gender: Optional[Gender] = Field(default=None, description="Nuevo género del estudiante.")
    blood_type: Optional[BloodType] = Field(
        default=None, description="Nuevo tipo de sangre del estudiante."
    )
    identity_number: Optional[str] = Field(
        default=None, description="Nuevo documento de identidad del estudiante."
    )

    @field_validator("birthday")
    @classmethod
    def validate_birthday(cls, v: Optional[date]) -> Optional[date]:
        if v is None:
            return v
        today = date.today()
        # Detectar fechas imposibles ya es tarea de Pydantic al parsear `date`,
        # pero validamos la regla de negocio: no mayor de 21 ni fecha futura.
        age = today.year - v.year - ((today.month, today.day) < (v.month, v.day))
        if v > today:
            raise ValueError("La fecha de nacimiento no puede ser una fecha futura.")
        if age > 21:
            raise ValueError("El estudiante no puede ser mayor a 21 años.")
        return v

class StudentResponse(BaseModel):
    """Representación de un estudiante tal como se expone al cliente."""

    id: int = Field(description="Identificador único del estudiante.")
    name: str = Field(description="Nombre del estudiante.")
    lastname: str = Field(description="Apellido del estudiante.")
    birthday: Optional[date] = Field(default=None, description="Fecha de nacimiento del estudiante.")
    gender: Optional[str] = Field(default=None, description="Género del estudiante.")
    blood_type: Optional[str] = Field(default=None, description="Tipo de sangre del estudiante.")
    identity_number: Optional[str] = Field(
        default=None, description="Documento de identidad del estudiante."
    )
    is_active: bool = Field(description="True si el estudiante está activo en la escuela.")
    is_deleted: bool = Field(
        description="True si el estudiante fue eliminado lógicamente (soft delete)."
    )

class ClassroomRegistrationCreate(BaseModel):
    """Inscripción de un estudiante en un salón para un año académico."""

    student_id: int = Field(description="ID del estudiante que se desea inscribir.")
    academic_year_id: int = Field(description="ID del año académico al que corresponde la inscripción.")
    classroom_id: int = Field(description="ID del salón en el que se inscribe al estudiante.")
    status_id: Optional[int] = Field(
        default=2, description="ID del estado de la inscripción. Por defecto 2 (inscrito)."
    )

class BulkClassroomRegistrationCreate(BaseModel):
    """Inscripción masiva de varios estudiantes en un mismo salón y año académico."""

    student_ids: List[int] = Field(description="IDs de los estudiantes que se desean inscribir.")
    academic_year_id: int = Field(description="ID del año académico al que corresponden las inscripciones.")
    classroom_id: int = Field(description="ID del salón en el que se inscribe a los estudiantes.")
    status_id: Optional[int] = Field(
        default=2, description="ID del estado de las inscripciones. Por defecto 2 (inscrito)."
    )

class StudentUpdateSchoolAdmin(StudentUpdate):
    """Campos extra para la actualización del estudiante por un administrador escolar.

    Extiende :class:`StudentUpdate` con la reasignación del representante.
    """

    representative_id: Optional[int] = Field(
        default=None, description="ID del representante al que se vincula el estudiante."
    )


class StudentHealthRead(BaseModel):
    """Estudiante tal como lo devuelve la búsqueda global (issue #66).

    Proviene de la vista ``student_health_status``, que solo contiene estudiantes
    inscritos en el año académico vigente. Expone los mismos campos de salud que
    el listado por salón (``bmi_status`` y ``has_active_medical_case``) y añade la
    ubicación del estudiante, imprescindible cuando el resultado ya no viene
    acotado a un salón conocido.

    No incluye peso ni talla en crudo: una búsqueda masiva no los necesita y
    mantenerlos fuera reduce la exposición de datos de salud en la respuesta. El
    detalle individual sigue siendo ``GET /school-admin/students/{student_id}``.
    """

    student_id: int = Field(description="Identificador único del estudiante.")
    school_id: int = Field(description="Escuela a la que pertenece el salón donde está inscrito.")
    name: str = Field(description="Nombre del estudiante.")
    lastname: str = Field(description="Apellido del estudiante.")
    full_name: str = Field(description="Nombre y apellido concatenados; es el campo sobre el que busca 'q'.")
    birthday: Optional[date] = Field(default=None, description="Fecha de nacimiento del estudiante.")
    gender: Optional[str] = Field(default=None, description="Género del estudiante.")
    blood_type: Optional[str] = Field(default=None, description="Tipo de sangre del estudiante.")
    identity_number: Optional[str] = Field(
        default=None, description="Documento de identidad del estudiante (cédula escolar), si aplica."
    )
    is_active: bool = Field(description="True si el estudiante está activo en la escuela.")
    classroom_id: int = Field(description="Salón del año académico vigente en el que está inscrito.")
    category: Optional[int] = Field(default=None, description="ID de la categoría del salón (ej. 1=PREESCOLAR).")
    level: Optional[int] = Field(default=None, description="Nivel o grado del salón.")
    section: Optional[str] = Field(default=None, description="Letra de la sección del salón.")
    academic_year_id: Optional[int] = Field(default=None, description="Año académico vigente del salón.")
    bmi: Optional[float] = Field(
        default=None,
        description="Índice de masa corporal de la medición vigente, redondeado a dos decimales. Null si no hay medición.",
    )
    bmi_status: BmiStatus = Field(
        description="Estado nutricional derivado del IMC. 'SIN DATOS' cuando el estudiante no tiene medición vigente."
    )
    has_active_medical_case: bool = Field(
        description="True si el estudiante tiene al menos un caso médico sin fecha de cierre."
    )
