"""
Tests for Auth endpoints and service layer:
  - POST /api/v1/auth/register-system-admin
  - POST /api/v1/auth/register-school-admin
  - POST /api/v1/auth/register-doctor
  - POST /api/v1/auth/register-parent
  - POST /api/v1/auth/client/login (portal de clientes, scope=client)
  - POST /api/v1/auth/login        (alias obsoleto del portal de clientes)
  - POST /api/v1/auth/admin/login  (portal de administración, scope=admin)
  - POST /api/v1/auth/logout
  - POST /api/v1/auth/change-password (cualquier sesión válida, ambos portales)
  - Gates de procedencia get_current_admin / get_current_client (claim `scope`)

Adheres strictly to the 5 laws of vitaly-feature-testing:
  1. APIResponse envelope verification (success, data, message).
  2. Multi-tenancy isolation & token claim security (NO school_id in JWT claims).
  3. Deterministic supabase-py mock.
  4. Inactive/deleted user rejection (403), wrong credentials (401), duplicate registration (400).
  5. RBAC & revocation: revoked tokens rejected with 401, logout invalidation, 422 schema validation.
"""
try:
    from datetime import UTC
except ImportError:
    from datetime import timezone
    UTC = timezone.utc  # noqa: UP017
from datetime import date
from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from jose import jwt
from postgrest.exceptions import APIError

from app.api.deps import get_current_admin, get_current_client
from app.core.config import get_settings
from app.core.security import ALGORITHM, create_access_token, get_password_hash, verify_password
from app.main import app
from app.schemas.user import PasswordChangeCreate, SystemAdminRegister
from app.services.auth_service import auth_service
from tests.fake_supabase import FakeSupabase

SERVICE_MODULE = "app.services.auth_service"
AUTH_BASE_URL = "/api/v1/auth"
settings = get_settings()

TEST_PASSWORD = "SecurePassword123!"
HASHED_PASSWORD = get_password_hash(TEST_PASSWORD)


# ──────────────────────────────────────────────────────────
# Chainable supabase-py mock helpers
# ──────────────────────────────────────────────────────────

def make_query(data=None, count=None) -> MagicMock:
    """Chainable mock for supabase-py queries."""
    query = MagicMock()
    for method in (
        "select", "eq", "neq", "in_", "is_", "ilike", "gte", "lte",
        "order", "range", "limit", "insert", "update", "delete", "or_", "contains"
    ):
        getattr(query, method).return_value = query
    query.execute.return_value = MagicMock(data=[] if data is None else data, count=count)
    return query


def route_tables(mock_supabase: MagicMock, **tables: MagicMock) -> None:
    """Route supabase.table(...) calls to table-specific query mocks."""
    mock_supabase.table.side_effect = lambda name: tables.get(name, make_query([]))


@pytest.fixture
def client():
    """Client with no dependency overrides: auth gates and endpoints run normally."""
    app.dependency_overrides.clear()
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


# ──────────────────────────────────────────────────────────
# 1. POST /register-system-admin
# ──────────────────────────────────────────────────────────

class TestRegisterSystemAdmin:
    def test_register_system_admin_success(self, client):
        """Registro exitoso de admin_sistema retorna 200 con envelope y crea usuario inactivo."""
        user_insert = make_query([{"id": 10}])
        roles_query = make_query([{"id": 1}])
        sys_admin_query = make_query([{"admin_id": 10}])

        user_select = make_query([])
        user_table = MagicMock()
        user_table.select.return_value = user_select
        user_table.insert.return_value = user_insert

        with patch(f"{SERVICE_MODULE}.supabase") as mock_sb:
            route_tables(
                mock_sb,
                user=user_table,
                user_roles=roles_query,
                system_administrator=sys_admin_query,
            )

            payload = {
                "nombre": "Carlos",
                "apellido": "Mendoza",
                "correo": "admin.sistema@vitaly.com",
                "contraseña": TEST_PASSWORD,
                "fecha_de_nacimiento": "1985-04-12",
            }
            response = client.post(f"{AUTH_BASE_URL}/register-system-admin", json=payload)

        assert response.status_code == 200
        body = response.json()
        assert body["success"] is True
        assert "Registro completado para administrador de sistema" in body["message"]
        assert "creado exitosamente" in body["data"]

        # Verify base user insert was called with is_active=False
        user_table.insert.assert_called_once()
        inserted_user = user_table.insert.call_args[0][0]
        assert inserted_user["email"] == "admin.sistema@vitaly.com"
        assert inserted_user["is_active"] is False
        assert inserted_user["is_deleted"] is False

        # Verify system_administrator insert
        sys_admin_query.insert.assert_called_once_with({"admin_id": 10, "level_privilege": 1})

    def test_register_system_admin_duplicate_email_returns_400(self, client):
        """Intento de registro con correo existente -> 400 Bad Request."""
        user_query = make_query([{"id": 5}])
        roles_query = make_query([{"role": "admin_sistema"}])

        with patch(f"{SERVICE_MODULE}.supabase") as mock_sb:
            route_tables(mock_sb, user=user_query, user_roles=roles_query)

            payload = {
                "nombre": "Carlos",
                "apellido": "Mendoza",
                "correo": "existente@vitaly.com",
                "contraseña": TEST_PASSWORD,
                "fecha_de_nacimiento": "1985-04-12",
            }
            response = client.post(f"{AUTH_BASE_URL}/register-system-admin", json=payload)

        assert response.status_code == 400
        body = response.json()
        assert body["success"] is False
        assert "El usuario ya existe bajo el rol: admin_sistema" in body["message"]

    def test_register_system_admin_invalid_schema_returns_422(self, client):
        """Payload incompleto (sin contraseña) -> 422 Unprocessable Entity."""
        payload = {
            "nombre": "Carlos",
            "apellido": "Mendoza",
            "correo": "invalid-email",
        }
        response = client.post(f"{AUTH_BASE_URL}/register-system-admin", json=payload)
        assert response.status_code == 422
        body = response.json()
        assert body["success"] is False


# ──────────────────────────────────────────────────────────
# 2. POST /register-school-admin
# ──────────────────────────────────────────────────────────

class TestRegisterSchoolAdmin:
    def test_register_school_admin_success(self, client):
        """Registro exitoso de admin_escuela con school_id y administrative_position."""
        user_insert = make_query([{"id": 20}])
        roles_query = make_query([{"id": 2}])
        school_admin_query = make_query([{"sch_admin_id": 1}])

        user_select = make_query([])
        user_table = MagicMock()
        user_table.select.return_value = user_select
        user_table.insert.return_value = user_insert

        with patch(f"{SERVICE_MODULE}.supabase") as mock_sb:
            route_tables(
                mock_sb,
                user=user_table,
                user_roles=roles_query,
                school_administrator=school_admin_query,
            )

            payload = {
                "nombre": "María",
                "apellido": "González",
                "correo": "admin.escuela@vitaly.com",
                "contraseña": TEST_PASSWORD,
                "fecha_de_nacimiento": "1988-06-20",
                "school_id": 1,
                "administrative_position": "Directora General",
            }
            response = client.post(f"{AUTH_BASE_URL}/register-school-admin", json=payload)

        assert response.status_code == 200
        body = response.json()
        assert body["success"] is True
        assert "Registro completado para administrador de escuela" in body["message"]

        # Verify school_administrator insert: status=2 (Inactivo/Pendiente)
        school_admin_query.insert.assert_called_once_with({
            "user_id": 20,
            "school_id": 1,
            "administrative_position": "Directora General",
            "status": 2,
        })

    def test_register_school_admin_duplicate_email_returns_400(self, client):
        """Intento de registro con correo existente de admin_escuela -> 400."""
        user_query = make_query([{"id": 20}])
        roles_query = make_query([{"role": "admin_escuela"}])

        with patch(f"{SERVICE_MODULE}.supabase") as mock_sb:
            route_tables(mock_sb, user=user_query, user_roles=roles_query)

            payload = {
                "nombre": "María",
                "apellido": "González",
                "correo": "admin.escuela@vitaly.com",
                "contraseña": TEST_PASSWORD,
                "fecha_de_nacimiento": "1988-06-20",
                "school_id": 1,
                "administrative_position": "Directora General",
            }
            response = client.post(f"{AUTH_BASE_URL}/register-school-admin", json=payload)

        assert response.status_code == 400
        body = response.json()
        assert body["success"] is False
        assert "El usuario ya existe bajo el rol: admin_escuela" in body["message"]

    def test_register_school_admin_missing_school_id_returns_422(self, client):
        """Omisión de school_id requerido -> 422 Unprocessable Entity."""
        payload = {
            "nombre": "María",
            "apellido": "González",
            "correo": "admin.escuela@vitaly.com",
            "contraseña": TEST_PASSWORD,
            "fecha_de_nacimiento": "1988-06-20",
            "administrative_position": "Directora General",
        }
        response = client.post(f"{AUTH_BASE_URL}/register-school-admin", json=payload)
        assert response.status_code == 422


# ──────────────────────────────────────────────────────────
# 3. POST /register-doctor
# ──────────────────────────────────────────────────────────

class TestRegisterDoctor:
    def test_register_doctor_success(self, client):
        """Registro exitoso de doctor con doc_license_number y especially."""
        user_insert = make_query([{"id": 30}])
        roles_query = make_query([{"id": 3}])
        doctor_query = make_query([{"doc_id": 1}])

        user_select = make_query([])
        user_table = MagicMock()
        user_table.select.return_value = user_select
        user_table.insert.return_value = user_insert

        with patch(f"{SERVICE_MODULE}.supabase") as mock_sb:
            route_tables(
                mock_sb,
                user=user_table,
                user_roles=roles_query,
                doctor=doctor_query,
            )

            payload = {
                "nombre": "Roberto",
                "apellido": "Sánchez",
                "correo": "doctor@vitaly.com",
                "contraseña": TEST_PASSWORD,
                "fecha_de_nacimiento": "1980-01-15",
                "doc_license_number": 98765,
                "especially": "Pediatría Integral",
            }
            response = client.post(f"{AUTH_BASE_URL}/register-doctor", json=payload)

        assert response.status_code == 200
        body = response.json()
        assert body["success"] is True
        assert "Registro completado para doctor" in body["message"]

        doctor_query.insert.assert_called_once_with({
            "user_id": 30,
            "doc_license_number": 98765,
            "especially": "Pediatría Integral",
        })

    def test_register_doctor_duplicate_email_returns_400(self, client):
        """Intento de registro con correo existente de doctor -> 400."""
        user_query = make_query([{"id": 30}])
        roles_query = make_query([{"role": "doctor"}])

        with patch(f"{SERVICE_MODULE}.supabase") as mock_sb:
            route_tables(mock_sb, user=user_query, user_roles=roles_query)

            payload = {
                "nombre": "Roberto",
                "apellido": "Sánchez",
                "correo": "doctor@vitaly.com",
                "contraseña": TEST_PASSWORD,
                "fecha_de_nacimiento": "1980-01-15",
                "doc_license_number": 98765,
                "especially": "Pediatría",
            }
            response = client.post(f"{AUTH_BASE_URL}/register-doctor", json=payload)

        assert response.status_code == 400
        body = response.json()
        assert body["success"] is False
        assert "El usuario ya existe bajo el rol: doctor" in body["message"]

    def test_register_doctor_missing_license_returns_422(self, client):
        """Omisión de doc_license_number -> 422."""
        payload = {
            "nombre": "Roberto",
            "apellido": "Sánchez",
            "correo": "doctor@vitaly.com",
            "contraseña": TEST_PASSWORD,
            "fecha_de_nacimiento": "1980-01-15",
            "especially": "Pediatría",
        }
        response = client.post(f"{AUTH_BASE_URL}/register-doctor", json=payload)
        assert response.status_code == 422


# ──────────────────────────────────────────────────────────
# 4. POST /register-parent
# ──────────────────────────────────────────────────────────

class TestRegisterParent:
    def test_register_parent_success_without_linked_students(self, client):
        """Registro de representante sin coincidencias de estudiantes."""
        user_insert = make_query([{"id": 40}])
        roles_query = make_query([{"id": 4}])
        parent_query = make_query([{"id": 1}])

        user_select = make_query([])
        user_table = MagicMock()
        user_table.select.return_value = user_select
        user_table.insert.return_value = user_insert

        with patch(f"{SERVICE_MODULE}.supabase") as mock_sb, \
             patch("app.services.parent_service.parent_service.link_students_by_identity", return_value={"linked_students": []}) as mock_link:
            route_tables(
                mock_sb,
                user=user_table,
                user_roles=roles_query,
                parent=parent_query,
            )

            payload = {
                "nombre": "Ana",
                "apellido": "López",
                "correo": "parent@vitaly.com",
                "contraseña": TEST_PASSWORD,
                "fecha_de_nacimiento": "1990-09-05",
                "identity_number": "V-12345678",
                "type_representative": "Madre",
                "occupation": "Ingeniera de Sistemas",
            }
            response = client.post(f"{AUTH_BASE_URL}/register-parent", json=payload)

        assert response.status_code == 200
        body = response.json()
        assert body["success"] is True
        assert "Registro completado para representante" in body["message"]
        assert "Debe esperar la verificación del sistema" in body["data"]
        mock_link.assert_called_once_with(user_id=40, identity_number="V-12345678")

    def test_register_parent_success_with_auto_linked_students(self, client):
        """Registro de representante con vinculación automática de alumnos autorizados."""
        user_insert = make_query([{"id": 41}])
        roles_query = make_query([{"id": 4}])
        parent_query = make_query([{"id": 2}])

        user_select = make_query([])
        user_table = MagicMock()
        user_table.select.return_value = user_select
        user_table.insert.return_value = user_insert

        link_result = {
            "linked_students": [{"student_id": 1, "name": "Pedro López"}, {"student_id": 2, "name": "Lucía López"}],
            "message": "Se vincularon 2 estudiantes",
        }

        with patch(f"{SERVICE_MODULE}.supabase") as mock_sb, \
             patch("app.services.parent_service.parent_service.link_students_by_identity", return_value=link_result):
            route_tables(
                mock_sb,
                user=user_table,
                user_roles=roles_query,
                parent=parent_query,
            )

            payload = {
                "nombre": "Ana",
                "apellido": "López",
                "correo": "parent.linked@vitaly.com",
                "contraseña": TEST_PASSWORD,
                "fecha_de_nacimiento": "1990-09-05",
                "identity_number": "V-12345678",
                "type_representative": "Madre",
            }
            response = client.post(f"{AUTH_BASE_URL}/register-parent", json=payload)

        assert response.status_code == 200
        body = response.json()
        assert "Se vincularon automáticamente 2 estudiante(s)" in body["data"]

    def test_register_parent_duplicate_email_returns_400(self, client):
        """Intento de registro con correo existente de representante -> 400."""
        user_query = make_query([{"id": 40}])
        roles_query = make_query([{"role": "representante"}])

        with patch(f"{SERVICE_MODULE}.supabase") as mock_sb:
            route_tables(mock_sb, user=user_query, user_roles=roles_query)

            payload = {
                "nombre": "Ana",
                "apellido": "López",
                "correo": "parent@vitaly.com",
                "contraseña": TEST_PASSWORD,
                "fecha_de_nacimiento": "1990-09-05",
                "identity_number": "V-12345678",
                "type_representative": "Madre",
            }
            response = client.post(f"{AUTH_BASE_URL}/register-parent", json=payload)

        assert response.status_code == 400
        body = response.json()
        assert body["success"] is False
        assert "El usuario ya existe bajo el rol: representante" in body["message"]

    def test_register_parent_missing_identity_returns_422(self, client):
        """Omisión de identity_number en el payload de representante -> 422."""
        payload = {
            "nombre": "Ana",
            "apellido": "López",
            "correo": "parent@vitaly.com",
            "contraseña": TEST_PASSWORD,
            "fecha_de_nacimiento": "1990-09-05",
            "type_representative": "Madre",
        }
        response = client.post(f"{AUTH_BASE_URL}/register-parent", json=payload)
        assert response.status_code == 422


# ──────────────────────────────────────────────────────────
# 5. POST /client/login - Claims estrictos y reglas de autenticación
# ──────────────────────────────────────────────────────────

class TestLogin:
    def test_admin_login_system_admin_success_claims_parity(self, client):
        """Login de admin_sistema por el portal de administración: scope=admin, sub, role, email y SIN school_id."""
        user_data = {
            "id": 100,
            "email": "sysadmin@vitaly.com",
            "password": HASHED_PASSWORD,
            "name": "Carlos",
            "lastname": "Mendoza",
            "gender": "MASCULINO",
            "is_active": True,
            "is_deleted": False,
        }
        user_query = make_query([user_data])
        roles_query = make_query([{"role": "admin_sistema"}])

        with patch(f"{SERVICE_MODULE}.supabase") as mock_sb:
            route_tables(mock_sb, user=user_query, user_roles=roles_query)

            payload = {"email": "sysadmin@vitaly.com", "password": TEST_PASSWORD}
            response = client.post(f"{AUTH_BASE_URL}/admin/login", json=payload)

        assert response.status_code == 200
        body = response.json()
        assert body["success"] is True
        assert body["message"] == "Login exitoso"
        token = body["data"]["access_token"]
        assert body["data"]["token_type"] == "bearer"

        # Decode token and verify claims
        claims = jwt.decode(token, settings.SECRET_KEY, algorithms=[ALGORITHM])
        assert claims["sub"] == "100"
        assert claims["email"] == "sysadmin@vitaly.com"
        assert claims["role"] == "admin_sistema"
        assert claims["scope"] == "admin"
        assert claims["name"] == "Carlos Mendoza"
        assert claims["gender"] == "MASCULINO"
        # CRITICAL TENANCY LAW: school_id MUST NOT be in JWT claims
        assert "school_id" not in claims

    def test_login_school_admin_success_includes_metadata_without_school_id(self, client):
        """Login de admin_escuela: incluye cargo y nombre de escuela, pero NO school_id."""
        user_data = {
            "id": 101,
            "email": "admin.escuela@vitaly.com",
            "password": HASHED_PASSWORD,
            "name": "Laura",
            "lastname": "Pérez",
            "gender": "FEMENINO",
            "is_active": True,
            "is_deleted": False,
        }
        user_query = make_query([user_data])
        roles_query = make_query([{"role": "admin_escuela"}])
        admin_query = make_query([{"administrative_position": "Directora", "school_id": 1}])
        school_query = make_query([{"name": "Colegio San Ignacio"}])

        with patch(f"{SERVICE_MODULE}.supabase") as mock_sb:
            route_tables(
                mock_sb,
                user=user_query,
                user_roles=roles_query,
                school_administrator=admin_query,
                school=school_query,
            )

            payload = {"email": "admin.escuela@vitaly.com", "password": TEST_PASSWORD}
            response = client.post(f"{AUTH_BASE_URL}/client/login", json=payload)

        assert response.status_code == 200
        body = response.json()
        token = body["data"]["access_token"]
        claims = jwt.decode(token, settings.SECRET_KEY, algorithms=[ALGORITHM])
        assert claims["role"] == "admin_escuela"
        assert claims["scope"] == "client"
        assert claims["administrative_position"] == "Directora"
        assert claims["school"] == "Colegio San Ignacio"
        assert "school_id" not in claims

    def test_login_doctor_success_includes_license_and_specialty(self, client):
        """Login de doctor: incluye especialidad y licencia profesional."""
        user_data = {
            "id": 102,
            "email": "doctor@vitaly.com",
            "password": HASHED_PASSWORD,
            "name": "Roberto",
            "lastname": "Sánchez",
            "gender": "MASCULINO",
            "is_active": True,
            "is_deleted": False,
        }
        user_query = make_query([user_data])
        roles_query = make_query([{"role": "doctor"}])
        doc_query = make_query([{"especially": "Pediatría", "doc_license_number": 98765}])

        with patch(f"{SERVICE_MODULE}.supabase") as mock_sb:
            route_tables(
                mock_sb,
                user=user_query,
                user_roles=roles_query,
                doctor=doc_query,
            )

            payload = {"email": "doctor@vitaly.com", "password": TEST_PASSWORD}
            response = client.post(f"{AUTH_BASE_URL}/client/login", json=payload)

        assert response.status_code == 200
        body = response.json()
        token = body["data"]["access_token"]
        claims = jwt.decode(token, settings.SECRET_KEY, algorithms=[ALGORITHM])
        assert claims["role"] == "doctor"
        assert claims["scope"] == "client"
        assert claims["especially"] == "Pediatría"
        assert claims["doc_license_number"] == 98765
        assert "school_id" not in claims

    def test_login_parent_success_includes_type_representative(self, client):
        """Login de representante: incluye tipo de parentesco."""
        user_data = {
            "id": 103,
            "email": "parent@vitaly.com",
            "password": HASHED_PASSWORD,
            "name": "Ana",
            "lastname": "López",
            "gender": "FEMENINO",
            "is_active": True,
            "is_deleted": False,
        }
        user_query = make_query([user_data])
        roles_query = make_query([{"role": "representante"}])
        parent_query = make_query([{"type_representative": "Madre"}])

        with patch(f"{SERVICE_MODULE}.supabase") as mock_sb:
            route_tables(
                mock_sb,
                user=user_query,
                user_roles=roles_query,
                parent=parent_query,
            )

            payload = {"email": "parent@vitaly.com", "password": TEST_PASSWORD}
            response = client.post(f"{AUTH_BASE_URL}/client/login", json=payload)

        assert response.status_code == 200
        body = response.json()
        token = body["data"]["access_token"]
        claims = jwt.decode(token, settings.SECRET_KEY, algorithms=[ALGORITHM])
        assert claims["role"] == "representante"
        assert claims["scope"] == "client"
        assert claims["type_representative"] == "Madre"
        assert "school_id" not in claims

    def test_login_inactive_user_returns_403(self, client):
        """Usuario con is_active=False -> 403 Forbidden ('El usuario está inactivo')."""
        user_data = {
            "id": 104,
            "email": "inactivo@vitaly.com",
            "password": HASHED_PASSWORD,
            "is_active": False,
            "is_deleted": False,
        }
        user_query = make_query([user_data])

        with patch(f"{SERVICE_MODULE}.supabase") as mock_sb:
            route_tables(mock_sb, user=user_query)

            payload = {"email": "inactivo@vitaly.com", "password": TEST_PASSWORD}
            response = client.post(f"{AUTH_BASE_URL}/client/login", json=payload)

        assert response.status_code == 403
        body = response.json()
        assert body["success"] is False
        assert "El usuario está inactivo, debe esperar verificación" in body["message"]

    def test_login_deleted_user_returns_403(self, client):
        """Usuario con is_deleted=True -> 403 Forbidden ('El usuario ha sido eliminado')."""
        user_data = {
            "id": 105,
            "email": "eliminado@vitaly.com",
            "password": HASHED_PASSWORD,
            "is_active": True,
            "is_deleted": True,
        }
        user_query = make_query([user_data])

        with patch(f"{SERVICE_MODULE}.supabase") as mock_sb:
            route_tables(mock_sb, user=user_query)

            payload = {"email": "eliminado@vitaly.com", "password": TEST_PASSWORD}
            response = client.post(f"{AUTH_BASE_URL}/client/login", json=payload)

        assert response.status_code == 403
        body = response.json()
        assert body["success"] is False
        assert "El usuario ha sido eliminado" in body["message"]

    def test_login_wrong_password_returns_401(self, client):
        """Contraseña errónea -> 401 Unauthorized ('Credenciales incorrectas')."""
        user_data = {
            "id": 106,
            "email": "user@vitaly.com",
            "password": HASHED_PASSWORD,
            "is_active": True,
            "is_deleted": False,
        }
        user_query = make_query([user_data])

        with patch(f"{SERVICE_MODULE}.supabase") as mock_sb:
            route_tables(mock_sb, user=user_query)

            payload = {"email": "user@vitaly.com", "password": "WrongPassword!"}
            response = client.post(f"{AUTH_BASE_URL}/client/login", json=payload)

        assert response.status_code == 401
        body = response.json()
        assert body["success"] is False
        assert "Credenciales incorrectas" in body["message"]

    def test_login_nonexistent_user_returns_401(self, client):
        """Correo no registrado en BD -> 401 Unauthorized ('Credenciales incorrectas')."""
        user_query = make_query([])

        with patch(f"{SERVICE_MODULE}.supabase") as mock_sb:
            route_tables(mock_sb, user=user_query)

            payload = {"email": "noexiste@vitaly.com", "password": TEST_PASSWORD}
            response = client.post(f"{AUTH_BASE_URL}/client/login", json=payload)

        assert response.status_code == 401
        body = response.json()
        assert body["success"] is False
        assert "Credenciales incorrectas" in body["message"]

    def test_login_invalid_email_format_returns_422(self, client):
        """Formato de correo no válido -> 422 Unprocessable Entity."""
        payload = {"email": "not-an-email", "password": TEST_PASSWORD}
        response = client.post(f"{AUTH_BASE_URL}/client/login", json=payload)
        assert response.status_code == 422


# ──────────────────────────────────────────────────────────
# 5.1 Separación de portales: rechazo antes de emitir el JWT
# ──────────────────────────────────────────────────────────

def active_user(user_id: int = 110) -> dict:
    return {
        "id": user_id,
        "email": "cuenta@vitaly.com",
        "password": HASHED_PASSWORD,
        "name": "Cuenta",
        "lastname": "Prueba",
        "is_active": True,
        "is_deleted": False,
    }


class TestLoginPortalSeparation:
    def test_client_login_rejects_system_admin_with_403(self, client):
        """admin_sistema en el portal de clientes -> 403 y ningún token emitido."""
        with patch(f"{SERVICE_MODULE}.supabase") as mock_sb, \
             patch(f"{SERVICE_MODULE}.create_access_token") as mock_token:
            route_tables(
                mock_sb,
                user=make_query([active_user()]),
                user_roles=make_query([{"role": "admin_sistema"}]),
            )
            response = client.post(
                f"{AUTH_BASE_URL}/client/login",
                json={"email": "cuenta@vitaly.com", "password": TEST_PASSWORD},
            )

        assert response.status_code == 403
        body = response.json()
        assert body["success"] is False
        assert body["data"] is None
        assert "portal de clientes" in body["message"]
        mock_token.assert_not_called()

    @pytest.mark.parametrize("role", ["admin_escuela", "doctor", "representante"])
    def test_admin_login_rejects_client_roles_with_403(self, client, role):
        """Roles del portal de clientes en el portal de administración -> 403 y ningún token emitido."""
        with patch(f"{SERVICE_MODULE}.supabase") as mock_sb, \
             patch(f"{SERVICE_MODULE}.create_access_token") as mock_token:
            route_tables(
                mock_sb,
                user=make_query([active_user()]),
                user_roles=make_query([{"role": role}]),
            )
            response = client.post(
                f"{AUTH_BASE_URL}/admin/login",
                json={"email": "cuenta@vitaly.com", "password": TEST_PASSWORD},
            )

        assert response.status_code == 403
        body = response.json()
        assert body["success"] is False
        assert body["data"] is None
        assert "portal de administración" in body["message"]
        mock_token.assert_not_called()

    def test_client_login_rejects_user_without_role_with_403(self, client):
        """Usuario sin fila en user_roles -> 403 (antes se emitía un token con role 'desconocido')."""
        with patch(f"{SERVICE_MODULE}.supabase") as mock_sb:
            route_tables(mock_sb, user=make_query([active_user()]), user_roles=make_query([]))
            response = client.post(
                f"{AUTH_BASE_URL}/client/login",
                json={"email": "cuenta@vitaly.com", "password": TEST_PASSWORD},
            )

        assert response.status_code == 403
        assert response.json()["success"] is False

    def test_admin_login_wrong_password_returns_401(self, client):
        """Contraseña errónea en el portal de administración -> 401 sin revelar el rol."""
        with patch(f"{SERVICE_MODULE}.supabase") as mock_sb:
            route_tables(
                mock_sb,
                user=make_query([active_user()]),
                user_roles=make_query([{"role": "representante"}]),
            )
            response = client.post(
                f"{AUTH_BASE_URL}/admin/login",
                json={"email": "cuenta@vitaly.com", "password": "WrongPassword!"},
            )

        assert response.status_code == 401
        assert "Credenciales incorrectas" in response.json()["message"]

    def test_admin_login_invalid_email_format_returns_422(self, client):
        """Formato de correo no válido en el portal de administración -> 422."""
        response = client.post(
            f"{AUTH_BASE_URL}/admin/login",
            json={"email": "not-an-email", "password": TEST_PASSWORD},
        )
        assert response.status_code == 422
        assert response.json()["success"] is False


class TestLoginUnexpectedErrors:
    @pytest.mark.parametrize(
        ("login_path", "role", "failed_table"),
        [
            ("/login", "representante", "user"),
            ("/login", "representante", "user_roles"),
            ("/admin/login", "admin_sistema", "user"),
            ("/admin/login", "admin_sistema", "user_roles"),
            ("/login", "doctor", "doctor"),
            ("/login", "representante", "parent"),
            ("/login", "admin_escuela", "school_administrator"),
            ("/login", "admin_escuela", "school"),
        ],
    )
    def test_database_error_returns_500_envelope(
        self, client, caplog, login_path, role, failed_table
    ):
        """Una consulta fallida en cualquier etapa devuelve JSON 500 sin emitir JWT."""
        tables = {
            "user": make_query([active_user()]),
            "user_roles": make_query([{"role": role}]),
            "doctor": make_query([]),
            "parent": make_query([]),
            "school_administrator": make_query([{"school_id": 1}]),
            "school": make_query([]),
        }
        tables[failed_table].execute.side_effect = APIError({
            "message": "simulated database failure",
            "code": "XX000",
            "details": "internal database diagnostic",
            "hint": None,
        })

        with patch(f"{SERVICE_MODULE}.supabase") as mock_sb, \
             patch(f"{SERVICE_MODULE}.create_access_token") as mock_token:
            route_tables(mock_sb, **tables)
            response = client.post(
                f"{AUTH_BASE_URL}{login_path}",
                json={"email": "cuenta@vitaly.com", "password": TEST_PASSWORD},
            )

        assert response.status_code == 500
        assert response.headers["content-type"].startswith("application/json")
        assert response.json() == {
            "success": False,
            "message": "No se pudo iniciar sesión. Inténtalo de nuevo más tarde.",
            "data": None,
            "errors": None,
            "meta": None,
        }
        mock_token.assert_not_called()
        assert any(
            record.name == SERVICE_MODULE and record.exc_info is not None
            for record in caplog.records
        )

    @pytest.mark.parametrize(
        ("login_path", "role"),
        [("/login", "representante"), ("/admin/login", "admin_sistema")],
    )
    def test_token_generation_error_returns_500_envelope(self, client, login_path, role):
        """Un error al firmar el JWT tampoco expone detalles internos al cliente."""
        with patch(f"{SERVICE_MODULE}.supabase") as mock_sb, \
             patch(
                 f"{SERVICE_MODULE}.create_access_token",
                 side_effect=RuntimeError("internal signing diagnostic"),
             ):
            route_tables(
                mock_sb,
                user=make_query([active_user()]),
                user_roles=make_query([{"role": role}]),
            )
            response = client.post(
                f"{AUTH_BASE_URL}{login_path}",
                json={"email": "cuenta@vitaly.com", "password": TEST_PASSWORD},
            )

        assert response.status_code == 500
        body = response.json()
        assert body["success"] is False
        assert body["message"] == "No se pudo iniciar sesión. Inténtalo de nuevo más tarde."
        assert body["data"] is None
        assert body["errors"] is None


# ──────────────────────────────────────────────────────────
# 5.1.1 Alias obsoleto POST /login (transición hacia /client/login)
# ──────────────────────────────────────────────────────────

class TestDeprecatedClientLoginAlias:
    def test_alias_issues_client_scope_token(self, client):
        """El alias /login se comporta como /client/login: 200 y JWT con scope=client."""
        with patch(f"{SERVICE_MODULE}.supabase") as mock_sb:
            route_tables(
                mock_sb,
                user=make_query([active_user()]),
                user_roles=make_query([{"role": "representante"}]),
                parent=make_query([{"type_representative": "Madre"}]),
            )
            response = client.post(
                f"{AUTH_BASE_URL}/login",
                json={"email": "cuenta@vitaly.com", "password": TEST_PASSWORD},
            )

        assert response.status_code == 200
        body = response.json()
        assert body["success"] is True
        assert body["message"] == "Login exitoso"
        claims = jwt.decode(body["data"]["access_token"], settings.SECRET_KEY, algorithms=[ALGORITHM])
        assert claims["role"] == "representante"
        assert claims["scope"] == "client"

    def test_alias_rejects_system_admin_with_403(self, client):
        """El alias conserva el rechazo de admin_sistema antes de emitir el JWT."""
        with patch(f"{SERVICE_MODULE}.supabase") as mock_sb, \
             patch(f"{SERVICE_MODULE}.create_access_token") as mock_token:
            route_tables(
                mock_sb,
                user=make_query([active_user()]),
                user_roles=make_query([{"role": "admin_sistema"}]),
            )
            response = client.post(
                f"{AUTH_BASE_URL}/login",
                json={"email": "cuenta@vitaly.com", "password": TEST_PASSWORD},
            )

        assert response.status_code == 403
        assert response.json()["success"] is False
        mock_token.assert_not_called()

    def test_openapi_marks_only_the_alias_as_deprecated(self, client):
        """En /docs, /login aparece obsoleto y /client/login no."""
        paths = client.get("/openapi.json").json()["paths"]
        assert paths[f"{AUTH_BASE_URL}/login"]["post"]["deprecated"] is True
        assert paths[f"{AUTH_BASE_URL}/client/login"]["post"].get("deprecated", False) is False
        assert paths[f"{AUTH_BASE_URL}/client/login"]["post"]["tags"] == ["Autenticación - Clientes"]


# ──────────────────────────────────────────────────────────
# 5.2 Gates de procedencia del token (claim `scope`)
# ──────────────────────────────────────────────────────────

class TestScopeGates:
    def test_get_current_admin_accepts_admin_scope(self):
        payload = {"sub": "1", "role": "admin_sistema", "scope": "admin"}
        assert get_current_admin(payload) == payload

    @pytest.mark.parametrize("role", ["admin_escuela", "doctor", "representante"])
    def test_get_current_client_accepts_client_roles(self, role):
        payload = {"sub": "1", "role": role, "scope": "client"}
        assert get_current_client(payload) == payload

    @pytest.mark.parametrize("gate", [get_current_admin, get_current_client])
    def test_token_without_scope_returns_401(self, gate):
        """Token emitido antes de la separación de portales -> 401 para forzar un nuevo login."""
        with pytest.raises(HTTPException) as exc_info:
            gate({"sub": "1", "role": "admin_sistema"})
        assert exc_info.value.status_code == 401

    def test_client_scope_token_rejected_by_admin_gate(self):
        with pytest.raises(HTTPException) as exc_info:
            get_current_admin({"sub": "1", "role": "representante", "scope": "client"})
        assert exc_info.value.status_code == 403

    def test_admin_scope_token_rejected_by_client_gate(self):
        with pytest.raises(HTTPException) as exc_info:
            get_current_client({"sub": "1", "role": "admin_sistema", "scope": "admin"})
        assert exc_info.value.status_code == 403

    @pytest.mark.parametrize(
        ("gate", "payload"),
        [
            # scope y rol incoherentes (token manipulado o de otro portal): nunca se aceptan
            (get_current_admin, {"sub": "1", "role": "representante", "scope": "admin"}),
            (get_current_client, {"sub": "1", "role": "admin_sistema", "scope": "client"}),
        ],
    )
    def test_scope_role_mismatch_returns_403(self, gate, payload):
        with pytest.raises(HTTPException) as exc_info:
            gate(payload)
        assert exc_info.value.status_code == 403

    def test_admin_route_rejects_legacy_token_without_scope_with_401(self, client):
        """Ruta de admin_sistema con un token real sin `scope` -> 401 con envelope."""
        token = create_access_token(data={"sub": "1", "role": "admin_sistema"})
        with patch.object(auth_service, "is_token_revoked", return_value=False), \
             patch.object(auth_service, "assert_account_available"):
            response = client.get(
                "/api/v1/admin/users",
                headers={"Authorization": f"Bearer {token}"},
            )
        assert response.status_code == 401
        body = response.json()
        assert body["success"] is False
        assert "inicia sesión de nuevo" in body["message"]

    def test_client_route_rejects_admin_scope_token_with_403(self, client):
        """Ruta de representante con un token del portal de administración -> 403 con envelope."""
        token = create_access_token(data={"sub": "1", "role": "admin_sistema", "scope": "admin"})
        with patch.object(auth_service, "is_token_revoked", return_value=False), \
             patch.object(auth_service, "assert_account_available"):
            response = client.post(
                "/api/v1/parent/link-students",
                headers={"Authorization": f"Bearer {token}"},
            )
        assert response.status_code == 403
        assert response.json()["success"] is False


# ──────────────────────────────────────────────────────────
# 6. POST /logout e invalidación de token
# ──────────────────────────────────────────────────────────

class TestLogoutAndRevocation:
    def test_logout_success_and_revocation(self, client):
        """Logout exitoso registra token en blacklist; llamada posterior con ese token retorna 401."""
        token_data = {
            "sub": "200",
            "email": "user@vitaly.com",
            "role": "admin_sistema",
            "scope": "admin",
            "name": "Test User",
            "gender": "MASCULINO",
        }
        valid_token = create_access_token(data=token_data)

        blacklist_insert = make_query([{"id": 1}])
        blacklist_empty = make_query([])
        blacklist_found = make_query([{"id": 1}])

        # First logout call: token is not revoked yet
        with patch(f"{SERVICE_MODULE}.supabase") as mock_sb:
            route_tables(mock_sb, token_blacklist=blacklist_empty,
                         user=make_query([{"id": 200, "is_deleted": False}]))
            blacklist_empty.insert = MagicMock(return_value=blacklist_insert)

            response = client.post(
                f"{AUTH_BASE_URL}/logout",
                headers={"Authorization": f"Bearer {valid_token}"},
            )

        assert response.status_code == 200
        body = response.json()
        assert body["success"] is True
        assert body["data"] == "Logout exitoso"
        assert "Sesión cerrada correctamente" in body["message"]

        # Subsequent call with the same token: blacklist returns record -> 401
        with patch(f"{SERVICE_MODULE}.supabase") as mock_sb:
            route_tables(mock_sb, token_blacklist=blacklist_found)

            retry_response = client.post(
                f"{AUTH_BASE_URL}/logout",
                headers={"Authorization": f"Bearer {valid_token}"},
            )

        assert retry_response.status_code == 401
        retry_body = retry_response.json()
        assert retry_body["success"] is False
        assert "El token ha sido revocado" in retry_body["message"]

    def test_logout_unauthenticated_returns_401_or_403(self, client):
        """Llamar a logout sin cabecera Authorization -> 401/403."""
        response = client.post(f"{AUTH_BASE_URL}/logout")
        assert response.status_code in (401, 403)

    def test_logout_malformed_token_returns_401(self, client):
        """Llamar a logout con token corrupto -> 401."""
        response = client.post(
            f"{AUTH_BASE_URL}/logout",
            headers={"Authorization": "Bearer not-a-valid-jwt-token"},
        )
        assert response.status_code == 401
        body = response.json()
        assert body["success"] is False


# ──────────────────────────────────────────────────────────
# 7. Métodos del Servicio y Cobertura de Ramas Internas
# ──────────────────────────────────────────────────────────

class TestAuthServiceEdgeCases:
    def test_create_base_user_insert_failure_raises_500(self):
        """Fallo en supabase al insertar usuario base -> 500 Internal Server Error."""
        user_select = make_query([])
        user_insert_empty = make_query([])  # data is empty

        user_table = MagicMock()
        user_table.select.return_value = user_select
        user_table.insert.return_value = user_insert_empty

        with patch(f"{SERVICE_MODULE}.supabase") as mock_sb:
            route_tables(mock_sb, user=user_table)

            dummy_user = SystemAdminRegister(
                nombre="Test",
                apellido="Fail",
                correo="fail@vitaly.com",
                contraseña=TEST_PASSWORD,
                fecha_de_nacimiento=date(1990, 1, 1),
            )
            with pytest.raises(HTTPException) as exc_info:
                auth_service.register_system_admin(dummy_user)

            assert exc_info.value.status_code == 500
            assert "Error al crear el usuario base" in exc_info.value.detail

    def test_is_token_revoked_returns_boolean(self):
        """Verifica comportamiento de auth_service.is_token_revoked."""
        found_query = make_query([{"id": 1}])
        empty_query = make_query([])

        with patch(f"{SERVICE_MODULE}.supabase") as mock_sb:
            route_tables(mock_sb, token_blacklist=found_query)
            assert auth_service.is_token_revoked("some-token") is True

        with patch(f"{SERVICE_MODULE}.supabase") as mock_sb:
            route_tables(mock_sb, token_blacklist=empty_query)
            assert auth_service.is_token_revoked("another-token") is False


# ──────────────────────────────────────────────────────────
# 8. POST /change-password
# ──────────────────────────────────────────────────────────

NEW_PASSWORD = "BrandNewPassword456!"
CHANGE_PASSWORD_URL = f"{AUTH_BASE_URL}/change-password"


def password_db(**overrides) -> FakeSupabase:
    """Base en memoria con el usuario 110 (autenticado) y el 111 (ajeno)."""
    me = {**active_user(110), **overrides}
    other = {**active_user(111), "email": "otra@vitaly.com"}
    return FakeSupabase(
        user=[me, other],
        user_roles=[{"user_id": 110, "role": "representante"}, {"user_id": 111, "role": "doctor"}],
        token_blacklist=[],
    )


def bearer(user_id: int = 110, role: str = "representante", scope: str = "client") -> dict:
    token = create_access_token(data={"sub": str(user_id), "role": role, "scope": scope})
    return {"Authorization": f"Bearer {token}"}


def stored_hash(db: FakeSupabase, user_id: int) -> str:
    return next(row["password"] for row in db.db["user"] if row["id"] == user_id)


class TestChangePassword:
    @pytest.mark.parametrize(
        ("role", "scope"),
        [
            ("admin_sistema", "admin"),
            ("admin_escuela", "client"),
            ("doctor", "client"),
            ("representante", "client"),
        ],
    )
    def test_success_for_every_role_and_portal(self, client, role, scope):
        """Contraseña actual correcta -> 200 y solo cambia el hash del usuario del token."""
        db = password_db()
        other_hash = stored_hash(db, 111)
        with patch(f"{SERVICE_MODULE}.supabase", db):
            response = client.post(
                CHANGE_PASSWORD_URL,
                headers=bearer(role=role, scope=scope),
                json={"current_password": TEST_PASSWORD, "new_password": NEW_PASSWORD},
            )

        assert response.status_code == 200
        body = response.json()
        assert body["success"] is True
        assert body["data"] == "Contraseña actualizada"
        assert body["message"] == "Contraseña actualizada correctamente"
        assert verify_password(NEW_PASSWORD, stored_hash(db, 110))
        assert not verify_password(TEST_PASSWORD, stored_hash(db, 110))
        assert stored_hash(db, 111) == other_hash

    def test_new_password_works_on_login_and_old_one_does_not(self, client):
        """Tras el cambio, el login acepta la nueva contraseña y rechaza la anterior."""
        db = password_db()
        with patch(f"{SERVICE_MODULE}.supabase", db):
            change = client.post(
                CHANGE_PASSWORD_URL,
                headers=bearer(),
                json={"current_password": TEST_PASSWORD, "new_password": NEW_PASSWORD},
            )
            new_login = client.post(
                f"{AUTH_BASE_URL}/client/login",
                json={"email": "cuenta@vitaly.com", "password": NEW_PASSWORD},
            )
            old_login = client.post(
                f"{AUTH_BASE_URL}/client/login",
                json={"email": "cuenta@vitaly.com", "password": TEST_PASSWORD},
            )

        assert change.status_code == 200
        assert new_login.status_code == 200
        assert old_login.status_code == 401

    def test_current_session_stays_valid(self, client):
        """La sesión se mantiene: el mismo token sigue autenticando tras el cambio."""
        db = password_db()
        headers = bearer()
        with patch(f"{SERVICE_MODULE}.supabase", db):
            first = client.post(
                CHANGE_PASSWORD_URL,
                headers=headers,
                json={"current_password": TEST_PASSWORD, "new_password": NEW_PASSWORD},
            )
            second = client.post(
                CHANGE_PASSWORD_URL,
                headers=headers,
                json={"current_password": NEW_PASSWORD, "new_password": "ThirdPassword789!"},
            )

        assert first.status_code == 200
        assert second.status_code == 200
        assert db.db["token_blacklist"] == []

    def test_wrong_current_password_returns_400_not_401(self, client):
        """Contraseña actual incorrecta -> 400 (el cliente cierra sesión ante un 401) sin tocar el hash."""
        db = password_db()
        original_hash = stored_hash(db, 110)
        with patch(f"{SERVICE_MODULE}.supabase", db):
            response = client.post(
                CHANGE_PASSWORD_URL,
                headers=bearer(),
                json={"current_password": "WrongPassword!", "new_password": NEW_PASSWORD},
            )

        assert response.status_code == 400
        body = response.json()
        assert body["success"] is False
        assert body["data"] is None
        assert body["message"] == "La contraseña actual es incorrecta."
        assert stored_hash(db, 110) == original_hash

    def test_new_password_equal_to_current_returns_400(self, client):
        """La nueva contraseña no puede repetir la actual."""
        db = password_db()
        original_hash = stored_hash(db, 110)
        with patch(f"{SERVICE_MODULE}.supabase", db):
            response = client.post(
                CHANGE_PASSWORD_URL,
                headers=bearer(),
                json={"current_password": TEST_PASSWORD, "new_password": TEST_PASSWORD},
            )

        assert response.status_code == 400
        assert response.json()["message"] == "La nueva contraseña debe ser distinta de la actual."
        assert stored_hash(db, 110) == original_hash

    @pytest.mark.parametrize(
        "payload",
        [
            {"current_password": TEST_PASSWORD, "new_password": "short"},
            {"current_password": "", "new_password": NEW_PASSWORD},
            {"current_password": TEST_PASSWORD},
            # 40 caracteres pero 80 bytes en UTF-8: bcrypt no los admite.
            {"current_password": TEST_PASSWORD, "new_password": "ñ" * 40},
            {"current_password": "a" * 73, "new_password": NEW_PASSWORD},
        ],
    )
    def test_invalid_payload_returns_422_without_touching_db(self, client, payload):
        """Longitudes fuera de rango o campos ausentes -> 422 antes de llegar al servicio."""
        db = password_db()
        original_hash = stored_hash(db, 110)
        with patch(f"{SERVICE_MODULE}.supabase", db):
            response = client.post(CHANGE_PASSWORD_URL, headers=bearer(), json=payload)

        assert response.status_code == 422
        assert response.json()["success"] is False
        assert stored_hash(db, 110) == original_hash

    def test_body_user_id_is_ignored(self, client):
        """El usuario sale del claim sub: un id en el cuerpo no redirige el cambio a otra cuenta."""
        db = password_db()
        other_hash = stored_hash(db, 111)
        with patch(f"{SERVICE_MODULE}.supabase", db):
            response = client.post(
                CHANGE_PASSWORD_URL,
                headers=bearer(),
                json={"user_id": 111, "current_password": TEST_PASSWORD, "new_password": NEW_PASSWORD},
            )

        assert response.status_code == 200
        assert stored_hash(db, 111) == other_hash
        assert verify_password(NEW_PASSWORD, stored_hash(db, 110))

    def test_missing_token_returns_401(self, client):
        response = client.post(
            CHANGE_PASSWORD_URL,
            json={"current_password": TEST_PASSWORD, "new_password": NEW_PASSWORD},
        )
        assert response.status_code == 401
        assert response.json()["success"] is False

    def test_malformed_token_returns_401(self, client):
        response = client.post(
            CHANGE_PASSWORD_URL,
            headers={"Authorization": "Bearer not-a-valid-jwt-token"},
            json={"current_password": TEST_PASSWORD, "new_password": NEW_PASSWORD},
        )
        assert response.status_code == 401
        assert response.json()["success"] is False

    def test_revoked_token_returns_401_without_touching_db(self, client):
        """Un token revocado por logout no puede cambiar la contraseña."""
        headers = bearer()
        db = password_db()
        db.db["token_blacklist"] = [{"id": 1, "token": headers["Authorization"].removeprefix("Bearer ")}]
        original_hash = stored_hash(db, 110)
        with patch(f"{SERVICE_MODULE}.supabase", db):
            response = client.post(
                CHANGE_PASSWORD_URL,
                headers=headers,
                json={"current_password": TEST_PASSWORD, "new_password": NEW_PASSWORD},
            )

        assert response.status_code == 401
        assert "El token ha sido revocado" in response.json()["message"]
        assert stored_hash(db, 110) == original_hash

    def test_inactive_account_returns_403(self, client):
        """Una cuenta desactivada después del login no puede cambiar la contraseña."""
        db = password_db(is_active=False)
        original_hash = stored_hash(db, 110)
        with patch(f"{SERVICE_MODULE}.supabase", db):
            response = client.post(
                CHANGE_PASSWORD_URL,
                headers=bearer(),
                json={"current_password": TEST_PASSWORD, "new_password": NEW_PASSWORD},
            )

        assert response.status_code == 403
        assert response.json()["success"] is False
        assert stored_hash(db, 110) == original_hash

    def test_deleted_account_session_returns_401(self, client):
        """Una cuenta eliminada ya no puede usar su sesión para cambiar contraseña."""
        db = password_db(is_deleted=True)
        original_hash = stored_hash(db, 110)
        with patch(f"{SERVICE_MODULE}.supabase", db):
            response = client.post(
                CHANGE_PASSWORD_URL,
                headers=bearer(),
                json={"current_password": TEST_PASSWORD, "new_password": NEW_PASSWORD},
            )

        assert response.status_code == 401
        assert response.json()["message"] == "La cuenta no está disponible. Por favor inicia sesión de nuevo."
        assert stored_hash(db, 110) == original_hash

    def test_database_error_returns_500_envelope(self, client, caplog):
        """Un fallo de Supabase se registra y responde el envelope 500 sin detalles internos."""
        db = password_db()
        db.failing_table = "user"
        with patch(f"{SERVICE_MODULE}.supabase", db), \
             patch.object(auth_service, "assert_account_available"):
            response = client.post(
                CHANGE_PASSWORD_URL,
                headers=bearer(),
                json={"current_password": TEST_PASSWORD, "new_password": NEW_PASSWORD},
            )

        assert response.status_code == 500
        body = response.json()
        assert body["success"] is False
        assert body["message"] == "No se pudo cambiar la contraseña. Inténtalo de nuevo más tarde."
        assert "postgrest unavailable" not in response.text
        assert "Error inesperado al cambiar la contraseña" in caplog.text

    def test_row_deleted_between_read_and_update_returns_404(self):
        """Si la actualización no afecta a ninguna fila, el servicio responde 404."""
        user_table = MagicMock()
        user_table.select.return_value = make_query([{"id": 110, "password": HASHED_PASSWORD, "is_active": True}])
        user_table.update.return_value = make_query([])

        with patch(f"{SERVICE_MODULE}.supabase") as mock_sb:
            route_tables(mock_sb, user=user_table)
            with pytest.raises(HTTPException) as exc_info:
                auth_service.change_password(
                    user_id=110,
                    data=PasswordChangeCreate(current_password=TEST_PASSWORD, new_password=NEW_PASSWORD),
                )

        assert exc_info.value.status_code == 404


# ──────────────────────────────────────────────────────────
# 9. Límite de 72 bytes de bcrypt en login y registro (GH-74)
# ──────────────────────────────────────────────────────────

class TestBcrypt72BytePasswordLimit:
    @pytest.mark.parametrize(
        ("login_path", "role"),
        [
            ("/client/login", "representante"),
            ("/login", "representante"),
            ("/admin/login", "admin_sistema"),
        ],
    )
    @pytest.mark.parametrize(
        "long_password",
        [
            "a" * 73,
            "ñ" * 37,  # 37 caracteres = 74 bytes en UTF-8
        ],
    )
    def test_login_password_over_72_bytes_returns_401_without_stack_trace(
        self, client, caplog, login_path, role, long_password
    ):
        """Login con más de 72 bytes en UTF-8 responde 401 sin lanzar ValueError ni registrar stack trace."""
        with patch(f"{SERVICE_MODULE}.supabase") as mock_sb:
            route_tables(
                mock_sb,
                user=make_query([active_user()]),
                user_roles=make_query([{"role": role}]),
            )
            response = client.post(
                f"{AUTH_BASE_URL}{login_path}",
                json={"email": "cuenta@vitaly.com", "password": long_password},
            )

        assert response.status_code == 401
        body = response.json()
        assert body["success"] is False
        assert body["message"] == "Credenciales incorrectas"
        assert not any(record.exc_info is not None for record in caplog.records)

    @pytest.mark.parametrize(
        ("endpoint", "extra_fields"),
        [
            ("/register-system-admin", {}),
            (
                "/register-school-admin",
                {"school_id": 1, "administrative_position": "Directora"},
            ),
            (
                "/register-doctor",
                {"doc_license_number": 12345, "especially": "Pediatría"},
            ),
            (
                "/register-parent",
                {"identity_number": "V-12345678", "type_representative": "Madre"},
            ),
        ],
    )
    @pytest.mark.parametrize(
        "invalid_password",
        [
            "a" * 73,
            "ñ" * 37,  # 74 bytes en UTF-8 (> 72 bytes)
        ],
    )
    def test_register_password_over_72_bytes_returns_422(
        self, client, endpoint, extra_fields, invalid_password
    ):
        """El registro con una contraseña > 72 bytes en UTF-8 responde 422 antes de tocar Supabase."""
        payload = {
            "nombre": "Ana",
            "apellido": "López",
            "correo": "nuevo@vitaly.com",
            "contraseña": invalid_password,
            "fecha_de_nacimiento": "1990-05-10",
            **extra_fields,
        }
        with patch(f"{SERVICE_MODULE}.supabase") as mock_sb:
            response = client.post(f"{AUTH_BASE_URL}{endpoint}", json=payload)
            mock_sb.table.assert_not_called()

        assert response.status_code == 422
        body = response.json()
        assert body["success"] is False

    def test_register_multibyte_password_within_72_bytes_succeeds(self, client):
        """25 caracteres 'ñ' (50 bytes en UTF-8 <= 72 bytes) son aceptados en el registro."""
        valid_multibyte_password = "ñ" * 25  # 50 bytes en UTF-8
        user_insert = make_query([{"id": 99}])
        roles_query = make_query([{"id": 1}])
        sys_admin_query = make_query([{"admin_id": 99}])

        user_table = MagicMock()
        user_table.select.return_value = make_query([])
        user_table.insert.return_value = user_insert

        with patch(f"{SERVICE_MODULE}.supabase") as mock_sb:
            route_tables(
                mock_sb,
                user=user_table,
                user_roles=roles_query,
                system_administrator=sys_admin_query,
            )
            payload = {
                "nombre": "Carlos",
                "apellido": "Mendoza",
                "correo": "multibyte@vitaly.com",
                "contraseña": valid_multibyte_password,
                "fecha_de_nacimiento": "1985-04-12",
            }
            response = client.post(f"{AUTH_BASE_URL}/register-system-admin", json=payload)

        assert response.status_code == 200
        assert response.json()["success"] is True

