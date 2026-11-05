"""JSON representation shared by grading evidence writers and report reconciliation."""

import math
from collections.abc import Mapping
from typing import Any


def jsonable(value: Any) -> Any:
    """JSON without NaN or infinities: a non-finite float becomes the string 'inf' etc."""
    if isinstance(value, float) and not math.isfinite(value):
        return "nan" if math.isnan(value) else ("inf" if value > 0 else "-inf")
    if isinstance(value, Mapping):
        return {str(k): jsonable(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [jsonable(v) for v in value]
    return value
