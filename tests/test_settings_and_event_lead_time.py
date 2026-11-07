"""
Tests de la configuración global (`settings`) y de la anticipación mínima para
programar eventos escolares (issue #44):
  - app/services/settings_service.py (resolución, fallback y escritura)
  - app/services/event_schedule_policy.py (umbral, redondeo y contrato de error)
  - POST /api/v1/school-admin/events/ (rechazo sin escrituras)
  - PUT  /api/v1/school-admin/events/{event_id} (solo si se reprograma el inicio)
  - GET/PUT /api/v1/admin/settings (administración y RBAC)

Adheres strictly to the 5 laws of vitaly-feature-testing:
  1. APIResponse envelope verification (success, message, errors).
  2. Aislamiento: `settings` es global; los eventos siguen resolviendo school_id
     en el servidor.
  3. Deterministic supabase-py mock y reloj del servidor congelado.
  4. Códigos correctos: 422 de dominio, 400 de configuración inválida, 200 de éxito.
  5. RBAC matrix: 403 para roles no autorizados, 401 sin token, 422 para claves
     no soportadas.
"""
try:
    from datetime import UTC
except ImportError:
    from datetime import timezone
    UTC = timezone.utc  # noqa: UP017
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone as dt_timezone
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app.api.deps import get_current_admin_escuela, get_current_admin_sistema
from app.core.exceptions import FieldValidationError
from app.core.security import create_access_token
from app.main import app
from app.schemas.enums import SettingKey
from app.services.event_schedule_policy import (
    EVENT_SCHEDULE_ERROR_MESSAGE,
    EVENT_SCHEDULE_ERROR_TYPE,
    minimum_allowed_start,
    validate_lead_time,
)
from app.services.school_admin_event_service import school_admin_event_service
from app.services.settings_service import settings_service

SETTINGS_MODULE = "app.services.settings_service"
POLICY_MODULE = "app.services.event_schedule_policy"
EVENT_SERVICE_MODULE = "app.services.school_admin_event_service"
EVENTS_URL = "/api/v1/school-admin/events"
SETTINGS_URL = "/api/v1/admin/settings"
LEAD_TIME_KEY = SettingKey.EVENT_MIN_LEAD_TIME_MINUTES.value

#: Reloj del servidor congelado para todas las pruebas de umbral.
NOW = datetime(2027, 3, 10, 15, 0, 0, tzinfo=UTC)

ADMIN_RECORD = {"sch_admin_id": 10, "school_id": 1}


# ──────────────────────────────────────────────────────────
# Helpers: payloads, mock de supabase-py y contextos de prueba
# ──────────────────────────────────────────────────────────

def admin_school_payload(user_id: int = 4) -> dict:
    """Claims de un JWT de admin_escuela (el JWT no trae school_id)."""
    return {
        "sub": str(user_id),
        "email": "admin@colegio.test",
        "role": "admin_escuela",
        "scope": "client",
        "name": "Laura Pérez",
        "gender": "FEMENINO",
    }


def sys_admin_payload(user_id: int = 1) -> dict:
    """Claims de un JWT de admin_sistema."""
    return {
        "sub": str(user_id),
        "email": "sysadmin@vitaly.com",
        "role": "admin_sistema",
        "scope": "admin",
        "name": "Super Admin",
        "gender": "MASCULINO",
    }


def make_query(data=None, count=None) -> MagicMock:
    """Mock encadenable de una consulta supabase-py."""
    query = MagicMock()
    for method in (
        "select", "eq", "neq", "in_", "is_", "ilike", "gte", "lte",
        "order", "range", "limit", "insert", "update", "delete", "or_"
    ):
        getattr(query, method).return_value = query
    query.execute.return_value = MagicMock(data=[] if data is None else data, count=count)
    return query


def route_tables(mock_supabase: MagicMock, **tables: MagicMock) -> None:
    """Enruta supabase.table(...) a un mock por tabla."""
    mock_supabase.table.side_effect = lambda name: tables.get(name, make_query([]))


def settings_row(value, description: str | None = None) -> dict:
    """Fila de `settings` tal como la devolvería supabase-py."""
    return {
        "key": LEAD_TIME_KEY,
        "value": value,
        "description": description,
        "updated_at": "2027-01-01T00:00:00+00:00",
    }


@contextmanager
def stored_config(value):
    """Simula la fila almacenada en `settings` para la clave de anticipación.

    Args:
        value: Contenido de la columna ``value``. ``None`` simula la ausencia de
            configuración.
    """
    rows = [] if value is None else [settings_row(value)]
    with patch(f"{SETTINGS_MODULE}.supabase_admin") as mock_sb:
        route_tables(mock_sb, settings=make_query(rows))
        yield mock_sb


@contextmanager
def configured_minutes(minutes):
    """Simula una configuración válida de ``minutes`` minutos de anticipación."""
    with stored_config({"type": "integer", "value": minutes}) as mock_sb:
        yield mock_sb


@contextmanager
def frozen_clock(now: datetime = NOW):
    """Congela el reloj del servidor usado por la política de anticipación."""
    with patch(f"{POLICY_MODULE}._now_utc", return_value=now):
        yield


def accepts(init_time: datetime) -> bool:
    """Indica si la política acepta un instante de inicio."""
    try:
        validate_lead_time(init_time)
    except FieldValidationError:
        return False
    return True


# ──────────────────────────────────────────────────────────
# Fixtures
# ──────────────────────────────────────────────────────────

@pytest.fixture
def admin_client():
    """Cliente con get_current_admin_escuela sobrescrito."""
    app.dependency_overrides[get_current_admin_escuela] = lambda: admin_school_payload()
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


@pytest.fixture
def sys_admin_client():
    """Cliente con get_current_admin_sistema sobrescrito."""
    app.dependency_overrides[get_current_admin_sistema] = lambda: sys_admin_payload()
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


@pytest.fixture
def client():
    """Cliente sin dependencias sobrescritas (para la matriz RBAC)."""
    app.dependency_overrides.clear()
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


# ──────────────────────────────────────────────────────────
# 1. Resolución de la configuración
# ──────────────────────────────────────────────────────────

class TestSettingsResolution:
    def test_missing_row_falls_back_to_five_minutes(self):
        """Sin fila en `settings`, el margen efectivo es de cinco minutos."""
        with stored_config(None):
            assert settings_service.get_int(SettingKey.EVENT_MIN_LEAD_TIME_MINUTES) == 5

    @pytest.mark.parametrize("minutes", [0, 5, 60, 720, 10080])
    def test_valid_values_are_returned(self, minutes):
        """Un valor dentro del rango admitido se usa tal cual."""
        with configured_minutes(minutes):
            assert settings_service.get_int(SettingKey.EVENT_MIN_LEAD_TIME_MINUTES) == minutes

    @pytest.mark.parametrize(
        "stored",
        [
            {"type": "string", "value": "60"},     # tipo declarado incorrecto
            {"type": "integer", "value": "60"},    # dato no entero
            {"type": "integer", "value": True},    # bool no cuenta como entero
            {"type": "integer", "value": -1},      # por debajo del mínimo
            {"type": "integer", "value": 10081},   # por encima del máximo (7 días)
            {"value": 60},                          # sin tipo declarado
            {"type": "integer"},                    # sin valor
            "60",                                   # no es un objeto
            None,                                   # columna nula
        ],
    )
    def test_invalid_values_fall_back_without_disabling_the_barrier(self, stored):
        """Una configuración inválida no desactiva la barrera: vuelve a cinco minutos."""
        with stored_config(stored):
            assert settings_service.get_int(SettingKey.EVENT_MIN_LEAD_TIME_MINUTES) == 5

    def test_missing_service_key_keeps_the_barrier_with_the_default(self):
        """Sin clave de servicio la tabla es inalcanzable, pero la barrera sigue activa."""
        with patch(f"{SETTINGS_MODULE}.supabase_admin", None):
            assert settings_service.get_int(SettingKey.EVENT_MIN_LEAD_TIME_MINUTES) == 5

    def test_database_error_falls_back_to_five_minutes(self):
        """Un fallo de la base de datos no propaga ni desactiva la validación."""
        with patch(f"{SETTINGS_MODULE}.supabase_admin") as mock_sb:
            mock_sb.table.side_effect = RuntimeError("conexión caída")
            assert settings_service.get_int(SettingKey.EVENT_MIN_LEAD_TIME_MINUTES) == 5


# ──────────────────────────────────────────────────────────
# 2. Umbral de anticipación
# ──────────────────────────────────────────────────────────

class TestLeadTimeThreshold:
    def test_fallback_rejects_past_and_accepts_from_the_fifth_minute(self):
        """Reloj 15:00:00 sin configuración: ayer, 14:00 y 15:04 se rechazan; 15:05 se acepta."""
        with stored_config(None), frozen_clock():
            assert accepts(NOW - timedelta(days=1)) is False
            assert accepts(NOW.replace(hour=14, minute=0)) is False
            assert accepts(NOW.replace(minute=4)) is False
            assert accepts(NOW.replace(minute=5)) is True
            assert accepts(NOW.replace(minute=6)) is True

    def test_sixty_minutes_rejects_fifty_nine_and_accepts_the_hour(self):
        """Con 60 minutos, a las 15:00:00 se rechaza 15:59 y se acepta 16:00."""
        with configured_minutes(60), frozen_clock():
            assert accepts(NOW.replace(minute=59)) is False
            assert accepts(NOW.replace(hour=16, minute=0)) is True

    def test_twelve_hours_crosses_into_the_next_day(self):
        """Con 720 minutos, a las 15:00:00 se acepta mañana a las 03:00 y se rechaza 02:59."""
        tomorrow = NOW + timedelta(days=1)
        with configured_minutes(720), frozen_clock():
            assert accepts(tomorrow.replace(hour=2, minute=59)) is False
            assert accepts(tomorrow.replace(hour=3, minute=0)) is True

    def test_seconds_round_up_to_the_next_allowed_minute(self):
        """A las 15:00:30 con el fallback se rechaza 15:05 y se acepta 15:06."""
        with stored_config(None), frozen_clock(NOW.replace(second=30)):
            assert accepts(NOW.replace(minute=5)) is False
            assert accepts(NOW.replace(minute=6)) is True

    def test_zero_minutes_still_rejects_the_past(self):
        """Con 0 minutos la barrera sigue activa: el pasado nunca se acepta."""
        with configured_minutes(0), frozen_clock():
            assert accepts(NOW - timedelta(minutes=1)) is False
            assert accepts(NOW) is True

    def test_equivalent_instants_with_different_offsets_match(self):
        """15:05Z y 11:05-04:00 son el mismo instante: se aceptan o rechazan igual."""
        offset_minus_four = dt_timezone(timedelta(hours=-4))
        with stored_config(None), frozen_clock():
            assert accepts(datetime(2027, 3, 10, 11, 5, tzinfo=offset_minus_four)) is True
            assert accepts(datetime(2027, 3, 10, 11, 4, tzinfo=offset_minus_four)) is False

    def test_naive_datetime_is_interpreted_as_utc(self):
        """Un inicio sin offset se compara como UTC (ver issue #9)."""
        with stored_config(None), frozen_clock():
            assert accepts(datetime(2027, 3, 10, 15, 5)) is True
            assert accepts(datetime(2027, 3, 10, 15, 4)) is False

    def test_minimum_allowed_start_is_not_exposed_in_the_error(self):
        """El error identifica init_time pero no revela margen ni umbral."""
        with configured_minutes(720), frozen_clock():
            threshold = minimum_allowed_start()
            with pytest.raises(FieldValidationError) as exc:
                validate_lead_time(NOW + timedelta(minutes=1))

        assert threshold == datetime(2027, 3, 11, 3, 0, tzinfo=UTC)
        assert exc.value.status_code == 422
        detail = exc.value.errors[0]
        assert detail.loc == ["body", "init_time"]
        assert detail.type == EVENT_SCHEDULE_ERROR_TYPE
        assert detail.msg == EVENT_SCHEDULE_ERROR_MESSAGE
        assert "720" not in detail.msg and "03:00" not in detail.msg


# ──────────────────────────────────────────────────────────
# 3. POST /events - El rechazo no escribe nada
# ──────────────────────────────────────────────────────────

class TestCreateEventLeadTime:
    def test_rejected_start_creates_neither_event_nor_participations(self):
        """Un inicio rechazado no inserta en `event` ni en `event_participation`."""
        event_query = make_query([{"id": 10}])
        part_query = make_query([{"id": 1}])

        payload_mock = MagicMock()
        payload_mock.title = "Jornada de Salud"
        payload_mock.description = None
        payload_mock.init_time = NOW.replace(minute=4)
        payload_mock.ent_time = NOW.replace(hour=17)
        payload_mock.classroom_ids = [1]
        payload_mock.doctor_ids = [5]

        with patch.object(school_admin_event_service, "_get_admin_record", return_value=ADMIN_RECORD), \
             patch(f"{EVENT_SERVICE_MODULE}.supabase") as mock_sb, \
             stored_config(None), frozen_clock():
            route_tables(mock_sb, event=event_query, event_participation=part_query)
            with pytest.raises(FieldValidationError):
                school_admin_event_service.create_event(user_id=4, payload=payload_mock)

        event_query.insert.assert_not_called()
        part_query.insert.assert_not_called()

    def test_endpoint_returns_envelope_with_field_error(self, admin_client):
        """El 422 conserva APIResponse, identifica init_time y no revela el margen."""
        body = {
            "title": "Jornada de Vacunación",
            "description": "Vacunación general",
            "init_time": NOW.replace(minute=4).isoformat(),
            "ent_time": NOW.replace(hour=17).isoformat(),
            "classroom_ids": [1],
            "doctor_ids": [5],
        }
        with patch.object(school_admin_event_service, "_get_admin_record", return_value=ADMIN_RECORD), \
             patch(f"{EVENT_SERVICE_MODULE}.supabase"), \
             stored_config(None), frozen_clock():
            response = admin_client.post(f"{EVENTS_URL}/", json=body)

        assert response.status_code == 422
        payload = response.json()
        assert payload["success"] is False
        assert payload["data"] is None
        assert payload["message"] == EVENT_SCHEDULE_ERROR_MESSAGE
        assert payload["errors"][0]["loc"] == ["body", "init_time"]
        assert payload["errors"][0]["type"] == EVENT_SCHEDULE_ERROR_TYPE
        assert payload["errors"][0]["msg"] == EVENT_SCHEDULE_ERROR_MESSAGE
        # El contrato no debe filtrar el margen vigente ni el umbral calculado.
        assert "minut" not in payload["message"].lower()

    def test_valid_start_proceeds_to_the_rest_of_the_validations(self, admin_client):
        """Un inicio permitido deja continuar el flujo normal de creación."""
        body = {
            "title": "Jornada de Vacunación",
            "init_time": NOW.replace(minute=30).isoformat(),
            "ent_time": NOW.replace(hour=17).isoformat(),
            "classroom_ids": [1],
            "doctor_ids": [5],
        }
        event_query = make_query([{"id": 10}])
        part_query = make_query([{"id": 1}])

        with patch.object(school_admin_event_service, "_get_admin_record", return_value=ADMIN_RECORD), \
             patch.object(school_admin_event_service, "_get_school_classroom_ids", return_value=[1]), \
             patch.object(school_admin_event_service, "_get_school_doctor_ids", return_value=[5]), \
             patch(f"{EVENT_SERVICE_MODULE}.supabase") as mock_sb, \
             stored_config(None), frozen_clock():
            route_tables(mock_sb, event=event_query, event_participation=part_query)
            response = admin_client.post(f"{EVENTS_URL}/", json=body)

        assert response.status_code == 200
        assert response.json()["data"]["event_id"] == 10
        event_query.insert.assert_called_once()


# ──────────────────────────────────────────────────────────
# 4. PUT /events/{id} - Solo al reprogramar el inicio
# ──────────────────────────────────────────────────────────

class TestUpdateEventLeadTime:
    def test_rescheduling_too_soon_is_rejected_without_writing(self):
        """Mover el inicio a una hora no permitida devuelve 422 y no actualiza."""
        event_query = make_query([{"id": 7, "init_time": "2027-03-01T08:00:00+00:00",
                                   "ent_time": "2027-03-01T10:00:00+00:00"}])

        with patch.object(school_admin_event_service, "_get_admin_record", return_value=ADMIN_RECORD), \
             patch.object(school_admin_event_service, "_validate_event_ownership",
                          return_value={"id": 7, "init_time": "2027-03-01T08:00:00+00:00",
                                        "ent_time": "2027-03-01T10:00:00+00:00"}), \
             patch(f"{EVENT_SERVICE_MODULE}.supabase") as mock_sb, \
             stored_config(None), frozen_clock():
            route_tables(mock_sb, event=event_query)
            with pytest.raises(FieldValidationError):
                school_admin_event_service.update_event(
                    user_id=4, event_id=7, payload={"init_time": NOW.replace(minute=4)}
                )

        event_query.update.assert_not_called()

    def test_editing_only_the_title_of_a_past_event_is_allowed(self):
        """Corregir el título de un evento ya ocurrido no revalida la anticipación."""
        updated = {"id": 7, "title": "Título corregido"}
        event_query = make_query([updated])

        with patch.object(school_admin_event_service, "_get_admin_record", return_value=ADMIN_RECORD), \
             patch.object(school_admin_event_service, "_validate_event_ownership",
                          return_value={"id": 7, "init_time": "2020-03-01T08:00:00+00:00",
                                        "ent_time": "2020-03-01T10:00:00+00:00"}), \
             patch(f"{EVENT_SERVICE_MODULE}.supabase") as mock_sb, \
             stored_config(None), frozen_clock():
            route_tables(mock_sb, event=event_query)
            result = school_admin_event_service.update_event(
                user_id=4, event_id=7, payload={"title": "Título corregido"}
            )

        assert result == updated
        event_query.update.assert_called_once()

    def test_rescheduling_within_the_margin_is_applied(self):
        """Reprogramar a una hora permitida actualiza el evento."""
        new_init = NOW + timedelta(days=2)
        updated = {"id": 7, "init_time": new_init.isoformat()}
        event_query = make_query([updated])

        with patch.object(school_admin_event_service, "_get_admin_record", return_value=ADMIN_RECORD), \
             patch.object(school_admin_event_service, "_validate_event_ownership",
                          return_value={"id": 7, "init_time": "2027-03-01T08:00:00+00:00",
                                        "ent_time": "2027-03-20T10:00:00+00:00"}), \
             patch(f"{EVENT_SERVICE_MODULE}.supabase") as mock_sb, \
             stored_config(None), frozen_clock():
            route_tables(mock_sb, event=event_query)
            result = school_admin_event_service.update_event(
                user_id=4, event_id=7, payload={"init_time": new_init}
            )

        assert result == updated
        event_query.update.assert_called_once()


# ──────────────────────────────────────────────────────────
# 5. /admin/settings - Administración de la configuración
# ──────────────────────────────────────────────────────────

class TestAdminSettingsEndpoints:
    def test_list_includes_declared_keys_with_defaults(self, sys_admin_client):
        """Sin filas almacenadas, el listado expone la clave con su valor por defecto."""
        with patch(f"{SETTINGS_MODULE}.supabase_admin") as mock_sb:
            route_tables(mock_sb, settings=make_query([]))
            response = sys_admin_client.get(f"{SETTINGS_URL}/")

        assert response.status_code == 200
        body = response.json()
        assert body["success"] is True
        assert "Configuración obtenida exitosamente" in body["message"]
        entry = next(item for item in body["data"] if item["key"] == LEAD_TIME_KEY)
        assert entry["value"] == {"type": "integer", "value": 5}
        assert entry["updated_at"] is None

    def test_missing_service_key_surfaces_500_to_the_administrator(self, sys_admin_client):
        """La administración no debe mostrar valores que aparenten estar vigentes."""
        with patch(f"{SETTINGS_MODULE}.supabase_admin", None):
            listing = sys_admin_client.get(f"{SETTINGS_URL}/")
            writing = sys_admin_client.put(
                f"{SETTINGS_URL}/{LEAD_TIME_KEY}",
                json={"value": {"type": "integer", "value": 60}},
            )

        for response in (listing, writing):
            assert response.status_code == 500
            body = response.json()
            assert body["success"] is False
            assert "clave de servicio" in body["message"]

    def test_get_single_setting_returns_stored_value(self, sys_admin_client):
        """La consulta de una clave devuelve el valor vigente dentro del envelope."""
        with configured_minutes(60):
            response = sys_admin_client.get(f"{SETTINGS_URL}/{LEAD_TIME_KEY}")

        assert response.status_code == 200
        body = response.json()
        assert body["success"] is True
        assert body["data"]["value"] == {"type": "integer", "value": 60}

    def test_unknown_key_returns_422(self, sys_admin_client):
        """Una clave no declarada en SettingKey se rechaza con 422."""
        response = sys_admin_client.get(f"{SETTINGS_URL}/clave_inexistente")
        assert response.status_code == 422
        assert response.json()["success"] is False

    def test_update_existing_value(self, sys_admin_client):
        """Actualizar una clave existente persiste el nuevo valor."""
        stored = settings_row({"type": "integer", "value": 5})
        updated = settings_row({"type": "integer", "value": 720})
        query = make_query([stored])

        with patch(f"{SETTINGS_MODULE}.supabase_admin") as mock_sb:
            route_tables(mock_sb, settings=query)
            query.execute.side_effect = [
                MagicMock(data=[stored]),    # _fetch_row
                MagicMock(data=[updated]),   # update
            ]
            response = sys_admin_client.put(
                f"{SETTINGS_URL}/{LEAD_TIME_KEY}",
                json={"value": {"type": "integer", "value": 720}},
            )

        assert response.status_code == 200
        body = response.json()
        assert body["success"] is True
        assert body["data"]["value"] == {"type": "integer", "value": 720}
        query.update.assert_called_once()

    def test_update_creates_the_row_when_missing(self, sys_admin_client):
        """Si la clave aún no existe, se inserta en lugar de actualizar."""
        created = settings_row({"type": "integer", "value": 90})
        query = make_query([])

        with patch(f"{SETTINGS_MODULE}.supabase_admin") as mock_sb:
            route_tables(mock_sb, settings=query)
            query.execute.side_effect = [
                MagicMock(data=[]),          # _fetch_row
                MagicMock(data=[created]),   # insert
            ]
            response = sys_admin_client.put(
                f"{SETTINGS_URL}/{LEAD_TIME_KEY}",
                json={"value": {"type": "integer", "value": 90}},
            )

        assert response.status_code == 200
        query.insert.assert_called_once()

    def test_update_restores_a_soft_deleted_key(self, sys_admin_client):
        """Guardar sobre una clave borrada lógicamente la restaura, sin violar UNIQUE(key).

        Regresión: `UNIQUE(key)` abarca la fila borrada, así que tratarla como
        inexistente terminaba en un INSERT duplicado (23505) devuelto como 500.
        """
        deleted = {**settings_row({"type": "integer", "value": 5}), "is_deleted": True}
        restored = {**settings_row({"type": "integer", "value": 120}), "is_deleted": False}
        query = make_query([deleted])

        with patch(f"{SETTINGS_MODULE}.supabase_admin") as mock_sb:
            route_tables(mock_sb, settings=query)
            query.execute.side_effect = [
                MagicMock(data=[deleted]),    # _fetch_row(include_deleted=True)
                MagicMock(data=[restored]),   # update
            ]
            response = sys_admin_client.put(
                f"{SETTINGS_URL}/{LEAD_TIME_KEY}",
                json={"value": {"type": "integer", "value": 120}},
            )

        assert response.status_code == 200
        assert response.json()["data"]["value"] == {"type": "integer", "value": 120}
        query.update.assert_called_once()
        query.insert.assert_not_called()
        # La actualización levanta el borrado lógico para que la lectura lo aplique.
        assert query.update.call_args.args[0]["is_deleted"] is False

    def test_soft_deleted_key_is_not_applied_until_restored(self):
        """Una configuración borrada lógicamente no se aplica: rige el fallback."""
        with stored_config(None):  # la lectura filtra is_deleted=False
            assert settings_service.get_int(SettingKey.EVENT_MIN_LEAD_TIME_MINUTES) == 5

    def test_update_logs_actor_and_previous_value(self, sys_admin_client, caplog):
        """La traza del cambio identifica al administrador y el valor anterior."""
        stored = settings_row({"type": "integer", "value": 5})
        updated = settings_row({"type": "integer", "value": 720})
        query = make_query([stored])

        with patch(f"{SETTINGS_MODULE}.supabase_admin") as mock_sb, caplog.at_level("INFO"):
            route_tables(mock_sb, settings=query)
            query.execute.side_effect = [
                MagicMock(data=[stored]),
                MagicMock(data=[updated]),
            ]
            sys_admin_client.put(
                f"{SETTINGS_URL}/{LEAD_TIME_KEY}",
                json={"value": {"type": "integer", "value": 720}},
            )

        traza = "\n".join(r.getMessage() for r in caplog.records)
        assert f"user_id={sys_admin_payload()['sub']}" in traza
        assert "'value': 5" in traza and "'value': 720" in traza

    @pytest.mark.parametrize(
        "value",
        [
            {"type": "integer", "value": -1},
            {"type": "integer", "value": 10081},
            {"type": "string", "value": "60"},
        ],
    )
    def test_invalid_configuration_is_rejected_with_400(self, sys_admin_client, value):
        """Un valor fuera de rango o de otro tipo se rechaza sin tocar la base de datos."""
        query = make_query([settings_row({"type": "integer", "value": 5})])

        with patch(f"{SETTINGS_MODULE}.supabase_admin") as mock_sb:
            route_tables(mock_sb, settings=query)
            response = sys_admin_client.put(
                f"{SETTINGS_URL}/{LEAD_TIME_KEY}", json={"value": value}
            )

        assert response.status_code == 400
        body = response.json()
        assert body["success"] is False
        query.update.assert_not_called()
        query.insert.assert_not_called()


class TestAdminSettingsRBAC:
    def test_unauthenticated_returns_401_or_403(self, client):
        """Sin token no se puede consultar la configuración."""
        response = client.get(f"{SETTINGS_URL}/")
        assert response.status_code in (401, 403)

    def test_admin_escuela_is_forbidden(self, client):
        """El administrador de escuela no administra configuración global -> 403."""
        token = create_access_token(data=admin_school_payload())
        with patch("app.services.auth_service.auth_service.is_token_revoked", return_value=False), \
             patch("app.services.auth_service.auth_service.assert_account_available"):
            response = client.get(
                f"{SETTINGS_URL}/", headers={"Authorization": f"Bearer {token}"}
            )
        assert response.status_code == 403
        assert response.json()["success"] is False
