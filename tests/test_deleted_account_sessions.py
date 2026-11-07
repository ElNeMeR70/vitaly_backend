"""Las sesiones se validan contra el estado actual de la cuenta en cada petición."""

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from jose import jwt

from app.core.config import get_settings
from app.main import app
from app.services.auth_service import AuthService, auth_service
from app.services.doctor_service import doctor_service


def token(**claims):
    payload = {
        "sub": "12", "role": "doctor", "scope": "client",
        "exp": int((datetime.now(timezone.utc) + timedelta(hours=1)).timestamp()),
    }
    payload.update(claims)
    return jwt.encode(payload, get_settings().SECRET_KEY, algorithm="HS256")


@pytest.fixture
def session():
    app.dependency_overrides.clear()
    query = MagicMock()
    query.select.return_value = query
    query.eq.return_value = query
    query.execute.return_value = MagicMock(data=[{"id": 12, "is_deleted": False}])
    with patch("app.services.auth_service.supabase") as database, \
         patch.object(auth_service, "is_token_revoked", return_value=False), \
         patch.object(doctor_service, "get_my_profile", return_value={}) as profile:
        database.table.return_value = query
        yield TestClient(app), query, profile
    app.dependency_overrides.clear()


def request(client, signed_token):
    return client.get("/api/v1/doctor/profile", headers={"Authorization": f"Bearer {signed_token}"})


def test_two_sessions_stop_accessing_after_account_deleted(session):
    client, query, profile = session
    sessions = [token(jti="session-one"), token(jti="session-two")]
    for signed_token in sessions:
        assert request(client, signed_token).status_code == 200
    query.execute.return_value.data[0]["is_deleted"] = True
    for signed_token in sessions:
        response = request(client, signed_token)
        assert response.status_code == 401
        assert response.json()["success"] is False
    assert profile.call_count == 2
    query.eq.assert_any_call("id", 12)
    query.select.assert_called_with("id, is_deleted")


def test_missing_account_is_unauthorized(session):
    client, query, profile = session
    query.execute.return_value.data = []
    assert request(client, token()).status_code == 401
    profile.assert_not_called()


def test_inactive_existing_account_preserves_previous_gate_behavior(session):
    client, query, _ = session
    query.execute.return_value.data[0]["is_active"] = False
    assert request(client, token()).status_code == 200


@pytest.mark.parametrize("claims", [
    {"sub": None}, {"sub": "abc"}, {"sub": "-1"}, {"sub": "0"},
    {"sub": "١٢"}, {"sub": True}, {"sub": 12},
    {"exp": None}, {"exp": "invalid"}, {"exp": True},
    {"exp": 9999999999.5}, {"exp": "9999999999"},
])
def test_malformed_session_claims_are_unauthorized(session, claims):
    client, query, profile = session
    assert request(client, token(**claims)).status_code == 401
    query.execute.assert_not_called()
    profile.assert_not_called()


def test_missing_expiration_is_unauthorized(session):
    client, query, _ = session
    signed_token = jwt.encode({"sub": "12", "role": "doctor", "scope": "client"},
                              get_settings().SECRET_KEY, algorithm="HS256")
    assert request(client, signed_token).status_code == 401
    query.execute.assert_not_called()


def test_account_lookup_failure_keeps_error_envelope_and_hides_provider_message(session, caplog):
    client, query, profile = session
    query.execute.side_effect = RuntimeError("sensitive-provider-message")
    response = request(client, token())
    assert response.status_code == 500
    assert response.json()["success"] is False
    assert "sensitive-provider-message" not in response.text + caplog.text
    profile.assert_not_called()


def test_revocation_lookup_failure_keeps_error_envelope_without_token(session, caplog):
    client, query, profile = session
    signed_token = token()
    query.execute.side_effect = RuntimeError(signed_token)
    with patch.object(auth_service, "is_token_revoked",
                      side_effect=lambda value: AuthService.is_token_revoked(auth_service, value)):
        response = request(client, signed_token)
    assert response.status_code == 500
    assert response.json()["success"] is False
    assert signed_token not in response.text + caplog.text
    profile.assert_not_called()
