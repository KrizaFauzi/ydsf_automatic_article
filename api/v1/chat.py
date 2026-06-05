"""
Chat API Routes.

Routes:
- GET  /sessions                      : List semua chat session milik current user
- POST /sessions                      : Buat session baru + jalankan crawl
- GET  /sessions/{session_id}/messages: Ambil semua pesan di session
- POST /ask                           : Ask question via RAG (simpan pesan ke DB)
- POST /crawl                         : (legacy) Crawl data saja tanpa buat session

Flow:
1. /sessions POST : Validate topic → Generate session_id → Run crawler → Save session ke DB
2. /ask          : Validate → Load session dari DB → Ambil vector_db_path → Fetch history
                   → Call AI service → Simpan user msg + bot msg ke DB → Return answer
"""

from typing import Optional
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel, Field, ConfigDict
from sqlmodel import Session
import uuid
import time as time_module
from datetime import datetime, timezone

from core.logger import logger_chat
from core.deps import get_current_user
from core.exceptions import (
    ValidationError,
    NotFoundError,
    VectorDBNotFoundError,
    AIServiceError,
    CrawlerError,
    DatabaseError,
)
from core.constants import (
    ERROR_MSG_EMPTY_QUESTION,
    ERROR_MSG_EMPTY_QUERY,
)
from migration.base import get_session
from migration.models import User, ChatSession
from crud.chat import (
    create_chat_session,
    get_chat_session,
    get_user_chat_sessions,
    delete_chat_session,
    update_chat_session_status,
    update_session_paths,
    set_session_ready,
    set_session_error,
    create_chat_message,
    get_chat_messages,
    get_session_message_count,
    create_article_sources_batch,
    get_article_sources,
)
from services.crawler import run_crawler
from services.ai import ask, generate_article
from schemas.chat import ArticleConfig

router = APIRouter()


# ─── Request / Response Schemas ──────────────────────────────────────────────


class CreateSessionRequest(BaseModel):
    """Schema untuk POST /sessions — buat room baru & jalankan crawl"""

    topic: str = Field(
        ...,
        min_length=1,
        max_length=500,
        description="Topik / query yang akan di-crawl",
    )
    title: str | None = Field(
        default=None,
        max_length=255,
        description="Judul session (opsional, default dari topic)",
    )
    config: Optional[ArticleConfig] = Field(
        default=None,
        description="Konfigurasi artikel tingkat lanjut (opsional)"
    )

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "topic": "kecerdasan buatan 2025",
                "title": "AI di 2025",
                "config": {
                    "topic": "kecerdasan buatan 2025",
                    "length": "long",
                    "seo_keywords": ["AI", "Future"]
                }
            }
        }
    )


class SessionResponse(BaseModel):
    """Schema untuk response session"""

    id: str
    title: str
    topic: str
    vector_db_path: str
    is_ready: bool
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class SessionWithArticleResponse(BaseModel):
    """Schema untuk response session dengan article yang sudah di-generate"""

    session: SessionResponse
    article: str  # Markdown string dari generated article


class MessageResponse(BaseModel):
    """Schema untuk satu chat message"""

    id: str
    role: str
    content: str
    order: int
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class AskRequest(BaseModel):
    """Schema untuk POST /ask"""

    session_id: str = Field(..., description="ID ChatSession yang aktif")
    question: str = Field(
        ...,
        min_length=1,
        max_length=5000,
        description="Pertanyaan dari user",
    )

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "session_id": "uuid-session-di-sini",
                "question": "Apa dampak AI terhadap dunia kerja?",
            }
        }
    )


class AskResponse(BaseModel):
    """Schema untuk response /ask"""

    answer: str
    session_id: str
    retrieved_docs: int = 3
    processing_time_ms: float


class GenerateRequest(BaseModel):
    """Schema untuk POST /generate — generate artikel dari topik session"""

    session_id: str = Field(..., description="ID ChatSession yang aktif")
    config: Optional[ArticleConfig] = Field(
        default=None, 
        description="Konfigurasi artikel tingkat lanjut (opsional)"
    )

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "session_id": "uuid-session-di-sini",
                "config": {
                    "topic": "kecerdasan buatan 2025",
                    "length": "medium",
                    "seo_keywords": ["AI", "Future"],
                    "key_points": ["Impact on jobs", "Healthcare innovation"]
                }
            }
        }
    )


class GenerateResponse(BaseModel):
    """Schema untuk response /generate"""

    article: str
    session_id: str
    sources: list[dict] | None = None
    processing_time_ms: float


class SessionStatusResponse(BaseModel):
    """Schema untuk response GET /sessions/{id}/status"""

    session_id: str
    processing_status: str   # 'processing' | 'ready' | 'error'
    progress_count: int
    is_ready: bool
    has_article: bool
    error_message: str | None = None

    model_config = ConfigDict(from_attributes=True)


# Legacy schemas (untuk /crawl lama)
class ChatRequest(BaseModel):
    """Schema untuk POST /crawl legacy (chat)."""
    question: str = Field(..., min_length=1, max_length=5000)
    vector_db_path: str = Field(...)
    history: list = Field(default=[])


class ChatResponse(BaseModel):
    """Schema untuk response dari /crawl legacy (chat)."""
    answer: str
    retrieved_docs: int = 3
    processing_time_ms: float


class CrawlRequest(BaseModel):
    """Schema untuk POST /crawl legacy (inisialisasi)."""
    query: str = Field(..., min_length=1, max_length=500)


class CrawlResponse(BaseModel):
    """Schema untuk response dari /crawl legacy (inisialisasi)."""
    status: str
    query: str
    vector_db_path: str
    twitter_count: int
    web_count: int
    total_docs: int
    total_chunks: int
    message: str


# ─── Background Logic (Internal) ───────────────────────────────────────────


async def _run_article_generation_logic(session_id: str, topic: str, config: Optional[ArticleConfig], vector_db_path: str) -> None:
    """
    Logika inti untuk generate artikel, saving to DB, and updating status.
    Didesain untuk dijalankan di background task.
    """
    from migration.base import engine
    from sqlmodel import Session as DBSession

    def _new_db(): return DBSession(engine)

    try:
        # 1. Update status ke processing
        with _new_db() as db:
            chat_session = get_chat_session(db, session_id)
            chat_session.processing_status = "processing"
            db.add(chat_session)
            db.commit()

        # 2. Generate artikel via RAG
        logger_chat.info(f"[BG] GENERATE ARTICLE START — session={session_id} topic='{topic}'")
        t_gen = time_module.time()
        
        article_config = config or ArticleConfig(topic=topic)
        if not article_config.topic: article_config.topic = topic
        
        article_result = await generate_article(config=article_config, vector_db_path=vector_db_path)
        article_content = article_result["article"]
        sources = article_result.get("sources", [])
        
        # Format sources section and append to content
        if sources and "## Sumber & Referensi" not in article_content:
            from services.ai import _format_sources_section
            formatted_sources = _format_sources_section(sources)
            article_content += formatted_sources
            
        logger_chat.info(f"[BG] GENERATE ARTICLE DONE — {len(article_content)} chars ({time_module.time()-t_gen:.1f}s)")

        # 3. Simpan artikel sebagai pesan bot
        with _new_db() as db:
            msg_count = get_session_message_count(db, session_id)
            create_chat_message(
                session=db,
                session_id=session_id,
                role="bot",
                content=article_content,
                order=msg_count,
            )
            
            # 4. Simpan sources ke DB
            if sources:
                create_article_sources_batch(
                    session=db,
                    session_id=session_id,
                    sources=sources,
                )
            
            # 5. Mark ready
            set_session_ready(session=db, session_id=session_id)
            
        logger_chat.info(f"[BG] ARTICLE SAVED & SESSION READY: {session_id}")

    except Exception as e:
        logger_chat.error(f"[BG] ARTICLE GENERATION FAILED: {e}", exc_info=True)
        with _new_db() as db:
            set_session_error(
                session=db,
                session_id=session_id,
                error_message=f"GenerationError: {str(e)[:480]}",
            )


async def _background_crawl_and_generate(session_id: str, topic: str, config: Optional[ArticleConfig] = None) -> None:
    """
    Background task: jalankan crawl + generate artikel (inisialisasi).
    """
    from migration.base import engine
    from sqlmodel import Session as DBSession

    logger_chat.info(f"[BG] ===== BACKGROUND ORCHESTRATOR START: session={session_id} =====")
    t0 = time_module.time()

    def _new_db(): return DBSession(engine)

    try:
        # ── STEP 1: Crawler ──────────────────────────────────────────
        use_crawling = config.use_crawling if config else True
        vector_db_path = ""
        summary = ""

        if use_crawling:
            logger_chat.info(f"[BG][1/2] CRAWLER START — topic='{topic}'")
            
            # Anti-timeout heartbeats
            from crud.chat import increment_session_progress
            def _heartbeat():
                with _new_db() as db: increment_session_progress(db, session_id)

            seo_kws = config.seo_keywords if config else None
            crawl_result = await run_crawler(
                query=topic, 
                session_id=session_id, 
                progress_callback=_heartbeat,
                seo_keywords=seo_kws
            )
            vector_db_path = crawl_result["vector_db_path"]
            sources_summary = crawl_result.get("sources", {})
            
            summary = (
                f"Crawled {crawl_result['total_docs']} docs, "
                f"{crawl_result['total_chunks']} chunks indexed."
            )
            logger_chat.info(f"[BG][1/2] CRAWLER DONE ({time_module.time()-t0:.1f}s)")
        else:
            logger_chat.info(f"[BG][1/2] CRAWLER SKIPPED")
            summary = "Generated without external crawling."

        # ── STEP 2: Update Path & Run Generation ─────────────────────
        with _new_db() as db:
            update_session_paths(
                session=db,
                session_id=session_id,
                vector_db_path=vector_db_path,
                summary=summary,
            )
        
        # Call generation logic (as part of this bg task, so no need for add_task here)
        await _run_article_generation_logic(session_id, topic, config, vector_db_path)
        
        logger_chat.info(f"[BG] ===== ORCHESTRATOR COMPLETE ({time_module.time()-t0:.1f}s) =====")

    except Exception as e:
        logger_chat.error(f"[BG] ORCHESTRATOR FAILED: {e}", exc_info=True)
        try:
            with _new_db() as db:
                set_session_error(
                    session=db,
                    session_id=session_id,
                    error_message=f"{type(e).__name__}: {str(e)[:480]}",
                )
        except Exception as db_err:
            logger_chat.error(f"[BG] Cannot set error to DB: {db_err}")



# ─── Session Endpoints ────────────────────────────────────────────────────────


@router.get("/sessions", response_model=list[SessionResponse])
def list_sessions(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
):
    """
    List semua chat session milik current user, terbaru di atas.
    """
    logger_chat.info(f"List sessions for user: {current_user.id}")
    return get_user_chat_sessions(db, user_id=current_user.id, limit=50)


@router.post("/sessions", response_model=dict, status_code=202)
def create_session(
    payload: CreateSessionRequest,
    background_tasks: BackgroundTasks,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
):
    """
    Buat chat session baru dan jadwalkan crawl + generate artikel di background.
    """
    topic = payload.topic.strip()
    title = payload.title.strip() if payload.title else (
        topic[:80] + "…" if len(topic) > 80 else topic
    )

    logger_chat.info(
        f"Create session for user: {current_user.id}, topic: '{topic}'"
    )

    # 1. Simpan session ke DB (is_ready=False, processing_status='processing')
    chat_session = create_chat_session(
        session=db,
        user_id=current_user.id,
        title=title,
        topic=topic,
        vector_db_path="",  # akan diisi oleh background task
        model_choice=payload.config.model_choice if payload.config else None,
    )

    # 2. Jadwalkan background task — tidak blocking HTTP
    background_tasks.add_task(
        _background_crawl_and_generate,
        session_id=chat_session.id,
        topic=topic,
        config=payload.config
    )

    logger_chat.info(
        f"Session created: {chat_session.id}. Background task scheduled."
    )

    # 3. Langsung return — tidak timeout
    return {
        "session": {
            "id": chat_session.id,
            "title": chat_session.title,
            "topic": chat_session.topic,
            "vector_db_path": chat_session.vector_db_path,
            "is_ready": chat_session.is_ready,
            "processing_status": "processing",
            "created_at": chat_session.created_at.isoformat(),
        },
        "status": "processing",
    }

@router.get("/sessions/{session_id}/status", response_model=SessionStatusResponse)
def get_session_status(
    session_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
):
    """
    Cek status background task untuk session.
    """
    chat_session = get_chat_session(db, session_id)
    if chat_session.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Akses ditolak")

    # Cek apakah sudah ada artikel tersimpan
    msg_count = get_session_message_count(db, session_id)
    has_article = msg_count > 0

    return SessionStatusResponse(
        session_id=session_id,
        processing_status=getattr(chat_session, 'processing_status', 
            'ready' if chat_session.is_ready else 'processing'),
        progress_count=getattr(chat_session, 'progress_count', 0),
        is_ready=chat_session.is_ready,
        has_article=has_article,
        error_message=getattr(chat_session, 'error_message', None),
    )



@router.get("/sessions/{session_id}/messages", response_model=list[MessageResponse])
def get_messages(
    session_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
):
    """
    Ambil semua pesan di session tertentu, ordered oldest first.
    """
    logger_chat.info(f"Get messages for session: {session_id}")
    # Verify session belongs to user
    chat_session = get_chat_session(db, session_id)
    if chat_session.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Akses ditolak")

    return get_chat_messages(db, session_id=session_id, limit=200)


# ─── Sources Endpoint ─────────────────────────────────────────────────────────


@router.get("/sessions/{session_id}/sources")
def get_sources(
    session_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
):
    """
    Ambil semua article sources (references) untuk session tertentu.
    """
    logger_chat.info(f"Get sources for session: {session_id}")
    # Verify session belongs to user
    chat_session = get_chat_session(db, session_id)
    if chat_session.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Akses ditolak")

    # Get sources from DB
    sources_objs = get_article_sources(db, session_id=session_id)
    
    # Convert to dicts for JSON response
    sources_data = [
        {
            "judul": src.judul,
            "sumber": src.sumber,
            "url": src.url,
            "order": src.order,
        }
        for src in sources_objs
    ]

    # Format as markdown
    from services.ai import _format_sources_section
    formatted = _format_sources_section(sources_data)

    return {
        "formatted": formatted,
        "sources": sources_data,
        "count": len(sources_data),
    }


@router.delete("/sessions/{session_id}", status_code=204)
def delete_session_endpoint(
    session_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
):
    """
    Hapus chat session secara permanen, termasuk ChromaDB di disk.
    """
    import os
    import shutil

    # 1. Load session & check ownership
    chat_session = get_chat_session(db, session_id)
    if chat_session.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Akses ditolak")

    vector_db_path = chat_session.vector_db_path

    # 2. Hapus dari database (cascading messages & sources)
    delete_chat_session(db, session_id)

    # 3. Hapus directory ChromaDB jika ada
    if vector_db_path and os.path.exists(vector_db_path):
        try:
            # Sanitasi path untuk mencegah penghapusan direktori yang tidak diinginkan
            if "session_" in vector_db_path:
                shutil.rmtree(vector_db_path)
                logger_chat.info(f"ChromaDB deleted: {vector_db_path}")
        except Exception as e:
            logger_chat.error(f"Failed to delete ChromaDB dir: {e}")

    return None


# ─── Ask Endpoint (RAG) ───────────────────────────────────────────────────────


@router.post("/ask", response_model=AskResponse)
async def chat_ask(
    payload: AskRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
):
    """
    Ask question menggunakan RAG. Pesan user & bot disimpan ke DB.
    """
    logger_chat.info(f"Ask request: session={payload.session_id}, q={payload.question[:50]}...")
    start_time = time_module.time()

    # 1. Load session — verifikasi ownership & ambil vector_db_path
    chat_session: ChatSession = get_chat_session(db, payload.session_id)
    if chat_session.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Akses ditolak")

    if not chat_session.is_ready:
        raise HTTPException(status_code=400, detail="Session belum siap, crawler masih berjalan")

    vector_db_path = chat_session.vector_db_path
    logger_chat.debug(f"Using ChromaDB: {vector_db_path}")

    # 2. Ambil history pesan dari DB (max 10 terakhir sebagai context)
    from crud.chat import get_latest_chat_messages
    history_msgs = get_latest_chat_messages(db, session_id=payload.session_id, limit=10)
    history = [
        {"role": msg.role, "content": msg.content}
        for msg in history_msgs
    ]
    logger_chat.debug(f"History length: {len(history)}")

    # 3. Dapatkan urutan pesan berikutnya
    msg_count = get_session_message_count(db, payload.session_id)

    # 4. Simpan pesan user ke DB
    create_chat_message(
        session=db,
        session_id=payload.session_id,
        role="user",
        content=payload.question,
        order=msg_count,
    )

    # 5. Call AI service
    logger_chat.debug("Calling AI service...")
    answer = await ask(
        question=payload.question,
        vector_db_path=vector_db_path,
        history=history,
        model_name=chat_session.model_choice
    )

    processing_time_ms = (time_module.time() - start_time) * 1000

    # 6. Simpan pesan bot ke DB
    create_chat_message(
        session=db,
        session_id=payload.session_id,
        role="bot",
        content=answer,
        order=msg_count + 1,
    )

    logger_chat.info(f"Answer generated in {processing_time_ms:.2f}ms")
    return AskResponse(
        answer=answer,
        session_id=payload.session_id,
        retrieved_docs=3,
        processing_time_ms=processing_time_ms,
    )


# ─── Generate Article Endpoint ────────────────────────────────────────────────


@router.post("/generate", response_model=dict, status_code=202)
async def generate_article_endpoint(
    payload: GenerateRequest,
    background_tasks: BackgroundTasks,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
):
    """
    Generate artikel via RAG di background untuk mencegah timeout.
    """
    logger_chat.info(f"Generate article request: session={payload.session_id}")

    # 1. Load session — verifikasi ownership
    chat_session: ChatSession = get_chat_session(db, payload.session_id)
    if chat_session.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Akses ditolak")

    if not chat_session.is_ready:
        raise HTTPException(status_code=400, detail="Session belum siap (crawler masih berjalan)")

    # 2. Check: apakah sudah ada artikel di session ini?
    existing_messages = get_chat_messages(db, session_id=payload.session_id, limit=20)
    for msg in existing_messages:
        if msg.role == "bot" and (msg.order == 0 or msg.content.startswith("## ")):
            raise HTTPException(
                status_code=409,
                detail="Artikel sudah ada di session ini."
            )

    # 3. Jadwalkan background task
    background_tasks.add_task(
        _run_article_generation_logic,
        session_id=payload.session_id,
        topic=chat_session.topic,
        config=payload.config,
        vector_db_path=chat_session.vector_db_path
    )

    return {
        "status": "processing",
        "message": "Pembuatan artikel dimulai di latar belakang.",
        "session_id": payload.session_id
    }


# ─── Legacy Endpoints ─────────────────────────────────────────────────────────


@router.post("/crawl", response_model=CrawlResponse, tags=["Legacy"])
def crawl_data(payload: CrawlRequest):
    """
    (Legacy) Crawl data dari Twitter & Google Search, build vector database.
    Tidak membuat session di DB. Gunakan POST /sessions untuk workflow baru.
    """
    logger_chat.info(f"[Legacy] Crawl request for query: {payload.query[:50]}")

    try:
        if not payload.query.strip():
            raise ValidationError(ERROR_MSG_EMPTY_QUERY)

        session_id = str(uuid.uuid4())
        result = run_crawler(payload.query, session_id)

        return CrawlResponse(**result)

    except ValidationError as e:
        raise HTTPException(status_code=e.status_code, detail=e.message)
    except CrawlerError as e:
        raise HTTPException(status_code=500, detail=e.message)
    except Exception as e:
        logger_chat.error(f"Unexpected error in crawl_data: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Crawler error tidak terduga")