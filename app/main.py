from __future__ import annotations

from collections.abc import Callable
from contextlib import asynccontextmanager

from pathlib import Path

from fastapi import Body, FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from typing import Annotated

from app.config import Settings
from app.db import Database
from app.llm import LLMError, classify_with_llm, operator_fallback, redact_error
from app.rate_limit import RateLimiter
from app.schemas import (
    OPERATOR_REPLY,
    TRIAGE_EXAMPLE,
    ClientRecord,
    TicketRecord,
    TriageIn,
    TriageOut,
)

ClassifyFn = Callable[[TriageIn], TriageOut]


def create_app(
    settings: Settings | None = None,
    classify_fn: ClassifyFn | None = None,
) -> FastAPI:
    settings = settings or Settings()
    db = Database(settings.sqlite_path, seed_demo=settings.seed_demo)
    limiter = RateLimiter(settings.rate_limit_per_minute)
    classify = classify_fn or (lambda payload: classify_with_llm(settings, payload))
    require_api_key = classify_fn is None

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        if require_api_key and not settings.resolved_api_key():
            raise RuntimeError("Set OPENAI_API_KEY or PROXY_API_KEY (ProxyAPI key)")
        yield
        db.close()

    app = FastAPI(title="Support Triage", version="1.0.0", lifespan=lifespan)
    app.state.settings = settings
    app.state.db = db
    app.state.limiter = limiter

    @app.get("/", response_class=HTMLResponse)
    def root():
        page = Path(__file__).with_name("demo.html").read_text(encoding="utf-8")
        return HTMLResponse(page)

    @app.get("/health")
    def health():
        return {"status": "ok"}

    @app.post("/triage", response_model=TriageOut)
    def triage(
        payload: Annotated[
            TriageIn,
            Body(
                openapi_examples={
                    "billing": {
                        "summary": "Пример: оплата",
                        "value": TRIAGE_EXAMPLE,
                    }
                }
            ),
        ],
    ) -> TriageOut:
        payload = db.enrich_payload(payload)
        if not limiter.allow(payload.client_id):
            limited = operator_fallback()
            db.insert_ticket(payload=payload, result=limited, error="rate_limited")
            raise HTTPException(
                status_code=429,
                detail="Too many requests for this client_id",
            )

        error: str | None = None
        try:
            result = classify(payload)
        except LLMError as exc:
            error = redact_error(str(exc), settings.resolved_api_key())
            result = operator_fallback()
        except Exception as exc:
            error = redact_error(f"unexpected: {exc}", settings.resolved_api_key())
            result = operator_fallback()

        if result.confidence == "low":
            result = result.model_copy(update={"escalate": True})
        if not result.draft_reply.strip():
            result = operator_fallback()
            error = error or "empty_draft"

        db.insert_ticket(payload=payload, result=result, error=error)
        if error and result.draft_reply != OPERATOR_REPLY:
            result = operator_fallback()
        return result

    @app.get("/tickets", response_model=list[TicketRecord])
    def tickets():
        return db.list_tickets()

    @app.get("/clients", response_model=list[ClientRecord])
    def clients():
        return db.list_clients()

    return app


app = create_app()
