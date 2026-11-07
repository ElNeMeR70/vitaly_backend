"""Regresión GH-61: ejecutar el servicio sobre filas numéricas, sin red."""

from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.api.deps import get_current_admin_escuela
from app.main import app
from app.services.school_admin_event_service import school_admin_event_service
from tests.fake_supabase import FakeSupabase

SERVICE_MODULE = "app.services.school_admin_event_service"
CATEGORIES = [
    {"id": 1, "classroom_type_name": "Preescolar"},
    {"id": 2, "classroom_type_name": "Primaria"},
    {"id": 3, "classroom_type_name": "Bachillerato"},
]


@pytest.fixture
def detail_db():
    """Filtra filas reales en memoria y registra consultas y ejecuciones."""
    db = FakeSupabase(
        school_administrator=[{"user_id": 4, "sch_admin_id": 10, "school_id": 1}],
        event=[{
            "id": 5, "title": "Revisión", "init_time": "2026-12-01T12:00:00Z",
            "ent_time": "2026-12-01T14:00:00Z", "is_deleted": False,
            "is_canceled": False,
        }],
        event_participation=[{
            "id_event": 5, "id_school_admin": 10, "is_deleted": False,
        }],
        classroom=[], classroom_category=CATEGORIES,
        user=[],
    )
    queries = {}

    def table(name):
        assert name in db.db, f"Consulta inesperada: {name}"
        query = db.table(name)
        query.execute = MagicMock(wraps=query.execute)
        query.in_ = MagicMock(wraps=query.in_)
        queries.setdefault(name, []).append(query)
        return query

    def run(rows, *, through_client=None):
        db.db["classroom"] = rows
        db.db["event_participation"] += [
            {"id_event": 5, "id_classroom": row["id"], "is_deleted": False}
            for row in rows
        ]
        with patch(f"{SERVICE_MODULE}.supabase") as client:
            client.table.side_effect = table
            if through_client:
                return through_client.get("/api/v1/school-admin/events/5")
            return school_admin_event_service.get_event_detail(user_id=4, event_id=5)

    return db, queries, run


def classroom(category=2, level=1, section="A", *, row_id=11, school_id=1):
    return {"id": row_id, "category": category, "level": level,
            "section": section, "school_id": school_id}


@pytest.mark.parametrize("category,level,expected", [
    (2, 1, "Primer grado primaria"), (2, 2, "Segundo grado primaria"),
    (2, 3, "Tercer grado primaria"), (2, 4, "Cuarto grado primaria"),
    (2, 5, "Quinto grado primaria"), (2, 6, "Sexto grado primaria"),
    (1, 1, "Primer nivel preescolar"), (1, 2, "Segundo nivel preescolar"),
    (1, 3, "Tercer nivel preescolar"),
    (3, 1, "Primer año bachillerato"), (3, 2, "Segundo año bachillerato"),
    (3, 3, "Tercer año bachillerato"), (3, 4, "Cuarto año bachillerato"),
    (3, 5, "Quinto año bachillerato"),
])
def test_detail_formats_numeric_classrooms(detail_db, category, level, expected):
    _, _, run = detail_db
    assert run([classroom(category, level)])["classrooms"] == [
        {"id": 11, "label": f"{expected} · Sección A"},
    ]


@pytest.mark.parametrize("section", [None, "", "   "])
def test_detail_omits_missing_section(detail_db, section):
    _, _, run = detail_db
    assert run([classroom(section=section)])["classrooms"] == [
        {"id": 11, "label": "Primer grado primaria"},
    ]


@pytest.mark.parametrize("category,name", [(999, None), (2, None), (2, "  "), (None, None)])
def test_detail_unresolved_category_has_no_id(detail_db, category, name):
    db, _, run = detail_db
    db.db["classroom_category"] = [{"id": 2, "classroom_type_name": name}]
    assert run([classroom(category=category)])["classrooms"] == [
        {"id": 11, "label": "Primer nivel · Categoría no disponible · Sección A"},
    ]


def test_detail_uses_catalog_names_and_batches_queries(detail_db):
    db, queries, run = detail_db
    # Los nombres provienen del catálogo, no de un mapa rígido por ID.
    db.db["classroom_category"].append({"id": 7, "classroom_type_name": "Primaria"})
    rows = [classroom(category=7, row_id=i, section=str(i)) for i in range(20, 40)]
    rows += [classroom(1, 2, "A", row_id=40), classroom(3, 5, "B", row_id=41)]
    labels = run(rows)["classrooms"]
    assert labels[0] == {"id": 20, "label": "Primer grado primaria · Sección 20"}
    assert len({c["label"] for c in labels}) == len(rows)
    for name in ("classroom", "classroom_category"):
        assert len(queries[name]) == 1
        queries[name][0].execute.assert_called_once_with()
    queries["classroom_category"][0].in_.assert_called_once_with("id", [1, 3, 7])


def test_detail_endpoint_preserves_labels_and_contract(detail_db):
    _, _, run = detail_db
    app.dependency_overrides[get_current_admin_escuela] = lambda: {"sub": "4"}
    try:
        with TestClient(app) as client:
            response = run([
                classroom(section="A"),
                classroom(section="B", row_id=12),
                classroom(level=2, section="B", row_id=13),
            ], through_client=client)
    finally:
        app.dependency_overrides.clear()
    assert response.status_code == 200
    assert response.json()["success"] is True
    assert response.json()["data"]["classrooms"] == [
        {"id": 11, "label": "Primer grado primaria · Sección A"},
        {"id": 12, "label": "Primer grado primaria · Sección B"},
        {"id": 13, "label": "Segundo grado primaria · Sección B"},
    ]


@pytest.mark.parametrize("rows", [[], [classroom(school_id=2)]])
def test_detail_empty_or_foreign_classrooms_skip_catalog(detail_db, rows):
    _, queries, run = detail_db
    assert run(rows)["classrooms"] == []
    assert "classroom_category" not in queries


@pytest.mark.parametrize("table", ["classroom", "classroom_category"])
def test_detail_query_failure_becomes_http_500(detail_db, table):
    db, _, run = detail_db
    db.failing_table = table
    with patch(f"{SERVICE_MODULE}.logger") as logger, pytest.raises(HTTPException) as exc:
        run([classroom()])
    logger.exception.assert_called_once_with("Error al consultar los salones o categorías del evento")
    assert exc.value.status_code == 500
    assert exc.value.detail == "No se pudieron obtener los salones del evento."
