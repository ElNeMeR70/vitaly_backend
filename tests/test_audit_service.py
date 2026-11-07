"""
Tests de la bitácora de auditoría (`app/services/audit_service.py`, issue #66).

La tabla `audit_log` nace en esta misma rama, así que su único camino de escritura
no tenía ninguna prueba: `tests/test_student_search.py` fuerza
`supabase_admin = None` en todos sus casos, de modo que el `insert` nunca llegaba a
ejecutarse y un nombre de columna equivocado habría pasado inadvertido.

El doble captura la fila insertada y los nombres de columna se contrastan contra el
DDL real de la migración, no contra una lista escrita a mano.
"""
import re
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from fastapi import HTTPException

from app.services.audit_service import audit_service

MIGRATION = Path("supabase/migrations/20261107120100_create_audit_log_table.sql")

ACTOR = {"sub": "40", "role": "doctor", "scope": "client"}


class RecordingTable:
    """Captura la fila insertada sin tocar la red."""

    def __init__(self, store, fail=False):
        self._store = store
        self._fail = fail

    def insert(self, row):
        self._store.append(row)
        return self

    def execute(self):
        if self._fail:
            raise RuntimeError("postgrest unavailable")
        return SimpleNamespace(data=self._store[-1:])


class RecordingClient:
    def __init__(self, fail=False):
        self.rows = []
        self.tables = []
        self._fail = fail

    def table(self, name):
        self.tables.append(name)
        return RecordingTable(self.rows, fail=self._fail)


def columns_declared_in_migration() -> set[str]:
    """Extrae los nombres de columna del CREATE TABLE de la migración."""
    sql = MIGRATION.read_text()
    body = sql[sql.index("CREATE TABLE"):sql.index("CONSTRAINT")]
    names = set()
    for line in body.splitlines()[1:]:
        match = re.match(r"\s{4}([a-z_]+)\s+\S", line)
        if match:
            names.add(match.group(1))
    return names


# ──────────────────────────────────────────────────────────
# Camino de escritura
# ──────────────────────────────────────────────────────────

def test_record_writes_one_row_to_audit_log():
    """La escritura llega a 'audit_log' con el actor, la acción y el alcance."""
    client = RecordingClient()
    with patch("app.services.audit_service.supabase_admin", client):
        audit_service.record(
            actor=ACTOR, action="read", entity="student_health_status",
            school_id=1, metadata={"result_count": 3},
        )

    assert client.tables == ["audit_log"]
    assert len(client.rows) == 1
    row = client.rows[0]
    assert row["actor_id"] == 40 and isinstance(row["actor_id"], int)
    assert row["actor_role"] == "doctor"
    assert row["school_id"] == 1
    assert row["action"] == "read"
    assert row["entity"] == "student_health_status"
    assert row["metadata"] == {"result_count": 3}
    assert row["changes"] == {}


def test_inserted_columns_exist_in_the_migration():
    """Toda clave insertada corresponde a una columna real del DDL.

    Es la prueba que faltaba: sin ella, un nombre de columna equivocado solo se
    descubriría al aplicar la migración en un entorno con datos.
    """
    client = RecordingClient()
    with patch("app.services.audit_service.supabase_admin", client):
        audit_service.record(
            actor=ACTOR, action="read", entity="student", entity_id=7,
            school_id=1, changes={"field": "x"}, metadata={"k": "v"},
        )

    declared = columns_declared_in_migration()
    assert declared, "no se pudieron leer las columnas de la migración"
    assert set(client.rows[0]) <= declared, set(client.rows[0]) - declared


def test_entity_id_is_stringified_and_optional():
    """'entity_id' es text en el DDL; None se conserva para acciones sobre un conjunto."""
    client = RecordingClient()
    with patch("app.services.audit_service.supabase_admin", client):
        audit_service.record(actor=ACTOR, action="read", entity="student", entity_id=7)
        audit_service.record(actor=ACTOR, action="read", entity="student_health_status")

    assert client.rows[0]["entity_id"] == "7"
    assert client.rows[1]["entity_id"] is None


@pytest.mark.parametrize("action", ["read", "create", "update", "delete", "approve", "reject", "export"])
def test_actions_match_the_check_constraint(action):
    """Las acciones usadas por el servicio son las que admite el CHECK de la tabla."""
    allowed = re.search(r"action IN \(([^)]+)\)", MIGRATION.read_text()).group(1)
    assert f"'{action}'" in allowed


# ──────────────────────────────────────────────────────────
# Degradación
# ──────────────────────────────────────────────────────────

def test_missing_service_key_does_not_break_a_non_critical_action():
    """Sin SUPABASE_SERVICE_KEY la acción no se audita, pero tampoco falla."""
    with patch("app.services.audit_service.supabase_admin", None):
        audit_service.record(actor=ACTOR, action="read", entity="student")


def test_missing_service_key_raises_when_critical():
    """Si la auditoría es crítica, su ausencia aborta la operación con 500."""
    with patch("app.services.audit_service.supabase_admin", None):
        with pytest.raises(HTTPException) as exc:
            audit_service.record(actor=ACTOR, action="delete", entity="student", critical=True)
    assert exc.value.status_code == 500


def test_write_failure_is_swallowed_when_not_critical():
    """Una bitácora caída no tumba una lectura."""
    client = RecordingClient(fail=True)
    with patch("app.services.audit_service.supabase_admin", client):
        audit_service.record(actor=ACTOR, action="read", entity="student")


def test_write_failure_raises_http_exception_when_critical():
    """Un fallo crítico sale como HTTPException, no como excepción desnuda.

    'app/main.py' no registra handler genérico: una excepción cruda saldría como
    500 en texto plano, sin el envelope APIResponse.
    """
    client = RecordingClient(fail=True)
    with patch("app.services.audit_service.supabase_admin", client):
        with pytest.raises(HTTPException) as exc:
            audit_service.record(actor=ACTOR, action="delete", entity="student", critical=True)
    assert exc.value.status_code == 500
