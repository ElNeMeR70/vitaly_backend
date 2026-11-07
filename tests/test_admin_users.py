"""
Tests for System Admin User Management endpoints and service layer:
  - GET /api/v1/admin/users (get_all_users)
  - GET /api/v1/admin/users/pending (get_pending_users)
  - PATCH /api/v1/admin/users/approve (approve_user)
  - PATCH /api/v1/admin/users/approve/bulk (approve_users_bulk)
  - GET /api/v1/admin/users/{user_id}/details (get_user_details)
  - PATCH /api/v1/admin/users/{user_id}/toggle-active (toggle_user_active)

Adheres strictly to the 5 laws of vitaly-feature-testing:
  1. APIResponse envelope verification (success, data, message).
  2. Multi-tenancy and global administration: exclusive access for admin_sistema.
  3. Deterministic supabase-py mock.
  4. Correct status codes: 404 for missing entities, 200 for lists, 400 for business conflicts.
  5. RBAC matrix: 403 for unauthorized roles (admin_escuela, doctor, representante),
     401 for unauthenticated/revoked tokens, 422 for schema errors.
"""
try:
    from datetime import UTC
except ImportError:
    from datetime import timezone
    UTC = timezone.utc  # noqa: UP017
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app.api.deps import get_current_admin_sistema
from app.core.security import create_access_token
from app.main import app

SERVICE_MODULE = "app.services.admin_service"
ADMIN_USERS_BASE_URL = "/api/v1/admin"


# ──────────────────────────────────────────────────────────
# JWT Payload Helpers
# ──────────────────────────────────────────────────────────

def sys_admin_payload(user_id: int = 1) -> dict:
    return {
        "sub": str(user_id),
        "email": "sysadmin@vitaly.com",
        "role": "admin_sistema",
        "scope": "admin",
        "name": "Super Admin",
        "gender": "MASCULINO",
    }


def admin_school_payload(user_id: int = 2) -> dict:
    return {
        "sub": str(user_id),
        "email": "admin.escuela@vitaly.com",
        "role": "admin_escuela",
        "scope": "client",
        "name": "Directora",
        "gender": "FEMENINO",
    }


def doctor_payload(user_id: int = 3) -> dict:
    return {
        "sub": str(user_id),
        "email": "doctor@vitaly.com",
        "role": "doctor",
        "scope": "client",
        "name": "Dr. House",
        "gender": "MASCULINO",
    }


def parent_payload(user_id: int = 4) -> dict:
    return {
        "sub": str(user_id),
        "email": "parent@vitaly.com",
        "role": "representante",
        "scope": "client",
        "name": "Madre",
        "gender": "FEMENINO",
    }


# ──────────────────────────────────────────────────────────
# Mock Helpers
# ──────────────────────────────────────────────────────────

def make_query(data=None, count=None) -> MagicMock:
    query = MagicMock()
    for method in (
        "select", "eq", "neq", "in_", "is_", "ilike", "gte", "lte",
        "order", "range", "limit", "insert", "update", "delete", "or_", "contains"
    ):
        getattr(query, method).return_value = query
    query.execute.return_value = MagicMock(data=[] if data is None else data, count=count)
    return query


def route_tables(mock_supabase: MagicMock, **tables: MagicMock) -> None:
    mock_supabase.table.side_effect = lambda name: tables.get(name, make_query([]))


@pytest.fixture
def sys_admin_client():
    app.dependency_overrides[get_current_admin_sistema] = lambda: sys_admin_payload()
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


@pytest.fixture
def client():
    app.dependency_overrides.clear()
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


# ──────────────────────────────────────────────────────────
# 1. GET /users y GET /users/pending
# ──────────────────────────────────────────────────────────

class TestGetUsers:
    def test_get_all_users_success(self, sys_admin_client):
        """Listado de todos los usuarios retorna 200 con formato paginado y envelope."""
        users_mock = [
            {"id": 1, "email": "admin@vitaly.com", "name": "Admin", "lastname": "Sys", "birthday": "1980-01-01", "is_active": True, "is_deleted": False},
            {"id": 2, "email": "doc@vitaly.com", "name": "Doc", "lastname": "Med", "birthday": "1985-05-05", "is_active": False, "is_deleted": False},
        ]
        user_query = make_query(users_mock, count=2)

        with patch(f"{SERVICE_MODULE}.supabase") as mock_sb:
            route_tables(mock_sb, user=user_query)
            response = sys_admin_client.get(f"{ADMIN_USERS_BASE_URL}/users?page=1&size=20")

        assert response.status_code == 200
        body = response.json()
        assert body["success"] is True
        assert "Lista de todos los usuarios obtenida exitosamente" in body["message"]
        assert len(body["data"]["data"]) == 2
        assert body["data"]["total"] == 2
        assert body["data"]["page"] == 1

    def test_get_pending_users_filters_inactive(self, sys_admin_client):
        """Listado de usuarios pendientes retorna 200 y aplica filtro is_active=False."""
        pending_users = [
            {"id": 2, "email": "doc@vitaly.com", "name": "Doc", "lastname": "Med", "birthday": "1985-05-05", "is_active": False, "is_deleted": False},
        ]
        user_query = make_query(pending_users, count=1)

        with patch(f"{SERVICE_MODULE}.supabase") as mock_sb:
            route_tables(mock_sb, user=user_query)
            response = sys_admin_client.get(f"{ADMIN_USERS_BASE_URL}/users/pending?page=1&size=20")

        assert response.status_code == 200
        body = response.json()
        assert body["success"] is True
        assert "Usuarios pendientes obtenidos exitosamente" in body["message"]
        assert len(body["data"]["data"]) == 1
        assert body["data"]["data"][0]["is_active"] is False


# ──────────────────────────────────────────────────────────
# 2. PATCH /users/approve y PATCH /users/approve/bulk
# ──────────────────────────────────────────────────────────

class TestApproveUsers:
    def test_approve_user_success(self, sys_admin_client):
        """Aprobación individual de usuario inactivo pasa is_active a True."""
        user_data = [{"is_active": False, "is_deleted": False}]
        user_query = make_query(user_data)

        with patch(f"{SERVICE_MODULE}.supabase") as mock_sb:
            route_tables(mock_sb, user=user_query)
            payload = {"user_id_to_approve": 10, "admin_id": 1}
            response = sys_admin_client.patch(f"{ADMIN_USERS_BASE_URL}/users/approve", json=payload)

        assert response.status_code == 200
        body = response.json()
        assert body["success"] is True
        assert "aprobado exitosamente" in body["data"]

    def test_approve_user_not_found_returns_404(self, sys_admin_client):
        """Usuario a aprobar no existe -> 404 Not Found."""
        user_query = make_query([])

        with patch(f"{SERVICE_MODULE}.supabase") as mock_sb:
            route_tables(mock_sb, user=user_query)
            payload = {"user_id_to_approve": 999, "admin_id": 1}
            response = sys_admin_client.patch(f"{ADMIN_USERS_BASE_URL}/users/approve", json=payload)

        assert response.status_code == 404
        body = response.json()
        assert body["success"] is False
        assert "no encontrado" in body["message"]

    def test_approve_user_already_active_returns_400(self, sys_admin_client):
        """Usuario ya aprobado previamente -> 400 Bad Request."""
        user_data = [{"is_active": True, "is_deleted": False}]
        user_query = make_query(user_data)

        with patch(f"{SERVICE_MODULE}.supabase") as mock_sb:
            route_tables(mock_sb, user=user_query)
            payload = {"user_id_to_approve": 10, "admin_id": 1}
            response = sys_admin_client.patch(f"{ADMIN_USERS_BASE_URL}/users/approve", json=payload)

        assert response.status_code == 400
        body = response.json()
        assert body["success"] is False
        assert "ya se encuentra aprobado" in body["message"]

    def test_approve_user_deleted_returns_400(self, sys_admin_client):
        """Usuario marcado como eliminado -> 400 Bad Request."""
        user_data = [{"is_active": False, "is_deleted": True}]
        user_query = make_query(user_data)

        with patch(f"{SERVICE_MODULE}.supabase") as mock_sb:
            route_tables(mock_sb, user=user_query)
            payload = {"user_id_to_approve": 10, "admin_id": 1}
            response = sys_admin_client.patch(f"{ADMIN_USERS_BASE_URL}/users/approve", json=payload)

        assert response.status_code == 400
        body = response.json()
        assert body["success"] is False
        assert "porque está eliminado" in body["message"]

    def test_approve_users_bulk_success(self, sys_admin_client):
        """Aprobación masiva de múltiples usuarios inactivos."""
        updated_data = [{"id": 10}, {"id": 11}]
        user_query = make_query(updated_data)

        with patch(f"{SERVICE_MODULE}.supabase") as mock_sb:
            route_tables(mock_sb, user=user_query)
            payload = {"user_ids_to_approve": [10, 11], "admin_id": 1}
            response = sys_admin_client.patch(f"{ADMIN_USERS_BASE_URL}/users/approve/bulk", json=payload)

        assert response.status_code == 200
        body = response.json()
        assert body["success"] is True
        assert "Se han aprobado exitosamente 2 usuarios" in body["data"]

    def test_approve_users_bulk_zero_updated(self, sys_admin_client):
        """Aprobación masiva cuando ningún usuario cumplió las condiciones."""
        user_query = make_query([])

        with patch(f"{SERVICE_MODULE}.supabase") as mock_sb:
            route_tables(mock_sb, user=user_query)
            payload = {"user_ids_to_approve": [999], "admin_id": 1}
            response = sys_admin_client.patch(f"{ADMIN_USERS_BASE_URL}/users/approve/bulk", json=payload)

        assert response.status_code == 200
        body = response.json()
        assert body["success"] is True
        assert "No se ha aprobado ningún usuario" in body["data"]


# ──────────────────────────────────────────────────────────
# 3. GET /users/{user_id}/details
# ──────────────────────────────────────────────────────────

class TestGetUserDetails:
    def test_get_user_details_doctor_success(self, sys_admin_client):
        """Detalle de doctor incluye información clínica y rol."""
        user_data = [{"id": 5, "email": "doc@vitaly.com", "name": "Roberto", "lastname": "Sánchez", "birthday": "1980-01-01", "is_active": True, "is_deleted": False}]
        role_data = [{"role": "doctor"}]
        doc_data = [{"doc_id": 1, "doc_license_number": 9999, "especially": "Pediatría"}]

        user_query = make_query(user_data)
        role_query = make_query(role_data)
        doc_query = make_query(doc_data)

        with patch(f"{SERVICE_MODULE}.supabase") as mock_sb:
            route_tables(mock_sb, user=user_query, user_roles=role_query, doctor=doc_query)
            response = sys_admin_client.get(f"{ADMIN_USERS_BASE_URL}/users/5/details")

        assert response.status_code == 200
        body = response.json()
        assert body["success"] is True
        assert body["data"]["role"] == "doctor"
        assert body["data"]["details"]["especially"] == "Pediatría"

    def test_get_user_details_school_admin_success(self, sys_admin_client):
        """Detalle de admin_escuela incluye información de su cargo y escuela."""
        user_data = [{"id": 6, "email": "admin@colegio.com", "name": "Laura", "lastname": "Pérez", "birthday": "1988-06-20", "is_active": True, "is_deleted": False}]
        role_data = [{"role": "admin_escuela"}]
        sch_admin_data = [{"sch_admin_id": 2, "school_id": 1, "administrative_position": "Directora"}]

        user_query = make_query(user_data)
        role_query = make_query(role_data)
        sch_query = make_query(sch_admin_data)

        with patch(f"{SERVICE_MODULE}.supabase") as mock_sb:
            route_tables(mock_sb, user=user_query, user_roles=role_query, school_administrator=sch_query)
            response = sys_admin_client.get(f"{ADMIN_USERS_BASE_URL}/users/6/details")

        assert response.status_code == 200
        body = response.json()
        assert body["success"] is True
        assert body["data"]["role"] == "admin_escuela"
        assert body["data"]["details"]["administrative_position"] == "Directora"

    def test_get_user_details_not_found_returns_404(self, sys_admin_client):
        """Usuario inexistente -> 404 Not Found."""
        user_query = make_query([])

        with patch(f"{SERVICE_MODULE}.supabase") as mock_sb:
            route_tables(mock_sb, user=user_query)
            response = sys_admin_client.get(f"{ADMIN_USERS_BASE_URL}/users/999/details")

        assert response.status_code == 404
        body = response.json()
        assert body["success"] is False
        assert "Usuario no encontrado" in body["message"]


# ──────────────────────────────────────────────────────────
# 4. PATCH /users/{user_id}/toggle-active
# ──────────────────────────────────────────────────────────

class TestToggleUserActive:
    def test_toggle_user_active_success(self, sys_admin_client):
        """Alternar estado de activo a inactivo exitosamente."""
        user_data = [{"is_active": True, "is_deleted": False}]
        role_data = [{"role": "doctor"}]

        user_query = make_query(user_data)
        role_query = make_query(role_data)

        with patch(f"{SERVICE_MODULE}.supabase") as mock_sb:
            route_tables(mock_sb, user=user_query, user_roles=role_query)
            payload = {"user_id_to_toggle": 5, "admin_id": 1}
            response = sys_admin_client.patch(f"{ADMIN_USERS_BASE_URL}/users/5/toggle-active", json=payload)

        assert response.status_code == 200
        body = response.json()
        assert body["success"] is True
        assert "cambiado a Inactivo" in body["data"]

    def test_toggle_user_active_prevents_self_disable(self, sys_admin_client):
        """Un administrador intentando deshabilitarse a sí mismo -> 400 Bad Request."""
        payload = {"user_id_to_toggle": 1, "admin_id": 1}
        response = sys_admin_client.patch(f"{ADMIN_USERS_BASE_URL}/users/1/toggle-active", json=payload)

        assert response.status_code == 400
        body = response.json()
        assert body["success"] is False
        assert "No puedes deshabilitarte a ti mismo" in body["message"]

    def test_toggle_user_active_target_not_found_returns_404(self, sys_admin_client):
        """Usuario destino no encontrado -> 404 Not Found."""
        user_query = make_query([])

        with patch(f"{SERVICE_MODULE}.supabase") as mock_sb:
            route_tables(mock_sb, user=user_query)
            payload = {"user_id_to_toggle": 999, "admin_id": 1}
            response = sys_admin_client.patch(f"{ADMIN_USERS_BASE_URL}/users/999/toggle-active", json=payload)

        assert response.status_code == 404
        body = response.json()
        assert body["success"] is False
        assert "no encontrado" in body["message"]

    def test_toggle_user_active_hierarchy_check_returns_403(self, sys_admin_client):
        """Alterar a otro admin_sistema con nivel igual o superior -> 403 Forbidden."""
        user_data = [{"is_active": True, "is_deleted": False}]
        role_data = [{"role": "admin_sistema"}]
        # Solicitante nivel 2, destino nivel 2 (igual nivel: no puede alterar a iguales o superiores)
        sys_admin_query = make_query([{"level_privilege": 2}])

        user_query = make_query(user_data)
        role_query = make_query(role_data)

        with patch(f"{SERVICE_MODULE}.supabase") as mock_sb:
            route_tables(
                mock_sb,
                user=user_query,
                user_roles=role_query,
                system_administrator=sys_admin_query,
            )

            payload = {"user_id_to_toggle": 2, "admin_id": 1}
            response = sys_admin_client.patch(f"{ADMIN_USERS_BASE_URL}/users/2/toggle-active", json=payload)

        assert response.status_code == 403
        body = response.json()
        assert body["success"] is False
        assert "No tienes suficientes privilegios" in body["message"]


# ──────────────────────────────────────────────────────────
# 5. Matriz RBAC para Endpoints de Admin Usuarios
# ──────────────────────────────────────────────────────────

class TestAdminUsersRBAC:
    def test_get_users_unauthenticated_returns_401_or_403(self, client):
        """Llamada sin token -> 401/403."""
        response = client.get(f"{ADMIN_USERS_BASE_URL}/users")
        assert response.status_code in (401, 403)

    def test_get_users_forbidden_for_admin_escuela(self, client):
        """Rol admin_escuela intentando acceder -> 403 Forbidden."""
        token = create_access_token(data=admin_school_payload())
        with patch("app.services.auth_service.auth_service.is_token_revoked", return_value=False), \
             patch("app.services.auth_service.auth_service.assert_account_available"):
            response = client.get(
                f"{ADMIN_USERS_BASE_URL}/users",
                headers={"Authorization": f"Bearer {token}"},
            )
        assert response.status_code == 403
        body = response.json()
        assert body["success"] is False
        assert "token emitido por el portal de administración" in body["message"]

    def test_get_users_forbidden_for_doctor(self, client):
        """Rol doctor intentando acceder -> 403 Forbidden."""
        token = create_access_token(data=doctor_payload())
        with patch("app.services.auth_service.auth_service.is_token_revoked", return_value=False), \
             patch("app.services.auth_service.auth_service.assert_account_available"):
            response = client.get(
                f"{ADMIN_USERS_BASE_URL}/users",
                headers={"Authorization": f"Bearer {token}"},
            )
        assert response.status_code == 403
        body = response.json()
        assert body["success"] is False
        assert "token emitido por el portal de administración" in body["message"]

    def test_get_users_forbidden_for_parent(self, client):
        """Rol representante intentando acceder -> 403 Forbidden."""
        token = create_access_token(data=parent_payload())
        with patch("app.services.auth_service.auth_service.is_token_revoked", return_value=False), \
             patch("app.services.auth_service.auth_service.assert_account_available"):
            response = client.get(
                f"{ADMIN_USERS_BASE_URL}/users",
                headers={"Authorization": f"Bearer {token}"},
            )
        assert response.status_code == 403
        body = response.json()
        assert body["success"] is False
        assert "token emitido por el portal de administración" in body["message"]
