from __future__ import annotations

import json
from pathlib import Path

from jsonschema import Draft202012Validator

_ROOT = Path(__file__).resolve().parent
_SCHEMAS = _ROOT / "schemas"
_FIXTURES = _ROOT / "fixtures"

_CACHE: dict[str, Draft202012Validator] = {}


def _validator(name: str) -> Draft202012Validator:
    if name not in _CACHE:
        path = _SCHEMAS / f"{name}.json"
        schema = json.loads(path.read_text())
        _CACHE[name] = Draft202012Validator(schema)
    return _CACHE[name]


def validate_instance(name: str, data: object) -> None:
    _validator(name).validate(data)


def load_fixture(kind: str, filename: str) -> dict:
    return json.loads((_FIXTURES / kind / filename).read_text())
