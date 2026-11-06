"""
Tests for the Doctor Events endpoint and service layer:
  - GET /api/v1/doctor/events/by-month (get_events_by_month)

Adheres strictly to the 5 laws of vitaly-feature-testing:
  1. APIResponse envelope verification, including the 500 path.
  2. Scope resolved server-side from the JWT 'sub': a doctor sees an event only
     when they hold an active participation AND an active doctor_to_school link
     with the convoking school.
  3. Deterministic supabase-py fake.
  4. 200 with data=[] and total=0 for an empty month (never 404).
  5. RBAC matrix: 403 unauthorized role, 401 unauthenticated, 422 query validation.

Why `tests.fake_supabase` instead of MagicMock: a stub that returns a canned
list proves only that a filter method was *called*, so mutating the month
length, the sort order or an `.in_()` left the suite green in the first version
of this file. The fake applies the filters over in-memory rows, so a dropped or
inverted filter changes the result and fails.
"""
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.api.deps import get_current_doctor, get_current_user_token
from app.core.security import create_access_token
from app.main import app
from app.services.auth_service import auth_service
from tests.fake_supabase import FakeSupabase
from app.services.doctor_event_service import (
    ACTIVE_DOCTOR_SCHOOL_STATUS,
    doctor_event_service,
)

SERVICE_MODULE = "app.services.doctor_event_service"
BASE_URL = "/api/v1/doctor/events"

USER_ID = 20
DOC_ID = 7
OTHER_DOC_ID = 8
SCH_ADMIN_ID = 10
OTHER_SCH_ADMIN_ID = 11
SCHOOL_ID = 1
OTHER_SCHOOL_ID = 2
SUSPENDED_STATUS = 1


# ──────────────────────────────────────────────────────────
# Filas de prueba (el falso cliente vive en tests/fake_supabase.py)
# ──────────────────────────────────────────────────────────

def event_row(event_id, init_time, *, title="Evaluación Médica",
              is_canceled=False, is_deleted=False, ent_time=..., **extra):
    """Row shaped like the real 'event' table; defaults to a healthy event."""
    row = {
        "id": event_id,
        "title": title,
        "description": "Revisión general",
        "init_time": init_time,
        "ent_time": init_time.replace("T09:", "T12:") if ent_time is ... else ent_time,
        "created_at": "2026-03-01T10:00:00+00:00",
        "is_canceled": is_canceled,
        "is_deleted": is_deleted,
    }
    row.update(extra)
    return row


def participation_row(event_id, *, doctor_id=DOC_ID, sch_admin_id=SCH_ADMIN_ID,
                      is_canceled=False, is_deleted=False):
    """Row shaped like the real 'event_participation' table."""
    return {
        "id_event": event_id,
        "id_doctor": doctor_id,
        "id_school_admin": sch_admin_id,
        "is_canceled": is_canceled,
        "is_deleted": is_deleted,
    }


#: Filas de una segunda médica, presentes en TODA base de prueba. Sin ellas, las
#: tablas que se filtran por identidad contienen una sola fila y el filtro
#: '.eq("doctor_id", ...)' se puede borrar sin que falle un solo test.
OTHER_DOCTOR_ROWS = [{"doc_id": OTHER_DOC_ID, "user_id": 21}]
OTHER_DOCTOR_LINKS = [
    {"doctor_id": OTHER_DOC_ID, "school_id": SCHOOL_ID, "status": ACTIVE_DOCTOR_SCHOOL_STATUS},
    {"doctor_id": OTHER_DOC_ID, "school_id": OTHER_SCHOOL_ID, "status": ACTIVE_DOCTOR_SCHOOL_STATUS},
]


def build_db(events, participations, *, links=None, administrators=None, schools=None):
    """Assembles a coherent fake database around the rows under test.

    Las tablas 'doctor' y 'doctor_to_school' incluyen siempre las filas de una
    segunda médica, para que un filtro por identidad que se pierda cambie el
    resultado en lugar de pasar desapercibido.
    """
    return FakeSupabase(
        doctor=[{"doc_id": DOC_ID, "user_id": USER_ID}] + OTHER_DOCTOR_ROWS,
        doctor_to_school=(links if links is not None else [
            {"doctor_id": DOC_ID, "school_id": SCHOOL_ID, "status": ACTIVE_DOCTOR_SCHOOL_STATUS},
        ]) + OTHER_DOCTOR_LINKS,
        event_participation=participations,
        event=events,
        school_administrator=administrators if administrators is not None else [
            {"sch_admin_id": SCH_ADMIN_ID, "school_id": SCHOOL_ID},
            {"sch_admin_id": OTHER_SCH_ADMIN_ID, "school_id": OTHER_SCHOOL_ID},
        ],
        school=schools if schools is not None else [
            {"sch_id": SCHOOL_ID, "name": "Colegio San Ignacio"},
            {"sch_id": OTHER_SCHOOL_ID, "name": "Unidad Educativa Bolívar"},
        ],
    )


def run_by_month(fake, year=2026, month=4, user_id=USER_ID):
    """Calls the service against a fake database."""
    with patch(f"{SERVICE_MODULE}.supabase", fake):
        return doctor_event_service.get_events_by_month(user_id=user_id, year=year, month=month)


# ──────────────────────────────────────────────────────────
# Fixtures
# ──────────────────────────────────────────────────────────

def doctor_payload(user_id: int = USER_ID) -> dict:
    """Claims present in the JWT for the doctor role (no school_id in JWT)."""
    return {
        "sub": str(user_id),
        "email": "dr.vargas@vitaly.app",
        "role": "doctor",
        "scope": "client",
        "name": "Ricardo Vargas",
        "gender": "MASCULINO",
        "especially": "Pediatría",
        "doc_license_number": "MPPS-12345",
    }


@pytest.fixture
def doctor_client():
    """Client with get_current_doctor overridden."""
    app.dependency_overrides[get_current_doctor] = lambda: doctor_payload()
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


@pytest.fixture
def client():
    """Client with no overrides: real authentication/RBAC gates run."""
    app.dependency_overrides.clear()
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


# ──────────────────────────────────────────────────────────
# 1. Contrato del endpoint y OpenAPI
# ──────────────────────────────────────────────────────────

def test_endpoint_returns_envelope_and_month_contract(doctor_client):
    """200 con envelope APIResponse y el contrato {data, year, month, total}."""
    fake = build_db(
        events=[event_row(2, "2026-04-15T09:00:00+00:00")],
        participations=[participation_row(2)],
    )
    with patch(f"{SERVICE_MODULE}.supabase", fake):
        response = doctor_client.get(f"{BASE_URL}/by-month?year=2026&month=4")

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert "Eventos del mes obtenidos exitosamente" in body["message"]
    assert body["errors"] is None
    assert body["data"]["year"] == 2026 and body["data"]["month"] == 4
    assert body["data"]["total"] == 1
    event = body["data"]["data"][0]
    assert event["id"] == 2
    assert event["school_id"] == SCHOOL_ID
    assert event["school_name"] == "Colegio San Ignacio"
    # El envelope no expone columnas internas del evento.
    assert "is_canceled" not in event and "is_deleted" not in event


def test_endpoint_passes_the_authenticated_user_id(doctor_client):
    """El handler deriva user_id del claim 'sub' (string) y lo pasa como int."""
    with patch.object(doctor_event_service, "get_events_by_month",
                      return_value={"data": [], "year": 2026, "month": 4, "total": 0}) as svc:
        doctor_client.get(f"{BASE_URL}/by-month?year=2026&month=4")
    svc.assert_called_once_with(user_id=USER_ID, year=2026, month=4)


def test_endpoint_appears_in_openapi(doctor_client):
    """El endpoint queda documentado en OpenAPI con su envelope tipado."""
    schema = doctor_client.get("/openapi.json").json()
    operation = schema["paths"]["/api/v1/doctor/events/by-month"]["get"]

    assert operation["tags"] == ["Médico - Eventos"]
    assert {p["name"] for p in operation["parameters"]} == {"year", "month"}
    ref = operation["responses"]["200"]["content"]["application/json"]["schema"]["$ref"]
    assert ref.endswith("APIResponse_DoctorEventMonthRead_")
    event_props = schema["components"]["schemas"]["DoctorEventRead"]["properties"]
    assert "school_id" in event_props and "school_name" in event_props
    assert set(schema["components"]["schemas"]["DoctorEventMonthRead"]["required"]) == {
        "data", "year", "month", "total"
    }


# ──────────────────────────────────────────────────────────
# 2. Alcance: participación activa del médico autenticado
# ──────────────────────────────────────────────────────────

def test_excludes_other_doctors_events():
    """Un evento con participación de OTRO médico no entra en la agenda."""
    result = run_by_month(build_db(
        events=[
            event_row(2, "2026-04-15T09:00:00+00:00"),
            event_row(99, "2026-04-16T09:00:00+00:00", title="Evento ajeno"),
        ],
        participations=[
            participation_row(2),
            participation_row(99, doctor_id=OTHER_DOC_ID),
        ],
    ))
    assert [e["id"] for e in result["data"]] == [2]
    assert result["total"] == 1


def test_excludes_canceled_and_deleted_events():
    """Los eventos cancelados y los eliminados quedan fuera."""
    result = run_by_month(build_db(
        events=[
            event_row(2, "2026-04-15T09:00:00+00:00"),
            event_row(3, "2026-04-16T09:00:00+00:00", is_canceled=True),
            event_row(4, "2026-04-17T09:00:00+00:00", is_deleted=True),
        ],
        participations=[participation_row(2), participation_row(3), participation_row(4)],
    ))
    assert [e["id"] for e in result["data"]] == [2]


def test_excludes_deleted_participations():
    """Una participación eliminada deja de dar acceso al evento.

    No hay caso para 'participation.is_canceled': 'cancel_event' cancela el
    evento junto con sus participaciones, así que el filtro sobre 'event' ya lo
    cubre, y el servicio no lee esa columna (ver el comentario en
    'get_events_by_month').
    """
    result = run_by_month(build_db(
        events=[
            event_row(2, "2026-04-15T09:00:00+00:00"),
            event_row(3, "2026-04-17T09:00:00+00:00"),
        ],
        participations=[
            participation_row(2),
            participation_row(3, is_deleted=True),
        ],
    ))
    assert [e["id"] for e in result["data"]] == [2]


def test_duplicate_participations_yield_one_event():
    """Varias participaciones del mismo médico sobre un evento no lo duplican."""
    result = run_by_month(build_db(
        events=[event_row(2, "2026-04-15T09:00:00+00:00")],
        participations=[participation_row(2), participation_row(2)],
    ))
    assert [e["id"] for e in result["data"]] == [2]
    assert result["total"] == 1


def test_doctor_id_is_derived_from_the_authenticated_user():
    """El doc_id sale del user_id; otro usuario no hereda la agenda."""
    fake = build_db(
        events=[event_row(2, "2026-04-15T09:00:00+00:00")],
        participations=[participation_row(2)],
    )
    assert run_by_month(fake, user_id=USER_ID)["total"] == 1
    with pytest.raises(HTTPException) as exc:
        run_by_month(fake, user_id=999)
    assert exc.value.status_code == 403


# ──────────────────────────────────────────────────────────
# 3. Alcance: vínculo vigente con la escuela (doctor_to_school)
# ──────────────────────────────────────────────────────────

def test_suspended_school_link_hides_its_events():
    """Un médico suspendido deja de ver los eventos de esa escuela."""
    fake = build_db(
        events=[
            event_row(2, "2026-04-15T09:00:00+00:00"),
            event_row(3, "2026-04-16T09:00:00+00:00", title="Otra escuela"),
        ],
        participations=[
            participation_row(2),
            participation_row(3, sch_admin_id=OTHER_SCH_ADMIN_ID),
        ],
        links=[
            {"doctor_id": DOC_ID, "school_id": SCHOOL_ID, "status": SUSPENDED_STATUS},
            {"doctor_id": DOC_ID, "school_id": OTHER_SCHOOL_ID,
             "status": ACTIVE_DOCTOR_SCHOOL_STATUS},
        ],
    )
    result = run_by_month(fake)
    # La participación histórica en la escuela 1 sobrevive; la visibilidad no.
    assert [e["id"] for e in result["data"]] == [3]
    assert result["data"][0]["school_id"] == OTHER_SCHOOL_ID


def test_another_doctors_active_link_does_not_grant_access():
    """El vínculo vigente de OTRA médica con la misma escuela no abre la agenda.

    Sin esta comprobación, borrar el '.eq("doctor_id", ...)' de
    '_get_active_school_ids' degrada la lista blanca a "cualquier escuela que
    tenga alguna médica vigente" y el suspendido recupera el acceso.
    """
    fake = build_db(
        events=[event_row(2, "2026-04-15T09:00:00+00:00")],
        participations=[participation_row(2)],
        # La médica 7 está suspendida en la escuela 1; la 8 sigue vigente allí
        # (la añade build_db). Solo cuenta el vínculo de quien consulta.
        links=[{"doctor_id": DOC_ID, "school_id": SCHOOL_ID, "status": SUSPENDED_STATUS}],
    )
    assert run_by_month(fake) == {"data": [], "year": 2026, "month": 4, "total": 0}


def test_another_doctors_profile_does_not_resolve_the_doc_id():
    """El doc_id se resuelve por user_id; la fila de otra médica no sirve."""
    fake = build_db(
        events=[event_row(2, "2026-04-15T09:00:00+00:00")],
        participations=[participation_row(2, doctor_id=OTHER_DOC_ID)],
    )
    # El usuario autenticado es el 20 (doc_id 7); la participación es de la 8.
    assert run_by_month(fake)["total"] == 0


def test_doctor_without_any_active_link_sees_nothing():
    """Sin ningún vínculo vigente la agenda va vacía, no 404."""
    result = run_by_month(build_db(
        events=[event_row(2, "2026-04-15T09:00:00+00:00")],
        participations=[participation_row(2)],
        links=[],
    ))
    assert result == {"data": [], "year": 2026, "month": 4, "total": 0}


def test_event_without_resolvable_school_is_dropped():
    """Sin escuela resoluble no se puede comprobar el vínculo: el evento no se devuelve."""
    # (a) participación sin administrador convocante
    orphan = run_by_month(build_db(
        events=[event_row(2, "2026-04-15T09:00:00+00:00")],
        participations=[participation_row(2, sch_admin_id=None)],
    ))
    assert orphan["data"] == [] and orphan["total"] == 0

    # (b) administrador sin fila en school_administrator
    missing_admin = run_by_month(build_db(
        events=[event_row(2, "2026-04-15T09:00:00+00:00")],
        participations=[participation_row(2)],
        administrators=[],
    ))
    assert missing_admin["data"] == [] and missing_admin["total"] == 0


def test_event_resolves_its_school_from_any_participation_that_has_one():
    """Con dos participaciones del mismo evento, una sin administrador, gana la que sí lo tiene."""
    result = run_by_month(build_db(
        events=[event_row(2, "2026-04-15T09:00:00+00:00")],
        participations=[
            participation_row(2, sch_admin_id=None),   # llega primero
            participation_row(2, sch_admin_id=SCH_ADMIN_ID),
        ],
    ))
    assert result["total"] == 1
    assert result["data"][0]["school_id"] == SCHOOL_ID


def test_school_name_may_be_null_without_hiding_the_event():
    """Una escuela sin nombre degrada 'school_name'; no oculta el evento."""
    result = run_by_month(build_db(
        events=[event_row(2, "2026-04-15T09:00:00+00:00")],
        participations=[participation_row(2)],
        schools=[{"sch_id": SCHOOL_ID, "name": None}],
    ))
    assert result["total"] == 1
    assert result["data"][0]["school_id"] == SCHOOL_ID
    assert result["data"][0]["school_name"] is None


def test_each_event_carries_its_own_school():
    """Con dos escuelas vigentes, cada evento se etiqueta con la suya."""
    result = run_by_month(build_db(
        events=[
            event_row(2, "2026-04-15T09:00:00+00:00"),
            event_row(3, "2026-04-20T09:00:00+00:00"),
        ],
        participations=[
            participation_row(2),
            participation_row(3, sch_admin_id=OTHER_SCH_ADMIN_ID),
        ],
        links=[
            {"doctor_id": DOC_ID, "school_id": SCHOOL_ID, "status": ACTIVE_DOCTOR_SCHOOL_STATUS},
            {"doctor_id": DOC_ID, "school_id": OTHER_SCHOOL_ID,
             "status": ACTIVE_DOCTOR_SCHOOL_STATUS},
        ],
    ))
    by_id = {e["id"]: (e["school_id"], e["school_name"]) for e in result["data"]}
    assert by_id == {
        2: (SCHOOL_ID, "Colegio San Ignacio"),
        3: (OTHER_SCHOOL_ID, "Unidad Educativa Bolívar"),
    }


# ──────────────────────────────────────────────────────────
# 4. Ventana del mes
# ──────────────────────────────────────────────────────────

def test_month_window_is_closed_at_the_start_and_open_at_the_end():
    """Incluye el primer instante y el último segundo del mes; excluye el siguiente."""
    events = [
        event_row(1, "2026-03-31T23:59:59+00:00", title="Marzo"),
        event_row(2, "2026-04-01T00:00:00+00:00", title="Primer instante"),
        event_row(3, "2026-04-30T23:59:59+00:00", title="Último segundo"),
        event_row(4, "2026-05-01T00:00:00+00:00", title="Mayo"),
    ]
    parts = [participation_row(e["id"]) for e in events]
    result = run_by_month(build_db(events=events, participations=parts), year=2026, month=4)
    assert [e["id"] for e in result["data"]] == [2, 3]


@pytest.mark.parametrize("year,month,inside,outside", [
    # Febrero bisiesto: el día 29 existe y entra.
    (2024, 2, "2024-02-29T23:00:00+00:00", "2024-03-01T00:00:00+00:00"),
    # Febrero no bisiesto: el mes cierra el día 28.
    (2026, 2, "2026-02-28T23:00:00+00:00", "2026-03-01T00:00:00+00:00"),
    # Mes de 31 días.
    (2026, 1, "2026-01-31T23:00:00+00:00", "2026-02-01T00:00:00+00:00"),
    # Diciembre cruza de año correctamente.
    (2026, 12, "2026-12-31T23:00:00+00:00", "2027-01-01T00:00:00+00:00"),
])
def test_month_window_respects_real_month_lengths(year, month, inside, outside):
    """La ventana se calcula por mes real, no con una longitud fija."""
    events = [event_row(1, inside, title="dentro"), event_row(2, outside, title="fuera")]
    parts = [participation_row(1), participation_row(2)]
    result = run_by_month(build_db(events=events, participations=parts), year=year, month=month)
    assert [e["id"] for e in result["data"]] == [1]
    assert result["year"] == year and result["month"] == month


def test_events_are_ordered_chronologically():
    """La agenda sale ascendente por init_time aunque la tabla no esté ordenada."""
    events = [
        event_row(1, "2026-04-20T09:00:00+00:00"),
        event_row(2, "2026-04-05T09:00:00+00:00"),
        event_row(3, "2026-04-12T09:00:00+00:00"),
    ]
    parts = [participation_row(e["id"]) for e in events]
    result = run_by_month(build_db(events=events, participations=parts))
    assert [e["id"] for e in result["data"]] == [2, 3, 1]


# ──────────────────────────────────────────────────────────
# 5. Mes vacío: 200 con data=[] y total=0 (nunca 404)
# ──────────────────────────────────────────────────────────

@pytest.mark.parametrize("events,participations", [
    pytest.param([], [], id="sin-participaciones"),
    pytest.param([event_row(2, "2026-11-15T09:00:00+00:00")], [participation_row(2)],
                 id="eventos-fuera-del-mes"),
])
def test_empty_month_returns_empty_list(doctor_client, events, participations):
    """Un mes sin eventos visibles responde 200 con data=[] y total=0."""
    fake = build_db(events=events, participations=participations)
    with patch(f"{SERVICE_MODULE}.supabase", fake):
        response = doctor_client.get(f"{BASE_URL}/by-month?year=2026&month=4")

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert body["data"]["data"] == []
    assert body["data"]["total"] == 0
    assert body["data"]["year"] == 2026 and body["data"]["month"] == 4


# ──────────────────────────────────────────────────────────
# 6. Filas degradadas no tumban el mes entero
# ──────────────────────────────────────────────────────────

@pytest.mark.parametrize("broken", [
    pytest.param({"title": None}, id="title-nulo"),
    pytest.param({"ent_time": None}, id="ent_time-nulo"),
])
def test_degraded_row_does_not_break_the_month(doctor_client, broken):
    """Una fila heredada con campos a nulo se devuelve degradada, no como 500."""
    events = [
        event_row(2, "2026-04-15T09:00:00+00:00", **broken),
        event_row(3, "2026-04-16T09:00:00+00:00", title="Evento sano"),
    ]
    fake = build_db(events=events, participations=[participation_row(2), participation_row(3)])
    with patch(f"{SERVICE_MODULE}.supabase", fake):
        response = doctor_client.get(f"{BASE_URL}/by-month?year=2026&month=4")

    assert response.status_code == 200
    body = response.json()
    assert body["data"]["total"] == 2
    assert body["data"]["data"][0][next(iter(broken))] is None
    assert body["data"]["data"][1]["title"] == "Evento sano"


# ──────────────────────────────────────────────────────────
# 7. Errores inesperados: 500 CON envelope
# ──────────────────────────────────────────────────────────

@pytest.mark.parametrize("failing_table", [
    "doctor", "doctor_to_school", "event_participation", "event", "school_administrator", "school",
])
def test_supabase_failure_returns_enveloped_500(doctor_client, failing_table):
    """Un fallo de supabase se traduce a 500 con el envelope estándar."""
    fake = build_db(
        events=[event_row(2, "2026-04-15T09:00:00+00:00")],
        participations=[participation_row(2)],
    )
    fake.failing_table = failing_table

    with patch(f"{SERVICE_MODULE}.supabase", fake):
        response = doctor_client.get(f"{BASE_URL}/by-month?year=2026&month=4")

    assert response.status_code == 500
    body = response.json()
    assert body["success"] is False
    assert "agenda de eventos" in body["message"]
    assert body["data"] is None


# ──────────────────────────────────────────────────────────
# 8. RBAC & Autenticación
# ──────────────────────────────────────────────────────────

def test_unauthenticated_returns_401(client):
    """Sin credenciales -> 401."""
    assert client.get(f"{BASE_URL}/by-month?year=2026&month=4").status_code == 401


def test_invalid_jwt_returns_401(client):
    """Token malformado -> 401."""
    response = client.get(
        f"{BASE_URL}/by-month?year=2026&month=4",
        headers={"Authorization": "Bearer jwt-malformado"},
    )
    assert response.status_code == 401


def test_revoked_jwt_returns_401(client):
    """Token revocado -> 401."""
    token = create_access_token({"sub": str(USER_ID), "role": "doctor", "scope": "client"})
    with patch.object(auth_service, "is_token_revoked", return_value=True):
        response = client.get(
            f"{BASE_URL}/by-month?year=2026&month=4",
            headers={"Authorization": f"Bearer {token}"},
        )
    assert response.status_code == 401


def test_token_without_scope_claim_returns_401(client):
    """Token anterior a la separación de portales (sin 'scope') -> 401."""
    app.dependency_overrides[get_current_user_token] = lambda: {"sub": "20", "role": "doctor"}
    response = client.get(f"{BASE_URL}/by-month?year=2026&month=4")
    assert response.status_code == 401
    assert response.json()["success"] is False


@pytest.mark.parametrize("role,scope", [
    ("admin_escuela", "client"),
    ("representante", "client"),
    ("admin_sistema", "admin"),  # token del otro portal
])
def test_wrong_role_returns_403(client, role, scope):
    """Rol no autorizado -> 403 Forbidden con envelope."""
    app.dependency_overrides[get_current_user_token] = lambda: {
        "sub": "4", "role": role, "scope": scope,
    }
    response = client.get(f"{BASE_URL}/by-month?year=2026&month=4")
    assert response.status_code == 403
    assert response.json()["success"] is False


def test_doctor_without_profile_returns_403(doctor_client):
    """Usuario con rol doctor pero sin fila en 'doctor' -> 403 Forbidden."""
    fake = build_db(events=[], participations=[])
    fake.db["doctor"] = []
    with patch(f"{SERVICE_MODULE}.supabase", fake):
        response = doctor_client.get(f"{BASE_URL}/by-month?year=2026&month=4")

    assert response.status_code == 403
    body = response.json()
    assert body["success"] is False
    assert "no tiene un perfil de médico" in body["message"]


# ──────────────────────────────────────────────────────────
# 9. Validación de parámetros (422)
# ──────────────────────────────────────────────────────────

@pytest.mark.parametrize("invalid_url", [
    f"{BASE_URL}/by-month",                     # year y month obligatorios
    f"{BASE_URL}/by-month?year=2026",           # falta month
    f"{BASE_URL}/by-month?month=4",             # falta year
    f"{BASE_URL}/by-month?year=2015&month=5",   # year ge=2020
    f"{BASE_URL}/by-month?year=2101&month=5",   # year le=2100
    f"{BASE_URL}/by-month?year=2026&month=0",   # month ge=1
    f"{BASE_URL}/by-month?year=2026&month=13",  # month le=12
    f"{BASE_URL}/by-month?year=abc&month=4",    # year no numérico
])
def test_validation_errors(doctor_client, invalid_url):
    """Parámetros ausentes o fuera de rango devuelven 422 con formato estándar."""
    response = doctor_client.get(invalid_url)
    assert response.status_code == 422
    body = response.json()
    assert body["success"] is False
    assert body["errors"]
