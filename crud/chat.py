"""
Chat CRUD Operations.

Operations:
- Create chat session
- Get chat session
- Create chat message
- Get chat messages untuk session
- Update chat session status
"""

from sqlmodel import Session, select
from datetime import datetime, timezone

from core.logger import logger_crud
from core.exceptions import NotFoundError, DatabaseError
from migration.models import ChatSession, ChatMessage, ArticleSource


# ─── ChatSession CRUD ────────────────────────────────────────────────────────


def create_chat_session(
    session: Session,
    user_id: str,
    title: str,
    topic: str,
    vector_db_path: str,
) -> ChatSession:
    """
    Create new chat session.
    
    Args:
        session: Database session
        user_id: User ID yang membuat session
        title: Chat session title
        topic: Topic yang di-crawl
        vector_db_path: Path ke ChromaDB untuk session ini
    
    Returns:
        ChatSession yang baru dibuat
    
    Raises:
        DatabaseError: Jika gagal create
    """
    try:
        chat_session = ChatSession(
            user_id=user_id,
            title=title,
            topic=topic,
            vector_db_path=vector_db_path,
            is_ready=False,  # Default belum siap sampai crawl selesai
        )
        session.add(chat_session)
        session.commit()
        session.refresh(chat_session)

        logger_crud.info(f"Chat session created: {chat_session.id}")
        return chat_session

    except Exception as e:
        logger_crud.error(f"Error creating chat session: {e}", exc_info=True)
        session.rollback()
        raise DatabaseError(f"Gagal create chat session: {str(e)}")


def get_chat_session(session: Session, session_id: str) -> ChatSession:
    """
    Get chat session by ID.
    
    Args:
        session: Database session
        session_id: Chat session ID
    
    Returns:
        ChatSession
    
    Raises:
        NotFoundError: Jika session tidak ditemukan
    """
    try:
        chat_session = session.exec(
            select(ChatSession).where(ChatSession.id == session_id)
        ).first()

        if not chat_session:
            logger_crud.warning(f"Chat session not found: {session_id}")
            raise NotFoundError(f"Chat session tidak ditemukan: {session_id}")

        return chat_session

    except NotFoundError:
        raise
    except Exception as e:
        logger_crud.error(f"Error getting chat session: {e}", exc_info=True)
        raise DatabaseError(f"Gagal get chat session: {str(e)}")


def get_user_chat_sessions(
    session: Session,
    user_id: str,
    limit: int = 20,
    skip: int = 0,
) -> list[ChatSession]:
    """
    Get all chat sessions untuk user tertentu.
    
    Args:
        session: Database session
        user_id: User ID
        limit: Jumlah hasil maksimal
        skip: Jumlah hasil untuk skip (untuk pagination)
    
    Returns:
        List of ChatSession, ordered by newest first
    """
    try:
        sessions = session.exec(
            select(ChatSession)
            .where(ChatSession.user_id == user_id)
            .order_by(ChatSession.created_at.desc())
            .offset(skip)
            .limit(limit)
        ).all()

        logger_crud.debug(f"Found {len(sessions)} chat sessions for user: {user_id}")
        return sessions

    except Exception as e:
        logger_crud.error(f"Error getting user chat sessions: {e}", exc_info=True)
        raise DatabaseError(f"Gagal get chat sessions: {str(e)}")


def update_chat_session_status(
    session: Session,
    session_id: str,
    is_ready: bool = True,
    summary: str | None = None,
) -> ChatSession:
    """
    Update chat session status (mark as ready setelah crawler selesai).

    Args:
        session: Database session
        session_id: Chat session ID
        is_ready: Apakah session sudah siap (crawler selesai)
        summary: Optional summary dari crawled content

    Returns:
        Updated ChatSession

    Raises:
        NotFoundError: Jika session tidak ditemukan
    """
    try:
        chat_session = get_chat_session(session, session_id)
        chat_session.is_ready = is_ready
        if summary:
            chat_session.summary = summary
        chat_session.updated_at = datetime.now(timezone.utc)

        session.add(chat_session)
        session.commit()
        session.refresh(chat_session)

        logger_crud.info(f"Chat session status updated: {session_id}, ready={is_ready}")
        return chat_session

    except NotFoundError:
        raise
    except Exception as e:
        logger_crud.error(f"Error updating chat session: {e}", exc_info=True)
        session.rollback()
        raise DatabaseError(f"Gagal update chat session: {str(e)}")


def update_session_paths(
    session: Session,
    session_id: str,
    vector_db_path: str,
    summary: str | None = None,
) -> ChatSession:
    """
    Update vector_db_path setelah crawl selesai di background.

    Args:
        session: Database session
        session_id: Chat session ID
        vector_db_path: Path ChromaDB yang sudah selesai di-build
        summary: Optional crawl summary

    Returns:
        Updated ChatSession
    """
    try:
        chat_session = get_chat_session(session, session_id)
        chat_session.vector_db_path = vector_db_path
        if summary:
            chat_session.summary = summary
        chat_session.updated_at = datetime.now(timezone.utc)

        session.add(chat_session)
        session.commit()
        session.refresh(chat_session)

        logger_crud.info(f"Session paths updated: {session_id} → {vector_db_path}")
        return chat_session

    except NotFoundError:
        raise
    except Exception as e:
        logger_crud.error(f"Error updating session paths: {e}", exc_info=True)
        session.rollback()
        raise DatabaseError(f"Gagal update session paths: {str(e)}")


def set_session_ready(
    session: Session,
    session_id: str,
) -> ChatSession:
    """
    Tandai session sebagai ready (background task selesai sukses).
    """
    try:
        chat_session = get_chat_session(session, session_id)
        chat_session.is_ready = True
        chat_session.processing_status = "ready"
        chat_session.updated_at = datetime.now(timezone.utc)

        session.add(chat_session)
        session.commit()
        session.refresh(chat_session)

        logger_crud.info(f"Session marked ready: {session_id}")
        return chat_session

    except NotFoundError:
        raise
    except Exception as e:
        logger_crud.error(f"Error marking session ready: {e}", exc_info=True)
        session.rollback()
        raise DatabaseError(f"Gagal mark session ready: {str(e)}")


def set_session_error(
    session: Session,
    session_id: str,
    error_message: str,
) -> ChatSession:
    """
    Simpan error ke session jika background task gagal.

    Args:
        session: Database session
        session_id: Chat session ID
        error_message: Pesan error yang terjadi

    Returns:
        Updated ChatSession
    """
    try:
        chat_session = get_chat_session(session, session_id)
        chat_session.processing_status = "error"
        chat_session.error_message = error_message
        chat_session.updated_at = datetime.now(timezone.utc)

        session.add(chat_session)
        session.commit()
        session.refresh(chat_session)

        logger_crud.info(f"Session error set: {session_id} — {error_message[:80]}")
        return chat_session

    except NotFoundError:
        raise
    except Exception as e:
        logger_crud.error(f"Error setting session error: {e}", exc_info=True)
        session.rollback()
        raise DatabaseError(f"Gagal set session error: {str(e)}")


# ─── ChatMessage CRUD ────────────────────────────────────────────────────────


def create_chat_message(
    session: Session,
    session_id: str,
    role: str,  # "user" or "bot"
    content: str,
    order: int = 0,
) -> ChatMessage:
    """
    Create new chat message.
    
    Args:
        session: Database session
        session_id: Chat session ID (foreign key)
        role: "user" atau "bot"
        content: Message content
        order: Message order dalam session (untuk sorting)
    
    Returns:
        ChatMessage yang baru dibuat
    
    Raises:
        DatabaseError: Jika gagal create
    """
    try:
        message = ChatMessage(
            session_id=session_id,
            role=role,
            content=content,
            order=order,
        )
        session.add(message)
        session.commit()
        session.refresh(message)

        logger_crud.debug(f"Chat message created for session: {session_id}")
        return message

    except Exception as e:
        logger_crud.error(f"Error creating chat message: {e}", exc_info=True)
        session.rollback()
        raise DatabaseError(f"Gagal create chat message: {str(e)}")


def get_chat_messages(
    session: Session,
    session_id: str,
    limit: int = 50,
    skip: int = 0,
) -> list[ChatMessage]:
    """
    Get all chat messages untuk session, ordered by creation time.
    
    Args:
        session: Database session
        session_id: Chat session ID
        limit: Jumlah hasil maksimal
        skip: Jumlah hasil untuk skip (untuk pagination)
    
    Returns:
        List of ChatMessage, ordered by oldest first
    """
    try:
        messages = session.exec(
            select(ChatMessage)
            .where(ChatMessage.session_id == session_id)
            .order_by(ChatMessage.order)
            .offset(skip)
            .limit(limit)
        ).all()

        logger_crud.debug(f"Found {len(messages)} messages for session: {session_id}")
        return messages

    except Exception as e:
        logger_crud.error(f"Error getting chat messages: {e}", exc_info=True)
        raise DatabaseError(f"Gagal get chat messages: {str(e)}")


def get_latest_chat_messages(
    session: Session,
    session_id: str,
    limit: int = 10,
) -> list[ChatMessage]:
    """
    Get latest chat messages untuk session (untuk context).
    
    Args:
        session: Database session
        session_id: Chat session ID
        limit: Jumlah pesan terbaru yang diambil
    
    Returns:
        List of latest ChatMessage
    """
    try:
        # Get total count
        total = session.exec(
            select(ChatMessage).where(ChatMessage.session_id == session_id)
        ).all()

        # Get last N messages
        skip = max(0, len(total) - limit)
        messages = session.exec(
            select(ChatMessage)
            .where(ChatMessage.session_id == session_id)
            .order_by(ChatMessage.order)
            .offset(skip)
            .limit(limit)
        ).all()

        logger_crud.debug(f"Retrieved {len(messages)} latest messages for session: {session_id}")
        return messages

    except Exception as e:
        logger_crud.error(f"Error getting latest chat messages: {e}", exc_info=True)
        raise DatabaseError(f"Gagal get latest messages: {str(e)}")


def get_session_message_count(session: Session, session_id: str) -> int:
    """
    Get total message count untuk session (untuk ordering).
    
    Args:
        session: Database session
        session_id: Chat session ID
    
    Returns:
        Total message count
    """
    try:
        count = len(
            session.exec(
                select(ChatMessage).where(ChatMessage.session_id == session_id)
            ).all()
        )
        return count
    except Exception as e:
        logger_crud.error(f"Error getting message count: {e}", exc_info=True)
        return 0


# ─── ArticleSource CRUD ──────────────────────────────────────────────────────


def create_article_source(
    session: Session,
    session_id: str,
    judul: str,
    sumber: str,
    url: str,
    order: int = 0,
) -> ArticleSource:
    """
    Create new article source reference.
    
    Args:
        session: Database session
        session_id: Chat session ID (foreign key)
        judul: Judul artikel/post dari sumber
        sumber: Nama sumber (Wikipedia, Twitter, GDELT, NewsAPI, dll)
        url: URL artikel/post
        order: Urutan dalam daftar sources (untuk sorting)
    
    Returns:
        ArticleSource yang baru dibuat
    
    Raises:
        DatabaseError: Jika gagal create
    """
    try:
        source = ArticleSource(
            session_id=session_id,
            judul=judul,
            sumber=sumber,
            url=url,
            order=order,
        )
        session.add(source)
        session.commit()
        session.refresh(source)

        logger_crud.debug(f"Article source created: {sumber} ({url[:50]})")
        return source

    except Exception as e:
        logger_crud.error(f"Error creating article source: {e}", exc_info=True)
        session.rollback()
        raise DatabaseError(f"Gagal create article source: {str(e)}")


def create_article_sources_batch(
    session: Session,
    session_id: str,
    sources: list[dict],
) -> list[ArticleSource]:
    """
    Create multiple article sources sekaligus (batch).
    
    Args:
        session: Database session
        session_id: Chat session ID (foreign key)
        sources: list[dict] dengan structure:
                 [{"judul": str, "sumber": str, "url": str}, ...]
    
    Returns:
        List of created ArticleSource objects
    
    Raises:
        DatabaseError: Jika gagal create
    """
    if not sources:
        return []
    
    try:
        created_sources = []
        for order, src in enumerate(sources):
            source = ArticleSource(
                session_id=session_id,
                judul=src.get("judul", "Untitled"),
                sumber=src.get("sumber", "Unknown"),
                url=src.get("url", ""),
                order=order,
            )
            session.add(source)
            created_sources.append(source)
        
        session.commit()
        for source in created_sources:
            session.refresh(source)

        logger_crud.info(f"Created {len(created_sources)} article sources for session: {session_id}")
        return created_sources

    except Exception as e:
        logger_crud.error(f"Error creating article sources batch: {e}", exc_info=True)
        session.rollback()
        raise DatabaseError(f"Gagal create article sources: {str(e)}")


def get_article_sources(
    session: Session,
    session_id: str,
) -> list[ArticleSource]:
    """
    Get all article sources untuk session, ordered by order.
    
    Args:
        session: Database session
        session_id: Chat session ID
    
    Returns:
        List of ArticleSource, ordered by order
    """
    try:
        sources = session.exec(
            select(ArticleSource)
            .where(ArticleSource.session_id == session_id)
            .order_by(ArticleSource.order)
        ).all()

        logger_crud.debug(f"Found {len(sources)} article sources for session: {session_id}")
        return sources

    except Exception as e:
        logger_crud.error(f"Error getting article sources: {e}", exc_info=True)
        raise DatabaseError(f"Gagal get article sources: {str(e)}")
