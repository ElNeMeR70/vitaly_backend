"""Regresiones de filtros SQL para médicos eliminados y convocatorias."""

from unittest.mock import MagicMock, call, patch

import pytest
from fastapi import HTTPException

from app.services.school_admin_event_service import school_admin_event_service
from app.services.school_admin_service import school_admin_service


def query(rows=(), count=None):
    result = MagicMock()
    for method in ("select", "eq", "in_", "order", "range"):
        getattr(result, method).return_value = result
    result.execute.return_value = MagicMock(data=list(rows), count=count)
    return result


def test_doctor_count_and_page_filter_deleted_users_before_pagination():
    count_query = query(count=3)
    page_query = query([{"doctor_id": 8, "status": 2}])
    tables = {
        "doctor": query([{"doc_id": 8, "user_id": 40}]),
        "user": query([{"id": 40, "name": "Ana", "lastname": "Pérez"}]),
        "system_status": query([{"status_id": 2, "status_name": "Activo"}]),
    }
    relations = iter([count_query, page_query])
    with patch.object(school_admin_service, "_get_school_id_for_admin", return_value=17) as tenant, \
         patch("app.services.school_admin_service.supabase") as database:
        database.table.side_effect = lambda name: next(relations) if name == "doctor_to_school" else tables[name]
        result = school_admin_service.get_doctors(user_id=41, page=2, size=2)

    tenant.assert_called_once_with(41)
    assert result["total"] == 3
    assert [doctor["id"] for doctor in result["data"]] == [8]
    for statement in (count_query, page_query):
        statement.eq.assert_any_call("school_id", 17)
        statement.eq.assert_any_call("doctor.user.is_deleted", False)
        assert "doctor!inner(user!inner(id))" in statement.select.call_args.args[0]
    assert count_query.eq.call_args_list == page_query.eq.call_args_list
    count_query.select.assert_called_once_with("id, doctor!inner(user!inner(id))", count="exact")
    count_query.range.assert_not_called()
    calls = page_query.method_calls
    assert calls.index(call.eq("doctor.user.is_deleted", False)) < calls.index(call.order("id"))
    assert calls.index(call.order("id")) < calls.index(call.range(2, 3))


def test_school_with_only_deleted_doctors_has_empty_collection():
    count_query = query(count=0)
    with patch.object(school_admin_service, "_get_school_id_for_admin", return_value=17), \
         patch("app.services.school_admin_service.supabase") as database:
        database.table.return_value = count_query
        result = school_admin_service.get_doctors(user_id=41, page=1, size=20)
    assert result == {"data": [], "total": 0, "page": 1, "size": 20}
    count_query.eq.assert_any_call("doctor.user.is_deleted", False)
    database.table.assert_called_once_with("doctor_to_school")


@pytest.mark.parametrize("linked, expected", [(True, 404), (False, 403)])
def test_deleted_doctor_detail_preserves_school_ownership(linked, expected):
    relation = query([{"status": 2}] if linked else [])
    doctor = query([{"doc_id": 8, "user": {"id": 40, "is_deleted": True}}])
    with patch.object(school_admin_service, "_get_school_id_for_admin", return_value=17), \
         patch("app.services.school_admin_service.supabase") as database:
        database.table.side_effect = lambda name: {"doctor_to_school": relation, "doctor": doctor}[name]
        with pytest.raises(HTTPException) as error:
            school_admin_service.get_doctor_detail(user_id=41, doctor_id=8)
    assert error.value.status_code == expected
    relation.eq.assert_any_call("school_id", 17)
    if not linked:
        doctor.execute.assert_not_called()


@pytest.mark.parametrize("rows, expected", [([{"doctor_id": 8}], [8]), ([], [])])
def test_invitation_candidates_require_school_and_live_user(rows, expected):
    statement = query(rows)
    with patch("app.services.school_admin_event_service.supabase") as database:
        database.table.return_value = statement
        assert school_admin_event_service._get_school_doctor_ids(17) == expected
    statement.select.assert_called_once_with("doctor_id, doctor!inner(user!inner(id))")
    assert statement.eq.call_args_list == [
        call("school_id", 17), call("doctor.user.is_deleted", False), call("doctor.user.is_active", True),
    ]
