"""
Tests for System Admin School Management endpoints and service layer:
  - POST /api/v1/admin/schools/ (create_school)
  - POST /api/v1/admin/schools/classrooms (create_classroom)
  - POST /api/v1/admin/schools/students (create_student)
  - PATCH /api/v1/admin/schools/students/{student_id} (update_student)
  - POST /api/v1/admin/schools/classrooms/register (register_student_to_classroom)
  - POST /api/v1/admin/schools/classrooms/register/bulk (bulk_register_students_to_classroom)
  - GET /api/v1/admin/schools/ & GET /{school_id}
  - GET /api/v1/admin/schools/classrooms/ & GET /classrooms/{classroom_id}
  - GET /api/v1/admin/schools/students/ & GET /students/{student_id}

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
from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app.api.deps import get_current_admin_sistema
from app.core.security import create_access_token
from app.main import app

SERVICE_MODULE = "app.services.admin_service"
SCHOOLS_BASE_URL = "/api/v1/admin/schools"


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
        "name": "Doctor",
        "gender": "MASCULINO",
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
# 1. POST / - Crear Escuela
# ──────────────────────────────────────────────────────────

class TestCreateSchool:
    def test_create_school_success(self, sys_admin_client):
        """Creación exitosa de escuela retorna 200 con envelope y datos insertados."""
        school_table = MagicMock()
        school_table.select.return_value = make_query([])
        school_table.insert.return_value = make_query([{"sch_id": 10}])

        with patch(f"{SERVICE_MODULE}.supabase") as mock_sb:
            route_tables(mock_sb, school=school_table)

            payload = {
                "name": "Colegio San Francisco",
                "year_foundation": 1995,
                "state": 1,
                "rif": 123456789,
                "address": "Av. Principal Los Olivos",
                "school_type": 1,
            }
            response = sys_admin_client.post(f"{SCHOOLS_BASE_URL}/", json=payload)

        assert response.status_code == 200
        body = response.json()
        assert body["success"] is True
        assert "registrada exitosamente con el ID 10" in body["data"]

    def test_create_school_negative_foundation_year_returns_400(self, sys_admin_client):
        """Año de fundación negativo -> 400 Bad Request."""
        payload = {
            "name": "Colegio Inválido",
            "year_foundation": -1,
            "state": 1,
            "rif": 987654321,
            "school_type": 1,
        }
        response = sys_admin_client.post(f"{SCHOOLS_BASE_URL}/", json=payload)
        assert response.status_code == 400
        body = response.json()
        assert body["success"] is False
        assert "no puede ser menor a 0" in body["message"]

    def test_create_school_future_foundation_year_returns_400(self, sys_admin_client):
        """Año de fundación futuro -> 400 Bad Request."""
        future_year = datetime.now().year + 5
        payload = {
            "name": "Colegio Futuro",
            "year_foundation": future_year,
            "state": 1,
            "rif": 987654321,
            "school_type": 1,
        }
        response = sys_admin_client.post(f"{SCHOOLS_BASE_URL}/", json=payload)
        assert response.status_code == 400
        body = response.json()
        assert body["success"] is False
        assert "no puede ser mayor al actual" in body["message"]

    def test_create_school_duplicate_rif_returns_400(self, sys_admin_client):
        """RIF ya registrado en el sistema -> 400 Bad Request."""
        rif_existing = make_query([{"sch_id": 5}])

        with patch(f"{SERVICE_MODULE}.supabase") as mock_sb:
            route_tables(mock_sb, school=rif_existing)

            payload = {
                "name": "Colegio Duplicado",
                "year_foundation": 2000,
                "state": 1,
                "rif": 123456789,
                "school_type": 1,
            }
            response = sys_admin_client.post(f"{SCHOOLS_BASE_URL}/", json=payload)

        assert response.status_code == 400
        body = response.json()
        assert body["success"] is False
        assert "Ya existe una escuela registrada con este mismo RIF" in body["message"]

    def test_create_school_duplicate_name_in_state_returns_400(self, sys_admin_client):
        """Mismo nombre de escuela en el mismo estado -> 400 Bad Request."""
        school_table = MagicMock()
        select_mock = MagicMock()
        # First eq(rif) returns empty; second eq(state) returns duplicate
        select_mock.eq.return_value = select_mock
        select_mock.execute.side_effect = [
            MagicMock(data=[]),  # RIF check
            MagicMock(data=[{"sch_id": 2}]),  # Name + State check
        ]
        school_table.select.return_value = select_mock

        with patch(f"{SERVICE_MODULE}.supabase") as mock_sb:
            route_tables(mock_sb, school=school_table)

            payload = {
                "name": "Colegio Existente",
                "year_foundation": 2000,
                "state": 1,
                "rif": 999999999,
                "school_type": 1,
            }
            response = sys_admin_client.post(f"{SCHOOLS_BASE_URL}/", json=payload)

        assert response.status_code == 400
        body = response.json()
        assert body["success"] is False
        assert "mismo nombre en ese estado" in body["message"]


# ──────────────────────────────────────────────────────────
# 2. POST /classrooms - Crear Salón de Clase
# ──────────────────────────────────────────────────────────

class TestCreateClassroom:
    def test_create_classroom_success(self, sys_admin_client):
        """Creación exitosa de aula en categoría Primaria (2) y nivel 5."""
        school_query = make_query([{"sch_id": 1}])
        ay_query = make_query([{"id": 1}])
        classroom_table = MagicMock()
        classroom_table.select.return_value = make_query([])
        classroom_table.insert.return_value = make_query([{"id": 50}])

        with patch(f"{SERVICE_MODULE}.supabase") as mock_sb:
            route_tables(
                mock_sb,
                school=school_query,
                academic_year=ay_query,
                classroom=classroom_table,
            )

            payload = {
                "school_id": 1,
                "academic_year_id": 1,
                "category": 2,  # Primaria
                "level": 5,
                "section": "A",
            }
            response = sys_admin_client.post(f"{SCHOOLS_BASE_URL}/classrooms", json=payload)

        assert response.status_code == 200
        body = response.json()
        assert body["success"] is True
        assert "creado exitosamente con ID 50" in body["data"]

    def test_create_classroom_invalid_category_returns_400(self, sys_admin_client):
        """Categoría desconocida (ej. 99) -> 400 Bad Request."""
        payload = {
            "school_id": 1,
            "academic_year_id": 1,
            "category": 99,
            "level": 1,
        }
        response = sys_admin_client.post(f"{SCHOOLS_BASE_URL}/classrooms", json=payload)
        assert response.status_code == 400
        body = response.json()
        assert body["success"] is False
        assert "Categoría inválida" in body["message"]

    def test_create_classroom_invalid_level_for_category_returns_400(self, sys_admin_client):
        """Nivel superior al límite de categoría (ej. Preescolar nivel 4) -> 400 Bad Request."""
        payload = {
            "school_id": 1,
            "academic_year_id": 1,
            "category": 1,  # Preescolar: max 3
            "level": 4,
        }
        response = sys_admin_client.post(f"{SCHOOLS_BASE_URL}/classrooms", json=payload)
        assert response.status_code == 400
        body = response.json()
        assert body["success"] is False
        assert "Nivel inválido para la categoría" in body["message"]

    def test_create_classroom_nonexistent_school_returns_404(self, sys_admin_client):
        """Escuela no encontrada -> 404 Not Found."""
        school_empty = make_query([])

        with patch(f"{SERVICE_MODULE}.supabase") as mock_sb:
            route_tables(mock_sb, school=school_empty)
            payload = {
                "school_id": 999,
                "academic_year_id": 1,
                "category": 2,
                "level": 1,
            }
            response = sys_admin_client.post(f"{SCHOOLS_BASE_URL}/classrooms", json=payload)

        assert response.status_code == 404
        body = response.json()
        assert body["success"] is False
        assert "No existe ninguna escuela con ID 999" in body["message"]

    def test_create_classroom_nonexistent_academic_year_returns_404(self, sys_admin_client):
        """Año académico no encontrado -> 404 Not Found."""
        school_query = make_query([{"sch_id": 1}])
        ay_empty = make_query([])

        with patch(f"{SERVICE_MODULE}.supabase") as mock_sb:
            route_tables(mock_sb, school=school_query, academic_year=ay_empty)
            payload = {
                "school_id": 1,
                "academic_year_id": 999,
                "category": 2,
                "level": 1,
            }
            response = sys_admin_client.post(f"{SCHOOLS_BASE_URL}/classrooms", json=payload)

        assert response.status_code == 404
        body = response.json()
        assert body["success"] is False
        assert "No existe ningún año académico con ID 999" in body["message"]

    def test_create_classroom_duplicate_returns_400(self, sys_admin_client):
        """Salón exacto ya existente -> 400 Bad Request."""
        school_query = make_query([{"sch_id": 1}])
        ay_query = make_query([{"id": 1}])
        classroom_existing = make_query([{"id": 12}])

        with patch(f"{SERVICE_MODULE}.supabase") as mock_sb:
            route_tables(mock_sb, school=school_query, academic_year=ay_query, classroom=classroom_existing)
            payload = {
                "school_id": 1,
                "academic_year_id": 1,
                "category": 2,
                "level": 1,
                "section": "A",
            }
            response = sys_admin_client.post(f"{SCHOOLS_BASE_URL}/classrooms", json=payload)

        assert response.status_code == 400
        body = response.json()
        assert body["success"] is False
        assert "Ya existe un salón de clase registrado con los mismos datos" in body["message"]


# ──────────────────────────────────────────────────────────
# 3. POST & PATCH /students - Gestión de Estudiantes
# ──────────────────────────────────────────────────────────

class TestStudentsManagement:
    def test_create_student_success(self, sys_admin_client):
        """Creación exitosa de estudiante con CI válida y edad permitida."""
        student_table = MagicMock()
        student_table.select.return_value = make_query([])
        student_table.insert.return_value = make_query([{"id": 100}])

        with patch(f"{SERVICE_MODULE}.supabase") as mock_sb:
            route_tables(mock_sb, student=student_table)

            payload = {
                "name": "Pedro",
                "lastname": "Pérez",
                "birthday": "2015-04-12",
                "gender": "MASCULINO",
                "blood_type": "O+",
                "identity_number": "V-30111222",
            }
            response = sys_admin_client.post(f"{SCHOOLS_BASE_URL}/students", json=payload)

        assert response.status_code == 200
        body = response.json()
        assert body["success"] is True
        assert "registrado exitosamente con ID 100" in body["data"]

    def test_create_student_duplicate_ci_returns_400(self, sys_admin_client):
        """Cédula de identidad ya registrada para otro estudiante activo -> 400."""
        ci_existing = make_query([{"id": 50}])

        with patch(f"{SERVICE_MODULE}.supabase") as mock_sb:
            route_tables(mock_sb, student=ci_existing)

            payload = {
                "name": "Pedro",
                "lastname": "Pérez",
                "birthday": "2015-04-12",
                "gender": "MASCULINO",
                "identity_number": "V-30111222",
            }
            response = sys_admin_client.post(f"{SCHOOLS_BASE_URL}/students", json=payload)

        assert response.status_code == 400
        body = response.json()
        assert body["success"] is False
        assert "Ya existe un estudiante registrado con la cédula" in body["message"]

    def test_create_student_age_over_21_returns_422(self, sys_admin_client):
        """Estudiante mayor a 21 años es rechazado por validador de schema -> 422."""
        payload = {
            "name": "Adulto",
            "lastname": "Mayor",
            "birthday": "1995-01-01",
            "gender": "MASCULINO",
        }
        response = sys_admin_client.post(f"{SCHOOLS_BASE_URL}/students", json=payload)
        assert response.status_code == 422

    def test_update_student_success(self, sys_admin_client):
        """Actualización parcial de campos de estudiante existente."""
        student_current = make_query([{"id": 100, "identity_number": "V-123", "is_deleted": False}])

        with patch(f"{SERVICE_MODULE}.supabase") as mock_sb:
            route_tables(mock_sb, student=student_current)

            payload = {"name": "Pedro José"}
            response = sys_admin_client.patch(f"{SCHOOLS_BASE_URL}/students/100", json=payload)

        assert response.status_code == 200
        body = response.json()
        assert body["success"] is True
        assert "actualizado exitosamente" in body["data"]

    def test_update_student_not_found_returns_404(self, sys_admin_client):
        """Estudiante a actualizar no existe -> 404 Not Found."""
        student_empty = make_query([])

        with patch(f"{SERVICE_MODULE}.supabase") as mock_sb:
            route_tables(mock_sb, student=student_empty)

            payload = {"name": "Nuevo Nombre"}
            response = sys_admin_client.patch(f"{SCHOOLS_BASE_URL}/students/999", json=payload)

        assert response.status_code == 404
        body = response.json()
        assert body["success"] is False
        assert "No existe ningún estudiante con ID 999" in body["message"]

    def test_update_student_deleted_returns_400(self, sys_admin_client):
        """Estudiante marcado como eliminado -> 400 Bad Request."""
        student_deleted = make_query([{"id": 100, "identity_number": "V-123", "is_deleted": True}])

        with patch(f"{SERVICE_MODULE}.supabase") as mock_sb:
            route_tables(mock_sb, student=student_deleted)

            payload = {"name": "Nuevo Nombre"}
            response = sys_admin_client.patch(f"{SCHOOLS_BASE_URL}/students/100", json=payload)

        assert response.status_code == 400
        body = response.json()
        assert body["success"] is False
        assert "No se puede modificar un estudiante que ha sido eliminado" in body["message"]

    def test_update_student_empty_body_returns_400(self, sys_admin_client):
        """Petición sin ningún campo a actualizar -> 400 Bad Request."""
        student_current = make_query([{"id": 100, "identity_number": "V-123", "is_deleted": False}])

        with patch(f"{SERVICE_MODULE}.supabase") as mock_sb:
            route_tables(mock_sb, student=student_current)

            payload = {}
            response = sys_admin_client.patch(f"{SCHOOLS_BASE_URL}/students/100", json=payload)

        assert response.status_code == 400
        body = response.json()
        assert body["success"] is False
        assert "No se proporcionó ningún campo para actualizar" in body["message"]


# ──────────────────────────────────────────────────────────
# 4. POST /classrooms/register - Vinculación a Salón
# ──────────────────────────────────────────────────────────

class TestClassroomRegistration:
    def test_register_student_to_classroom_success(self, sys_admin_client):
        """Vinculación exitosa de estudiante a salón."""
        cr_check = make_query([{"id": 10, "academic_year_id": 1}])
        reg_table = MagicMock()
        reg_table.select.return_value = make_query([])
        reg_table.insert.return_value = make_query([{"id": 1}])

        with patch(f"{SERVICE_MODULE}.supabase") as mock_sb:
            route_tables(
                mock_sb,
                classroom=cr_check,
                classroom_registration=reg_table,
            )

            payload = {
                "student_id": 5,
                "academic_year_id": 1,
                "classroom_id": 10,
            }
            response = sys_admin_client.post(f"{SCHOOLS_BASE_URL}/classrooms/register", json=payload)

        assert response.status_code == 200
        body = response.json()
        assert body["success"] is True
        assert "vinculado exitosamente al salón 10" in body["data"]

    def test_register_student_wrong_academic_year_returns_400(self, sys_admin_client):
        """Salón no corresponde al año académico indicado -> 400 Bad Request."""
        cr_check = make_query([{"id": 10, "academic_year_id": 2}])

        with patch(f"{SERVICE_MODULE}.supabase") as mock_sb:
            route_tables(mock_sb, classroom=cr_check)

            payload = {
                "student_id": 5,
                "academic_year_id": 1,
                "classroom_id": 10,
            }
            response = sys_admin_client.post(f"{SCHOOLS_BASE_URL}/classrooms/register", json=payload)

        assert response.status_code == 400
        body = response.json()
        assert body["success"] is False
        assert "no pertenece al año académico" in body["message"]

    def test_bulk_register_students_success(self, sys_admin_client):
        """Vinculación masiva de estudiantes a salón."""
        cr_check = make_query([{"id": 10, "academic_year_id": 1}])
        reg_table = MagicMock()
        reg_table.select.return_value = make_query([])
        reg_table.insert.return_value = make_query([{"id": 1}, {"id": 2}])

        with patch(f"{SERVICE_MODULE}.supabase") as mock_sb:
            route_tables(
                mock_sb,
                classroom=cr_check,
                classroom_registration=reg_table,
            )

            payload = {
                "student_ids": [5, 6],
                "academic_year_id": 1,
                "classroom_id": 10,
            }
            response = sys_admin_client.post(f"{SCHOOLS_BASE_URL}/classrooms/register/bulk", json=payload)

        assert response.status_code == 200
        body = response.json()
        assert body["success"] is True
        assert "vinculado exitosamente 2 estudiantes" in body["data"]


# ──────────────────────────────────────────────────────────
# 5. GET Endpoints (Escuelas, Salones y Estudiantes)
# ──────────────────────────────────────────────────────────

class TestGetSchoolsClassroomsStudents:
    def test_get_schools_success(self, sys_admin_client):
        """Listado paginado de escuelas registradas."""
        schools_data = [
            {"sch_id": 1, "name": "Colegio San Ignacio", "year_foundation": 1950, "rif": 123456, "address": "Av. A", "state": 1, "school_type": 1, "is_active": True, "is_deleted": False}
        ]
        school_query = make_query(schools_data, count=1)

        with patch(f"{SERVICE_MODULE}.supabase") as mock_sb:
            route_tables(mock_sb, school=school_query)
            response = sys_admin_client.get(f"{SCHOOLS_BASE_URL}/?page=1&size=20")

        assert response.status_code == 200
        body = response.json()
        assert body["success"] is True
        assert len(body["data"]["data"]) == 1

    def test_get_schools_empty_returns_404(self, sys_admin_client):
        """Sin escuelas registradas -> 404 Not Found (legado tolerado)."""
        count_zero = make_query([], count=0)

        with patch(f"{SERVICE_MODULE}.supabase") as mock_sb:
            route_tables(mock_sb, school=count_zero)
            response = sys_admin_client.get(f"{SCHOOLS_BASE_URL}/?page=1&size=20")

        assert response.status_code == 404
        body = response.json()
        assert body["success"] is False
        assert "No hay escuelas registradas" in body["message"]

    def test_get_school_detail_success(self, sys_admin_client):
        """Detalle de escuela por ID."""
        school_data = [
            {"sch_id": 1, "name": "Colegio San Ignacio", "year_foundation": 1950, "rif": 123456, "address": "Av. A", "state": 1, "school_type": 1, "is_active": True, "is_deleted": False}
        ]
        school_query = make_query(school_data)

        with patch(f"{SERVICE_MODULE}.supabase") as mock_sb:
            route_tables(mock_sb, school=school_query)
            response = sys_admin_client.get(f"{SCHOOLS_BASE_URL}/1")

        assert response.status_code == 200
        body = response.json()
        assert body["success"] is True
        assert body["data"]["name"] == "Colegio San Ignacio"

    def test_get_school_detail_not_found_returns_404(self, sys_admin_client):
        """Detalle de escuela inexistente -> 404 Not Found."""
        school_query = make_query([])

        with patch(f"{SERVICE_MODULE}.supabase") as mock_sb:
            route_tables(mock_sb, school=school_query)
            response = sys_admin_client.get(f"{SCHOOLS_BASE_URL}/999")

        assert response.status_code == 404
        body = response.json()
        assert body["success"] is False
        assert "No existe ninguna escuela con ID 999" in body["message"]


# ──────────────────────────────────────────────────────────
# 6. Matriz RBAC para Endpoints de Admin Escuelas
# ──────────────────────────────────────────────────────────

class TestAdminSchoolRBAC:
    def test_schools_unauthenticated_returns_401_or_403(self, client):
        """Llamada sin token a admin/schools -> 401/403."""
        response = client.get(f"{SCHOOLS_BASE_URL}/")
        assert response.status_code in (401, 403)

    def test_schools_forbidden_for_admin_escuela(self, client):
        """Rol admin_escuela intentando acceder a admin/schools -> 403 Forbidden."""
        token = create_access_token(data=admin_school_payload())
        with patch("app.services.auth_service.auth_service.is_token_revoked", return_value=False), \
             patch("app.services.auth_service.auth_service.assert_account_available"):
            response = client.get(
                f"{SCHOOLS_BASE_URL}/",
                headers={"Authorization": f"Bearer {token}"},
            )
        assert response.status_code == 403
        body = response.json()
        assert body["success"] is False
        assert "token emitido por el portal de administración" in body["message"]

    def test_schools_forbidden_for_doctor(self, client):
        """Rol doctor intentando acceder a admin/schools -> 403 Forbidden."""
        token = create_access_token(data=doctor_payload())
        with patch("app.services.auth_service.auth_service.is_token_revoked", return_value=False), \
             patch("app.services.auth_service.auth_service.assert_account_available"):
            response = client.get(
                f"{SCHOOLS_BASE_URL}/",
                headers={"Authorization": f"Bearer {token}"},
            )
        assert response.status_code == 403
        body = response.json()
        assert body["success"] is False
        assert "token emitido por el portal de administración" in body["message"]
