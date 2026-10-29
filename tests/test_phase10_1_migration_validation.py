"""Phase 10.1 migration validation unit tests."""
from datetime import timezone

import pytest

from app.commands.migrate_sqlite_to_postgres import _parse_bool, _parse_datetime, _validate_uuid_string
from app.database.types import MalformedJSONValue, normalize_json_value

pytestmark = pytest.mark.migration_integration


def test_legacy_json_normalization():
    assert normalize_json_value('{"nested":[1,true,null]}') == {"nested": [1, True, None]}
    assert normalize_json_value('[1,2]') == [1, 2]
    assert normalize_json_value({"native": True}) == {"native": True}


def test_malformed_json_is_not_silently_swallowed():
    with pytest.raises(MalformedJSONValue, match="Malformed persisted JSON"):
        normalize_json_value("{broken", location="questions/q1/options_json")


@pytest.mark.parametrize(("source", "expected"), [(0, False), (1, True), (False, False), (True, True)])
def test_boolean_conversion(source, expected):
    assert _parse_bool(source) is expected


def test_invalid_boolean_rejected():
    with pytest.raises(ValueError, match="0 or 1"):
        _parse_bool(2)


def test_uuid_validation_and_preservation():
    value = "00000000-0000-0000-0000-000000000007"
    assert _validate_uuid_string(value) == value
    with pytest.raises(ValueError):
        _validate_uuid_string("not-a-uuid")


def test_datetime_conversion_is_aware_utc():
    value = _parse_datetime("2026-08-10T12:00:00")
    assert value.tzinfo == timezone.utc


def test_invalid_datetime_rejected():
    with pytest.raises(ValueError):
        _parse_datetime("not-a-datetime")
