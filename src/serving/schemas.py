"""Request validation generated from `reports/feature_schema.json` (the contract with the model).

Pure Python on purpose: small, explicit error messages that never echo a submitted value back in
logs, and no dependency beyond the standard library. `inference.records_to_frame` re-checks the
fields afterwards as a last line of defence.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from typing import Any


class RequestError(ValueError):
    """The request body is not a usable payload (maps to HTTP 400)."""


def _is_number(value: Any) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool)


def validate_record(record: Any, inputs: Sequence[Mapping[str, Any]]) -> list[dict[str, str]]:
    """Return a list of {field, code, message}; an empty list means the record is valid."""
    if not isinstance(record, Mapping):
        return [{"field": "", "code": "type", "message": "each record must be a JSON object"}]
    errors: list[dict[str, str]] = []
    for spec in inputs:
        name = spec["name"]
        if name not in record or record[name] is None:
            errors.append({"field": name, "code": "missing", "message": "field is required"})
            continue
        value = record[name]
        if not _is_number(value):
            errors.append({"field": name, "code": "type", "message": "must be a number"})
            continue
        if not math.isfinite(value):
            errors.append({"field": name, "code": "type", "message": "must be a finite number"})
            continue
        if spec.get("dtype") == "integer" and float(value) != int(value):
            errors.append({"field": name, "code": "type", "message": "must be a whole number"})
            continue
        if "allowed" in spec and value not in spec["allowed"]:
            allowed = ", ".join(str(a) for a in spec["allowed"])
            errors.append({"field": name, "code": "range", "message": f"must be one of: {allowed}"})
            continue
        low, high = spec.get("min"), spec.get("max")
        if (low is not None and value < low) or (high is not None and value > high):
            errors.append(
                {"field": name, "code": "range", "message": f"must be between {low} and {high}"}
            )
    return errors


def ignored_fields(record: Mapping[str, Any], inputs: Sequence[Mapping[str, Any]]) -> list[str]:
    """Keys the model does not use (e.g. SEX). Names only: values are never kept or logged."""
    known = {spec["name"] for spec in inputs}
    return sorted(str(key) for key in record if key not in known)


def parse_records(body: Any, max_batch: int) -> list[Mapping[str, Any]]:
    """One record (`{...fields...}`) or a batch (`{"records": [...]}`) -> list of records."""
    if not isinstance(body, Mapping):
        raise RequestError("the body must be a JSON object")
    if "records" in body:
        records = body["records"]
        if not isinstance(records, list) or not records:
            raise RequestError('"records" must be a non-empty list')
        if len(records) > max_batch:
            raise RequestError(f'"records" has {len(records)} items; the maximum is {max_batch}')
        return records
    if not body:
        raise RequestError("the body is empty")
    return [body]
