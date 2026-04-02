"""
FastAPI Application - YDSF AI Article Chatbot

This application provides:
1. User authentication (register, login, logout)
2. AI-powered Q&A using RAG (Retrieval-Augmented Generation)
3. Data crawling dari Twitter & Web Search
4. Vector database management (ChromaDB)

Tech Stack:
- FastAPI: Web framework
- SQLModel/SQLAlchemy: ORM & database
- ChromaDB: Vector database
- LangChain: LLM orchestration
- Groq API: LLM provider
- RapidAPI: Twitter & Google Search APIs
"""
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.responses import JSONResponse
from sqlmodel import SQLModel

from migration.base import engine
from migration import models  # noqa: F401
from api.v1 import auth, chat
from core.logger import get_logger
from core.exceptions import AppException
from core.responses import error_response

# ─── Logger ─────────────────────────────────────────────────────────────────
logger = get_logger("main")

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Jalankan saat aplikasi startup dan shutdown"""
    logger.info("Application starting up...")
    logger.info("Creating database tables...")
    # Tables akan di-create when /migrate endpoint is called
    logger.info("Startup complete")
    yield
    logger.info("Application shutting down...")

# ─── Create FastAPI App ──────────────────────────────────────────────────────
app = FastAPI(
    title="YDSF AI Article Chatbot",
    description="AI-powered Q&A chatbot dengan RAG",
    version="1.0.0",
    lifespan=lifespan,
)

# ─── Static & Templates ──────────────────────────────────────────────────────
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")


# ─── Exception Handlers ──────────────────────────────────────────────────────


@app.exception_handler(AppException)
async def app_exception_handler(request: Request, exc: AppException):
    """
    Handle custom AppException dan convert ke HTTP response.
    
    Converts:
    - ValidationError (400)
    - AuthenticationError (401)
    - InvalidTokenError (401)
    - InvalidCredentialsError (401)
    - NotFoundError (404)
    - DuplicateError (409)
    - AIServiceError (500)
    - CrawlerError (500)
    - etc.
    """
    logger.warning(f"AppException: {exc.error_code} - {exc.message}")
    response = error_response(
        error=exc.error_code,
        message=exc.message,
        status_code=exc.status_code,
        details=exc.details,
    )
    return JSONResponse(status_code=exc.status_code, content=response[0])


@app.exception_handler(Exception)
async def general_exception_handler(request: Request, exc: Exception):
    """Handle unexpected exceptions"""
    logger.error(f"Unexpected exception: {exc}", exc_info=True)
    response = error_response(
        error="INTERNAL_SERVER_ERROR",
        message="Terjadi error yang tidak terduga",
        status_code=500,
    )
    return JSONResponse(status_code=500, content=response[0])



# ─── Health Check ───────────────────────────────────────────────────────────


@app.get("/health", tags=["Health"])
def health_check():
    """Health check endpoint"""
    return {
        "status": "healthy",
        "service": "YDSF AI Article Chatbot",
        "version": "1.0.0",
    }


# ─── Page Routes (HTML) ──────────────────────────────────────────────────────


@app.get("/", tags=["Pages"])
def read_root(request: Request):
    """Home page"""
    return templates.TemplateResponse(request=request, name="index.html")


@app.get("/login", tags=["Pages"])
def read_login(request: Request):
    """Login page"""
    return templates.TemplateResponse(request=request, name="login.html")


@app.get("/signup", tags=["Pages"])
def read_signup(request: Request):
    """Sign up page"""
    return templates.TemplateResponse(request=request, name="signup.html")


@app.get("/chat", tags=["Pages"])
def read_chat(request: Request):
    """Chat page"""
    return templates.TemplateResponse(request=request, name="chat.html")


# ─── API Routes ──────────────────────────────────────────────────────────────
app.include_router(auth.router, prefix="/api/auth", tags=["Auth"])
app.include_router(chat.router, prefix="/api/chat", tags=["Chat"])
# app.include_router(chat.router, prefix="/api/v1/chat", tags=["Chat"])  ← untuk update v2 nanti


# ─── Migration ────────────────────────────────────────────────────────────────


@app.post("/migrate", tags=["Migration"])
def run_migration():
    """
    Run database migration - create all tables.

    Call this endpoint once to initialize database.
    """
    try:
        logger.info("Starting database migration...")
        SQLModel.metadata.create_all(engine)
        tables = list(SQLModel.metadata.tables.keys())
        logger.info(f"Migration successful. Tables created: {tables}")

        return {
            "status": "success",
            "message": "Semua tabel berhasil dibuat di MariaDB",
            "tables": tables,
        }
    except Exception as e:
        logger.error(f"Migration failed: {e}", exc_info=True)
        return {
            "status": "error",
            "message": f"Migration gagal: {str(e)}",
            "tables": [],
        }


@app.post("/migrate/alter", tags=["Migration"])
def run_alter_migration():
    """
    Tambah kolom baru ke tabel yang sudah ada (ALTER TABLE).

    Aman dijalankan berkali-kali — kolom hanya ditambah jika belum ada.
    Jalankan ini setelah deploy untuk menambah:
    - chat_sessions.processing_status
    - chat_sessions.error_message
    """
    from sqlalchemy import text

    alterations = [
        (
            "processing_status",
            "ALTER TABLE chat_sessions ADD COLUMN processing_status VARCHAR(20) NOT NULL DEFAULT 'processing'",
        ),
        (
            "error_message",
            "ALTER TABLE chat_sessions ADD COLUMN error_message TEXT DEFAULT NULL",
        ),
    ]

    results = []
    with engine.connect() as conn:
        for col_name, sql in alterations:
            try:
                conn.execute(text(sql))
                conn.commit()
                results.append({"column": col_name, "status": "added"})
                logger.info(f"Column added: chat_sessions.{col_name}")
            except Exception as e:
                err_str = str(e).lower()
                if "duplicate column" in err_str or "already exists" in err_str or "1060" in err_str:
                    results.append({"column": col_name, "status": "already_exists"})
                    logger.info(f"Column already exists (skip): {col_name}")
                else:
                    results.append({"column": col_name, "status": "error", "detail": str(e)})
                    logger.error(f"Failed to add column {col_name}: {e}")

    all_ok = all(r["status"] in ("added", "already_exists") for r in results)
    return {
        "status": "success" if all_ok else "partial_error",
        "message": "ALTER TABLE selesai",
        "columns": results,
    }