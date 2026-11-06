"""
Tests for School Admin Events endpoints and service layer:
  - POST /api/v1/school-admin/events/ (create_event)
  - GET /api/v1/school-admin/events/ (get_events)
  - GET /api/v1/school-admin/events/by-month (get_events_by_month)
  - GET /api/v1/school-admin/events/{event_id} (get_event_detail)
  - PATCH /api/v1/school-admin/events/{event_id}/cancel (cancel_event)
  - PUT /api/v1/school-admin/events/{event_id} (update_event)

Adheres strictly to the 5 laws of vitaly-feature-testing:
  1. APIResponse envelope verification.
  2. Multi-tenancy isolation via server-resolved school_id (403 foreign school / 400 foreign classrooms or doctors).
  3. Deterministic supabase-py mock.
  4. 404 for missing single entities / 200 with empty list for empty collections.
  5. RBAC matrix: 403 unauthorized role, 401 unauthenticated, 422 schema validation.
"""
try:
    from datetime import UTC
except ImportError:
    from datetime import timezone
    UTC = timezone.utc  # noqa: UP017
from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.api.deps import get_current_admin_escuela, get_current_user_token
from app.core.security import create_access_token
from app.main import app
from app.services.auth_service import auth_service
from app.services.school_admin_event_service import school_admin_event_service
from tests.fake_supabase import FakeSupabase

SERVICE_MODULE = "app.services.school_admin_event_service"
BASE_URL = "/api/v1/school-admin/events"
SCHOOL_ID = 1
ADMIN_RECORD = {"sch_admin_id": 10, "school_id": SCHOOL_ID}


# ──────────────────────────────────────────────────────────
# Helpers: realistic JWT payloads and chainable supabase-py mock
# ──────────────────────────────────────────────────────────

def admin_payload(user_id: int = 4) -> dict:
    """Claims present in JWT for admin_escuela (no school_id in JWT)."""
    return {
        "sub": str(user_id),
        "email": "admin@colegio.test",
        "role": "admin_escuela",
        "scope": "client",
        "name": "Laura Pérez",
        "gender": "FEMENINO",
        "administrative_position": "Directora",
        "school": "Colegio San Ignacio",
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


# ──────────────────────────────────────────────────────────
# Fixtures
# ──────────────────────────────────────────────────────────

@pytest.fixture
def admin_client():
    """Client with get_current_admin_escuela overridden."""
    app.dependency_overrides[get_current_admin_escuela] = lambda: admin_payload()
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
# 1. POST / - Crear Evento
# ──────────────────────────────────────────────────────────

def test_create_event_endpoint_success(admin_client):
    """Verifica respuesta 200 con envelope para creación de evento."""
    fake_data = {
        "event_id": 5,
        "title": "Jornada de Vacunación",
        "classrooms_linked": 2,
        "doctors_linked": 1
    }
    payload = {
        "title": "Jornada de Vacunación",
        "description": "Vacunación general",
        "init_time": "2026-05-10T08:00:00Z",
        "ent_time": "2026-05-10T12:00:00Z",
        "classroom_ids": [1, 2],
        "doctor_ids": [3]
    }
    with patch.object(school_admin_event_service, "create_event", return_value=fake_data) as mock_svc:
        response = admin_client.post(f"{BASE_URL}/", json=payload)

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert "Evento creado exitosamente" in body["message"]
    assert body["data"]["event_id"] == 5
    mock_svc.assert_called_once()


def test_create_event_service_success():
    """Prueba creación de evento e inserción de participaciones para aulas y doctores."""
    event_query = make_query([{"id": 10}])
    part_query = make_query([{"id": 1}])

    # La anticipación mínima se valida en su propia batería de pruebas
    # (tests/test_settings_and_event_lead_time.py); aquí interesa la inserción.
    with patch.object(school_admin_event_service, "_get_admin_record", return_value=ADMIN_RECORD), \
         patch.object(school_admin_event_service, "_get_school_classroom_ids", return_value=[1, 2]), \
         patch.object(school_admin_event_service, "_get_school_doctor_ids", return_value=[5]), \
         patch(f"{SERVICE_MODULE}.validate_lead_time"), \
         patch(f"{SERVICE_MODULE}.supabase") as mock_supabase:
        route_tables(mock_supabase, event=event_query, event_participation=part_query)

        payload_mock = MagicMock()
        payload_mock.title = "Jornada de Salud"
        payload_mock.description = "Revisión general"
        payload_mock.init_time = datetime(2027, 5, 1, 8, 0, tzinfo=UTC)
        payload_mock.ent_time = datetime(2027, 5, 1, 12, 0, tzinfo=UTC)
        payload_mock.classroom_ids = [1, 2]
        payload_mock.doctor_ids = [5]

        result = school_admin_event_service.create_event(user_id=4, payload=payload_mock)

    assert result["event_id"] == 10
    assert result["classrooms_linked"] == 2
    assert result["doctors_linked"] == 1
    event_query.insert.assert_called_once()
    part_query.insert.assert_called_once()


def test_create_event_foreign_classroom_returns_400():
    """Intentar crear evento con salones de otra escuela -> 400 Bad Request."""
    with patch.object(school_admin_event_service, "_get_admin_record", return_value=ADMIN_RECORD), \
         patch.object(school_admin_event_service, "_get_school_classroom_ids", return_value=[1, 2]), \
         patch(f"{SERVICE_MODULE}.validate_lead_time"):
        payload_mock = MagicMock()
        payload_mock.classroom_ids = [999]  # Foreign classroom
        payload_mock.doctor_ids = []

        with pytest.raises(HTTPException) as exc:
            school_admin_event_service.create_event(user_id=4, payload=payload_mock)

    assert exc.value.status_code == 400
    assert "no pertenece a tu escuela" in exc.value.detail


def test_create_event_foreign_doctor_returns_400():
    """Intentar crear evento con doctores no asignados a la escuela -> 400 Bad Request."""
    with patch.object(school_admin_event_service, "_get_admin_record", return_value=ADMIN_RECORD), \
         patch.object(school_admin_event_service, "_get_school_classroom_ids", return_value=[1]), \
         patch.object(school_admin_event_service, "_get_school_doctor_ids", return_value=[10]), \
         patch(f"{SERVICE_MODULE}.validate_lead_time"):
        payload_mock = MagicMock()
        payload_mock.classroom_ids = [1]
        payload_mock.doctor_ids = [999]  # Foreign doctor

        with pytest.raises(HTTPException) as exc:
            school_admin_event_service.create_event(user_id=4, payload=payload_mock)

    assert exc.value.status_code == 400
    assert "no está asignado a tu escuela" in exc.value.detail


# ──────────────────────────────────────────────────────────
# 2. GET / - Listado Paginado de Eventos
# ──────────────────────────────────────────────────────────

def test_get_events_endpoint_success(admin_client):
    """Verifica respuesta 200 con envelope para listado de eventos."""
    fake_data = {
        "data": [
            {
                "id": 1,
                "title": "Feria de Ciencias",
                "description": "Evento escolar",
                "init_time": "2026-06-01T08:00:00Z",
                "ent_time": "2026-06-01T14:00:00Z"
            }
        ],
        "total": 1,
        "page": 1,
        "size": 20
    }
    with patch.object(school_admin_event_service, "get_events", return_value=fake_data) as mock_svc:
        response = admin_client.get(f"{BASE_URL}/?page=1&size=20")

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert "Eventos obtenidos exitosamente" in body["message"]
    assert body["data"]["total"] == 1
    mock_svc.assert_called_once_with(user_id=4, page=1, size=20)


def test_get_events_empty_collection(admin_client):
    """Sin eventos registrados retorna 200 con data=[] (regla empty-table-validation)."""
    with patch.object(school_admin_event_service, "_get_admin_record", return_value=ADMIN_RECORD), \
         patch(f"{SERVICE_MODULE}.supabase") as mock_supabase:
        route_tables(mock_supabase, event_participation=make_query([]))
        response = admin_client.get(f"{BASE_URL}/")

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert body["data"]["data"] == []
    assert body["data"]["total"] == 0


# ──────────────────────────────────────────────────────────
# 3. GET /by-month - Eventos por Mes
# ──────────────────────────────────────────────────────────

def test_get_events_by_month_endpoint_success(admin_client):
    """Verifica respuesta 200 para consulta de eventos por mes."""
    fake_data = {
        "data": [
            {
                "id": 2,
                "title": "Evaluación Médica",
                "init_time": "2026-04-15T09:00:00Z",
                "ent_time": "2026-04-15T12:00:00Z"
            }
        ],
        "year": 2026,
        "month": 4,
        "total": 1
    }
    with patch.object(school_admin_event_service, "get_events_by_month", return_value=fake_data):
        response = admin_client.get(f"{BASE_URL}/by-month?year=2026&month=4")

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert "Eventos del mes obtenidos exitosamente" in body["message"]
    assert body["data"]["total"] == 1


def test_get_events_by_month_empty(admin_client):
    """Mes sin eventos retorna 200 con data=[] y total=0."""
    with patch.object(school_admin_event_service, "_get_admin_record", return_value=ADMIN_RECORD), \
         patch(f"{SERVICE_MODULE}.supabase") as mock_supabase:
        route_tables(mock_supabase, event_participation=make_query([]))
        response = admin_client.get(f"{BASE_URL}/by-month?year=2026&month=12")

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert body["data"]["data"] == []
    assert body["data"]["total"] == 0


# ──────────────────────────────────────────────────────────
# 4. GET /{event_id} - Detalle del Evento
# ──────────────────────────────────────────────────────────

def test_get_event_detail_endpoint_success(admin_client):
    """Verifica detalle de evento con salones, doctores y creador."""
    fake_data = {
        "event": {
            "id": 1,
            "title": "Jornada de Vacunación",
            "init_time": "2026-05-10T08:00:00Z",
            "ent_time": "2026-05-10T12:00:00Z"
        },
        "classrooms": [{"id": 1, "label": "PRIMARIA 1 A"}],
        "doctors": [{"id": 5, "fullname": "Dr. Carlos Ramos"}],
        "created_by": {"name": "Laura Pérez"}
    }
    with patch.object(school_admin_event_service, "get_event_detail", return_value=fake_data):
        response = admin_client.get(f"{BASE_URL}/1")

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert "Detalle del evento obtenido exitosamente" in body["message"]
    assert body["data"]["event"]["id"] == 1


def test_get_event_detail_not_found_returns_404():
    """Evento inexistente o cancelado -> 404 Not Found."""
    with patch.object(school_admin_event_service, "_get_admin_record", return_value=ADMIN_RECORD), \
         patch(f"{SERVICE_MODULE}.supabase") as mock_supabase:
        route_tables(mock_supabase, event=make_query([]))
        with pytest.raises(HTTPException) as exc:
            school_admin_event_service.get_event_detail(user_id=4, event_id=999)

    assert exc.value.status_code == 404
    assert "No existe un evento activo" in exc.value.detail


def test_get_event_detail_foreign_school_returns_403():
    """Evento perteneciente a otra escuela (sin participación del admin) -> 403 Forbidden."""
    event_row = {"id": 10, "title": "Evento Ajeno"}
    with patch.object(school_admin_event_service, "_get_admin_record", return_value=ADMIN_RECORD), \
         patch(f"{SERVICE_MODULE}.supabase") as mock_supabase:
        route_tables(
            mock_supabase,
            event=make_query([event_row]),
            event_participation=make_query([])  # No participation for this admin
        )
        with pytest.raises(HTTPException) as exc:
            school_admin_event_service.get_event_detail(user_id=4, event_id=10)

    assert exc.value.status_code == 403
    assert "No tienes permiso" in exc.value.detail


def _detail_with_updated_at(updated_at):
    """Ejecuta get_event_detail con una fila de evento que trae el ``updated_at`` dado."""
    event_row = {
        "id": 1,
        "title": "Revisión General",
        "description": None,
        "init_time": "2026-11-10T12:00:00+00:00",
        "ent_time": "2026-11-10T14:00:00+00:00",
        "created_at": "2026-11-01T09:00:00+00:00",
        "updated_at": updated_at,
    }
    with patch.object(school_admin_event_service, "_get_admin_record", return_value=ADMIN_RECORD), \
         patch(f"{SERVICE_MODULE}.supabase") as mock_supabase:
        route_tables(
            mock_supabase,
            event=make_query([event_row]),
            event_participation=make_query([{"id": 1, "id_classroom": None, "id_doctor": None, "id_school_admin": None}]),
        )
        return school_admin_event_service.get_event_detail(user_id=4, event_id=1)


@pytest.mark.parametrize(
    ("stored", "expected"),
    [
        ("2026-11-05T14:30:00.123456+00:00", "2026-11-05T14:30:00.123456+00:00"),
        ("2026-11-05T14:30:00Z", "2026-11-05T14:30:00Z"),
        ("2026-11-05T10:30:00-04:00", "2026-11-05T10:30:00-04:00"),
        # datetime.utcnow().isoformat() sin offset: se interpreta como UTC
        ("2026-11-05T14:30:00.123456", "2026-11-05T14:30:00.123456+00:00"),
        (None, None),
    ],
)
def test_get_event_detail_includes_updated_at_with_offset(stored, expected):
    """El detalle expone updated_at siempre con zona horaria (o None si no hay valor)."""
    result = _detail_with_updated_at(stored)

    assert result["event"]["updated_at"] == expected
    assert result["event"]["created_at"] == "2026-11-01T09:00:00+00:00"


# ──────────────────────────────────────────────────────────
# 5. PATCH /{event_id}/cancel - Cancelar Evento
# ──────────────────────────────────────────────────────────

def test_cancel_event_endpoint_success(admin_client):
    """Verifica cancelación de evento."""
    fake_data = {"event_id": 5, "status": "canceled"}
    with patch.object(school_admin_event_service, "cancel_event", return_value=fake_data):
        response = admin_client.patch(f"{BASE_URL}/5/cancel")

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert "Evento cancelado exitosamente" in body["message"]
    assert body["data"]["status"] == "canceled"


def test_cancel_event_service_success():
    """Valida actualización de is_canceled tanto en event como en event_participation."""
    event_query = make_query([{"id": 5}])
    part_query = make_query([{"id": 1}])

    with patch.object(school_admin_event_service, "_get_admin_record", return_value=ADMIN_RECORD), \
         patch.object(school_admin_event_service, "_validate_event_ownership", return_value={"id": 5}), \
         patch(f"{SERVICE_MODULE}.supabase") as mock_supabase:
        route_tables(mock_supabase, event=event_query, event_participation=part_query)
        result = school_admin_event_service.cancel_event(user_id=4, event_id=5)

    assert result["status"] == "canceled"
    event_query.update.assert_called_once()
    part_query.update.assert_called_once()


# ──────────────────────────────────────────────────────────
# 6. PUT /{event_id} - Actualizar Evento
# ──────────────────────────────────────────────────────────

def test_update_event_endpoint_success(admin_client):
    """Verifica actualización de metadatos de evento vía PUT."""
    fake_data = {"id": 5, "title": "Título Modificado"}
    payload = {"title": "Título Modificado"}
    with patch.object(school_admin_event_service, "update_event", return_value=fake_data):
        response = admin_client.put(f"{BASE_URL}/5", json=payload)

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert "Evento actualizado exitosamente" in body["message"]


def test_update_event_invalid_dates_returns_400():
    """ent_time anterior a init_time -> 400 Bad Request."""
    event_row = {
        "id": 5,
        "init_time": "2026-05-10T12:00:00Z",
        "ent_time": "2026-05-10T14:00:00Z"
    }
    with patch.object(school_admin_event_service, "_get_admin_record", return_value=ADMIN_RECORD), \
         patch.object(school_admin_event_service, "_validate_event_ownership", return_value=event_row):
        # Update ent_time before init_time
        payload = {"ent_time": "2026-05-10T08:00:00Z"}
        with pytest.raises(HTTPException) as exc:
            school_admin_event_service.update_event(user_id=4, event_id=5, payload=payload)

    assert exc.value.status_code == 400
    assert "posterior a la de inicio" in exc.value.detail


def test_update_event_stores_updated_at_with_offset():
    """La edición guarda ``updated_at`` con zona horaria, no un datetime ingenuo."""
    event_row = {
        "id": 5,
        "init_time": "2026-05-10T12:00:00Z",
        "ent_time": "2026-05-10T14:00:00Z",
    }
    with patch.object(school_admin_event_service, "_get_admin_record", return_value=ADMIN_RECORD), \
         patch.object(school_admin_event_service, "_validate_event_ownership", return_value=event_row), \
         patch(f"{SERVICE_MODULE}.supabase") as mock_supabase:
        event_query = make_query([{"id": 5, "title": "Nuevo"}])
        route_tables(mock_supabase, event=event_query)
        school_admin_event_service.update_event(user_id=4, event_id=5, payload={"title": "Nuevo"})

    stored = event_query.update.call_args.args[0]["updated_at"]
    # fromisoformat solo resuelve el offset si está presente; sin él, tzinfo es None
    assert datetime.fromisoformat(stored).tzinfo is not None


# ──────────────────────────────────────────────────────────
# 7. RBAC & Autenticación
# ──────────────────────────────────────────────────────────

def test_events_unauthenticated_returns_401(client):
    """Sin credenciales -> 401."""
    response = client.get(f"{BASE_URL}/")
    assert response.status_code == 401


def test_events_invalid_jwt_returns_401(client):
    """Token malformado -> 401."""
    response = client.get(
        f"{BASE_URL}/",
        headers={"Authorization": "Bearer jwt-malformado"}
    )
    assert response.status_code == 401


def test_events_revoked_jwt_returns_401(client):
    """Token revocado -> 401."""
    token = create_access_token({"sub": "4", "role": "admin_escuela"})
    with patch.object(auth_service, "is_token_revoked", return_value=True):
        response = client.get(
            f"{BASE_URL}/",
            headers={"Authorization": f"Bearer {token}"}
        )
    assert response.status_code == 401


def test_events_wrong_role_returns_403(client):
    """Rol no autorizado (ej. doctor) -> 403 Forbidden."""
    app.dependency_overrides[get_current_user_token] = lambda: {
        "sub": "20",
        "role": "doctor",
        "scope": "client"
    }
    response = client.get(f"{BASE_URL}/")
    assert response.status_code == 403
    body = response.json()
    assert body["success"] is False


def test_events_admin_unlinked_returns_403(admin_client):
    """Admin sin escuela vinculada -> 403 Forbidden."""
    with patch(f"{SERVICE_MODULE}.supabase") as mock_supabase:
        route_tables(mock_supabase, school_administrator=make_query([]))
        response = admin_client.get(f"{BASE_URL}/")

    assert response.status_code == 403
    body = response.json()
    assert body["success"] is False
    assert "no está vinculado a ninguna escuela" in body["message"]


# ──────────────────────────────────────────────────────────
# 8. Validación de parámetros (422)
# ──────────────────────────────────────────────────────────

@pytest.mark.parametrize("invalid_url", [
    f"{BASE_URL}/?page=0",                 # page ge=1
    f"{BASE_URL}/?size=500",               # size le=100
    f"{BASE_URL}/by-month?year=2015&month=5",  # year ge=2020
    f"{BASE_URL}/by-month?year=2026&month=13", # month le=12
    f"{BASE_URL}/abc",                     # event_id not int
])
def test_events_validation_errors(admin_client, invalid_url):
    """Parámetros fuera de rango devuelven 422 con formato estándar."""
    response = admin_client.get(invalid_url)
    assert response.status_code == 422
    body = response.json()
    assert body["success"] is False


# ──────────────────────────────────────────────────────────
# 9. GET /by-month - Ventana del mes (sobre un falso Supabase que filtra)
# ──────────────────────────────────────────────────────────
#
# Los tests de la sección 3 parchean el método del servicio o cortocircuitan con
# una lista de participaciones vacía, así que nunca llegan a ejercitar el cálculo
# de la ventana: con ellos solos se podía fijar el rango a enero de 1999 y la
# suite seguía verde. Estos usan el falso cliente de tests/fake_supabase.py, que
# aplica los filtros de verdad.

def _month_db(events):
    """Base de datos mínima con el admin de ADMIN_RECORD convocando cada evento."""
    return FakeSupabase(
        school_administrator=[{
            "sch_admin_id": ADMIN_RECORD["sch_admin_id"],
            "school_id": ADMIN_RECORD["school_id"],
            "user_id": 4,
        }],
        event_participation=[
            {
                "id_event": e["id"],
                "id_school_admin": ADMIN_RECORD["sch_admin_id"],
                "is_deleted": False,
            }
            for e in events
        ],
        event=events,
    )


def _admin_event(event_id, init_time, *, is_canceled=False, is_deleted=False):
    """Fila con la forma real de 'event'."""
    return {
        "id": event_id,
        "title": f"Evento {event_id}",
        "description": None,
        "init_time": init_time,
        "ent_time": init_time,
        "created_at": "2026-01-01T00:00:00+00:00",
        "is_canceled": is_canceled,
        "is_deleted": is_deleted,
    }


def _run_admin_month(events, year=2026, month=4):
    with patch(f"{SERVICE_MODULE}.supabase", _month_db(events)):
        return school_admin_event_service.get_events_by_month(user_id=4, year=year, month=month)


def test_admin_month_window_is_half_open():
    """El último segundo del mes entra; el primer instante del siguiente, no."""
    events = [
        _admin_event(1, "2026-03-31T23:59:59+00:00"),
        _admin_event(2, "2026-04-01T00:00:00+00:00"),
        _admin_event(3, "2026-04-30T23:59:59+00:00"),
        _admin_event(4, "2026-05-01T00:00:00+00:00"),
    ]
    assert [e["id"] for e in _run_admin_month(events)["data"]] == [2, 3]


@pytest.mark.parametrize("year,month,inside,outside", [
    (2024, 2, "2024-02-29T23:00:00+00:00", "2024-03-01T00:00:00+00:00"),  # bisiesto
    (2026, 2, "2026-02-28T23:00:00+00:00", "2026-03-01T00:00:00+00:00"),
    (2026, 1, "2026-01-31T23:00:00+00:00", "2026-02-01T00:00:00+00:00"),
    (2026, 12, "2026-12-31T23:00:00+00:00", "2027-01-01T00:00:00+00:00"),  # cruce de año
])
def test_admin_month_window_respects_real_month_lengths(year, month, inside, outside):
    """La ventana se calcula por mes real, no con una longitud fija."""
    events = [_admin_event(1, inside), _admin_event(2, outside)]
    result = _run_admin_month(events, year=year, month=month)
    assert [e["id"] for e in result["data"]] == [1]
    assert result["year"] == year and result["month"] == month


def test_admin_month_excludes_canceled_and_deleted():
    """El filtro de vigencia compartido sigue descartando cancelados y eliminados."""
    events = [
        _admin_event(1, "2026-04-10T09:00:00+00:00"),
        _admin_event(2, "2026-04-11T09:00:00+00:00", is_canceled=True),
        _admin_event(3, "2026-04-12T09:00:00+00:00", is_deleted=True),
    ]
    assert [e["id"] for e in _run_admin_month(events)["data"]] == [1]


def test_admin_month_is_ordered_chronologically():
    """El mes sale ascendente por init_time."""
    events = [
        _admin_event(1, "2026-04-20T09:00:00+00:00"),
        _admin_event(2, "2026-04-05T09:00:00+00:00"),
        _admin_event(3, "2026-04-12T09:00:00+00:00"),
    ]
    result = _run_admin_month(events)
    assert [e["id"] for e in result["data"]] == [2, 3, 1]
    assert result["total"] == 3


# ──────────────────────────────────────────────────────────
# 10. GET / - Paginación (sobre un falso Supabase que filtra)
# ──────────────────────────────────────────────────────────
#
# Los tests de la sección 2 usan el MagicMock encadenable, que devuelve las
# mismas filas se pagine como se pagine: con ellos solos se podía ignorar el
# parámetro 'page' o devolver size+1 filas sin que fallara nada.

def _paged_db(events):
    """Base de datos con el admin de ADMIN_RECORD convocando cada evento."""
    return FakeSupabase(
        school_administrator=[{
            "sch_admin_id": ADMIN_RECORD["sch_admin_id"],
            "school_id": ADMIN_RECORD["school_id"],
            "user_id": 4,
        }],
        event_participation=[
            {
                "id_event": e["id"],
                "id_school_admin": ADMIN_RECORD["sch_admin_id"],
                "is_deleted": False,
            }
            for e in events
        ],
        event=events,
    )


def _run_admin_events(events, page, size):
    with patch(f"{SERVICE_MODULE}.supabase", _paged_db(events)):
        return school_admin_event_service.get_events(user_id=4, page=page, size=size)


def test_admin_events_pagination_slices_by_page():
    """Cada página devuelve su tramo, y 'total' cuenta el conjunto completo."""
    events = [
        _admin_event(i, f"2026-04-{i:02d}T09:00:00+00:00")
        for i in range(1, 8)  # 7 eventos
    ]
    first = _run_admin_events(events, page=1, size=3)
    second = _run_admin_events(events, page=2, size=3)
    third = _run_admin_events(events, page=3, size=3)

    # Orden descendente por init_time: 7, 6, 5 | 4, 3, 2 | 1
    assert [e["id"] for e in first["data"]] == [7, 6, 5]
    assert [e["id"] for e in second["data"]] == [4, 3, 2]
    assert [e["id"] for e in third["data"]] == [1]
    # 'total' es el total de la colección, no el de la página.
    assert first["total"] == second["total"] == third["total"] == 7
    assert first["page"] == 1 and first["size"] == 3


def test_admin_events_pagination_excludes_canceled_from_the_count():
    """Los eventos no vigentes no cuentan ni aparecen."""
    events = [
        _admin_event(1, "2026-04-01T09:00:00+00:00"),
        _admin_event(2, "2026-04-02T09:00:00+00:00", is_canceled=True),
        _admin_event(3, "2026-04-03T09:00:00+00:00", is_deleted=True),
    ]
    result = _run_admin_events(events, page=1, size=20)
    assert [e["id"] for e in result["data"]] == [1]
    assert result["total"] == 1


def test_admin_events_page_beyond_the_end_is_empty():
    """Una página más allá del final devuelve lista vacía, no un error."""
    events = [_admin_event(1, "2026-04-01T09:00:00+00:00")]
    result = _run_admin_events(events, page=5, size=20)
    assert result["data"] == []
    assert result["total"] == 1
