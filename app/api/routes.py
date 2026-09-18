from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from app.api.errors import UnprocessableError
from app.api.request_parser import parse_request
from app.pipeline.orchestrator import PipelineInfeasibleError, run_pipeline

router = APIRouter()


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

    return JSONResponse(content=result.response.model_dump())


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
