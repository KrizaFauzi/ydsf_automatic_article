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

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "topic": "kecerdasan buatan 2025",
                "title": "AI di 2025",
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

    model_config = ConfigDict(
        json_schema_extra={"example": {"session_id": "uuid-session-di-sini"}}
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


# ─── Background Task ─────────────────────────────────────────────────────────


def _background_crawl_and_generate(session_id: str, topic: str) -> None:
    """
    Background task: jalankan crawl + generate artikel.

    Dipanggil via FastAPI BackgroundTasks sehingga tidak blocking HTTP.
    Setiap operasi DB menggunakan session terpisah agar aman dari timeout.

    Flow:
    1. Crawl semua sumber → build ChromaDB
    2. Update vector_db_path ke DB
    3. Generate artikel via RAG
    4. Simpan artikel sebagai pesan bot
    5. Mark session sebagai ready
    """
    from migration.base import engine
    from sqlmodel import Session as DBSession

    logger_chat.info(f"[BG] ===== BACKGROUND TASK START: session={session_id} topic='{topic}' =====")
    t0 = time_module.time()

    def _new_db():
        """Buat session DB baru (koneksi segar)."""
        return DBSession(engine)

    try:
        # ── STEP 1: Crawler ──────────────────────────────────────────
        logger_chat.info(f"[BG][1/5] CRAWLER START — topic='{topic}'")
        t1 = time_module.time()
        crawl_result = run_crawler(query=topic, session_id=session_id)
        vector_db_path = crawl_result["vector_db_path"]
        sources = crawl_result.get("sources", {})
        logger_chat.info(
            f"[BG][1/5] CRAWLER DONE — {crawl_result['total_docs']} docs, "
            f"{crawl_result['total_chunks']} chunks, path={vector_db_path} "
            f"({time_module.time()-t1:.1f}s)"
        )

        summary = (
            f"Crawled {crawl_result['total_docs']} docs "
            f"({sources.get('twitter', 0)} tweets, "
            f"{sources.get('rss', 0) + sources.get('google_news', 0)} news, "
            f"{sources.get('wikipedia', 0) + sources.get('wikidata', 0)} knowledge), "
            f"{crawl_result['total_chunks']} chunks indexed."
        )

        # ── STEP 2: Simpan ke DB ─────────────────────────────────────
        logger_chat.info(f"[BG][2/5] SAVING vector_db_path to DB...")
        with _new_db() as db:
            update_session_paths(
                session=db,
                session_id=session_id,
                vector_db_path=vector_db_path,
                summary=summary,
            )
        logger_chat.info(f"[BG][2/5] DONE — vector_db_path saved")

        # ── STEP 3: Generate artikel ──────────────────────────────────
        logger_chat.info(f"[BG][3/5] GENERATE ARTICLE START — topic='{topic}'")
        t3 = time_module.time()
        article_result = generate_article(topic=topic, vector_db_path=vector_db_path)
        article_content = article_result["article"]
        sources = article_result.get("sources", [])
        logger_chat.info(
            f"[BG][3/5] GENERATE ARTICLE DONE — {len(article_content)} chars, {len(sources)} sources "
            f"({time_module.time()-t3:.1f}s)"
        )

        # ── STEP 4: Simpan pesan ─────────────────────────────────────
        logger_chat.info(f"[BG][4/5] SAVING article as chat message...")
        with _new_db() as db:
            msg_count = get_session_message_count(db, session_id)
            create_chat_message(
                session=db,
                session_id=session_id,
                role="bot",
                content=article_content,
                order=msg_count,
            )
        logger_chat.info(f"[BG][4/5] DONE — message saved")

        # ── STEP 4.5: Simpan sources ke ArticleSource table ──────────
        logger_chat.info(f"[BG][4.5/5] SAVING {len(sources)} sources...")
        with _new_db() as db:
            if sources:
                create_article_sources_batch(
                    session=db,
                    session_id=session_id,
                    sources=sources,
                )
        logger_chat.info(f"[BG][4.5/5] DONE — {len(sources)} sources saved")

        # ── STEP 5: Mark ready ───────────────────────────────────────
        logger_chat.info(f"[BG][5/5] MARKING SESSION READY...")
        with _new_db() as db:
            set_session_ready(session=db, session_id=session_id)
        logger_chat.info(
            f"[BG][5/5] DONE ✓ — session {session_id} is READY "
            f"(total: {time_module.time()-t0:.1f}s)"
        )
        logger_chat.info(f"[BG] ===== BACKGROUND TASK COMPLETE =====")

    except Exception as e:
        logger_chat.error(
            f"[BG] ===== BACKGROUND TASK FAILED at step — {type(e).__name__}: {e} =====",
            exc_info=True
        )
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

    Returns:
        List of SessionResponse
    """
    logger_chat.info(f"List sessions for user: {current_user.id}")
    try:
        sessions = get_user_chat_sessions(db, user_id=current_user.id, limit=50)
        return sessions
    except DatabaseError as e:
        logger_chat.error(f"DB error listing sessions: {e.message}")
        raise HTTPException(status_code=500, detail=e.message)
    except Exception as e:
        logger_chat.error(f"Unexpected error listing sessions: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Gagal mengambil daftar session")


@router.post("/sessions", response_model=dict, status_code=202)
def create_session(
    payload: CreateSessionRequest,
    background_tasks: BackgroundTasks,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
):
    """
    Buat chat session baru dan jadwalkan crawl + generate artikel di background.

    Endpoint ini langsung return dalam <1 detik dengan status 'processing'.
    Proses berat (crawl + generate) berjalan di background thread.

    Frontend harus polling GET /sessions/{id}/status sampai is_ready=True.

    Returns:
        {
            "session": SessionResponse,
            "status": "processing"
        }
    """
    topic = payload.topic.strip()
    title = payload.title.strip() if payload.title else (
        topic[:80] + "…" if len(topic) > 80 else topic
    )

    logger_chat.info(
        f"Create session for user: {current_user.id}, topic: '{topic}'"
    )

    try:
        # 1. Simpan session ke DB (is_ready=False, processing_status='processing')
        chat_session = create_chat_session(
            session=db,
            user_id=current_user.id,
            title=title,
            topic=topic,
            vector_db_path="",  # akan diisi oleh background task
        )

        # 2. Jadwalkan background task — tidak blocking HTTP
        background_tasks.add_task(
            _background_crawl_and_generate,
            session_id=chat_session.id,
            topic=topic,
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

    except DatabaseError as e:
        logger_chat.error(f"DB error creating session: {e.message}")
        raise HTTPException(status_code=500, detail=e.message)
    except Exception as e:
        logger_chat.error(f"Unexpected error creating session: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Gagal membuat session baru")

@router.get("/sessions/{session_id}/status", response_model=SessionStatusResponse)
def get_session_status(
    session_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
):
    """
    Cek status background task untuk session.

    Frontend polling endpoint ini setiap beberapa detik sampai
    processing_status == 'ready' atau 'error'.

    Returns:
        SessionStatusResponse dengan:
        - processing_status: 'processing' | 'ready' | 'error'
        - is_ready: bool
        - has_article: apakah artikel sudah tersimpan di DB
        - error_message: pesan error jika gagal
    """
    try:
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
            is_ready=chat_session.is_ready,
            has_article=has_article,
            error_message=getattr(chat_session, 'error_message', None),
        )

    except HTTPException:
        raise
    except NotFoundError as e:
        raise HTTPException(status_code=404, detail=e.message)
    except Exception as e:
        logger_chat.error(f"Error getting session status: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Gagal cek status session")



@router.get("/sessions/{session_id}/messages", response_model=list[MessageResponse])
def get_messages(
    session_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
):
    """
    Ambil semua pesan di session tertentu, ordered oldest first.

    Args:
        session_id: ID ChatSession

    Returns:
        List of MessageResponse

    Raises:
        NotFoundError: Session tidak ditemukan
    """
    logger_chat.info(f"Get messages for session: {session_id}")
    try:
        # Verify session belongs to user
        chat_session = get_chat_session(db, session_id)
        if chat_session.user_id != current_user.id:
            raise HTTPException(status_code=403, detail="Akses ditolak")

        messages = get_chat_messages(db, session_id=session_id, limit=200)
        return messages

    except HTTPException:
        raise
    except NotFoundError as e:
        raise HTTPException(status_code=404, detail=e.message)
    except DatabaseError as e:
        raise HTTPException(status_code=500, detail=e.message)
    except Exception as e:
        logger_chat.error(f"Unexpected error getting messages: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Gagal mengambil pesan")


# ─── Sources Endpoint ─────────────────────────────────────────────────────────


@router.get("/sessions/{session_id}/sources")
def get_sources(
    session_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
):
    """
    Ambil semua article sources (references) untuk session tertentu.
    
    Returns sources dalam format yang bisa langsung di-display:
    - Formatted markdown untuk ditambahkan di bawah artikel
    - Array of sources untuk rendering yang lebih custom

    Args:
        session_id: ID ChatSession

    Returns:
        {
            "formatted": "## Sumber & Referensi\n\n1. [Title (Source)](URL)\n...",
            "sources": [
                {"judul": "...", "sumber": "...", "url": "...", "order": 0},
                ...
            ]
        }
    """
    logger_chat.info(f"Get sources for session: {session_id}")
    try:
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
        from services.ai import _format_sources_for_display
        formatted = _format_sources_for_display(sources_data)

        return {
            "formatted": formatted,
            "sources": sources_data,
            "count": len(sources_data),
        }

    except HTTPException:
        raise
    except NotFoundError as e:
        raise HTTPException(status_code=404, detail=e.message)
    except DatabaseError as e:
        raise HTTPException(status_code=500, detail=e.message)
    except Exception as e:
        logger_chat.error(f"Unexpected error getting sources: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Gagal mengambil sources")


# ─── Ask Endpoint (RAG) ───────────────────────────────────────────────────────


@router.post("/ask", response_model=AskResponse)
def chat_ask(
    payload: AskRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
):
    """
    Ask question menggunakan RAG. Pesan user & bot disimpan ke DB.

    Algorithm:
    1. Load session dari DB → dapatkan vector_db_path
    2. Ambil history pesan dari DB → format untuk LLM
    3. Simpan pesan user ke DB
    4. Call AI service (similarity search + LLM)
    5. Simpan pesan bot ke DB
    6. Return answer + metadata

    Args:
        payload: AskRequest dengan session_id & question

    Returns:
        AskResponse dengan answer + metadata

    Raises:
        ValidationError: Input tidak valid
        NotFoundError: Session tidak ada
        VectorDBNotFoundError: ChromaDB tidak ditemukan
        AIServiceError: LLM error
    """
    logger_chat.info(f"Ask request: session={payload.session_id}, q={payload.question[:50]}...")
    start_time = time_module.time()

    try:
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
        answer = ask(
            question=payload.question,
            vector_db_path=vector_db_path,
            history=history,
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

    except HTTPException:
        raise
    except ValidationError as e:
        raise HTTPException(status_code=400, detail=e.message)
    except NotFoundError as e:
        raise HTTPException(status_code=404, detail=e.message)
    except VectorDBNotFoundError as e:
        raise HTTPException(status_code=404, detail=e.message)
    except AIServiceError as e:
        logger_chat.error(f"AI error: {e.message}")
        raise HTTPException(status_code=500, detail=e.message)
    except Exception as e:
        logger_chat.error(f"Unexpected error in chat_ask: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Error tidak terduga saat menjawab")


# ─── Generate Article Endpoint ────────────────────────────────────────────────


@router.post("/generate", response_model=GenerateResponse)
def generate_article_endpoint(
    payload: GenerateRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
):
    """
    Generate artikel multi-seksi (6 poin) dari topik session menggunakan RAG.

    CONSTRAINT: Setiap session hanya boleh punya 1 artikel. 
    Jika sudah ada artikel di session, return error 409 Conflict.

    Tidak membutuhkan input pertanyaan dari user — topik sudah ada di ChatSession.
    Artikel disimpan ke DB sebagai pesan bot dan dikembalikan ke frontend.

    Args:
        payload: GenerateRequest dengan session_id

    Returns:
        GenerateResponse dengan article (Markdown) + sources + metadata
    """
    logger_chat.info(f"Generate article: session={payload.session_id}")
    start_time = time_module.time()

    try:
        # 1. Load session — verifikasi ownership
        chat_session: ChatSession = get_chat_session(db, payload.session_id)
        if chat_session.user_id != current_user.id:
            raise HTTPException(status_code=403, detail="Akses ditolak")

        if not chat_session.is_ready:
            raise HTTPException(status_code=400, detail="Session belum siap, crawler masih berjalan")

        # 2. Check: apakah sudah ada artikel di session ini?
        # Article adalah message dengan role="bot" yang paling awal (order=0)
        existing_messages = get_chat_messages(db, session_id=payload.session_id, limit=1)
        if existing_messages:
            first_msg = existing_messages[0]
            if first_msg.role == "bot":
                raise HTTPException(
                    status_code=409,
                    detail="Artikel sudah ada. Setiap session hanya bisa punya 1 artikel. Silakan buat session baru jika ingin artikel lain."
                )

        vector_db_path = chat_session.vector_db_path
        topic = chat_session.topic
        logger_chat.debug(f"Generating article for topic: '{topic}', ChromaDB: {vector_db_path}")

        # 3. Dapatkan urutan pesan berikutnya
        msg_count = get_session_message_count(db, payload.session_id)

        # 4. Generate artikel via RAG
        article_result = generate_article(
            topic=topic,
            vector_db_path=vector_db_path,
        )
        article_content = article_result["article"]
        sources = article_result.get("sources", [])

        processing_time_ms = (time_module.time() - start_time) * 1000

        # 5. Simpan artikel sebagai pesan bot ke DB
        create_chat_message(
            session=db,
            session_id=payload.session_id,
            role="bot",
            content=article_content,
            order=msg_count,
        )

        # 6. Simpan sources ke ArticleSource table
        if sources:
            create_article_sources_batch(
                session=db,
                session_id=payload.session_id,
                sources=sources,
            )
            logger_chat.info(f"Saved {len(sources)} article sources")

        logger_chat.info(f"Article saved. Generated in {processing_time_ms:.2f}ms")
        return GenerateResponse(
            article=article_content,
            sources=sources,
            session_id=payload.session_id,
            processing_time_ms=processing_time_ms,
        )

    except HTTPException:
        raise
    except ValidationError as e:
        raise HTTPException(status_code=400, detail=e.message)
    except NotFoundError as e:
        raise HTTPException(status_code=404, detail=e.message)
    except VectorDBNotFoundError as e:
        raise HTTPException(status_code=404, detail=e.message)
    except AIServiceError as e:
        logger_chat.error(f"AI error in generate: {e.message}")
        raise HTTPException(status_code=500, detail=e.message)
    except Exception as e:
        logger_chat.error(f"Unexpected error in generate_article_endpoint: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Error tidak terduga saat generate artikel")


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