"""
Tests de la búsqueda global de estudiantes (issue #66):
  - GET /api/v1/school-admin/search/students
  - GET /api/v1/doctor/students/search

Cumple las 5 leyes de vitaly-feature-testing:
  1. Envelope APIResponse, incluyendo 'meta' de paginación y la ruta 500.
  2. Alcance resuelto en el servidor desde el 'sub' del JWT: el admin ve solo su
     escuela; el médico, solo aquellas con vínculo vigente. El 'school_id' del
     cliente nunca amplía el alcance.
  3. Falso cliente supabase-py determinista (tests/fake_supabase.py).
  4. Sin resultados -> 200 con data=[] y total=0, nunca 404.
  5. Matriz RBAC: 401 sin credenciales, 403 rol ajeno, 422 validación.

Toda base de prueba incluye estudiantes de una SEGUNDA escuela. Sin ellos, el
filtro por 'school_id' se podría borrar y la suite seguiría en verde.
"""
from contextlib import ExitStack
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from app.api.deps import (
    get_current_admin_escuela,
    get_current_doctor,
    get_current_user_token,
)
from app.core.security import create_access_token
from app.main import app
from app.services.auth_service import auth_service
from app.services.doctor_event_service import ACTIVE_DOCTOR_SCHOOL_STATUS
from app.services.student_search_service import _escape_like
from tests.fake_supabase import FakeQuery, FakeSupabase

ADMIN_URL = "/api/v1/school-admin/search/students"
DOCTOR_URL = "/api/v1/doctor/students/search"

#: Cada servicio implicado importa su cliente en su propio módulo. La búsqueda lee
#: la vista con el cliente privilegiado, porque la migración se la deniega a `anon`;
#: los resolvedores de alcance siguen usando el cliente normal.
SUPABASE_TARGETS = (
    "app.services.student_search_service.supabase_admin",
    "app.services.school_admin_service.supabase",
    "app.services.doctor_event_service.supabase",
)

ADMIN_USER_ID = 30
DOCTOR_USER_ID = 40
DOC_ID = 7
OTHER_DOC_ID = 8
SCHOOL_ID = 1
OTHER_SCHOOL_ID = 2
THIRD_SCHOOL_ID = 3
SUSPENDED_STATUS = 1


# ──────────────────────────────────────────────────────────
# Filas de prueba
# ──────────────────────────────────────────────────────────

def student_row(student_id, full_name, *, school_id=SCHOOL_ID, bmi_status="OPTIMO",
                bmi=21.0, has_case=False, category=1, level=1, section="A",
                is_active=True):
    """Fila con la forma que devuelve la vista 'student_health_status'."""
    name, _, lastname = full_name.partition(" ")
    return {
        "student_id": student_id,
        "school_id": school_id,
        "name": name,
        "lastname": lastname,
        "full_name": full_name,
        "birthday": "2015-05-10",
        "gender": "MASCULINO",
        "blood_type": "O+",
        "identity_number": None,
        "is_active": is_active,
        "classroom_id": 100 + level,
        "category": category,
        "level": level,
        "section": section,
        "academic_year_id": 9,
        "bmi": bmi,
        "bmi_status": bmi_status,
        "has_active_medical_case": has_case,
    }


#: Estudiantes de otra escuela, presentes en TODA base de prueba.
FOREIGN_STUDENTS = [
    student_row(900, "Mariana Ajena", school_id=OTHER_SCHOOL_ID, bmi_status="SOBREPESO"),
    student_row(901, "Pedro Ajeno", school_id=OTHER_SCHOOL_ID, bmi_status="DESNUTRIDO"),
]


def build_db(students, *, links=None, administrators=None, doctors=None):
    """Arma una base falsa coherente alrededor de las filas bajo prueba."""
    return FakeSupabase(
        student_health_status=list(students) + FOREIGN_STUDENTS,
        school_administrator=administrators if administrators is not None else [
            {"user_id": ADMIN_USER_ID, "school_id": SCHOOL_ID},
            {"user_id": 31, "school_id": OTHER_SCHOOL_ID},
        ],
        doctor=doctors if doctors is not None else [
            {"doc_id": DOC_ID, "user_id": DOCTOR_USER_ID},
            {"doc_id": OTHER_DOC_ID, "user_id": 41},
        ],
        doctor_to_school=(links if links is not None else [
            {"doctor_id": DOC_ID, "school_id": SCHOOL_ID, "status": ACTIVE_DOCTOR_SCHOOL_STATUS},
        ]) + [
            {"doctor_id": OTHER_DOC_ID, "school_id": OTHER_SCHOOL_ID,
             "status": ACTIVE_DOCTOR_SCHOOL_STATUS},
        ],
    )


class patch_supabase(ExitStack):
    """Sustituye el cliente en los tres módulos de servicio implicados."""

    def __init__(self, fake):
        super().__init__()
        self._fake = fake

    def __enter__(self):
        super().__enter__()
        for target in SUPABASE_TARGETS:
            self.enter_context(patch(target, self._fake))
        # La bitácora usa el cliente privilegiado; aquí no se ejerce.
        self.enter_context(patch("app.services.audit_service.supabase_admin", None))
        return self._fake


# ──────────────────────────────────────────────────────────
# Fixtures
# ──────────────────────────────────────────────────────────

def admin_payload(user_id=ADMIN_USER_ID):
    return {"sub": str(user_id), "email": "admin@vitaly.app",
            "role": "admin_escuela", "scope": "client"}


def doctor_payload(user_id=DOCTOR_USER_ID):
    return {"sub": str(user_id), "email": "dr.vargas@vitaly.app",
            "role": "doctor", "scope": "client"}


@pytest.fixture
def admin_client():
    app.dependency_overrides[get_current_admin_escuela] = lambda: admin_payload()
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


@pytest.fixture
def doctor_client():
    app.dependency_overrides[get_current_doctor] = lambda: doctor_payload()
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
# 1. Contrato del endpoint y envelope
# ──────────────────────────────────────────────────────────

def test_admin_search_returns_envelope_with_pagination_meta(admin_client):
    """200 con envelope APIResponse y 'meta' de paginación poblada."""
    fake = build_db([student_row(1, "Juan Pérez")])
    with patch_supabase(fake):
        response = admin_client.get(ADMIN_URL)

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert body["errors"] is None
    assert body["meta"] == {"page": 1, "size": 20, "total": 1, "total_pages": 1}
    assert len(body["data"]) == 1
    student = body["data"][0]
    assert student["student_id"] == 1
    assert student["bmi_status"] == "OPTIMO"
    assert student["has_active_medical_case"] is False


def test_response_does_not_expose_raw_weight_and_height(admin_client):
    """La respuesta masiva no incluye peso ni talla en crudo."""
    fake = build_db([student_row(1, "Juan Pérez")])
    with patch_supabase(fake):
        response = admin_client.get(ADMIN_URL)

    student = response.json()["data"][0]
    assert "weight" not in student and "height" not in student
    assert student["bmi"] == 21.0


def test_service_failure_returns_500_with_envelope(admin_client):
    """Un fallo de postgrest se traduce a 500 con el envelope APIResponse."""
    fake = build_db([student_row(1, "Juan Pérez")])
    fake.failing_table = "student_health_status"
    with patch_supabase(fake):
        response = admin_client.get(ADMIN_URL)

    assert response.status_code == 500
    body = response.json()
    assert body["success"] is False
    assert "error al buscar estudiantes" in body["message"]


# ──────────────────────────────────────────────────────────
# 2. Aislamiento por escuela
# ──────────────────────────────────────────────────────────

def test_admin_only_sees_students_of_their_own_school(admin_client):
    """El admin nunca ve estudiantes de otra escuela, aunque existan."""
    fake = build_db([student_row(1, "Juan Pérez"), student_row(2, "Lucía Mora")])
    with patch_supabase(fake):
        response = admin_client.get(ADMIN_URL)

    body = response.json()
    assert body["meta"]["total"] == 2
    assert {s["student_id"] for s in body["data"]} == {1, 2}
    assert all(s["school_id"] == SCHOOL_ID for s in body["data"])


def test_admin_without_school_link_returns_403(admin_client):
    """Admin sin fila en 'school_administrator' -> 403."""
    fake = build_db([student_row(1, "Juan Pérez")], administrators=[])
    with patch_supabase(fake):
        response = admin_client.get(ADMIN_URL)

    assert response.status_code == 403
    assert response.json()["success"] is False


def test_doctor_sees_students_of_all_their_active_schools(doctor_client):
    """El médico ve a los estudiantes de cada escuela con vínculo vigente."""
    fake = build_db(
        [student_row(1, "Juan Pérez"),
         student_row(3, "Carla Díaz", school_id=THIRD_SCHOOL_ID)],
        links=[
            {"doctor_id": DOC_ID, "school_id": SCHOOL_ID, "status": ACTIVE_DOCTOR_SCHOOL_STATUS},
            {"doctor_id": DOC_ID, "school_id": THIRD_SCHOOL_ID, "status": ACTIVE_DOCTOR_SCHOOL_STATUS},
        ],
    )
    with patch_supabase(fake):
        response = doctor_client.get(DOCTOR_URL)

    body = response.json()
    assert body["meta"]["total"] == 2
    assert {s["student_id"] for s in body["data"]} == {1, 3}


def test_suspended_doctor_loses_access_to_that_school(doctor_client):
    """Un vínculo suspendido retira el acceso a los estudiantes de esa escuela."""
    fake = build_db(
        [student_row(1, "Juan Pérez")],
        links=[{"doctor_id": DOC_ID, "school_id": SCHOOL_ID, "status": SUSPENDED_STATUS}],
    )
    with patch_supabase(fake):
        response = doctor_client.get(DOCTOR_URL)

    assert response.status_code == 200
    body = response.json()
    assert body["data"] == []
    assert body["meta"]["total"] == 0


def test_doctor_requesting_foreign_school_returns_403(doctor_client):
    """El 'school_id' del cliente no amplía el alcance: escuela ajena -> 403."""
    fake = build_db([student_row(1, "Juan Pérez")])
    with patch_supabase(fake):
        response = doctor_client.get(f"{DOCTOR_URL}?school_id={OTHER_SCHOOL_ID}")

    assert response.status_code == 403
    body = response.json()
    assert body["success"] is False
    assert "vínculo vigente" in body["message"]


def test_doctor_can_narrow_to_one_of_their_schools(doctor_client):
    """'school_id' acota dentro del conjunto permitido, no lo amplía."""
    fake = build_db(
        [student_row(1, "Juan Pérez"),
         student_row(3, "Carla Díaz", school_id=THIRD_SCHOOL_ID)],
        links=[
            {"doctor_id": DOC_ID, "school_id": SCHOOL_ID, "status": ACTIVE_DOCTOR_SCHOOL_STATUS},
            {"doctor_id": DOC_ID, "school_id": THIRD_SCHOOL_ID, "status": ACTIVE_DOCTOR_SCHOOL_STATUS},
        ],
    )
    with patch_supabase(fake):
        response = doctor_client.get(f"{DOCTOR_URL}?school_id={THIRD_SCHOOL_ID}")

    body = response.json()
    assert [s["student_id"] for s in body["data"]] == [3]


def test_doctor_without_profile_returns_403(doctor_client):
    """Rol doctor pero sin fila en 'doctor' -> 403."""
    fake = build_db([student_row(1, "Juan Pérez")], doctors=[])
    with patch_supabase(fake):
        response = doctor_client.get(DOCTOR_URL)

    assert response.status_code == 403
    assert "perfil de médico" in response.json()["message"]


# ──────────────────────────────────────────────────────────
# 3. Filtro por estado nutricional
# ──────────────────────────────────────────────────────────

@pytest.mark.parametrize("status,expected_ids", [
    ("DESNUTRIDO", {11}),
    ("OPTIMO", {12}),
    ("SOBREPESO", {13}),
    ("SIN DATOS", {14}),
])
def test_filter_by_bmi_status(admin_client, status, expected_ids):
    """Cada valor del enum devuelve exactamente su banda."""
    fake = build_db([
        student_row(11, "Ana Baja", bmi_status="DESNUTRIDO", bmi=17.0),
        student_row(12, "Beto Medio", bmi_status="OPTIMO", bmi=21.0),
        student_row(13, "Caro Alta", bmi_status="SOBREPESO", bmi=27.0),
        student_row(14, "Dani Sin", bmi_status="SIN DATOS", bmi=None),
    ])
    with patch_supabase(fake):
        response = admin_client.get(ADMIN_URL, params={"bmi_status": status})

    body = response.json()
    assert {s["student_id"] for s in body["data"]} == expected_ids
    assert body["meta"]["total"] == len(expected_ids)


def test_unknown_bmi_status_returns_422(admin_client):
    """Un estado fuera del enum lo rechaza la validación, no la base de datos."""
    fake = build_db([student_row(1, "Juan Pérez")])
    with patch_supabase(fake):
        response = admin_client.get(ADMIN_URL, params={"bmi_status": "OBESO"})

    assert response.status_code == 422
    assert response.json()["success"] is False


def test_filter_by_active_medical_case(admin_client):
    """El filtro de caso abierto distingue ambos sentidos."""
    fake = build_db([
        student_row(21, "Con Caso", has_case=True),
        student_row(22, "Sin Caso", has_case=False),
    ])
    with patch_supabase(fake):
        con = admin_client.get(ADMIN_URL, params={"has_active_medical_case": "true"})
        sin = admin_client.get(ADMIN_URL, params={"has_active_medical_case": "false"})

    assert [s["student_id"] for s in con.json()["data"]] == [21]
    assert [s["student_id"] for s in sin.json()["data"]] == [22]


# ──────────────────────────────────────────────────────────
# 4. Búsqueda por texto
# ──────────────────────────────────────────────────────────

def test_text_search_is_partial_and_case_insensitive(admin_client):
    """'q' casa parcialmente sobre el nombre completo, sin distinguir mayúsculas."""
    fake = build_db([
        student_row(31, "Juan Pérez"),
        student_row(32, "Juana Pérez"),
        student_row(33, "Marco Solís"),
    ])
    with patch_supabase(fake):
        response = admin_client.get(ADMIN_URL, params={"q": "juan"})

    assert {s["student_id"] for s in response.json()["data"]} == {31, 32}


def test_text_search_escapes_wildcards(admin_client):
    """Un comodín escrito por el usuario no amplía el resultado.

    Limitación conocida: este caso se apoya en que `tests/fake_supabase.py` honra el
    escape igual que SQL. Demuestra la intención del servicio, no el comportamiento
    de PostgREST. Los dos tests siguientes cubren el límite real.
    """
    fake = build_db([student_row(31, "Juan Pérez"), student_row(32, "Marco Solís")])
    with patch_supabase(fake):
        response = admin_client.get(ADMIN_URL, params={"q": "%"})

    body = response.json()
    assert body["data"] == []
    assert body["meta"]["total"] == 0


def test_text_search_combines_with_status_filter(admin_client):
    """Los filtros se acumulan: texto y estado a la vez."""
    fake = build_db([
        student_row(41, "Juan Pérez", bmi_status="SOBREPESO"),
        student_row(42, "Juana Pérez", bmi_status="OPTIMO"),
    ])
    with patch_supabase(fake):
        response = admin_client.get(ADMIN_URL, params={"q": "juan", "bmi_status": "SOBREPESO"})

    assert [s["student_id"] for s in response.json()["data"]] == [41]


# ──────────────────────────────────────────────────────────
# 5. Filtros de salón y su jerarquía
# ──────────────────────────────────────────────────────────

def test_filter_by_classroom_dimensions(admin_client):
    """category + grade + section acotan al salón exacto."""
    fake = build_db([
        student_row(51, "Uno A", category=1, level=1, section="A"),
        student_row(52, "Uno B", category=1, level=1, section="B"),
        student_row(53, "Dos A", category=1, level=2, section="A"),
    ])
    with patch_supabase(fake):
        response = admin_client.get(
            ADMIN_URL, params={"category": 1, "grade": 1, "section": "a"}
        )

    assert [s["student_id"] for s in response.json()["data"]] == [51]


@pytest.mark.parametrize("params", [
    {"grade": 1},                 # grade sin category
    {"section": "A"},             # section sin category ni grade
    {"category": 1, "section": "A"},  # section sin grade
])
def test_hierarchical_classroom_filters_return_400(admin_client, params):
    """La jerarquía replica la de get_students_resume: falta el padre -> 400."""
    fake = build_db([student_row(1, "Juan Pérez")])
    with patch_supabase(fake):
        response = admin_client.get(ADMIN_URL, params=params)

    assert response.status_code == 400
    assert response.json()["success"] is False


# ──────────────────────────────────────────────────────────
# 6. Colección vacía y paginación
# ──────────────────────────────────────────────────────────

def test_no_matches_returns_200_with_empty_list(admin_client):
    """Sin coincidencias -> 200 con data=[] y total=0, nunca 404."""
    fake = build_db([student_row(1, "Juan Pérez")])
    with patch_supabase(fake):
        response = admin_client.get(ADMIN_URL, params={"q": "inexistente"})

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert body["data"] == []
    assert body["meta"]["total"] == 0
    assert body["meta"]["total_pages"] == 0
    assert "No se encontraron estudiantes" in body["message"]


def test_pagination_total_counts_matches_not_page_size(admin_client):
    """'meta.total' cuenta todas las coincidencias, no las de la página."""
    fake = build_db([student_row(i, f"Alumno{i:02d} Prueba") for i in range(1, 8)])
    with patch_supabase(fake):
        page1 = admin_client.get(ADMIN_URL, params={"page": 1, "size": 3})
        page3 = admin_client.get(ADMIN_URL, params={"page": 3, "size": 3})

    body1, body3 = page1.json(), page3.json()
    assert body1["meta"] == {"page": 1, "size": 3, "total": 7, "total_pages": 3}
    assert len(body1["data"]) == 3
    assert len(body3["data"]) == 1
    # Orden estable por nombre: las páginas no se solapan.
    assert body1["data"][0]["full_name"] == "Alumno01 Prueba"
    assert body3["data"][0]["full_name"] == "Alumno07 Prueba"


# ──────────────────────────────────────────────────────────
# 7. Auditoría del endpoint del médico
# ──────────────────────────────────────────────────────────

def test_doctor_search_records_an_audit_entry(doctor_client):
    """La consulta del médico se registra como acceso de lectura."""
    fake = build_db([student_row(1, "Juan Pérez", bmi_status="SOBREPESO")])
    with patch_supabase(fake), \
            patch("app.api.v1.endpoints.doctor_students.audit_service") as audit:
        response = doctor_client.get(DOCTOR_URL, params={"bmi_status": "SOBREPESO"})

    assert response.status_code == 200
    audit.record.assert_called_once()
    kwargs = audit.record.call_args.kwargs
    assert kwargs["action"] == "read"
    assert kwargs["actor"]["sub"] == str(DOCTOR_USER_ID)
    # El alcance resuelto, no el query param: un NULL aquí significaría
    # "acción global de admin_sistema" según el COMMENT de la migración.
    assert kwargs["school_id"] == SCHOOL_ID
    assert kwargs["metadata"]["searched_school_ids"] == [SCHOOL_ID]
    assert kwargs["metadata"]["filters"]["bmi_status"] == "SOBREPESO"
    assert kwargs["metadata"]["total_matches"] == 1


def test_audit_metadata_does_not_leak_student_identities(doctor_client):
    """La bitácora registra el acceso, no su contenido: ni nombres ni término buscado."""
    fake = build_db([student_row(1, "Juan Pérez")])
    with patch_supabase(fake), \
            patch("app.api.v1.endpoints.doctor_students.audit_service") as audit:
        doctor_client.get(DOCTOR_URL, params={"q": "Juan"})

    serialized = repr(audit.record.call_args.kwargs)
    assert "Juan" not in serialized
    assert audit.record.call_args.kwargs["metadata"]["filters"]["q"] is True


def test_search_survives_an_unavailable_audit_log(doctor_client):
    """Una bitácora caída no tumba la búsqueda (critical=False)."""
    fake = build_db([student_row(1, "Juan Pérez")])
    with patch_supabase(fake):
        response = doctor_client.get(DOCTOR_URL)

    assert response.status_code == 200
    assert response.json()["meta"]["total"] == 1


# ──────────────────────────────────────────────────────────
# 8. Matriz RBAC
# ──────────────────────────────────────────────────────────

@pytest.mark.parametrize("url", [ADMIN_URL, DOCTOR_URL])
def test_unauthenticated_returns_401(client, url):
    """Sin credenciales -> 401."""
    assert client.get(url).status_code == 401


@pytest.mark.parametrize("url", [ADMIN_URL, DOCTOR_URL])
def test_invalid_jwt_returns_401(client, url):
    """Token malformado -> 401."""
    response = client.get(url, headers={"Authorization": "Bearer jwt-malformado"})
    assert response.status_code == 401


@pytest.mark.parametrize("url,role", [(ADMIN_URL, "admin_escuela"), (DOCTOR_URL, "doctor")])
def test_revoked_jwt_returns_401(client, url, role):
    """Token revocado -> 401."""
    token = create_access_token({"sub": "1", "role": role, "scope": "client"})
    with patch.object(auth_service, "is_token_revoked", return_value=True):
        response = client.get(url, headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 401


@pytest.mark.parametrize("url,role,scope", [
    (ADMIN_URL, "doctor", "client"),
    (ADMIN_URL, "representante", "client"),
    (ADMIN_URL, "admin_sistema", "admin"),
    (DOCTOR_URL, "admin_escuela", "client"),
    (DOCTOR_URL, "representante", "client"),
    (DOCTOR_URL, "admin_sistema", "admin"),
])
def test_wrong_role_returns_403(client, url, role, scope):
    """Rol no autorizado -> 403 con envelope."""
    app.dependency_overrides[get_current_user_token] = lambda: {
        "sub": "4", "role": role, "scope": scope,
    }
    response = client.get(url)
    assert response.status_code == 403
    assert response.json()["success"] is False


# ──────────────────────────────────────────────────────────
# 9. Validación de parámetros (422)
# ──────────────────────────────────────────────────────────

@pytest.mark.parametrize("params", [
    {"page": 0},        # page empieza en 1
    {"size": 0},        # size mínimo 1
    {"size": 101},      # size máximo 100
    {"q": ""},          # q exige al menos un carácter
    {"category": "x"},  # category es entero
])
def test_invalid_query_params_return_422(admin_client, params):
    """Parámetros fuera de rango o de tipo -> 422 con envelope."""
    fake = build_db([student_row(1, "Juan Pérez")])
    with patch_supabase(fake):
        response = admin_client.get(ADMIN_URL, params=params)

    assert response.status_code == 422
    assert response.json()["success"] is False

def test_audit_records_one_entry_per_searched_school(doctor_client):
    """Sin 'school_id' el médico busca en varias escuelas: una fila de bitácora por cada una.

    Antes se registraba una sola fila con el query param del cliente, que en este
    caso llega vacío: la columna quedaba NULL, que la migración reserva para las
    acciones globales de admin_sistema, y el índice por escuela no servía.
    """
    fake = build_db(
        [student_row(1, "Juan Pérez"),
         student_row(3, "Carla Díaz", school_id=THIRD_SCHOOL_ID)],
        links=[
            {"doctor_id": DOC_ID, "school_id": SCHOOL_ID, "status": ACTIVE_DOCTOR_SCHOOL_STATUS},
            {"doctor_id": DOC_ID, "school_id": THIRD_SCHOOL_ID, "status": ACTIVE_DOCTOR_SCHOOL_STATUS},
        ],
    )
    with patch_supabase(fake), \
            patch("app.api.v1.endpoints.doctor_students.audit_service") as audit:
        response = doctor_client.get(DOCTOR_URL)

    assert response.status_code == 200
    registradas = [c.kwargs["school_id"] for c in audit.record.call_args_list]
    assert sorted(registradas) == [SCHOOL_ID, THIRD_SCHOOL_ID]
    assert all(c.kwargs["school_id"] is not None for c in audit.record.call_args_list)
    # Todas las filas comparten el alcance completo, para poder correlacionarlas.
    for call in audit.record.call_args_list:
        assert call.kwargs["metadata"]["searched_school_ids"] == [SCHOOL_ID, THIRD_SCHOOL_ID]


def test_audit_is_not_recorded_when_scope_is_empty(doctor_client):
    """Médico suspendido: no hay escuela consultada, así que no hay nada que auditar."""
    fake = build_db(
        [student_row(1, "Juan Pérez")],
        links=[{"doctor_id": DOC_ID, "school_id": SCHOOL_ID, "status": SUSPENDED_STATUS}],
    )
    with patch_supabase(fake), \
            patch("app.api.v1.endpoints.doctor_students.audit_service") as audit:
        response = doctor_client.get(DOCTOR_URL)

    assert response.status_code == 200
    audit.record.assert_not_called()


# ──────────────────────────────────────────────────────────
# 10. La vista no se lee con la clave pública
# ──────────────────────────────────────────────────────────

@pytest.mark.parametrize("url,client_fixture", [(ADMIN_URL, "admin_client"), (DOCTOR_URL, "doctor_client")])
def test_search_requires_the_privileged_client(request, url, client_fixture):
    """Sin SUPABASE_SERVICE_KEY la búsqueda falla; nunca cae de vuelta a la clave anónima.

    La migración deniega la vista a `anon` porque cruza datos de salud de todas las
    escuelas. Si el servicio volviera a leerla con el cliente normal, este test
    pasaría a verde en producción y la denegación sería inútil.
    """
    http = request.getfixturevalue(client_fixture)
    fake = build_db([student_row(1, "Juan Pérez")])
    with ExitStack() as stack:
        stack.enter_context(patch("app.services.school_admin_service.supabase", fake))
        stack.enter_context(patch("app.services.doctor_event_service.supabase", fake))
        stack.enter_context(patch("app.services.student_search_service.supabase_admin", None))
        stack.enter_context(patch("app.services.audit_service.supabase_admin", None))
        response = http.get(url)

    assert response.status_code == 500
    body = response.json()
    assert body["success"] is False
    assert "no está disponible" in body["message"]

def test_service_escapes_the_pattern_before_sending_it(admin_client):
    """El escape ocurre en el servicio, no en el doble: se captura lo que se envía.

    El test anterior pasaría igual si el servicio no escapara y el doble tampoco
    interpretara comodines. Este fija el contrato en el punto donde sale la consulta.
    """
    fake = build_db([student_row(1, "Juan Pérez")])
    enviado = []
    original = FakeQuery.ilike

    def spy(self, column, pattern):
        enviado.append((column, pattern))
        return original(self, column, pattern)

    with patch_supabase(fake), patch.object(FakeQuery, "ilike", spy):
        admin_client.get(ADMIN_URL, params={"q": "50%"})

    assert enviado == [("full_name", "%50\\%%")]


@pytest.mark.parametrize("raw,esperado", [
    ("juan", "ilike.%juan%"),
    ("50%", "ilike.%50\\%%"),
    ("a_b", "ilike.%a\\_b%"),
])
def test_escaped_pattern_survives_postgrest_serialization(raw, esperado):
    """La barra de escape sobrevive hasta el filtro que viaja en la URL.

    Se ejerce la librería real (`postgrest`), no el doble, que es lo más lejos que
    se puede verificar sin una instancia de Supabase. Lo que queda fuera de alcance
    y exige comprobación de integración es si PostgREST honra `\\%` como literal.
    """
    from postgrest import SyncPostgrestClient

    client = SyncPostgrestClient("http://localhost:3000", schema="public", headers={})
    query = (
        client.from_("student_health_status")
        .select("student_id")
        .ilike("full_name", f"%{_escape_like(raw)}%")
    )
    assert dict(query.request.params)["full_name"] == esperado
