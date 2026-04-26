# YDSF AI Article Chatbot

YDSF Automatic Article adalah sebuah aplikasi berbasis web yang menyediakan layanan pembuatan artikel secara otomatis dengan dukungan teknologi AI berserta *Retrieval-Augmented Generation* (RAG).

Aplikasi ini menggunakan FastAPI untuk arsitektur *backend* dan memungkinkan pencarian informasi tambahan melalui fitur *crawling* otomatis terhadap layanan pihak ketiga.

## 🚀 Fitur Utama

- **User Authentication**: Sistem registrasi, login, dan logout yang aman dengan enkripsi `bcrypt` dan JSON Web Tokens (JWT).
- **AI-powered Q&A & Article Generation**: Membuat artikel maupun melayani tanya jawab pintar dengan mengandalkan integrasi LangChain dan model LLM dari Groq.
- **RAG (Retrieval-Augmented Generation)**: Pencarian informasi berbasis vektor (*similarity search*) dari dokumen atau artikel menggunakan **ChromaDB**. Text-embeddings dikelola secara mandiri maupun melalui HuggingFace.
- **Data Crawling**: Fitur _scraping_ intelijen dari Web Search & Twitter menggunakan **RapidAPI**, disiapkan untuk memperkuat *knowledge base* dari chatbot.
- **Database Management & Migration**: Integrasi MariaDB/MySQL dengan kemudahan skema data dan migrasi menggunakan SQLModel/SQLAlchemy.
- **Modern Backend API**: Menggunakan kerangka kerja FastAPI yang kencang dan handal guna melayani halaman web serta *endpoint* antarmuka.

## 🛠️ Stack Teknologi

- **Backend Framework**: `FastAPI`, `Uvicorn`
- **Database ORM**: `SQLModel`, `SQLAlchemy`, `PyMySQL` (MariaDB/MySQL)
- **Vector Database**: `ChromaDB`
- **AI / LLM Orchestration**: `LangChain`, `Groq API`, `HuggingFace Transformers`
- **Data Extractor**: `praw`, `feedparser`, `RapidAPI`
- **Frontend Engine**: `Jinja2` (Template HTML)

## 📁 Struktur Direktori Utama

- `/api` - Berisi modul-modul routing dari FastAPI (seperti `/auth` dan `/chat`).
- `/core` - Konfigurasi utama seperti pengaturan dasar (settings), logger, pembuatan token keamanan, serta exception handling.
- `/crud` - Logika penanganan CRUD (Create, Read, Update, Delete) terkait interaksi dengan basis data.
- `/schemas` - Model validasi Pydantic / kontrak antar *request* dan *response*.
- `/services` - Berisi _business logic_ atau servis pengelola interaksi dengan fitur utama (ex: Chat service, Vector Search, Crawler API).
- `/migration` - Skrip & engine untuk inisialisasi awal (*create tables*) database.
- `/chroma_db` - Media penyimpanan lokal untuk Vector Database (ter-generate secara otomatis saat proses embedding).
- `/templates` & `/static` - Sumber utama untuk tampilan UI/UX aplikasi (File JS, CSS, dan HTML).

## ⚙️ Petunjuk Instalasi

Aplikasi ini telah direkomendasikan & berjalan di atas **Python 3.10.x**. 

1. **Clone repositori ini atau arahkan ke direktori Anda:**
   ```bash
   cd "Automatic Article"
   ```

2. **Buat dan Jalankan Virtual Environment (.venv)**
   ```bash
   # Di sistem Operasi Windows
   python -m venv .venv
   .venv\Scripts\activate
   
   # Di sistem Operasi Linux/Mac
   python3 -m venv .venv
   source .venv/bin/activate
   ```

3. **Instal seluruh Dependensi Proyek**
   Menghosting semua library dan package yang dibutuhkan.
   ```bash
   pip install -r requirements.txt
   ```

4. **Konfigurasi Environment Variable**
   Atur file `.env` yang berada di folder root untuk menghubungkan database ke MariaDB/MySQL, API Groq, RapidAPI, dll.

## 🏃‍♀️ Cara Menjalankan Aplikasi

1. **Mulai Server FastAPI**
   Jalankan perintah uvicorn di command line.
   ```bash
   uvicorn main:app --reload
   ```
   > Aplikasi akan berjalan di `http://127.0.0.1:8000/`. (Dokumentasi API FastAPI interaktif dapat diakses pada halaman `http://127.0.0.1:8000/docs`).

2. **Migrasi / Inisialisasi Database**
   Pastikan melakukan inisialisasi tabel pada *database* untuk pertama kali dengan menembak POST request ke route migrasi, atau lakukan setup manual:
   ```bash
   # Akses dari browser REST client, Postman, cURL, atau Swagger UI:
   POST http://127.0.0.1:8000/migrate
   # Dan jika terdapat update pada skema:
   POST http://127.0.0.1:8000/migrate/alter
   ```

## 🏥 Cek Kesehatan Server (Health Check)
Anda bisa mengecek apakah service telah berjalan baik atau tidak dengan mengakses route `/health`:
```bash
GET http://127.0.0.1:8000/health
```

---
Diciptakan untuk YDSF - Automasi Penulisan Artikel berdasarkan LLM dan RAG.
