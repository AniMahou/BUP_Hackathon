"""Raw-body request parsing (§6.2): read raw bytes so FastAPI's default validation never fires,
reject NaN/Infinity, strict-parse into OptimizeRequest, and split structural failures (400) from
semantic/business-rule failures (422, raised as SemanticValidationError from the model itself).
"""

import json

from pydantic import ValidationError

from app.api.errors import BadRequestError, UnprocessableError
from app.schemas.request import OptimizeRequest, SemanticValidationError

MAX_BODY_BYTES = 256 * 1024


def _reject_constant(name: str):
    raise ValueError(f"invalid JSON constant: {name}")


def parse_request(raw_body: bytes) -> OptimizeRequest:
    if len(raw_body) > MAX_BODY_BYTES:
        raise BadRequestError("request body exceeds the 256 KB limit")
    if not raw_body.strip():
        raise BadRequestError("empty request body")

    try:
        data = json.loads(raw_body, parse_constant=_reject_constant)
    except (json.JSONDecodeError, ValueError) as e:
        raise BadRequestError(f"invalid JSON: {e}") from e

    if not isinstance(data, dict):
        raise BadRequestError("request body must be a JSON object")

    try:
        return OptimizeRequest.model_validate(data)
    except ValidationError as e:
        errors = e.errors()
        for err in errors:
            inner = (err.get("ctx") or {}).get("error")
            if isinstance(inner, SemanticValidationError):
                raise UnprocessableError("request failed semantic validation", inner.errors) from e

        details = [{"field": ".".join(str(p) for p in err["loc"]), "issue": err["msg"]} for err in errors]
        raise BadRequestError("request failed structural validation", details) from e
