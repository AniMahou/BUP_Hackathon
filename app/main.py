import asyncio
import logging
import os
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from app.api.errors import BadRequestError, UnprocessableError, error_body
from app.api.routes import router
from app.config import settings
from app.llm.cache import InterpretationCache
from app.llm.gemini_client import GeminiClient
from app.llm.interpreter import NoteInterpreter
from app.logging_setup import setup_logging
from app.pipeline.deadline import Deadline

logger = logging.getLogger("gridwise")


async def _warm_up(interpreter: NoteInterpreter) -> None:
    if not settings.gemini_api_key:
        logger.warning("GEMINI_API_KEY not set; /optimize-energy will run in degraded mode")
        return
    try:
        from app.schemas.request import BatteryInput, HourInput, OptimizeRequest

        dummy = OptimizeRequest(
            scenario_id="warmup",
            operator_notes=["Solar output will drop to about 20% from 1 PM to 3 PM."],
            hours=[HourInput(hour=h, demand_kwh=100.0, solar_kwh=50.0, tariff_bdt_per_kwh=10.0) for h in range(24)],
            battery=BatteryInput(
                capacity_kwh=200, initial_energy_kwh=100, minimum_energy_kwh=20,
                max_charge_kwh_per_hour=50, max_discharge_kwh_per_hour=50,
            ),
        )
        await interpreter.interpret_request(dummy, Deadline(20.0))
        logger.info("LLM warm-up call succeeded")
    except Exception:
        logger.warning("LLM warm-up call failed (non-fatal; first real request will retry)")


@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_logging(settings.log_level)

    client = GeminiClient(settings.gemini_api_key)
    cache = InterpretationCache(settings.interpretation_cache_size)
    interpreter = NoteInterpreter(
        client=client,
        model_chain=settings.model_chain,
        primary_timeout_s=settings.llm_timeout_s,
        fallback_timeout_s=settings.llm_fallback_timeout_s,
        max_repairs=settings.llm_max_repairs,
        thinking_budget=settings.llm_thinking_budget,
        cache=cache,
        prompt_version=settings.prompt_version,
        enable_hedging=settings.enable_hedging,
        enable_rule_fallback=settings.enable_rule_fallback,
    )

    app.state.interpreter = interpreter
    app.state.settings = settings

    asyncio.create_task(_warm_up(interpreter))
    yield


def create_app() -> FastAPI:
    app = FastAPI(title="GridWise LLM", lifespan=lifespan)

    @app.middleware("http")
    async def add_request_id(request: Request, call_next):
        request.state.request_id = str(uuid.uuid4())
        return await call_next(request)

    @app.exception_handler(BadRequestError)
    async def _bad_request(request: Request, exc: BadRequestError):
        return JSONResponse(status_code=400, content=error_body("bad_request", exc.message, exc.details))

    @app.exception_handler(UnprocessableError)
    async def _unprocessable(request: Request, exc: UnprocessableError):
        return JSONResponse(status_code=422, content=error_body("unprocessable_entity", exc.message, exc.details))

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception):
        request_id = getattr(request.state, "request_id", "unknown")
        logger.exception("unhandled_exception request_id=%s", request_id)
        return JSONResponse(
            status_code=500,
            content=error_body("internal_error", "An internal error occurred.", [{"field": "request_id", "issue": request_id}]),
        )

    app.include_router(router)

    static_dir = os.path.join(os.path.dirname(__file__), "static")
    if os.path.isdir(static_dir):
        app.mount("/ui", StaticFiles(directory=static_dir, html=True), name="ui")

    return app


app = create_app()
