"""
Standardized API response models dan utilities.
Memastikan setiap API endpoint mengembalikan format response yang konsisten.
"""

from typing import Any, Generic, Optional, TypeVar
from pydantic import BaseModel, Field
from datetime import datetime

T = TypeVar("T")


class MessageResponse(BaseModel):
    """Untuk responses yang hanya berisi message (login, logout, dll)"""
    message: str


class DataResponse(BaseModel, Generic[T]):
    """
    Standardized response dengan data.
    
    Example:
        response = DataResponse[UserResponse](
            status="success",
            message="Login berhasil",
            data=user,
            timestamp=datetime.now()
        )
    """
    status: str  # "success", "error", dll
    message: str
    data: Optional[T] = None
    timestamp: datetime = Field(default_factory=datetime.now)


class ListResponse(BaseModel, Generic[T]):
    """
    Untuk responses dengan list of data dan metadata.
    
    Example:
        response = ListResponse[UserResponse](
            status="success",
            message="Users retrieved",
            data=users,
            total=len(users),
            count=len(users)
        )
    """
    status: str
    message: str
    data: list[T]
    total: int  # total items
    count: int  # items dalam response ini
    timestamp: datetime = Field(default_factory=datetime.now)


class CreatedResponse(BaseModel, Generic[T]):
    """Response untuk POST/CREATE operations"""
    status: str = "success"
    message: str
    data: T
    timestamp: datetime = Field(default_factory=datetime.now)


class UpdatedResponse(BaseModel, Generic[T]):
    """Response untuk PUT/PATCH operations"""
    status: str = "success"
    message: str
    data: T
    timestamp: datetime = Field(default_factory=datetime.now)


class DeletedResponse(BaseModel):
    """Response untuk DELETE operations"""
    status: str = "success"
    message: str
    timestamp: datetime = Field(default_factory=datetime.now)


class ErrorResponse(BaseModel):
    """
    Standardized error response.
    
    Example:
        response = ErrorResponse(
            status="error",
            error="VALIDATION_ERROR",
            message="Email atau password salah",
            details={"field": "email"}
        )
    """
    status: str = "error"
    error: str
    message: str
    details: Optional[dict[str, Any]] = None
    timestamp: datetime = Field(default_factory=datetime.now)


# ─── Specialized Responses ────────────────────────────────────────────────────

class ChatAskResponse(BaseModel):
    """Response untuk /api/chat/ask endpoint"""
    answer: str
    retrieved_docs: int = 3  # jumlah dokumen yang digunakan
    processing_time_ms: Optional[float] = None


class CrawlResponse(BaseModel):
    """Response untuk /api/chat/crawl endpoint"""
    status: str = "success"
    query: str
    vector_db_path: str
    twitter_count: int
    web_count: int
    total_docs: int
    message: str = "Crawl dan indexing berhasil"
    timestamp: datetime = Field(default_factory=datetime.now)


class MigrationResponse(BaseModel):
    """Response untuk /migrate endpoint"""
    status: str
    message: str
    tables: list[str]
    timestamp: datetime = Field(default_factory=datetime.now)


# ─── Helper functions ─────────────────────────────────────────────────────────

def success_response(
    message: str,
    data: Optional[Any] = None,
    status: str = "success"
) -> dict[str, Any]:
    """
    Helper untuk membuat success response.
    
    Args:
        message: Success message
        data: Data yang akan dikembalikan
        status: Response status (default: "success")
    
    Returns:
        Dictionary response yang siap dikembalikan dari endpoint
    """
    response = {
        "status": status,
        "message": message,
        "timestamp": datetime.now().isoformat(),
    }
    if data is not None:
        response["data"] = data
    return response


def error_response(
    error: str,
    message: str,
    status_code: int = 500,
    details: Optional[dict] = None
) -> dict[str, Any]:
    """
    Helper untuk membuat error response.
    
    Args:
        error: Error code/type
        message: Error message
        status_code: HTTP status code
        details: Additional error details
    
    Returns:
        Dictionary response yang siap dikembalikan dari endpoint
    """
    response = {
        "status": "error",
        "error": error,
        "message": message,
        "timestamp": datetime.now().isoformat(),
    }
    if details:
        response["details"] = details
    return response, status_code
