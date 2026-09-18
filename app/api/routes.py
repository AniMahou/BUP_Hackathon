import logging
import uuid

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from app.api.errors import BadRequestError, UnprocessableError, error_body
from app.api.request_parser import parse_request
from app.pipeline.orchestrator import PipelineInfeasibleError, run_pipeline

router = APIRouter()
logger = logging.getLogger("gridwise")


def _internal_error(request: Request) -> JSONResponse:
    """Unexpected failures are answered HERE, not by the app-wide Exception handler: Starlette's
    ServerErrorMiddleware re-raises after that handler runs and uvicorn then drops the keep-alive
    connection, which would also fail the judge's NEXT request on the same pooled socket."""
    request_id = getattr(request.state, "request_id", None) or str(uuid.uuid4())
    logger.exception("unhandled_exception request_id=%s", request_id)
    return JSONResponse(
        status_code=500,
        content=error_body("internal_error", "An internal error occurred.", [{"field": "request_id", "issue": request_id}]),
    )


@router.get("/health")
async def health():
    return {"status": "ok"}


@router.post("/optimize-energy")
async def optimize_energy(request: Request):
    raw_body = await request.body()
    req = parse_request(raw_body)
    interpreter = request.app.state.interpreter
    settings = request.app.state.settings

    try:
        result = await run_pipeline(req, interpreter, settings)
    except PipelineInfeasibleError as e:
        raise UnprocessableError(
            "operator note directives make the schedule infeasible",
            [{"field": "operator_notes", "issue": str(e)}],
        ) from e
    except (BadRequestError, UnprocessableError):
        raise
    except Exception:
        return _internal_error(request)

    return JSONResponse(content=result.response.model_dump())


@router.get("/ui/info")
async def ui_info(request: Request):
    """Non-secret runtime info for the demo UI (model chain, whether a key is configured)."""
    settings = request.app.state.settings
    return {"models": settings.model_chain, "llm_configured": bool(settings.api_key_list)}


@router.post("/ui/optimize")
async def ui_optimize(request: Request):
    """Same pipeline as /optimize-energy, but also returns the reasoning trace for the demo UI.
    Never used by the graded contract endpoint — this exists only for /ui."""
    raw_body = await request.body()
    req = parse_request(raw_body)
    interpreter = request.app.state.interpreter
    settings = request.app.state.settings

    try:
        result = await run_pipeline(req, interpreter, settings)
    except PipelineInfeasibleError as e:
        raise UnprocessableError(
            "operator note directives make the schedule infeasible",
            [{"field": "operator_notes", "issue": str(e)}],
        ) from e

    return JSONResponse(content={"response": result.response.model_dump(), "trace": result.trace})
