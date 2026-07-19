import logging
from contextlib import asynccontextmanager
from typing import AsyncGenerator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api.routes.conversation import router as conversation_router
from api.routes.health import router as health_router
from api.routes.webhook import router as webhook_router
from config import settings

logging.basicConfig(level=settings.LOG_LEVEL.upper())
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    logger.info(
        "Katha API starting — environment=%s log_level=%s",
        settings.ENVIRONMENT,
        settings.LOG_LEVEL,
    )

    from models.db import AsyncSessionLocal
    from scheduler.session_initiator import create_scheduler

    scheduler = create_scheduler(AsyncSessionLocal)
    scheduler.start()
    logger.info("Scheduler started")

    yield

    scheduler.shutdown()
    logger.info("Scheduler stopped")


app = FastAPI(title="Katha API", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health_router)
app.include_router(conversation_router, prefix="/conversation")
app.include_router(webhook_router)
