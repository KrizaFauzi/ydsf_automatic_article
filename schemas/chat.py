from typing import Optional, List
from pydantic import BaseModel, Field

class ArticleConfig(BaseModel):
    """
    Konfigurasi untuk generator artikel tingkat lanjut.
    """
    topic: str = Field(..., description="Topik utama artikel")
    additional_info: Optional[str] = Field(None, description="Informasi tambahan atau konteks dari user")
    key_points: Optional[List[str]] = Field(None, description="Poin-poin spesifik yang harus ada dalam artikel")
    length: str = Field("medium", description="Panjang artikel: 'short', 'medium', 'long'")
    seo_keywords: Optional[List[str]] = Field(None, description="Keywords SEO untuk dioptimasi")
    model_choice: Optional[str] = Field(None, description="Model LLM yang digunakan (misal dari Groq)")
    use_crawling: bool = Field(True, description="Apakah akan melakukan crawling data dari internet")

class ChatRequest(BaseModel):
    """Request schema untuk chat ask."""
    question: str
    session_id: str

class ChatResponse(BaseModel):
    """Response schema untuk chat message."""
    content: str
    session_id: str
    role: str = "bot"
