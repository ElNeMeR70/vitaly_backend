"""
Tests for the Doctor Events endpoint and service layer:
  - GET /api/v1/doctor/events/by-month (get_events_by_month)

Adheres strictly to the 5 laws of vitaly-feature-testing:
  1. APIResponse envelope verification.
  2. Scope resolved server-side from the JWT 'sub' (doc_id), never from the client:
     only events with an active participation of that doctor are returned.
  3. Deterministic supabase-py mock.
  4. 200 with data=[] and total=0 for an empty month (never 404).
  5. RBAC matrix: 403 unauthorized role, 401 unauthenticated, 422 query validation.
"""
from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.api.deps import get_current_doctor, get_current_user_token
from app.core.security import create_access_token
from app.main import app
from app.services.auth_service import auth_service
from app.services.doctor_event_service import doctor_event_service

SERVICE_MODULE = "app.services.doctor_event_service"
BASE_URL = "/api/v1/doctor/events"
DOC_ID = 7
SCH_ADMIN_ID = 10
SCHOOL_ID = 1


# ──────────────────────────────────────────────────────────
# Helpers: realistic JWT payloads and chainable supabase-py mock
# ──────────────────────────────────────────────────────────

def doctor_payload(user_id: int = 20) -> dict:
    """Claims present in JWT for the doctor role (no school_id in JWT)."""
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


def make_query(data=None, count=None) -> MagicMock:
    """Chainable mock for supabase-py queries."""
    query = MagicMock()
    for method in (
        "select", "eq", "neq", "in_", "is_", "ilike", "gte", "lte", "lt",
        "order", "range", "limit", "insert", "update", "delete", "or_"
    ):
        getattr(query, method).return_value = query
    query.execute.return_value = MagicMock(data=[] if data is None else data, count=count)
    return query


def route_tables(mock_supabase: MagicMock, **tables: MagicMock) -> None:
    """Route supabase.table(...) calls to table-specific query mocks."""
    mock_supabase.table.side_effect = lambda name: tables.get(name, make_query([]))


def event_row(event_id: int, title: str, init_time: str) -> dict:
    """Row shaped like the 'event' columns selected by the service."""
    return {
        "id": event_id,
        "title": title,
        "description": "Revisión general",
        "init_time": init_time,
        "ent_time": init_time.replace("T09:", "T12:"),
        "created_at": "2026-03-01T10:00:00+00:00",
    }


# ──────────────────────────────────────────────────────────
# Fixtures
# ──────────────────────────────────────────────────────────

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
# 1. GET /by-month - Contrato del endpoint
# ──────────────────────────────────────────────────────────

def test_get_my_events_by_month_endpoint_success(doctor_client):
    """200 con envelope APIResponse y el contrato {data, year, month, total}."""
    fake_data = {
        "data": [
            {
                "id": 2,
                "title": "Evaluación Médica",
                "description": "Revisión general",
                "init_time": "2026-04-15T09:00:00+00:00",
                "ent_time": "2026-04-15T12:00:00+00:00",
                "created_at": "2026-03-01T10:00:00+00:00",
                "school_id": SCHOOL_ID,
                "school_name": "Colegio San Ignacio",
            }
        ],
        "year": 2026,
        "month": 4,
        "total": 1,
    }
    with patch.object(doctor_event_service, "get_events_by_month", return_value=fake_data) as mock_svc:
        response = doctor_client.get(f"{BASE_URL}/by-month?year=2026&month=4")

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert "Eventos del mes obtenidos exitosamente" in body["message"]
    assert body["data"]["year"] == 2026
    assert body["data"]["month"] == 4
    assert body["data"]["total"] == 1
    assert body["data"]["data"][0]["school_id"] == SCHOOL_ID
    assert body["data"]["data"][0]["school_name"] == "Colegio San Ignacio"
    # El user_id llega como int desde el claim 'sub' (string en el JWT).
    mock_svc.assert_called_once_with(user_id=20, year=2026, month=4)


def test_get_my_events_by_month_appears_in_openapi(doctor_client):
    """El endpoint queda documentado en OpenAPI con su envelope tipado."""
    schema = doctor_client.get("/openapi.json").json()
    operation = schema["paths"]["/api/v1/doctor/events/by-month"]["get"]

    assert operation["tags"] == ["Médico - Eventos"]
    assert {p["name"] for p in operation["parameters"]} == {"year", "month"}
    ref = operation["responses"]["200"]["content"]["application/json"]["schema"]["$ref"]
    assert ref.endswith("APIResponse_DoctorEventMonthRead_")
    event_props = schema["components"]["schemas"]["DoctorEventRead"]["properties"]
    assert "school_id" in event_props and "school_name" in event_props


# ──────────────────────────────────────────────────────────
# 2. Servicio: alcance resuelto en el servidor
# ──────────────────────────────────────────────────────────

def test_service_filters_by_resolved_doctor_id():
    """El doc_id se deriva del user_id y filtra 'event_participation'."""
    doctor = make_query([{"doc_id": DOC_ID}])
    participation = make_query([{"id_event": 2, "id_school_admin": SCH_ADMIN_ID}])
    event = make_query([event_row(2, "Evaluación Médica", "2026-04-15T09:00:00+00:00")])
    administrator = make_query([{"sch_admin_id": SCH_ADMIN_ID, "school_id": SCHOOL_ID}])
    school = make_query([{"sch_id": SCHOOL_ID, "name": "Colegio San Ignacio"}])

    with patch(f"{SERVICE_MODULE}.supabase") as mock_supabase:
        route_tables(
            mock_supabase,
            doctor=doctor,
            event_participation=participation,
            event=event,
            school_administrator=administrator,
            school=school,
        )
        result = doctor_event_service.get_events_by_month(user_id=20, year=2026, month=4)

    # El alcance sale del usuario autenticado, nunca de un parámetro del cliente.
    doctor.eq.assert_any_call("user_id", 20)
    participation.eq.assert_any_call("id_doctor", DOC_ID)
    participation.eq.assert_any_call("is_deleted", False)

    assert result["total"] == 1
    assert result["year"] == 2026 and result["month"] == 4
    assert result["data"][0]["school_id"] == SCHOOL_ID
    assert result["data"][0]["school_name"] == "Colegio San Ignacio"


def test_service_excludes_canceled_deleted_and_other_doctors_events():
    """Excluye cancelados, eliminados y los eventos de otros médicos."""
    doctor = make_query([{"doc_id": DOC_ID}])
    # Solo el evento 2 tiene participación de este médico; el 99 es de otro.
    participation = make_query([{"id_event": 2, "id_school_admin": SCH_ADMIN_ID}])
    event = make_query([event_row(2, "Evaluación Médica", "2026-04-15T09:00:00+00:00")])
    administrator = make_query([{"sch_admin_id": SCH_ADMIN_ID, "school_id": SCHOOL_ID}])
    school = make_query([{"sch_id": SCHOOL_ID, "name": "Colegio San Ignacio"}])

    with patch(f"{SERVICE_MODULE}.supabase") as mock_supabase:
        route_tables(
            mock_supabase,
            doctor=doctor,
            event_participation=participation,
            event=event,
            school_administrator=administrator,
            school=school,
        )
        result = doctor_event_service.get_events_by_month(user_id=20, year=2026, month=4)

    # Los eventos de otros médicos nunca entran en la consulta: el IN se construye
    # exclusivamente con las participaciones del doc_id resuelto.
    event.in_.assert_any_call("id", [2])
    event.eq.assert_any_call("is_canceled", False)
    event.eq.assert_any_call("is_deleted", False)
    # Ventana del mes solicitado.
    event.gte.assert_any_call("init_time", "2026-04-01T00:00:00+00:00")
    event.lt.assert_any_call("init_time", "2026-05-01T00:00:00+00:00")

    assert [e["id"] for e in result["data"]] == [2]


def test_service_resolves_school_per_event():
    """Cada evento se enriquece con la escuela del administrador que lo convocó."""
    doctor = make_query([{"doc_id": DOC_ID}])
    participation = make_query([
        {"id_event": 2, "id_school_admin": SCH_ADMIN_ID},
        {"id_event": 3, "id_school_admin": 11},
    ])
    event = make_query([
        event_row(2, "Evaluación Médica", "2026-04-15T09:00:00+00:00"),
        event_row(3, "Jornada de Vacunación", "2026-04-20T09:00:00+00:00"),
    ])
    administrator = make_query([
        {"sch_admin_id": SCH_ADMIN_ID, "school_id": SCHOOL_ID},
        {"sch_admin_id": 11, "school_id": 2},
    ])
    school = make_query([
        {"sch_id": SCHOOL_ID, "name": "Colegio San Ignacio"},
        {"sch_id": 2, "name": "Unidad Educativa Bolívar"},
    ])

    with patch(f"{SERVICE_MODULE}.supabase") as mock_supabase:
        route_tables(
            mock_supabase,
            doctor=doctor,
            event_participation=participation,
            event=event,
            school_administrator=administrator,
            school=school,
        )
        result = doctor_event_service.get_events_by_month(user_id=20, year=2026, month=4)

    schools = {e["id"]: (e["school_id"], e["school_name"]) for e in result["data"]}
    assert schools[2] == (SCHOOL_ID, "Colegio San Ignacio")
    assert schools[3] == (2, "Unidad Educativa Bolívar")


# ──────────────────────────────────────────────────────────
# 3. Mes vacío: 200 con data=[] y total=0 (nunca 404)
# ──────────────────────────────────────────────────────────

def test_get_my_events_by_month_without_participations_returns_empty(doctor_client):
    """Médico sin ninguna participación -> 200 con data=[] y total=0."""
    with patch(f"{SERVICE_MODULE}.supabase") as mock_supabase:
        route_tables(
            mock_supabase,
            doctor=make_query([{"doc_id": DOC_ID}]),
            event_participation=make_query([]),
        )
        response = doctor_client.get(f"{BASE_URL}/by-month?year=2026&month=12")

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert body["data"]["data"] == []
    assert body["data"]["total"] == 0
    assert body["data"]["year"] == 2026 and body["data"]["month"] == 12


def test_get_my_events_by_month_without_events_in_range_returns_empty(doctor_client):
    """Participaciones fuera del mes consultado -> 200 con data=[] y total=0."""
    with patch(f"{SERVICE_MODULE}.supabase") as mock_supabase:
        route_tables(
            mock_supabase,
            doctor=make_query([{"doc_id": DOC_ID}]),
            event_participation=make_query([{"id_event": 2, "id_school_admin": SCH_ADMIN_ID}]),
            event=make_query([]),
        )
        response = doctor_client.get(f"{BASE_URL}/by-month?year=2026&month=12")

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert body["data"]["data"] == []
    assert body["data"]["total"] == 0


# ──────────────────────────────────────────────────────────
# 4. RBAC & Autenticación
# ──────────────────────────────────────────────────────────

def test_doctor_events_unauthenticated_returns_401(client):
    """Sin credenciales -> 401."""
    response = client.get(f"{BASE_URL}/by-month?year=2026&month=4")
    assert response.status_code == 401


def test_doctor_events_invalid_jwt_returns_401(client):
    """Token malformado -> 401."""
    response = client.get(
        f"{BASE_URL}/by-month?year=2026&month=4",
        headers={"Authorization": "Bearer jwt-malformado"}
    )
    assert response.status_code == 401


def test_doctor_events_revoked_jwt_returns_401(client):
    """Token revocado -> 401."""
    token = create_access_token({"sub": "20", "role": "doctor", "scope": "client"})
    with patch.object(auth_service, "is_token_revoked", return_value=True):
        response = client.get(
            f"{BASE_URL}/by-month?year=2026&month=4",
            headers={"Authorization": f"Bearer {token}"}
        )
    assert response.status_code == 401


@pytest.mark.parametrize("role,scope", [
    ("admin_escuela", "client"),
    ("representante", "client"),
])
def test_doctor_events_wrong_role_returns_403(client, role, scope):
    """Rol no autorizado -> 403 Forbidden con envelope."""
    app.dependency_overrides[get_current_user_token] = lambda: {
        "sub": "4",
        "role": role,
        "scope": scope,
    }
    response = client.get(f"{BASE_URL}/by-month?year=2026&month=4")
    assert response.status_code == 403
    assert response.json()["success"] is False


def test_doctor_without_profile_returns_403(doctor_client):
    """Usuario con rol doctor pero sin fila en 'doctor' -> 403 Forbidden."""
    with patch(f"{SERVICE_MODULE}.supabase") as mock_supabase:
        route_tables(mock_supabase, doctor=make_query([]))
        response = doctor_client.get(f"{BASE_URL}/by-month?year=2026&month=4")

    assert response.status_code == 403
    body = response.json()
    assert body["success"] is False
    assert "no tiene un perfil de médico" in body["message"]


def test_service_doctor_without_profile_raises_403():
    """El servicio levanta 403 directamente cuando no hay perfil de médico."""
    with patch(f"{SERVICE_MODULE}.supabase") as mock_supabase:
        route_tables(mock_supabase, doctor=make_query([]))
        with pytest.raises(HTTPException) as exc:
            doctor_event_service.get_events_by_month(user_id=999, year=2026, month=4)

    assert exc.value.status_code == 403
    assert "perfil de médico" in exc.value.detail


# ──────────────────────────────────────────────────────────
# 5. Validación de parámetros (422)
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
def test_doctor_events_validation_errors(doctor_client, invalid_url):
    """Parámetros ausentes o fuera de rango devuelven 422 con formato estándar."""
    response = doctor_client.get(invalid_url)
    assert response.status_code == 422
    body = response.json()
    assert body["success"] is False
    assert body["errors"]


# ──────────────────────────────────────────────────────────
# 6. Errores inesperados
# ──────────────────────────────────────────────────────────

def test_unexpected_supabase_error_is_not_swallowed(doctor_client):
    """Un fallo del cliente supabase no devuelve un 200 con datos parciales."""
    broken = make_query([])
    broken.execute.side_effect = Exception("postgrest down")

    with patch(f"{SERVICE_MODULE}.supabase") as mock_supabase:
        route_tables(mock_supabase, doctor=broken)
        with pytest.raises(Exception, match="postgrest down"):
            doctor_client.get(f"{BASE_URL}/by-month?year=2026&month=4")
