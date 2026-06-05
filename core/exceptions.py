"""
Custom exceptions untuk aplikasi.
Membuat error handling lebih terstruktur dan mudah di-debug.
"""

from typing import Any, Optional


class AppException(Exception):
    """Base exception untuk semua aplikasi exceptions"""
    
    def __init__(
        self,
        message: str,
        status_code: int = 500,
        error_code: Optional[str] = None,
        details: Optional[dict[str, Any]] = None
    ):
        self.message = message
        self.status_code = status_code
        self.error_code = error_code or self.__class__.__name__
        self.details = details or {}
        super().__init__(self.message)

    def to_dict(self) -> dict[str, Any]:
        """Convert exception ke dictionary untuk API response"""
        return {
            "error": self.error_code,
            "message": self.message,
            "details": self.details,
        }


# ─── Authentication Exceptions ────────────────────────────────────────────────

class AuthenticationError(AppException):
    """Saat user tidak terauthorisasi/tidak login"""
    def __init__(self, message: str = "Belum login", details: Optional[dict] = None):
        super().__init__(message, status_code=401, error_code="AUTHENTICATION_ERROR", details=details)


class InvalidTokenError(AppException):
    """Saat JWT token tidak valid atau expired"""
    def __init__(self, message: str = "Token tidak valid atau expired", details: Optional[dict] = None):
        super().__init__(message, status_code=401, error_code="INVALID_TOKEN", details=details)


class InvalidCredentialsError(AppException):
    """Saat email atau password salah"""
    def __init__(self, message: str = "Email atau password salah", details: Optional[dict] = None):
        super().__init__(message, status_code=401, error_code="INVALID_CREDENTIALS", details=details)


# ─── Validation Exceptions ───────────────────────────────────────────────────

class ValidationError(AppException):
    """Saat input validation gagal"""
    def __init__(self, message: str, details: Optional[dict] = None):
        super().__init__(message, status_code=400, error_code="VALIDATION_ERROR", details=details)


class DuplicateError(AppException):
    """Saat data sudah ada (email registered, dll)"""
    def __init__(self, message: str = "Data sudah ada", details: Optional[dict] = None):
        super().__init__(message, status_code=409, error_code="DUPLICATE_ERROR", details=details)


# ─── Resource Exceptions ─────────────────────────────────────────────────────

class NotFoundError(AppException):
    """Saat resource tidak ditemukan"""
    def __init__(self, message: str = "Resource tidak ditemukan", details: Optional[dict] = None):
        super().__init__(message, status_code=404, error_code="NOT_FOUND", details=details)


class VectorDBNotFoundError(AppException):
    """Saat ChromaDB untuk sesi tidak ada"""
    def __init__(self, path: str):
        details = {"path": path}
        super().__init__(
            f"ChromaDB tidak ditemukan di path: {path}",
            status_code=404,
            error_code="VECTOR_DB_NOT_FOUND",
            details=details
        )


# ─── Service Exceptions ──────────────────────────────────────────────────────

class AIServiceError(AppException):
    """Saat LLM service error"""
    def __init__(self, message: str, details: Optional[dict] = None):
        super().__init__(message, status_code=500, error_code="AI_SERVICE_ERROR", details=details)


class CrawlerError(AppException):
    """Saat crawler error"""
    def __init__(self, message: str, details: Optional[dict] = None):
        super().__init__(message, status_code=500, error_code="CRAWLER_ERROR", details=details)


class LLMInitializationError(AppException):
    """Saat LLM gagal initialize"""
    def __init__(self, model: str, error: str):
        details = {"model": model, "error": error}
        super().__init__(
            f"Gagal initialize LLM '{model}'",
            status_code=500,
            error_code="LLM_INIT_ERROR",
            details=details
        )


class APICallError(AppException):
    """Saat external API call gagal"""
    def __init__(self, api_name: str, status_code: int, details: Optional[dict] = None):
        d = details or {}
        d.update({"api": api_name, "status_code": status_code})
        super().__init__(
            f"External API '{api_name}' gagal dengan status {status_code}",
            status_code=500,
            error_code="API_CALL_ERROR",
            details=d
        )


# ─── Database Exceptions ────────────────────────────────────────────────────

class DatabaseError(AppException):
    """Saat operasi database gagal. Message user-friendly, detail technical di details."""
    def __init__(self, message: str = "Gagal memproses permintaan ke database", details: Optional[dict] = None):
        # Keamanan: Jangan leak raw DB error ke 'message' jika possible
        super().__init__(message, status_code=500, error_code="DATABASE_ERROR", details=details)
