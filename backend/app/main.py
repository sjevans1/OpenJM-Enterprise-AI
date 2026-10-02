from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import chat, data, knowledge, reports, report_definitions
from app.core.config import get_settings
from app.db import init_db


settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    yield


app = FastAPI(
    title="OpenJM Enterprise AI",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/health")
async def health():
    return {
        "status": "ok",
        "product": "OpenJM Enterprise AI",
        "knowledge_engine": "DB-GPT" if settings.knowledge_enabled else "disabled",
        "model": settings.model_name,
    }


app.include_router(chat.router, prefix="/api")
app.include_router(knowledge.router, prefix="/api")
app.include_router(data.router, prefix="/api")

app.include_router(reports.router, prefix="/api")
app.include_router(report_definitions.router, prefix="/api")
