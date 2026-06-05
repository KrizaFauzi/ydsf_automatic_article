

from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from core.logger import logger_crud
from core.exceptions import DatabaseError, DuplicateError
from migration.models import User
from core.security import hash_password


def get_user_by_email(session: Session, email: str) -> User | None:
    """
    Get user by email address.
    
    Args:
        session: Database session
        email: Email address to search for
    
    Returns:
        User object if found, None otherwise
    
    Raises:
        DatabaseError: If database query fails
    """
    try:
        user = session.exec(
            select(User).where(User.email == email)
        ).first()
        return user
    except Exception as e:
        logger_crud.error(f"Error getting user by email '{email}': {e}", exc_info=True)
        raise DatabaseError("Gagal mengambil data user berdasarkan email", details={"error": str(e)})


def get_user_by_username(session: Session, username: str) -> User | None:
    """
    Get user by username.

    Args:
        session: Database session
        username: Username to search for

    Returns:
        User object if found, None otherwise

    Raises:
        DatabaseError: If database query fails
    """
    try:
        user = session.exec(
            select(User).where(User.username == username)
        ).first()
        return user
    except Exception as e:
        logger_crud.error(f"Error getting user by username '{username}': {e}", exc_info=True)
        raise DatabaseError("Gagal mengambil data user berdasarkan username", details={"error": str(e)})


def get_user_by_id(session: Session, user_id: str) -> User | None:
    """
    Get user by ID (UUID).
    
    Args:
        session: Database session
        user_id: User ID (UUID string)
    
    Returns:
        User object if found, None otherwise
    
    Raises:
        DatabaseError: If database query fails
    """
    try:
        user = session.exec(
            select(User).where(User.id == user_id)
        ).first()
        return user
    except Exception as e:
        logger_crud.error(f"Error getting user by ID '{user_id}': {e}", exc_info=True)
        raise DatabaseError("Gagal mengambil data user berdasarkan ID", details={"error": str(e)})


def create_user(
    session: Session,
    username: str,
    email: str,
    password: str,
) -> User:
    """
    Create new user dengan password hashing.
    
    Args:
        session: Database session
        username: Username (unique)
        email: Email address (unique)
        password: Plain text password (akan di-hash dengan bcrypt)
    
    Returns:
        Created User object
    
    Raises:
        DatabaseError: If user creation fails
    """
    try:
        logger_crud.debug(f"Creating user: {email}")
        
        user = User(
            username=username,
            email=email,
            hashed_password=hash_password(password),
        )
        
        session.add(user)
        session.commit()
        session.refresh(user)
        
        logger_crud.info(f"User created successfully: {email}")
        return user
        
    except IntegrityError as e:
        session.rollback()
        err_str = str(e.orig) if hasattr(e, 'orig') else str(e)
        logger_crud.error(f"IntegrityError creating user '{email}': {err_str}")
        if "username" in err_str.lower():
            raise DuplicateError("Username sudah digunakan", {"username": username})
        raise DuplicateError("Email sudah terdaftar", {"email": email})
    except Exception as e:
        logger_crud.error(f"Error creating user '{email}': {e}", exc_info=True)
        session.rollback()
        raise DatabaseError("Gagal membuat user baru", details={"error": str(e)})