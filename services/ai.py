import os
import json
from typing import Optional
from dotenv import load_dotenv

from langchain_groq import ChatGroq
from langchain_chroma import Chroma
from langchain_huggingface import HuggingFaceEmbeddings

from services.reranker import rerank_to_docs

from core.logger import logger_ai
from core.exceptions import (
    AIServiceError,
    VectorDBNotFoundError,
    LLMInitializationError,
    ValidationError,
)
from core.constants import (
    DEFAULT_LLM_MODEL,
    DEFAULT_EMBED_MODEL,
    VECTOR_DB_BASE_PATH,
    VECTOR_RETRIEVAL_K,
    LLM_TEMPERATURE,
)

load_dotenv()

# ─── Configuration ────────────────────────────────────────────────────────────

GROQ_API_KEY = os.getenv("GROQ_API_KEY")
HF_TOKEN = os.getenv("HF_TOKEN")
VECTOR_DB_PATH = os.getenv("VECTOR_DB_PATH", VECTOR_DB_BASE_PATH)
EMBED_MODEL = os.getenv("EMBED_MODEL", DEFAULT_EMBED_MODEL)
LLM_MODEL = os.getenv("LLM_MODEL", DEFAULT_LLM_MODEL)

# Validate required env vars
if not GROQ_API_KEY:
    raise EnvironmentError("GROQ_API_KEY tidak ditemukan di .env")
if not HF_TOKEN:
    raise EnvironmentError("HF_TOKEN tidak ditemukan di .env")

os.environ["GROQ_API_KEY"] = GROQ_API_KEY
os.environ["HF_TOKEN"] = HF_TOKEN

logger_ai.info(f"AI Service initialized with LLM: {LLM_MODEL}")


# ─── Singleton LLM ────────────────────────────────────────────────────────────

_llm: Optional[ChatGroq] = None


def get_llm() -> ChatGroq:
    """
    Dapatkan LLM instance (singleton pattern).
    Lazy loading saat pertama kali dipanggil.
    
    Returns:
        ChatGroq instance untuk generating answers
    
    Raises:
        LLMInitializationError: Jika gagal initialize LLM
    """
    global _llm
    if _llm is None:
        try:
            logger_ai.info(f"Initializing LLM: {LLM_MODEL}")
            _llm = ChatGroq(model=LLM_MODEL, temperature=LLM_TEMPERATURE)
            logger_ai.info(f"LLM successfully loaded: {LLM_MODEL}")
        except Exception as e:
            logger_ai.error(f"Failed to load LLM '{LLM_MODEL}': {e}")
            raise LLMInitializationError(LLM_MODEL, str(e))
    return _llm


# ─── Load ChromaDB ────────────────────────────────────────────────────────────


def load_vectordb(vector_db_path: str) -> Chroma:
    """
    Load ChromaDB dari specified path.
    
    Args:
        vector_db_path: Path ke ChromaDB directory
    
    Returns:
        Chroma instance untuk retrieval
    
    Raises:
        ValidationError: Jika vector_db_path kosong
        VectorDBNotFoundError: Jika path tidak ada
        AIServiceError: Jika gagal load ChromaDB
    """
    # Validate input
    if not vector_db_path or not vector_db_path.strip():
        logger_ai.warning("vector_db_path is empty")
        raise ValidationError("Path ke ChromaDB tidak boleh kosong")

    # Check path existence
    if not os.path.exists(vector_db_path):
        logger_ai.warning(f"ChromaDB path not found: {vector_db_path}")
        raise VectorDBNotFoundError(vector_db_path)

    try:
        logger_ai.debug(f"Loading ChromaDB from: {vector_db_path}")
        embedding = HuggingFaceEmbeddings(model_name=EMBED_MODEL)
        vectordb = Chroma(
            persist_directory=vector_db_path,
            embedding_function=embedding,
        )
        logger_ai.info(f"ChromaDB successfully loaded from: {vector_db_path}")
        return vectordb
    except Exception as e:
        logger_ai.error(f"Failed to load ChromaDB from '{vector_db_path}': {e}")
        raise AIServiceError(f"Gagal load ChromaDB: {str(e)}", {"path": vector_db_path})


# ─── Prompt Builder ───────────────────────────────────────────────────────────
def expand_query(question: str, llm) -> dict[str, list[str]]:
    """
    Terima 1 question, kembalikan dict query per seksi artikel.
    """
    from datetime import datetime
    month_year = datetime.now().strftime("%B %Y")

    prompt = f"""Kamu membantu sistem yang membuat artikel otomatis.
Dari topik berikut, buat query pencarian yang dikelompokkan per seksi artikel.

Topik: "{question}"
Bulan sekarang: {month_year}

Balas HANYA dengan JSON berikut, tidak ada teks lain:
{{
  "universal": [
    "nama asli topik",
    "alias atau akronim topik",
    "topik dalam bahasa Inggris",
    "topik {month_year}",
    "topik terbaru"
  ],
  "latar_belakang": [
    "query untuk sejarah dan asal-usul topik ini",
    "query untuk konteks historis jangka panjang"
  ],
  "ringkasan": [
    "query untuk situasi dan kondisi terkini {month_year}",
    "query untuk perkembangan terbaru"
  ],
  "tokoh": [
    "query untuk pemimpin dan tokoh kunci yang terlibat",
    "query untuk organisasi dan pihak yang terlibat"
  ],
  "konflik": [
    "query untuk konflik dan ketegangan yang sedang terjadi",
    "query untuk insiden dan peristiwa spesifik"
  ],
  "prediksi": [
    "query untuk analisis dan proyeksi ke depan",
    "query untuk skenario kemungkinan resolusi"
  ]
}}"""

    try:
        response = llm.invoke(prompt)
        raw = response.content.strip()

        # Bersihkan jika LLM membungkus dengan ```json ... ```
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]

        result = json.loads(raw)

        # Pastikan question asli selalu ada di universal
        if question not in result.get("universal", []):
            result["universal"].insert(0, question)

        return result

    except Exception:
        logger_ai.warning("Query expansion gagal, fallback ke question asli")
        # Fallback: struktur minimal agar ask() tetap jalan
        return {
            "universal": [question],
            "latar_belakang": [question],
            "ringkasan": [question],
            "tokoh": [question],
            "konflik": [question],
            "prediksi": [question],
        }


# ─── Query Expansion untuk Data Fetching (Sinonim + HyDE) ────────────────────


def expand_query_for_search(query: str, llm) -> list[str]:
    """
    Query expansion sederhana khusus untuk tahap pengumpulan data (crawler).

    Menghasilkan variasi query yang lebih luas tanpa struktur per-seksi,
    cukup dengan dua teknik klasik:

    1. Sinonim / alias / terjemahan
       Menangkap dokumen yang menggunakan kata berbeda untuk konsep sama.
       Contoh: "kecerdasan buatan" → "AI", "artificial intelligence", "deep learning"

    2. HyDE — Hypothetical Document Embeddings
       LLM membayangkan seperti apa dokumen yang ideal untuk menjawab query,
       lalu kalimat hipotetis itu dijadikan query tambahan.
       Teknik ini sangat efektif karena embedding HyDE lebih mirip
       dengan embedding dokumen asli di database.
       Referensi: Gao et al. 2022 (https://arxiv.org/abs/2212.10496)

    Hasilnya: list query yang pendek (3–5 item), cocok untuk API calls.
    Tidak perlu kompleks — cukup buka pintu ke variasi leksikal dan semantik.

    Args:
        query: Query/topik asli dari user
        llm: LLM instance (ChatGroq)

    Returns:
        list[str] — query asli + sinonim + HyDE. Selalu minimal [query].
    """
    prompt = f"""Kamu membantu sistem pencarian informasi.
Dari topik berikut, bantu perluas pencarian dengan 2 cara:

Topik: "{query}"

Balas HANYA dengan JSON ini, tidak ada teks lain:
{{
  "sinonim": [
    "alias, akronim, atau terjemahan bahasa Inggris dari topik",
    "istilah lain yang sering menggantikan topik ini"
  ],
  "hyde": "Tulis 1 kalimat fakta singkat (max 25 kata) seolah-olah kamu membaca artikel tentang topik ini"
}}"""

    try:
        response = llm.invoke(prompt)
        raw = response.content.strip()

        # Bersihkan jika LLM membungkus dengan ```json ... ```
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]

        data = json.loads(raw)

        queries: list[str] = [query]  # query asli selalu pertama

        # Tambahkan sinonim (buang duplikat dan string kosong)
        for syn in data.get("sinonim", []):
            syn = syn.strip()
            if syn and syn.lower() != query.lower() and syn not in queries:
                queries.append(syn)

        # Tambahkan kalimat HyDE sebagai query semantik
        hyde = data.get("hyde", "").strip()
        if hyde and hyde not in queries:
            queries.append(hyde)

        logger_ai.info(f"expand_query_for_search: {len(queries)} queries dari '{query[:40]}'")
        return queries

    except Exception as e:
        logger_ai.warning(f"expand_query_for_search gagal ({e}), fallback ke query asli")
        return [query]


def build_prompt(context: str, history: list, question: str) -> str:
    """
    Build combined prompt dari context, history, dan question.
    
    Args:
        context: Retrieved dokumen content
        history: Chat history (list of {role, content} dicts)
        question: User question
    
    Returns:
        Formatted prompt string untuk LLM
    
    Raises:
        ValidationError: Jika question kosong
    """
    # Validate question
    if not question or not question.strip():
        logger_ai.warning("Question is empty")
        raise ValidationError("Pertanyaan tidak boleh kosong")

    # Format history
    history_text = ""
    for i, msg in enumerate(history):
        role = "User" if msg.get("role") == "user" else "Bot"
        content = msg.get("content", "").strip()
        history_text += f"{role}: {content}\n"

    # Build prompt
    prompt = f"""Use the following context and conversation history to answer the question.
Answer in the same language as the question. Be concise and helpful.

Context:
{context if context.strip() else "No context available."}

Conversation History:
{history_text if history_text.strip() else "No conversation history."}

Question:
{question}

Answer:"""

    logger_ai.debug(f"Prompt built ({len(prompt)} chars)")
    return prompt


# ─── Article Prompt Builder ──────────────────────────────────────────────────


def build_article_prompt(context: str, topic: str) -> str:
    """
    Build prompt untuk menghasilkan artikel terstruktur dengan 6 seksi.

    Setiap seksi menghasilkan 1-2 paragraf yang berfokus pada satu aspek
    konflik/masalah, membantu institusi yayasan dana memahami situasi.

    Args:
        context: Retrieved dokumen content dari ChromaDB
        topic: Topik artikel

    Returns:
        Formatted prompt string untuk LLM
    """
    from datetime import datetime
    month_year = datetime.now().strftime("%B %Y")

    return f"""Kamu adalah jurnalis analitik untuk institusi zakat dan filantropi Islam.

Topik: "{topic}"
Bulan/Tahun: {month_year}

Data referensi dari berbagai sumber:
{context if context.strip() else "Data terbatas tersedia, gunakan pengetahuanmu."}

Tulis artikel informatif berdasarkan data di atas dengan TEPAT 6 seksi berikut.
Setiap seksi terdiri dari 1-2 paragraf padat dan informatif.
Gunakan Bahasa Indonesia yang formal namun mudah dipahami.
Jangan tambahkan seksi lain di luar yang diminta.
Mulai langsung dengan heading ## pertama, jangan tulis judul artikel atau pendahuluan sebelumnya.

## Latar Belakang
[Tulis 1-2 paragraf tentang sejarah dan asal-usul konflik/isu ini. Kapan dan mengapa mulai terjadi.]

## Situasi Terkini
[Tulis 1-2 paragraf tentang kondisi nyata di lapangan per {month_year}. Perkembangan paling baru.]

## Pihak yang Terlibat
[Tulis 1-2 paragraf tentang tokoh kunci, kelompok, dan organisasi yang terlibat: korban, pelaku, mediator.]

## Dampak Kemanusiaan
[Tulis 1-2 paragraf tentang dampak terhadap warga sipil, angka korban atau pengungsi, kemiskinan, pendidikan, dan kesehatan.]

## Respons & Upaya Solusi
[Tulis 1-2 paragraf tentang langkah-langkah yang sudah diambil oleh pemerintah, NGO, komunitas internasional, atau lembaga kemanusiaan.]

## Penutup & Rekomendasi
[Tulis 1 paragraf ringkasan singkat dan rekomendasi konkret bagi lembaga zakat atau filantropi Islam untuk berkontribusi.]"""


# ─── Article Generator (Main) ─────────────────────────────────────────────────


def generate_article(topic: str, vector_db_path: str) -> str:
    """
    Generate artikel multi-seksi (6 poin) dari topic menggunakan RAG pipeline.

    Menggunakan arsitektur yang sama dengan ask() — ChromaDB + expand_query
    + reranker — tetapi prompt LLM diarahkan ke penulisan artikel terstruktur
    bukan Q&A. Cocok untuk kebutuhan institusi yayasan dana yang ingin
    memahami konflik/masalah secara komprehensif.

    Args:
        topic: Topik artikel (sama dengan topic dari ChatSession)
        vector_db_path: Path ke ChromaDB yang sudah di-crawl

    Returns:
        Artikel dalam format Markdown string (6 seksi ## heading)

    Raises:
        VectorDBNotFoundError: Jika ChromaDB tidak ditemukan
        AIServiceError: Jika LLM atau proses lainnya gagal
    """
    logger_ai.info(f"Generating article for topic: {topic[:50]}...")

    try:
        # 1. Load ChromaDB
        vectordb = load_vectordb(vector_db_path)
        retriever = vectordb.as_retriever(search_kwargs={"k": VECTOR_RETRIEVAL_K})

        # 2. Expand query per seksi (reuse fungsi existing)
        llm = get_llm()
        query_map = expand_query(topic, llm)
        logger_ai.info(f"Query expansion selesai: {len(query_map)} seksi")

        # 3. Retrieve per seksi, kumpulkan semua doc unik
        seen_docs: set = set()
        all_docs: list = []
        for section, queries in query_map.items():
            for query in queries:
                docs = retriever.invoke(query)
                for doc in docs:
                    doc_id = doc.page_content[:100]
                    if doc_id not in seen_docs:
                        seen_docs.add(doc_id)
                        all_docs.append(doc)

        logger_ai.info(f"Total unique docs sebelum reranking: {len(all_docs)}")

        # 4. Rerank
        reranked_docs = rerank_to_docs(topic, all_docs)
        logger_ai.info(f"Reranking selesai: {len(reranked_docs)} docs dipilih")

        # 5. Gabungkan context
        context = "\n\n".join([d.page_content for d in reranked_docs])

        # 6. Build article prompt
        prompt = build_article_prompt(context, topic)

        # 7. Invoke LLM
        response = llm.invoke(prompt)
        article = response.content.strip()

        logger_ai.info(f"Article generated ({len(article)} chars)")
        return article

    except (ValidationError, VectorDBNotFoundError, AIServiceError):
        raise
    except Exception as e:
        logger_ai.error(f"Unexpected error in generate_article(): {e}", exc_info=True)
        raise AIServiceError(f"Gagal generate artikel: {str(e)}")


# ─── Main Function (Legacy Q&A) ───────────────────────────────────────────────

def ask(
    question: str,
    vector_db_path: str,
    history: Optional[list] = None,
) -> str:
    if history is None:
        history = []

    logger_ai.info(f"Processing question: {question[:50]}...")

    try:
        # 1. Load ChromaDB — TIDAK BERUBAH
        vectordb = load_vectordb(vector_db_path)
        retriever = vectordb.as_retriever(search_kwargs={"k": VECTOR_RETRIEVAL_K})

        # 2. Expand query → dapat dict per seksi
        llm = get_llm()
        query_map = expand_query(question, llm)
        logger_ai.info(f"Query expansion selesai: {len(query_map)} seksi")

        # 3. Retrieve per seksi, kumpulkan semua doc unik
        seen_docs = set()
        all_docs: list = []  # pool flat semua dokumen dari semua seksi

        for section, queries in query_map.items():
            for query in queries:
                docs = retriever.invoke(query)
                for doc in docs:
                    doc_id = doc.page_content[:100]
                    if doc_id not in seen_docs:
                        seen_docs.add(doc_id)
                        all_docs.append(doc)

        logger_ai.info(f"Total unique docs sebelum reranking: {len(all_docs)}")

        # 4. Rerank — pilih dokumen paling relevan sebelum masuk ke LLM
        #
        # Reranker menilai tiap dokumen dengan 3 metode:
        #   BM25 (keyword match) + Cosine similarity + Cross-encoder (ML model)
        # Hanya top-N dokumen dengan skor tertinggi yang diteruskan ke prompt.
        # Ini memastikan LLM mendapat konteks yang benar-benar relevan,
        # bukan semua dokumen mentah dari ChromaDB.
        #
        reranked_docs = rerank_to_docs(question, all_docs)
        logger_ai.info(f"Reranking selesai: {len(reranked_docs)} docs dipilih")

        # 5. Gabungkan jadi context dari dokumen yang sudah di-rerank
        context = "\n\n".join([d.page_content for d in reranked_docs])

        # 6. build_prompt
        prompt = build_prompt(context, history, question)

        # 7. Invoke LLM untuk jawaban final
        response = llm.invoke(prompt)
        answer = response.content.strip()

        logger_ai.info(f"Answer generated ({len(answer)} chars)")
        return answer

    except (ValidationError, VectorDBNotFoundError, AIServiceError):
        raise
    except Exception as e:
        logger_ai.error(f"Unexpected error in ask(): {e}", exc_info=True)
        raise AIServiceError(f"Gagal generate jawaban: {str(e)}")