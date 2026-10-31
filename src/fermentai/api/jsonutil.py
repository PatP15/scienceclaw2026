"""JSON safety helpers: NaN/Infinity -> null, numpy/pandas/dataclasses -> plain JSON."""

from __future__ import annotations

import dataclasses
import json
import math
from typing import Any

from fastapi.responses import JSONResponse


def jsonable(obj: Any) -> Any:
    """Best-effort conversion into JSON-safe structures (non-finite floats become None)."""
    if obj is None or isinstance(obj, (bool, int, str)):
        return obj
    if isinstance(obj, float):
        return obj if math.isfinite(obj) else None
    if isinstance(obj, dict):
        return {str(k): jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple, set)):
        return [jsonable(v) for v in obj]
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        return jsonable(dataclasses.asdict(obj))
    if hasattr(obj, "model_dump"):
        return jsonable(obj.model_dump(mode="json"))
    if hasattr(obj, "to_dict") and hasattr(obj, "columns"):  # pandas DataFrame
        return {"columns": [str(c) for c in obj.columns],
                "data": jsonable(obj.to_dict(orient="list"))}
    if hasattr(obj, "tolist"):  # numpy
        return jsonable(obj.tolist())
    if hasattr(obj, "isoformat"):
        return obj.isoformat()
    if hasattr(obj, "to_dict"):
        return jsonable(obj.to_dict())
    return str(obj)


def load_json_file(path) -> Any:
    """json.load that tolerates NaN/Infinity literals (Python writes them) and maps them to None."""
    with open(path, encoding="utf-8") as fh:
        return jsonable(json.load(fh))


class SafeJSONResponse(JSONResponse):
    """Default response class: never 500s on NaN/Infinity, renders them as null."""

    def render(self, content: Any) -> bytes:
        try:
            return json.dumps(content, ensure_ascii=False, allow_nan=False,
                              separators=(",", ":")).encode("utf-8")
        except (ValueError, TypeError):
            return json.dumps(jsonable(content), ensure_ascii=False, allow_nan=False,
                              separators=(",", ":")).encode("utf-8")
