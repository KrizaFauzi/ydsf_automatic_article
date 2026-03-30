"""
Dependency injection utilities untuk FastAPI.

Dependencies:
- get_session: Dapatkan database session
- get_current_user: Validate JWT token & get current user
"""

from fastapi import Depends, Cookie
from sqlmodel import Session

from core.logger import logger_auth
from migration.base import get_session
from core.security import decode_access_token
from crud.user import get_user_by_id
from migration.models import User
from core.exceptions import AuthenticationError, InvalidTokenError, NotFoundError
from core.constants import COOKIE_KEY


def get_current_user(
    access_token: str | None = Cookie(default=None),
    session: Session = Depends(get_session),
) -> User:
    """
    Validate JWT token dari HTTPOnly cookie dan return current user.
    
    Flow:
    1. Extract access_token dari cookie
    2. Validate token (not empty, valid JWT)
    3. Extract user_id dari token
    4. Get user dari database
    5. Return user
    
    Args:
        access_token: JWT token dari HTTPOnly cookie
        session: Database session
    
    Returns:
        Current user dari database
    
    Raises:
        AuthenticationError: Token tidak ada (belum login)
        InvalidTokenError: Token invalid atau expired
        NotFoundError: User tidak ditemukan di database
    """
    logger_auth.debug("Validating JWT token...")

    # Check token exists
    if not access_token:
        logger_auth.warning("Access token is missing")
        raise AuthenticationError("Belum login")

    # Decode & validate token
    user_id = decode_access_token(access_token)
    if not user_id:
        logger_auth.warning("Invalid or expired token")
        raise InvalidTokenError("Token tidak valid atau expired")

    # Get user dari database
    user = get_user_by_id(session, user_id)
    if not user:
        logger_auth.warning(f"User not found for ID: {user_id}")
        raise NotFoundError("User tidak ditemukan")

    logger_auth.debug(f"User validated: {user.id}")
    return user