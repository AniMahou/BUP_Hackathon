import json

import pytest

from app.api.errors import BadRequestError, UnprocessableError
from app.api.request_parser import parse_request


def _valid_payload() -> dict:
    return {
        "scenario_id": "s1",
        "operator_notes": ["Solar output will drop to about 20% from 1 PM to 3 PM."],
        "hours": [
            {"hour": h, "demand_kwh": 100.0, "solar_kwh": 20.0, "tariff_bdt_per_kwh": 10.0} for h in range(24)
        ],
        "battery": {
            "capacity_kwh": 200.0, "initial_energy_kwh": 100.0, "minimum_energy_kwh": 20.0,
            "max_charge_kwh_per_hour": 50.0, "max_discharge_kwh_per_hour": 50.0,
        },
    }


def test_valid_request_parses():
    req = parse_request(json.dumps(_valid_payload()).encode())
    assert req.scenario_id == "s1"


def test_empty_body_is_400():
    with pytest.raises(BadRequestError):
        parse_request(b"")


def test_invalid_json_is_400():
    with pytest.raises(BadRequestError):
        parse_request(b"{not json")


def test_non_object_body_is_400():
    with pytest.raises(BadRequestError):
        parse_request(b"[1,2,3]")


def test_nan_is_rejected_as_400():
    with pytest.raises(BadRequestError):
        parse_request(b'{"scenario_id":"s","operator_notes":["x"],"hours":[],"battery":{},"x":NaN}')


def test_wrong_type_is_400():
    payload = _valid_payload()
    payload["hours"][0]["demand_kwh"] = "90"  # numeric string, should be rejected under strict mode
    with pytest.raises(BadRequestError):
        parse_request(json.dumps(payload).encode())


def test_bool_for_number_is_400():
    payload = _valid_payload()
    payload["hours"][0]["demand_kwh"] = True
    with pytest.raises(BadRequestError):
        parse_request(json.dumps(payload).encode())


def test_wrong_hour_count_is_400():
    payload = _valid_payload()
    payload["hours"] = payload["hours"][:23]
    with pytest.raises(BadRequestError):
        parse_request(json.dumps(payload).encode())


def test_too_many_notes_is_400():
    payload = _valid_payload()
    payload["operator_notes"] = ["a", "b", "c", "d"]
    with pytest.raises(BadRequestError):
        parse_request(json.dumps(payload).encode())


def test_negative_demand_is_422():
    payload = _valid_payload()
    payload["hours"][0]["demand_kwh"] = -5.0
    with pytest.raises(UnprocessableError):
        parse_request(json.dumps(payload).encode())


def test_initial_outside_bounds_is_422():
    payload = _valid_payload()
    payload["battery"]["initial_energy_kwh"] = 500.0
    with pytest.raises(UnprocessableError):
        parse_request(json.dumps(payload).encode())


def test_minimum_above_capacity_is_422():
    payload = _valid_payload()
    payload["battery"]["minimum_energy_kwh"] = 999.0
    with pytest.raises(UnprocessableError):
        parse_request(json.dumps(payload).encode())
