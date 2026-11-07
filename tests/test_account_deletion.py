"""Contrato HTTP de baja propia y traducción segura de resultados de la RPC."""

from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.core.security import create_access_token
from app.main import app
from app.services.account_deletion_service import account_deletion_service
from app.services.auth_service import auth_service


@pytest.fixture
def client():
    app.dependency_overrides.clear()
    with TestClient(app) as client:
        yield client
    app.dependency_overrides.clear()


@pytest.fixture
def available_account():
    with patch.object(auth_service, "assert_account_available"), \
         patch.object(auth_service, "is_token_revoked", return_value=False):
        yield


@pytest.mark.parametrize("role", ["admin_escuela", "doctor", "representante"])
def test_delete_own_account_success(client, available_account, role):
    token = create_access_token({"sub": "41", "role": role, "scope": "client"})
    with patch("app.services.account_deletion_service.supabase_admin") as sb:
        sb.rpc.return_value.execute.return_value.data = "ok"
        # Un ID extraño aportado por el cliente no decide qué usuario se elimina.
        response = client.delete("/api/v1/auth/me?user_id=99&school_id=9",
                                 headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200
    assert response.json() == {"success": True, "message": "Cuenta eliminada correctamente",
                               "data": None, "errors": None, "meta": None}
    name, args = sb.rpc.call_args.args
    assert name == "delete_own_account"
    assert args["p_user_id"] == 41
    assert args["p_token"] == token
    assert isinstance(args["p_expires_at"], int)


def test_delete_own_account_without_token(client):
    assert client.delete("/api/v1/auth/me").status_code == 401


def test_delete_own_account_system_admin_forbidden(client, available_account):
    token = create_access_token({"sub": "41", "role": "admin_sistema", "scope": "admin"})
    with patch.object(account_deletion_service, "delete_own_account") as delete:
        response = client.delete("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 403
    assert response.json()["success"] is False
    delete.assert_not_called()


@pytest.mark.parametrize("result,code", [
    ("last_school_admin", 409), ("active_medical_cases", 409), ("pending_reviews", 409),
    ("not_found", 401), ("forbidden", 403), (None, 500), ("unexpected", 500),
])
def test_deletion_rejection_envelope(client, available_account, result, code):
    token = create_access_token({"sub": "41", "role": "doctor", "scope": "client"})
    with patch("app.services.account_deletion_service.supabase_admin") as sb:
        sb.rpc.return_value.execute.return_value.data = result
        response = client.delete("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == code
    assert response.json()["success"] is False
    assert response.json()["data"] is None


def test_rpc_failure_is_sanitized(caplog):
    with patch("app.services.account_deletion_service.supabase_admin") as sb:
        sb.rpc.return_value.execute.side_effect = RuntimeError("secret-token")
        with pytest.raises(HTTPException) as caught:
            account_deletion_service.delete_own_account(41, "secret-token", 2000000000)
    assert caught.value.status_code == 500
    assert "secret-token" not in str(caught.value.detail) + caplog.text


def test_missing_privileged_client_is_safe_and_never_uses_anon():
    with patch("app.services.account_deletion_service.supabase_admin", None), \
         patch("app.core.database.supabase") as anonymous:
        with pytest.raises(HTTPException) as caught:
            account_deletion_service.delete_own_account(41, "secret-token", 2000000000)
    assert caught.value.status_code == 500
    anonymous.rpc.assert_not_called()


def test_deleted_user_login_still_returns_403(client):
    query = MagicMock()
    query.select.return_value = query.eq.return_value = query
    query.execute.return_value.data = [{"id": 41, "is_deleted": True, "is_active": False}]
    with patch("app.services.auth_service.supabase") as sb:
        sb.table.return_value = query
        response = client.post("/api/v1/auth/client/login", json={
            "email": "deleted@example.com", "password": "old-password",
        })
    assert response.status_code == 403
    assert response.json()["message"] == "El usuario ha sido eliminado"


def test_openapi_deletion_contract():
    operation = app.openapi()["paths"]["/api/v1/auth/me"]["delete"]
    assert "requestBody" not in operation
    assert operation["security"] == [{"HTTPBearer": []}]
    assert "409" in operation["responses"]
