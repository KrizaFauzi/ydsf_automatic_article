"""
User Schemas untuk request/response validation.

Schemas:
- UserRegister: Registration request
- UserLogin: Login request
- UserResponse: User data response
"""

from pydantic import BaseModel, EmailStr, ConfigDict, field_validator
from datetime import datetime

from core.constants import PASSWORD_MIN_LENGTH, USERNAME_MIN_LENGTH


class UserRegister(BaseModel):
    """Request schema untuk POST /register endpoint"""

    username: str
    email: EmailStr
    password: str

    @field_validator("username", mode="before")
    @classmethod
    def validate_username(cls, v):
        """Validate username format"""
        if not v or not str(v).strip():
            raise ValueError("Username tidak boleh kosong")
        v = str(v).strip()
        if len(v) < USERNAME_MIN_LENGTH:
            raise ValueError(f"Username minimal {USERNAME_MIN_LENGTH} karakter")
        if len(v) > 100:
            raise ValueError("Username maksimal 100 karakter")
        return v

    @field_validator("password", mode="before")
    @classmethod
    def validate_password(cls, v):
        """Validate password format"""
        if not v or not str(v).strip():
            raise ValueError("Password tidak boleh kosong")
        v = str(v).strip()
        if len(v) < PASSWORD_MIN_LENGTH:
            raise ValueError(f"Password minimal {PASSWORD_MIN_LENGTH} karakter")
        return v


class UserLogin(BaseModel):
    """Request schema untuk POST /login endpoint"""

    email: EmailStr
    password: str


class UserResponse(BaseModel):
    """Response schema untuk user info endpoints"""

    model_config = ConfigDict(
        from_attributes=True,
        json_schema_extra={
            "example": {
                "id": "550e8400-e29b-41d4-a716-446655440000",
                "username": "johndoe",
                "email": "johndoe@example.com",
                "is_active": True,
                "created_at": "2024-01-15T10:30:00Z",
            }
        }
    )

    id: str
    username: str
    email: str
    is_active: bool
    created_at: datetime