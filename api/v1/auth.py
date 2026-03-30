"""
Authentication API Routes.

Routes:
- POST /register: Register user baru
- POST /login: Login dan dapatkan JWT token
- POST /logout: Logout
- GET /me: Get current user info

Flow:
1. Register: Validate input → Check email exists → Hash password → Save to DB
2. Login: Verify email → Verify password → Create JWT → Set HTTPOnly cookie
3. Logout: Delete cookie
4. Me: Validate JWT from cookie → Get user from DB
"""

from fastapi import APIRouter, Depends, Response
from sqlmodel import Session

from core.logger import logger_auth
from core.deps import get_current_user
from core.security import create_access_token, verify_password
from core.exceptions import (
    DuplicateError,
    InvalidCredentialsError,
    ValidationError,
)
from core.constants import (
    COOKIE_KEY,
    COOKIE_MAX_AGE_SECONDS,
    ERROR_MSG_EMAIL_REGISTERED,
    ERROR_MSG_INVALID_CREDENTIALS,
    SUCCESS_MSG_REGISTER,
    SUCCESS_MSG_LOGIN,
    SUCCESS_MSG_LOGOUT,
)
from core.responses import success_response, error_response
from crud.user import create_user, get_user_by_email
from migration.base import get_session
from migration import models
from schemas.user import UserLogin, UserRegister, UserResponse

router = APIRouter()


@router.post("/register", response_model=UserResponse, status_code=201)
def register(
    payload: UserRegister,
    session: Session = Depends(get_session),
):
    """
    Register user baru.
    
    Args:
        payload: UserRegister dengan username, email, password
        session: Database session
    
    Returns:
        UserResponse dengan user data
    
    Raises:
        ValidationError: Input validation gagal
        DuplicateError: Email sudah terdaftar
    """
    logger_auth.info(f"Register attempt for email: {payload.email}")

    try:
        # Check if email already registered
        existing = get_user_by_email(session, payload.email)
        if existing:
            logger_auth.warning(f"Email already registered: {payload.email}")
            raise DuplicateError(ERROR_MSG_EMAIL_REGISTERED, {"email": payload.email})

        # Create user
        user = create_user(
            session=session,
            username=payload.username,
            email=payload.email,
            password=payload.password,
        )

        logger_auth.info(f"User registered successfully: {payload.email}")
        return user

    except (ValidationError, DuplicateError):
        raise
    except Exception as e:
        logger_auth.error(f"Register error: {e}", exc_info=True)
        raise


@router.post("/login")
def login(
    payload: UserLogin,
    response: Response,
    session: Session = Depends(get_session),
):
    """
    Login dan dapatkan JWT token via HTTPOnly cookie.
    
    Args:
        payload: UserLogin dengan email, password
        response: FastAPI Response untuk set cookie
        session: Database session
    
    Returns:
        Success message dengan username
    
    Raises:
        InvalidCredentialsError: Email atau password salah
    """
    logger_auth.info(f"Login attempt for email: {payload.email}")

    try:
        # Get user by email
        user = get_user_by_email(session, payload.email)

        # Validate user exists & password correct
        if not user or not verify_password(payload.password, user.hashed_password):
            logger_auth.warning(f"Invalid login attempt for: {payload.email}")
            raise InvalidCredentialsError(ERROR_MSG_INVALID_CREDENTIALS)

        # Create JWT token
        token = create_access_token(user.id)

        # Set HTTPOnly cookie
        response.set_cookie(
            key=COOKIE_KEY,
            value=token,
            httponly=True,  # Prevent JavaScript access (XSS protection)
            max_age=COOKIE_MAX_AGE_SECONDS,
            samesite="lax",  # CSRF protection
        )

        logger_auth.info(f"User logged in successfully: {payload.email}")
        return success_response(
            message=SUCCESS_MSG_LOGIN,
            data={"username": user.username},
        )

    except (ValidationError, InvalidCredentialsError):
        raise
    except Exception as e:
        logger_auth.error(f"Login error: {e}", exc_info=True)
        raise


@router.post("/logout")
def logout(response: Response):
    """
    Logout dan delete HTTPOnly cookie.
    
    Args:
        response: FastAPI Response untuk delete cookie
    
    Returns:
        Success message
    """
    logger_auth.info("Logout attempt")
    response.delete_cookie(key=COOKIE_KEY)
    logger_auth.info("User logged out")
    return success_response(message=SUCCESS_MSG_LOGOUT)


@router.get("/me", response_model=UserResponse)
def me(current_user: models.User = Depends(get_current_user)):
    """
    Get current user info dari JWT token di cookie.
    
    Args:
        current_user: Validated user dari dependency
    
    Returns:
        UserResponse dengan user data
    """
    logger_auth.debug(f"Getting current user: {current_user.id}")
    return current_user