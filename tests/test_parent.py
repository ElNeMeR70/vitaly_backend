"""
Tests for Parent endpoints and service layer:
  - POST /api/v1/parent/link-students (link_authorized_students)
  - GET/PUT /api/v1/parent/profile (own profile)
  - POST/DELETE /api/v1/parent/profile/photo (own profile photo)

Adheres strictly to the 5 laws of vitaly-feature-testing:
  1. APIResponse envelope verification (success, data, message).
  2. Multi-tenancy and role security: strictly gated by get_current_representante.
  3. Deterministic supabase-py mock for user, parent, student and student_representative.
  4. Correct status codes: 404 for missing parent profile, 200 with empty list when no matches.
  5. RBAC matrix: 403 for unauthorized roles (admin_escuela, admin_sistema, doctor),
     401 for unauthenticated/revoked tokens.
"""
try:
    from datetime import UTC
except ImportError:
    from datetime import timezone
    UTC = timezone.utc  # noqa: UP017
from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.api.deps import get_current_representante
from app.core.security import create_access_token
from app.main import app
from app.services import profile_photo_service
from app.services.imagekit_service import UploadedImage
from app.services.parent_service import parent_service

ENDPOINT_MODULE = "app.api.v1.endpoints.parent"
SERVICE_MODULE = "app.services.parent_service"
PARENT_BASE_URL = "/api/v1/parent"


# ──────────────────────────────────────────────────────────
# Realistic JWT payload helpers
# ──────────────────────────────────────────────────────────

def parent_payload(user_id: int = 50) -> dict:
    """Claims for authenticated representante."""
    return {
        "sub": str(user_id),
        "email": "madre@vitaly.com",
        "role": "representante",
        "scope": "client",
        "name": "Ana López",
        "gender": "FEMENINO",
        "type_representative": "Madre",
    }


def admin_school_payload(user_id: int = 20) -> dict:
    """Claims for admin_escuela (unauthorized for parent endpoints)."""
    return {
        "sub": str(user_id),
        "email": "admin@vitaly.com",
        "role": "admin_escuela",
        "scope": "client",
        "name": "Laura Pérez",
        "gender": "FEMENINO",
    }


def doctor_payload(user_id: int = 30) -> dict:
    """Claims for doctor (unauthorized for parent endpoints)."""
    return {
        "sub": str(user_id),
        "email": "doctor@vitaly.com",
        "role": "doctor",
        "scope": "client",
        "name": "Roberto Sánchez",
        "gender": "MASCULINO",
    }


def sys_admin_payload(user_id: int = 10) -> dict:
    """Claims for admin_sistema (unauthorized for parent endpoints)."""
    return {
        "sub": str(user_id),
        "email": "sysadmin@vitaly.com",
        "role": "admin_sistema",
        "scope": "admin",
        "name": "Carlos Mendoza",
        "gender": "MASCULINO",
    }


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
def parent_client():
    """Client with get_current_representante dependency overridden."""
    app.dependency_overrides[get_current_representante] = lambda: parent_payload()
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
# 1. POST /link-students - Flujos Funcionales
# ──────────────────────────────────────────────────────────

class TestLinkStudentsFunctional:
    def test_link_students_success_new_and_already_linked(self, parent_client):
        """Vinculación exitosa de estudiantes autorizados con detección de ya vinculados."""
        user_query = make_query([{"identity_number": "V-12345678"}])
        parent_query = make_query([{"id": 7}])

        # Students found matching authorization_number
        students_found = [
            {"id": 101, "name": "Pedro", "lastname": "López"},
            {"id": 102, "name": "Lucía", "lastname": "López"},
        ]
        student_query = make_query(students_found)

        # student_representative checks:
        # First student 101: not linked yet (empty)
        # Second student 102: already linked
        def rep_table_side_effect():
            rep_mock = MagicMock()
            for method in ("select", "eq", "insert"):
                getattr(rep_mock, method).return_value = rep_mock

            # Alternate between not found (101) and found (102)
            call_count = {"count": 0}

            def execute_side_effect():
                call_count["count"] += 1
                if call_count["count"] == 1:
                    return MagicMock(data=[])  # student 101 not linked
                if call_count["count"] == 2:
                    return MagicMock(data=[{"id": 1}])  # student 101 insert
                return MagicMock(data=[{"id": 500}])  # student 102 check: found

            rep_mock.execute.side_effect = execute_side_effect
            return rep_mock

        rep_mock = rep_table_side_effect()

        with patch(f"{ENDPOINT_MODULE}.supabase") as mock_ep_sb, \
             patch(f"{SERVICE_MODULE}.supabase") as mock_svc_sb:
            route_tables(mock_ep_sb, user=user_query)
            route_tables(
                mock_svc_sb,
                parent=parent_query,
                student=student_query,
                student_representative=rep_mock,
            )

            response = parent_client.post(f"{PARENT_BASE_URL}/link-students")

        assert response.status_code == 200
        body = response.json()
        assert body["success"] is True
        assert "Se vincularon" in body["message"]
        assert len(body["data"]["linked_students"]) == 1
        assert body["data"]["linked_students"][0]["student_id"] == 101
        assert len(body["data"]["already_linked"]) == 1
        assert body["data"]["already_linked"][0]["student_id"] == 102

    def test_link_students_no_identity_in_profile_returns_200_with_message(self, parent_client):
        """Usuario sin número de identidad registrado -> 200 con data=None y mensaje explicativo."""
        user_query = make_query([{"identity_number": None}])

        with patch(f"{ENDPOINT_MODULE}.supabase") as mock_ep_sb:
            route_tables(mock_ep_sb, user=user_query)

            response = parent_client.post(f"{PARENT_BASE_URL}/link-students")

        assert response.status_code == 200
        body = response.json()
        assert body["success"] is True
        assert body["data"] is None
        assert "No se encontró un número de identidad asociado a tu perfil" in body["message"]

    def test_link_students_no_authorized_students_found(self, parent_client):
        """No hay estudiantes autorizados con la cédula del representante -> 200 con lista vacía."""
        user_query = make_query([{"identity_number": "V-99999999"}])
        parent_query = make_query([{"id": 7}])
        student_query = make_query([])  # No matches

        with patch(f"{ENDPOINT_MODULE}.supabase") as mock_ep_sb, \
             patch(f"{SERVICE_MODULE}.supabase") as mock_svc_sb:
            route_tables(mock_ep_sb, user=user_query)
            route_tables(mock_svc_sb, parent=parent_query, student=student_query)

            response = parent_client.post(f"{PARENT_BASE_URL}/link-students")

        assert response.status_code == 200
        body = response.json()
        assert body["success"] is True
        assert body["data"]["linked_students"] == []
        assert "No se encontraron estudiantes autorizados" in body["message"]

    def test_link_students_missing_parent_profile_returns_404(self, parent_client):
        """No existe registro en tabla parent para el user_id -> 404 Not Found."""
        user_query = make_query([{"identity_number": "V-12345678"}])
        parent_query = make_query([])  # No parent row

        with patch(f"{ENDPOINT_MODULE}.supabase") as mock_ep_sb, \
             patch(f"{SERVICE_MODULE}.supabase") as mock_svc_sb:
            route_tables(mock_ep_sb, user=user_query)
            route_tables(mock_svc_sb, parent=parent_query)

            response = parent_client.post(f"{PARENT_BASE_URL}/link-students")

        assert response.status_code == 404
        body = response.json()
        assert body["success"] is False
        assert "No se encontró un perfil de representante asociado" in body["message"]


# ──────────────────────────────────────────────────────────
# 2. Matriz RBAC y Seguridad en /link-students
# ──────────────────────────────────────────────────────────

class TestLinkStudentsRBAC:
    def test_link_students_unauthenticated_returns_401_or_403(self, client):
        """Petición sin token -> 401/403."""
        response = client.post(f"{PARENT_BASE_URL}/link-students")
        assert response.status_code in (401, 403)

    def test_link_students_forbidden_for_admin_escuela(self, client):
        """Rol admin_escuela intentando invocar link-students -> 403 Forbidden."""
        token = create_access_token(data=admin_school_payload())
        with patch("app.services.auth_service.auth_service.is_token_revoked", return_value=False), \
             patch("app.services.auth_service.auth_service.assert_account_available"):
            response = client.post(
                f"{PARENT_BASE_URL}/link-students",
                headers={"Authorization": f"Bearer {token}"},
            )
        assert response.status_code == 403
        body = response.json()
        assert body["success"] is False
        assert "Se requiere rol de representante" in body["message"]

    def test_link_students_forbidden_for_doctor(self, client):
        """Rol doctor intentando invocar link-students -> 403 Forbidden."""
        token = create_access_token(data=doctor_payload())
        with patch("app.services.auth_service.auth_service.is_token_revoked", return_value=False), \
             patch("app.services.auth_service.auth_service.assert_account_available"):
            response = client.post(
                f"{PARENT_BASE_URL}/link-students",
                headers={"Authorization": f"Bearer {token}"},
            )
        assert response.status_code == 403
        body = response.json()
        assert body["success"] is False
        assert "Se requiere rol de representante" in body["message"]

    def test_link_students_forbidden_for_admin_sistema(self, client):
        """Rol admin_sistema intentando invocar link-students -> 403 Forbidden."""
        token = create_access_token(data=sys_admin_payload())
        with patch("app.services.auth_service.auth_service.is_token_revoked", return_value=False), \
             patch("app.services.auth_service.auth_service.assert_account_available"):
            response = client.post(
                f"{PARENT_BASE_URL}/link-students",
                headers={"Authorization": f"Bearer {token}"},
            )
        assert response.status_code == 403
        body = response.json()
        assert body["success"] is False
        assert "token emitido por el portal de clientes" in body["message"]

    def test_link_students_with_revoked_token_returns_401(self, client):
        """Token revocado previamente -> 401 Unauthorized."""
        token = create_access_token(data=parent_payload())
        with patch("app.services.auth_service.auth_service.is_token_revoked", return_value=True):
            response = client.post(
                f"{PARENT_BASE_URL}/link-students",
                headers={"Authorization": f"Bearer {token}"},
            )
        assert response.status_code == 401
        body = response.json()
        assert body["success"] is False
        assert "El token ha sido revocado" in body["message"]


# ──────────────────────────────────────────────────────────
# 3. Métodos del Servicio ParentService
# ──────────────────────────────────────────────────────────

class TestParentServiceDirect:
    def test_link_students_by_identity_direct_parent_not_found(self):
        """Invocación directa del servicio cuando no existe el perfil parent -> 404."""
        parent_query = make_query([])

        with patch(f"{SERVICE_MODULE}.supabase") as mock_sb:
            route_tables(mock_sb, parent=parent_query)

            with pytest.raises(HTTPException) as exc_info:
                parent_service.link_students_by_identity(user_id=99, identity_number="V-123")

            assert exc_info.value.status_code == 404
            assert "No se encontró un perfil de representante" in exc_info.value.detail


# ──────────────────────────────────────────────────────────
# 4. Perfil propio y foto de perfil (/profile, /profile/photo)
# ──────────────────────────────────────────────────────────

JPEG_BYTES = b"\xff\xd8\xff\xe0" + b"\x00" * 32
HTML_BYTES = b"<!DOCTYPE html><html><script>alert(1)</script></html>"


def parent_profile_row(**user_overrides) -> dict:
    """Row returned by the parent + embedded user select of get_my_profile."""
    user = {
        "email": "madre@vitaly.com",
        "name": "Ana",
        "lastname": "López",
        "identity_number": "V-12345678",
        "birthday": "1985-04-12",
        "gender": "FEMENINO",
        "address": "Av. Principal 123",
        "photo_url": "https://ik.imagekit.io/vitaly/profiles/parents/user_50.jpg",
    }
    user.update(user_overrides)
    return {
        "id": 7,
        "user_id": 50,
        "occupation": "Ingeniera",
        "type_representative": "Madre",
        "user": user,
    }


class TestParentProfileGet:
    def test_get_profile_returns_200_without_secrets(self, parent_client):
        """Perfil propio -> 200 con photo_url y sin password ni photo_file_id."""
        parent_query = make_query([parent_profile_row()])

        with patch(f"{SERVICE_MODULE}.supabase") as mock_sb:
            route_tables(mock_sb, parent=parent_query)
            response = parent_client.get(f"{PARENT_BASE_URL}/profile")

        assert response.status_code == 200
        body = response.json()
        assert body["success"] is True
        data = body["data"]
        assert data["id"] == 7
        assert data["user_id"] == 50
        assert data["name"] == "Ana"
        assert data["occupation"] == "Ingeniera"
        assert data["photo_url"].endswith("user_50.jpg")
        assert "password" not in data
        assert "photo_file_id" not in data
        # The query is scoped to the token's user and never selects secrets.
        parent_query.eq.assert_any_call("user_id", 50)
        parent_query.eq.assert_any_call("is_deleted", False)
        selected = parent_query.select.call_args[0][0]
        assert "*" not in selected
        assert "password" not in selected
        assert "photo_file_id" not in selected

    def test_get_profile_without_photo_returns_null_photo_url(self, parent_client):
        """Representante sin foto -> photo_url null."""
        parent_query = make_query([parent_profile_row(photo_url=None, address=None)])

        with patch(f"{SERVICE_MODULE}.supabase") as mock_sb:
            route_tables(mock_sb, parent=parent_query)
            response = parent_client.get(f"{PARENT_BASE_URL}/profile")

        assert response.status_code == 200
        assert response.json()["data"]["photo_url"] is None
        assert response.json()["data"]["address"] is None

    def test_get_profile_without_parent_row_returns_404(self, parent_client):
        """Sin fila en parent -> 404 en el envelope."""
        with patch(f"{SERVICE_MODULE}.supabase") as mock_sb:
            route_tables(mock_sb, parent=make_query([]))
            response = parent_client.get(f"{PARENT_BASE_URL}/profile")

        assert response.status_code == 404
        body = response.json()
        assert body["success"] is False
        assert "No se encontró un perfil de representante" in body["message"]

    def test_get_profile_db_error_returns_500_envelope(self, parent_client):
        """Error inesperado de supabase -> 500 con envelope."""
        parent_query = make_query([])
        parent_query.execute.side_effect = RuntimeError("db down")

        with patch(f"{SERVICE_MODULE}.supabase") as mock_sb:
            route_tables(mock_sb, parent=parent_query)
            response = parent_client.get(f"{PARENT_BASE_URL}/profile")

        assert response.status_code == 500
        assert response.json()["success"] is False
        assert "db down" not in response.json()["message"]


def make_profile_table(select_results=(), update_result=None):
    """Table mock with independent select and update chains.

    Keeping the chains apart lets tests assert which filters each operation used,
    and ``select_results`` feeds consecutive reads (pre-update and post-update).
    ``update_result`` is the update's row list, or an exception instance to raise.
    """
    table = MagicMock()
    select_q = MagicMock()
    select_q.select.return_value = select_q
    select_q.eq.return_value = select_q
    select_q.execute.side_effect = [MagicMock(data=rows) for rows in select_results]
    table.select.side_effect = lambda *args, **kwargs: select_q

    update_q = MagicMock()
    update_q.eq.return_value = update_q
    if isinstance(update_result, Exception):
        update_q.execute.side_effect = update_result
    else:
        update_q.execute.return_value = MagicMock(data=[] if update_result is None else update_result)
    table.update.return_value = update_q
    return table, select_q, update_q


def assert_profile_read_scoped(select_q: MagicMock, user_id: int = 50) -> None:
    """Every profile read filters by the token's user and excludes soft-deleted rows."""
    select_q.eq.assert_any_call("user_id", user_id)
    select_q.eq.assert_any_call("is_deleted", False)


class TestParentProfileUpdate:
    def test_put_updates_only_sent_user_fields(self, parent_client):
        """PUT parcial con campos de user -> solo 'user' se actualiza y la respuesta sale de la relectura."""
        before = parent_profile_row()
        after = parent_profile_row(name="Ana María", gender="FEMENINO")
        parent_table, parent_select, _ = make_profile_table(select_results=[[before], [after]])
        user_table, _, user_update = make_profile_table(update_result=[{"id": 50}])

        with patch(f"{SERVICE_MODULE}.supabase") as mock_sb:
            route_tables(mock_sb, parent=parent_table, user=user_table)
            response = parent_client.put(
                f"{PARENT_BASE_URL}/profile",
                json={"name": "  Ana María ", "birthday": "1985-04-12", "gender": "FEMENINO"},
            )

        assert response.status_code == 200
        body = response.json()
        assert body["success"] is True
        assert body["data"]["name"] == "Ana María"
        assert parent_select.execute.call_count == 2  # pre-check + post-update read
        assert_profile_read_scoped(parent_select)
        user_table.update.assert_called_once_with(
            {"name": "Ana María", "birthday": "1985-04-12", "gender": "FEMENINO"}
        )
        user_update.eq.assert_called_once_with("id", 50)
        parent_table.update.assert_not_called()

    def test_put_updates_only_sent_parent_fields(self, parent_client):
        """PUT parcial con campos de parent -> solo 'parent', filtrado por id y user_id."""
        before = parent_profile_row()
        after = {**parent_profile_row(), "occupation": "Docente"}
        parent_table, parent_select, parent_update = make_profile_table(
            select_results=[[before], [after]], update_result=[{"id": 7}]
        )
        user_table, _, _ = make_profile_table()

        with patch(f"{SERVICE_MODULE}.supabase") as mock_sb:
            route_tables(mock_sb, parent=parent_table, user=user_table)
            response = parent_client.put(
                f"{PARENT_BASE_URL}/profile", json={"occupation": "Docente"}
            )

        assert response.status_code == 200
        assert response.json()["data"]["occupation"] == "Docente"
        assert parent_select.execute.call_count == 2
        assert_profile_read_scoped(parent_select)
        parent_table.update.assert_called_once_with({"occupation": "Docente"})
        assert parent_update.eq.call_args_list == [(("id", 7),), (("user_id", 50),)]
        user_table.update.assert_not_called()

    def test_put_mixed_payload_updates_both_tables_with_correct_filters(self, parent_client):
        """PUT con campos de user y parent -> dos updates, cada uno con sus filtros."""
        before = parent_profile_row()
        after = {**parent_profile_row(lastname="Pérez"), "type_representative": "Tutor"}
        parent_table, parent_select, parent_update = make_profile_table(
            select_results=[[before], [after]], update_result=[{"id": 7}]
        )
        user_table, _, user_update = make_profile_table(update_result=[{"id": 50}])

        with patch(f"{SERVICE_MODULE}.supabase") as mock_sb:
            route_tables(mock_sb, parent=parent_table, user=user_table)
            response = parent_client.put(
                f"{PARENT_BASE_URL}/profile",
                json={"lastname": "Pérez", "type_representative": "Tutor"},
            )

        assert response.status_code == 200
        data = response.json()["data"]
        assert data["lastname"] == "Pérez"
        assert data["type_representative"] == "Tutor"
        user_table.update.assert_called_once_with({"lastname": "Pérez"})
        user_update.eq.assert_called_once_with("id", 50)
        parent_table.update.assert_called_once_with({"type_representative": "Tutor"})
        assert parent_update.eq.call_args_list == [(("id", 7),), (("user_id", 50),)]
        assert_profile_read_scoped(parent_select)

    def test_put_parent_update_without_affected_rows_returns_400(self, parent_client):
        """La actualización de 'parent' no afecta filas -> 400."""
        parent_table, _, _ = make_profile_table(
            select_results=[[parent_profile_row()]], update_result=[]
        )
        user_table, _, _ = make_profile_table()

        with patch(f"{SERVICE_MODULE}.supabase") as mock_sb:
            route_tables(mock_sb, parent=parent_table, user=user_table)
            response = parent_client.put(f"{PARENT_BASE_URL}/profile", json={"occupation": "Docente"})

        assert response.status_code == 400
        assert response.json()["success"] is False
        assert "No se pudo actualizar el perfil" in response.json()["message"]

    def test_put_parent_failure_after_user_update_returns_500_and_logs(self, parent_client, caplog):
        """'user' se actualiza y 'parent' falla -> 500 en el envelope, error registrado.

        Comportamiento documentado (sin transacción multi-tabla): el cambio en
        'user' ya aplicado no se revierte.
        """
        parent_table, _, _ = make_profile_table(
            select_results=[[parent_profile_row()]], update_result=RuntimeError("db down")
        )
        user_table, _, _ = make_profile_table(update_result=[{"id": 50}])

        with patch(f"{SERVICE_MODULE}.supabase") as mock_sb, \
                caplog.at_level("ERROR", logger=SERVICE_MODULE):
            route_tables(mock_sb, parent=parent_table, user=user_table)
            response = parent_client.put(
                f"{PARENT_BASE_URL}/profile", json={"name": "Ana", "occupation": "Docente"}
            )

        assert response.status_code == 500
        body = response.json()
        assert body["success"] is False
        assert "db down" not in body["message"]
        user_table.update.assert_called_once_with({"name": "Ana"})
        parent_table.update.assert_called_once_with({"occupation": "Docente"})
        assert any(
            "parent" in record.getMessage() and record.exc_info for record in caplog.records
        )

    def test_put_empty_payload_returns_current_profile_without_writes(self, parent_client):
        """PUT con {} -> 200 con el perfil actual y sin escrituras."""
        parent_query = make_query([parent_profile_row()])
        user_query = make_query([{"id": 50}])

        with patch(f"{SERVICE_MODULE}.supabase") as mock_sb:
            route_tables(mock_sb, parent=parent_query, user=user_query)
            response = parent_client.put(f"{PARENT_BASE_URL}/profile", json={})

        assert response.status_code == 200
        assert response.json()["data"]["name"] == "Ana"
        parent_query.update.assert_not_called()
        user_query.update.assert_not_called()

    def test_put_without_parent_row_returns_404(self, parent_client):
        """PUT sin fila en parent -> 404 y sin escrituras."""
        user_query = make_query([{"id": 50}])

        with patch(f"{SERVICE_MODULE}.supabase") as mock_sb:
            route_tables(mock_sb, parent=make_query([]), user=user_query)
            response = parent_client.put(f"{PARENT_BASE_URL}/profile", json={"name": "Ana"})

        assert response.status_code == 404
        assert response.json()["success"] is False
        user_query.update.assert_not_called()

    def test_put_user_update_without_affected_rows_returns_400(self, parent_client):
        """La actualización de 'user' no afecta filas -> 400."""
        parent_query = make_query([parent_profile_row()])
        user_query = make_query([])

        with patch(f"{SERVICE_MODULE}.supabase") as mock_sb:
            route_tables(mock_sb, parent=parent_query, user=user_query)
            response = parent_client.put(f"{PARENT_BASE_URL}/profile", json={"lastname": "Pérez"})

        assert response.status_code == 400
        assert response.json()["success"] is False

    @pytest.mark.parametrize(
        "payload",
        [
            {"email": "otro@vitaly.com"},
            {"password": "hacked123"},
            {"role": "admin_sistema"},
            {"photo_url": "https://evil.example/x.jpg"},
            {"identity_number": "V-99999999"},
            {"name": None},
            {"name": "   "},
            {"gender": "OTRO"},
            {"birthday": "no-es-fecha"},
            {"occupation": "x" * 101},
        ],
    )
    def test_put_invalid_payload_returns_422(self, parent_client, payload):
        """Campos no permitidos o inválidos -> 422 sin tocar la BD."""
        with patch(f"{SERVICE_MODULE}.supabase") as mock_sb:
            response = parent_client.put(f"{PARENT_BASE_URL}/profile", json=payload)

        assert response.status_code == 422
        assert response.json()["success"] is False
        mock_sb.table.assert_not_called()


class TestParentProfilePhoto:
    def test_upload_valid_photo_returns_photo_url(self, parent_client):
        """JPEG válido -> 200 con photo_url, subido a la carpeta de representantes."""
        uploaded = UploadedImage(
            url="https://ik.imagekit.io/vitaly/profiles/parents/user_50_new.jpg", file_id="new_id"
        )

        with patch.object(profile_photo_service, "imagekit_service") as mock_ik, \
                patch.object(profile_photo_service, "supabase") as mock_sb:
            mock_ik.upload_profile_image.return_value = uploaded
            table = mock_sb.table.return_value
            table.select.return_value.eq.return_value.execute.return_value = MagicMock(
                data=[{"photo_file_id": "old_id"}]
            )
            table.update.return_value.eq.return_value.execute.return_value = MagicMock(data=[{"id": 50}])

            response = parent_client.post(
                f"{PARENT_BASE_URL}/profile/photo",
                files={"file": ("me.jpg", JPEG_BYTES, "image/jpeg")},
            )

        assert response.status_code == 200
        body = response.json()
        assert body["success"] is True
        assert body["data"] == {"photo_url": uploaded.url}
        call_kwargs = mock_ik.upload_profile_image.call_args[1]
        assert call_kwargs["folder"] == profile_photo_service.PARENT_PHOTO_FOLDER
        assert call_kwargs["user_id"] == 50
        table.update.assert_called_once_with({"photo_url": uploaded.url, "photo_file_id": "new_id"})
        mock_ik.delete_file.assert_called_once_with("old_id", user_id=50)

    def test_upload_invalid_file_returns_400(self, parent_client):
        """Contenido HTML declarado como imagen -> 400 sin subir nada."""
        with patch.object(profile_photo_service, "imagekit_service") as mock_ik:
            response = parent_client.post(
                f"{PARENT_BASE_URL}/profile/photo",
                files={"file": ("me.jpg", HTML_BYTES, "image/jpeg")},
            )

        assert response.status_code == 400
        assert response.json()["success"] is False
        mock_ik.upload_profile_image.assert_not_called()

    def test_upload_oversized_file_returns_400(self, parent_client):
        """Archivo > 5 MB -> 400 sin subir nada."""
        big = JPEG_BYTES + b"\x00" * profile_photo_service.MAX_PROFILE_PHOTO_BYTES
        with patch.object(profile_photo_service, "imagekit_service") as mock_ik:
            response = parent_client.post(
                f"{PARENT_BASE_URL}/profile/photo",
                files={"file": ("me.jpg", big, "image/jpeg")},
            )

        assert response.status_code == 400
        assert "5 MB" in response.json()["message"]
        mock_ik.upload_profile_image.assert_not_called()

    def test_delete_photo_sets_photo_url_null(self, parent_client):
        """DELETE -> 200 con photo_url null y BD limpiada."""
        with patch.object(profile_photo_service, "imagekit_service") as mock_ik, \
                patch.object(profile_photo_service, "supabase") as mock_sb:
            table = mock_sb.table.return_value
            table.select.return_value.eq.return_value.execute.return_value = MagicMock(
                data=[{"photo_file_id": "old_id"}]
            )
            table.update.return_value.eq.return_value.execute.return_value = MagicMock(data=[{"id": 50}])

            response = parent_client.delete(f"{PARENT_BASE_URL}/profile/photo")

        assert response.status_code == 200
        body = response.json()
        assert body["success"] is True
        assert body["data"] == {"photo_url": None}
        table.update.assert_called_once_with({"photo_url": None, "photo_file_id": None})
        table.update.return_value.eq.assert_called_once_with("id", 50)
        mock_ik.delete_file.assert_called_once_with("old_id", user_id=50)


PROFILE_REQUESTS = [
    ("get", "/profile", {}),
    ("put", "/profile", {"json": {"name": "Ana"}}),
    ("post", "/profile/photo", {"files": {"file": ("me.jpg", JPEG_BYTES, "image/jpeg")}}),
    ("delete", "/profile/photo", {}),
]


class TestParentProfileRBAC:
    @pytest.mark.parametrize("method,path,kwargs", PROFILE_REQUESTS)
    def test_without_token_returns_401(self, client, method, path, kwargs):
        """Sin token -> 401 en los cuatro endpoints."""
        response = getattr(client, method)(f"{PARENT_BASE_URL}{path}", **kwargs)
        assert response.status_code == 401
        assert response.json()["success"] is False

    @pytest.mark.parametrize("method,path,kwargs", PROFILE_REQUESTS)
    @pytest.mark.parametrize("claims", [admin_school_payload(), doctor_payload(), sys_admin_payload()])
    def test_other_roles_return_403(self, client, method, path, kwargs, claims):
        """Token de otro rol -> 403 en los cuatro endpoints, sin tocar la BD."""
        token = create_access_token(data=claims)
        with patch("app.services.auth_service.auth_service.is_token_revoked", return_value=False), \
                patch("app.services.auth_service.auth_service.assert_account_available"), \
                patch(f"{SERVICE_MODULE}.supabase") as mock_sb, \
                patch.object(profile_photo_service, "supabase") as mock_photo_sb:
            response = getattr(client, method)(
                f"{PARENT_BASE_URL}{path}",
                headers={"Authorization": f"Bearer {token}"},
                **kwargs,
            )

        assert response.status_code == 403
        assert response.json()["success"] is False
        mock_sb.table.assert_not_called()
        mock_photo_sb.table.assert_not_called()

    @pytest.mark.parametrize("method,path,kwargs", PROFILE_REQUESTS)
    def test_revoked_token_returns_401(self, client, method, path, kwargs):
        """Token de representante revocado -> 401."""
        token = create_access_token(data=parent_payload())
        with patch("app.services.auth_service.auth_service.is_token_revoked", return_value=True):
            response = getattr(client, method)(
                f"{PARENT_BASE_URL}{path}",
                headers={"Authorization": f"Bearer {token}"},
                **kwargs,
            )

        assert response.status_code == 401
        assert "El token ha sido revocado" in response.json()["message"]
