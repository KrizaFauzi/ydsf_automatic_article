

from sqlmodel import Session, select

from core.logger import logger_crud
from core.exceptions import DatabaseError
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
        raise DatabaseError(f"Gagal get user by email: {str(e)}")


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
        raise DatabaseError(f"Gagal get user by ID: {str(e)}")


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
        
    except Exception as e:
        logger_crud.error(f"Error creating user '{email}': {e}", exc_info=True)
        session.rollback()
        raise DatabaseError(f"Gagal create user: {str(e)}")