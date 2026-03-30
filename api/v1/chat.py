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

from fastapi import APIRouter, Depends, HTTPException
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
    create_chat_message,
    get_chat_messages,
    get_session_message_count,
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
    processing_time_ms: float


# Legacy schemas (untuk /crawl lama)
class ChatRequest(BaseModel):
    question: str = Field(..., min_length=1, max_length=5000)
    vector_db_path: str = Field(...)
    history: list = Field(default=[])


class ChatResponse(BaseModel):
    answer: str
    retrieved_docs: int = 3
    processing_time_ms: float


class CrawlRequest(BaseModel):
    query: str = Field(..., min_length=1, max_length=500)


class CrawlResponse(BaseModel):
    status: str
    query: str
    vector_db_path: str
    twitter_count: int
    web_count: int
    total_docs: int
    total_chunks: int
    message: str


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


@router.post("/sessions", response_model=dict, status_code=201)
def create_session(
    payload: CreateSessionRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
):
    """
    Buat chat session baru, jalankan crawl, dan auto-generate artikel.

    Algorithm (ONE-CLICK):
    1. Generate session_id (UUID)
    2. Run crawler (Twitter + Google) → build ChromaDB di chroma_db/session_{id}
    3. Save ChatSession ke DB dengan vector_db_path & is_ready=True
    4. **Generate artikel otomatis** menggunakan RAG
    5. Simpan artikel sebagai pesan bot pertama
    6. Return session + article

    Args:
        payload: CreateSessionRequest dengan topic & optional title

    Returns:
        {
            "session": SessionResponse,
            "article": "markdown string artikel yang di-generate"
        }

    Raises:
        ValidationError: Topic kosong
        CrawlerError: Crawling gagal
        AIServiceError: Article generation gagal (tapi session tetap terbuat)
    """
    topic = payload.topic.strip()
    title = payload.title.strip() if payload.title else (
        topic[:80] + "…" if len(topic) > 80 else topic
    )

    logger_chat.info(f"Create session + generate article for user: {current_user.id}, topic: '{topic}'")

    try:
        # 1. Generate session_id
        session_id = str(uuid.uuid4())
        logger_chat.debug(f"Generated session_id: {session_id}")

        # 2. Run crawler — ini yang paling lama (5-15 detik)
        logger_chat.info(f"Starting crawl for topic: '{topic}'")
        crawl_result = run_crawler(query=topic, session_id=session_id)
        vector_db_path = crawl_result["vector_db_path"]
        logger_chat.info(f"Crawl done. Docs: {crawl_result['total_docs']}, Chunks: {crawl_result['total_chunks']}")

        # 3. Save session ke DB
        chat_session = create_chat_session(
            session=db,
            user_id=current_user.id,
            title=title,
            topic=topic,
            vector_db_path=vector_db_path,
        )
        sources = crawl_result.get("sources", {})
        
        summary = (
            f"Crawled {crawl_result['total_docs']} docs "
            f"({sources.get('twitter', 0)} tweets, "
            f"{sources.get('rss', 0) + sources.get('google_news', 0)} news, "
            f"{sources.get('wikipedia', 0) + sources.get('wikidata', 0)} knowledge), "
            f"{crawl_result['total_chunks']} chunks indexed."
        )

        # Mark as ready
        chat_session = update_chat_session_status(
            session=db,
            session_id=chat_session.id,
            is_ready=True,
            summary=summary,
        )

        logger_chat.info(f"Session created: {chat_session.id}. Now generating article...")

        # 4. **Generate artikel otomatis**
        try:
            article = generate_article(
                topic=topic,
                vector_db_path=vector_db_path,
            )
            logger_chat.info(f"Article generated ({len(article)} chars) for session {chat_session.id}")

            # 5. Simpan artikel sebagai pesan bot pertama ke DB
            msg_count = get_session_message_count(db, chat_session.id)
            create_chat_message(
                session=db,
                session_id=chat_session.id,
                role="bot",
                content=article,
                order=msg_count,
            )
            logger_chat.info(f"Article saved to DB for session {chat_session.id}")

        except Exception as e:
            # Jika generate gagal, tapi session sudah terbuat, log error tapi tetap return session
            logger_chat.error(f"Article generation failed for session {chat_session.id}: {e}")
            article = ""  # Return empty article, frontend bisa retry dengan /generate

        logger_chat.info(f"Session created and ready: {chat_session.id}")
        return {
            "session": chat_session,
            "article": article,
        }

    except ValidationError as e:
        raise HTTPException(status_code=400, detail=e.message)
    except CrawlerError as e:
        logger_chat.error(f"Crawler error: {e.message}")
        raise HTTPException(status_code=500, detail=e.message)
    except DatabaseError as e:
        logger_chat.error(f"DB error creating session: {e.message}")
        raise HTTPException(status_code=500, detail=e.message)
    except Exception as e:
        logger_chat.error(f"Unexpected error creating session: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Gagal membuat session baru")


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

    Tidak membutuhkan input pertanyaan dari user — topik sudah ada di ChatSession.
    Artikel disimpan ke DB sebagai pesan bot dan dikembalikan ke frontend.

    Args:
        payload: GenerateRequest dengan session_id

    Returns:
        GenerateResponse dengan article (Markdown) + metadata
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

        vector_db_path = chat_session.vector_db_path
        topic = chat_session.topic
        logger_chat.debug(f"Generating article for topic: '{topic}', ChromaDB: {vector_db_path}")

        # 2. Dapatkan urutan pesan berikutnya
        msg_count = get_session_message_count(db, payload.session_id)

        # 3. Generate artikel via RAG
        article = generate_article(
            topic=topic,
            vector_db_path=vector_db_path,
        )

        processing_time_ms = (time_module.time() - start_time) * 1000

        # 4. Simpan artikel sebagai pesan bot ke DB
        create_chat_message(
            session=db,
            session_id=payload.session_id,
            role="bot",
            content=article,
            order=msg_count,
        )

        logger_chat.info(f"Article saved. Generated in {processing_time_ms:.2f}ms")
        return GenerateResponse(
            article=article,
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