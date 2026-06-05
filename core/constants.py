"""
Centralized constants untuk seluruh aplikasi.
Memastikan konsistensi nilai yang digunakan di berbagai modul.
"""

# ─── Security ─────────────────────────────────────────────────────────────────
PASSWORD_MIN_LENGTH = 8
PASSWORD_MAX_LENGTH = 72  # bcrypt limitation
JWT_ALGORITHM = "HS256"
DEFAULT_ACCESS_TOKEN_EXPIRE_MINUTES = 60 * 24  # 1 hari
COOKIE_KEY = "access_token"
COOKIE_MAX_AGE_SECONDS = 60 * 60 * 24  # 1 hari

# ─── Validation ───────────────────────────────────────────────────────────────
USERNAME_MIN_LENGTH = 3
USERNAME_MAX_LENGTH = 100
EMAIL_MAX_LENGTH = 255
CHAT_QUESTION_MIN_LENGTH = 3
CHAT_QUESTION_MAX_LENGTH = 5000
CRAWL_QUERY_MIN_LENGTH = 1
CRAWL_QUERY_MAX_LENGTH = 500

# ─── API Limits ───────────────────────────────────────────────────────────────
RAW_API_LIMIT = 50  # untuk Twitter & Google Search
VECTOR_RETRIEVAL_K = 3  # jumlah dokumen yang di-retrieve
CHUNK_SIZE = 500  # ukuran chunk untuk text splitter
CHUNK_OVERLAP = 50  # overlap antar chunks

# ─── Paths ────────────────────────────────────────────────────────────────────
VECTOR_DB_BASE_PATH = "chroma_db"
CSV_DATA_DIR = "."  # direktori untuk save CSV

# ─── LLM & Embeddings ─────────────────────────────────────────────────────────
DEFAULT_LLM_MODEL = "llama-3.3-70b-versatile"
DEFAULT_LLM_MODEL_FAST = "llama-3.1-8b-instant"  # Lebih cepat & murah untuk query expansion
DEFAULT_EMBED_MODEL = "intfloat/multilingual-e5-large"
LLM_TEMPERATURE = 0  # deterministik, tidak kreatif

# ─── External APIs ────────────────────────────────────────────────────────────
TWITTER_API_URL = "https://twitter-x.p.rapidapi.com/search/"
TWITTER_API_HOST = "twitter-x.p.rapidapi.com"
TWITTER_SEARCH_PARAMS = {
    "section": "latest",
    "limit": RAW_API_LIMIT,
}

GOOGLE_SEARCH_API_URL = "https://google-search74.p.rapidapi.com/"
GOOGLE_SEARCH_API_HOST = "google-search74.p.rapidapi.com"
GOOGLE_SEARCH_PARAMS = {
    "limit": RAW_API_LIMIT,
    "related_keywords": "true",
}

# ─── Error Messages ───────────────────────────────────────────────────────────
ERROR_MSG_EMPTY_QUESTION = "Pertanyaan tidak boleh kosong"
ERROR_MSG_EMPTY_QUERY = "Query tidak boleh kosong"
ERROR_MSG_EMAIL_REGISTERED = "Email sudah terdaftar"
ERROR_MSG_INVALID_CREDENTIALS = "Email atau password salah"
ERROR_MSG_NOT_LOGGED_IN = "Belum login"
ERROR_MSG_TOKEN_INVALID = "Token tidak valid atau expired"
ERROR_MSG_USER_NOT_FOUND = "User tidak ditemukan"
ERROR_MSG_VECTOR_DB_NOT_FOUND = "ChromaDB tidak ditemukan untuk sesi ini"
ERROR_MSG_LLM_FAILED = "Gagal generate jawaban dari LLM"
ERROR_MSG_CRAWLER_FAILED = "Crawler error"

# ─── Success Messages ─────────────────────────────────────────────────────────
SUCCESS_MSG_REGISTER = "Registrasi berhasil"
SUCCESS_MSG_LOGIN = "Login berhasil"
SUCCESS_MSG_LOGOUT = "Logout berhasil"
SUCCESS_MSG_MIGRATION = "Semua tabel berhasil dibuat di MariaDB"
SUCCESS_MSG_CRAWL = "Crawl berhasil"

# ─── HTTP Status Codes ────────────────────────────────────────────────────────
HTTP_STATUS_OK = 200
HTTP_STATUS_CREATED = 201
HTTP_STATUS_BAD_REQUEST = 400
HTTP_STATUS_UNAUTHORIZED = 401
HTTP_STATUS_NOT_FOUND = 404
HTTP_STATUS_CONFLICT = 409
HTTP_STATUS_INTERNAL_ERROR = 500
