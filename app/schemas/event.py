from pydantic import BaseModel, Field, field_validator
from typing import Optional, List
from datetime import datetime


class EventCreate(BaseModel):
    """Esquema para la creación de un evento con sus participaciones."""

    title: str = Field(description="Título del evento mostrado en el calendario escolar.")
    description: Optional[str] = Field(
        default=None, description="Descripción o detalle opcional del evento."
    )
    init_time: datetime = Field(description="Fecha y hora de inicio del evento (ISO 8601).")
    ent_time: datetime = Field(
        description="Fecha y hora de finalización del evento. Debe ser posterior a 'init_time'."
    )
    classroom_ids: List[int] = Field(
        description="IDs de los salones convocados al evento. Debe incluir al menos uno."
    )
    doctor_ids: List[int] = Field(
        description="IDs de los médicos asignados al evento. Debe incluir al menos uno."
    )

    @field_validator("ent_time")
    @classmethod
    def validate_end_after_start(cls, v, info):
        if "init_time" in info.data and v <= info.data["init_time"]:
            raise ValueError("La fecha de finalización debe ser posterior a la de inicio.")
        return v

    @field_validator("classroom_ids")
    @classmethod
    def validate_classrooms_not_empty(cls, v):
        if not v:
            raise ValueError("Debe incluir al menos un salón en el evento.")
        return v

    @field_validator("doctor_ids")
    @classmethod
    def validate_doctors_not_empty(cls, v):
        if not v:
            raise ValueError("Debe incluir al menos un doctor en el evento.")
        return v


class EventUpdate(BaseModel):
    """Esquema para la actualización parcial de un evento (solo datos base).

    Los campos omitidos conservan su valor actual; las participaciones de salones y
    médicos se gestionan en endpoints dedicados.
    """

    title: Optional[str] = Field(default=None, description="Nuevo título del evento.")
    description: Optional[str] = Field(default=None, description="Nueva descripción del evento.")
    init_time: Optional[datetime] = Field(
        default=None, description="Nueva fecha y hora de inicio del evento (ISO 8601)."
    )
    ent_time: Optional[datetime] = Field(
        default=None, description="Nueva fecha y hora de finalización del evento (ISO 8601)."
    )


class DoctorEventRead(BaseModel):
    """Evento vigente en el que el médico autenticado tiene una participación activa.

    Resuelve la escuela a la que pertenece el evento (a través del administrador
    que lo convocó) para que el calendario del médico pueda agrupar o filtrar por
    institución sin una segunda llamada.
    """

    id: int = Field(description="Identificador único del evento.")
    title: Optional[str] = Field(
        default=None,
        description=(
            "Título del evento mostrado en el calendario. Declarado opcional a "
            "propósito: una fila heredada con el título a nulo degrada ese evento "
            "en lugar de tumbar con un 500 el mes entero del médico."
        ),
    )
    description: Optional[str] = Field(
        default=None, description="Descripción o detalle del evento; nulo si no se registró."
    )
    init_time: datetime = Field(
        description=(
            "Fecha y hora de inicio del evento (ISO 8601). Siempre presente: la "
            "ventana del mes filtra por esta columna, así que una fila sin ella "
            "nunca entra en el resultado."
        )
    )
    ent_time: Optional[datetime] = Field(
        default=None,
        description="Fecha y hora de finalización del evento (ISO 8601); opcional por el mismo motivo que 'title'.",
    )
    created_at: Optional[datetime] = Field(
        default=None, description="Fecha y hora de creación del registro del evento."
    )
    school_id: int = Field(
        description=(
            "ID de la escuela que convocó el evento. Siempre presente: un evento "
            "cuya escuela no se puede resolver, o con la que el médico ya no tiene "
            "vínculo vigente, no se devuelve."
        )
    )
    school_name: Optional[str] = Field(
        default=None,
        description="Nombre de la escuela que convocó el evento; nulo solo si la ficha de la escuela no tiene nombre.",
    )


class DoctorEventMonthRead(BaseModel):
    """Eventos de un mes completo para el médico autenticado.

    Replica el contrato de 'GET /school-admin/events/by-month': el mes se devuelve
    entero, sin paginar por offset, porque alimenta una vista de calendario.
    """

    data: List[DoctorEventRead] = Field(
        description="Eventos del mes ordenados cronológicamente por 'init_time'; vacío si no hay ninguno."
    )
    year: int = Field(description="Año consultado, repetido para que el cliente valide la ventana recibida.")
    month: int = Field(description="Mes consultado (1-12), repetido junto con 'year'.")
    total: int = Field(description="Cantidad de eventos devueltos en 'data'.")
