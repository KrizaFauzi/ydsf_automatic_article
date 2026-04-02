"""
services/crawler.py — Pipeline pengumpulan data dan indexing.

Mensinergikan data dari berbagai sumber (News, Wikipedia, Wikidata, Twitter, Google)
kemudian memproses dan menyimpannya ke vector database (ChromaDB) untuk RAG.
"""

import os
import csv
import shutil
from typing import Optional
from dotenv import load_dotenv

from langchain_chroma import Chroma
from langchain_community.document_loaders import CSVLoader
from langchain_huggingface import HuggingFaceEmbeddings
from langchain_text_splitters import RecursiveCharacterTextSplitter

from core.logger import logger_crawler
from core.exceptions import CrawlerError, ValidationError
from core.constants import (
    VECTOR_DB_BASE_PATH,
    CSV_DATA_DIR,
    DEFAULT_EMBED_MODEL,
    CHUNK_SIZE,
    CHUNK_OVERLAP,
)

# Sources — satu import per kategori
from sources.news import fetch_gdelt, fetch_newsapi, fetch_rss, fetch_google_news
from sources.scraper import fetch_google
from sources.social import fetch_twitter
from sources.knowledge import fetch_wikipedia, fetch_wikidata

# Query expansion sederhana (sinonim + HyDE) — hanya untuk tahap crawling
from services.ai import expand_query_for_search, get_llm, get_embedding

load_dotenv()

# ─── Configuration ─────────────────────────────────────────────────────────────

VECTOR_DB_PATH = os.getenv("VECTOR_DB_PATH", VECTOR_DB_BASE_PATH)
EMBED_MODEL_NAME = os.getenv("EMBED_MODEL", DEFAULT_EMBED_MODEL)

# Batas maksimum dokumen per sumber saat multi-query
# 10 artikel/query × maks ~4 queries = maks 40 docs per sumber
MAX_DOCS_PER_SOURCE = 40

logger_crawler.info("Crawler Service initialized")


# ─── CSV Operations ─────────────────────────────────────────────────────────────


def save_csv(data: list[dict], filename: str) -> bool:
    """
    Simpan list of dicts ke CSV file.

    Semua sumber mengembalikan list[dict] dengan format seragam,
    sehingga fungsi ini bisa menerima data dari sumber manapun.

    Args:
        data: List of dicts — hasil dari fungsi fetch di sources/
        filename: Nama file CSV (tanpa path, hanya filename)

    Returns:
        True jika berhasil disimpan, False jika data kosong

    Raises:
        CrawlerError: Jika gagal menulis file
    """
    if not data:
        logger_crawler.warning(f"No data to save: {filename}")
        return False

    try:
        filepath = os.path.join(CSV_DATA_DIR, filename)
        with open(filepath, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=data[0].keys())
            writer.writeheader()
            writer.writerows(data)

        logger_crawler.info(f"Saved {len(data)} rows → {filename}")
        return True

    except Exception as e:
        logger_crawler.error(f"Error saving CSV {filename}: {e}")
        raise CrawlerError(f"Gagal save CSV: {str(e)}", {"filename": filename})


# ─── ChromaDB Management ────────────────────────────────────────────────────────


def rebuild_vectordb(
    csv_files: list[str],
    vector_db_path: str,
    chunk_size: int = CHUNK_SIZE,
    chunk_overlap: int = CHUNK_OVERLAP,
) -> int:
    """
    Rebuild ChromaDB dari CSV files.

    Fungsi ini tidak berubah dari versi sebelumnya.
    Menerima CSV files, membangun ulang ChromaDB dari nol.

    Algorithm:
    1. Hapus ChromaDB lama (jika ada)
    2. Load semua dokumen dari CSV files
    3. Split menjadi chunks
    4. Generate embeddings (HuggingFace, lokal, gratis)
    5. Simpan ke ChromaDB

    Args:
        csv_files: List path CSV yang akan di-index
        vector_db_path: Path direktori output ChromaDB
        chunk_size: Ukuran tiap chunk teks
        chunk_overlap: Overlap antar chunk

    Returns:
        Jumlah chunks yang berhasil di-index

    Raises:
        CrawlerError: Jika proses rebuild gagal
    """
    try:
        logger_crawler.info(f"Rebuilding ChromaDB: {vector_db_path}")

        # 1. Hapus ChromaDB lama
        if os.path.exists(vector_db_path):
            logger_crawler.debug(f"Removing old ChromaDB: {vector_db_path}")
            shutil.rmtree(vector_db_path)

        # 2. Load dokumen dari semua CSV
        logger_crawler.debug(f"Loading documents dari {len(csv_files)} CSV files")
        docs = []
        from langchain_core.documents import Document

        for filepath in csv_files:
            if os.path.exists(filepath):
                try:
                    logger_crawler.debug(f"Loading: {filepath}")
                    with open(filepath, "r", encoding="utf-8") as f:
                        reader = csv.DictReader(f)
                        for i, row in enumerate(reader):
                            judul = row.get("Judul", "").strip()
                            sumber = row.get("Sumber", "").strip()
                            url = row.get("URL", "").strip()
                            konten = row.get("Konten", "").strip()

                            # Text combined for ChromaDB understanding
                            page_content = f"Judul: {judul}\nSumber: {sumber}\nURL: {url}\n\n{konten}"

                            # Explicit metadata injected so AI service can extract it later
                            meta = {
                                "source": filepath,
                                "row": i,
                                "Judul": judul,
                                "Sumber": sumber,
                                "URL": url,
                            }
                            docs.append(Document(page_content=page_content, metadata=meta))
                except Exception as e:
                    logger_crawler.warning(f"Gagal load {filepath}: {e}")
                    continue

        if not docs:
            raise CrawlerError("Tidak ada dokumen untuk di-index")

        logger_crawler.info(f"Total dokumen loaded: {len(docs)}")

        # 3. Split menjadi chunks
        splitter = RecursiveCharacterTextSplitter(
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
        )
        chunks = splitter.split_documents(docs)
        logger_crawler.info(f"Total chunks: {len(chunks)}")

        # 4. Embeddings + ChromaDB
        logger_crawler.debug("Getting embedding model (singleton)...")
        embedding = get_embedding()  # ambil dari singleton di ai.py

        Chroma.from_documents(
            documents=chunks,
            embedding=embedding,
            persist_directory=vector_db_path,
        )

        logger_crawler.info(f"ChromaDB built: {vector_db_path} ({len(chunks)} chunks)")
        return len(chunks)

    except CrawlerError:
        raise
    except Exception as e:
        logger_crawler.error(f"Error rebuilding ChromaDB: {e}", exc_info=True)
        raise CrawlerError(
            f"Gagal rebuild ChromaDB: {str(e)}", {"path": vector_db_path}
        )


# ─── Source Runner ──────────────────────────────────────────────────────────────


def _run_source(name: str, fn, *args) -> list[dict]:
    """
    Jalankan satu fungsi sumber dengan error handling seragam.

    Setiap sumber dijalankan secara terisolasi — jika satu sumber
    gagal, sumber lain tetap jalan. Crawler tidak berhenti karena
    satu sumber error.

    Args:
        name: Nama sumber (untuk logging)
        fn: Fungsi fetch dari sources/ yang akan dipanggil
        *args: Argumen yang diteruskan ke fn

    Returns:
        list[dict] hasil fetch, atau [] jika sumber gagal
    """
    try:
        logger_crawler.debug(f"Fetching: {name}")
        results = fn(*args)
        logger_crawler.info(f"{name}: {len(results)} docs")
        return results
    except Exception as e:
        # Sumber gagal → log warning, lanjutkan ke sumber berikutnya
        logger_crawler.warning(f"{name} gagal (dilanjutkan): {e}")
        return []


def _run_source_multi(
    name: str,
    fn,
    queries: list[str],
    max_total: int = MAX_DOCS_PER_SOURCE,
) -> list[dict]:
    """
    Jalankan satu fungsi sumber untuk beberapa query, gabungkan hasilnya.

    Dipakai untuk sumber API eksternal (GDELT, NewsAPI, Google, Twitter,
    Wikipedia, Wikidata) agar tiap variasi query menghasilkan dokumen berbeda.
    Sumber berbasis filter lokal (RSS, Google News RSS) cukup dipanggil 1x.

    Args:
        name: Nama sumber (untuk logging)
        fn: Fungsi fetch dari sources/ yang mendukung single-query call
        queries: List query dari expand_query_for_search() — [asli, sinonim, hyde]
        max_total: Batas total dokumen gabungan (deduplicated)

    Returns:
        list[dict] gabungan dari semua query, deduplicated via URL atau judul
    """
    seen_keys: set[str] = set()
    all_results: list[dict] = []

    for q in queries:
        if len(all_results) >= max_total:
            logger_crawler.debug(f"{name}: batas {max_total} tercapai, skip query berikutnya")
            break

        results = _run_source(
            f"{name} ['{q[:40]}...']" if len(q) > 40 else f"{name} ['{q}']",
            fn, q
        )
        for item in results:
            if len(all_results) >= max_total:
                break
            url = item.get("URL", "").strip()
            key = url if url else item.get("Judul", "")[:80]
            if key and key not in seen_keys:
                seen_keys.add(key)
                all_results.append(item)

    logger_crawler.info(f"{name}: {len(all_results)} docs unik dari {len(queries)} queries")
    return all_results


# ─── Main Orchestrator ──────────────────────────────────────────────────────────


def run_crawler(query: str, session_id: str) -> dict:
    """
    Orkestrator utama — kumpulkan data dari semua sumber, bangun ChromaDB.

    Algorithm:
    1. Validasi input
    2. Jalankan semua sumber dengan query asli dan query expansion (sinonim + HyDE)
    3. Gabungkan semua hasil menjadi satu pool
    4. Simpan ke CSV per kategori
    5. Rebuild ChromaDB dari semua CSV
    6. Return metadata hasil crawling

    Catatan desain:
    - Setiap sumber dijalankan via _run_source() yang mengisolasi error
    - Jika satu sumber gagal, sumber lain tetap berjalan
    - Crawler dianggap gagal total hanya jika SEMUA sumber menghasilkan 0 docs

    Args:
        query: Topik/query artikel yang akan dibuat
        session_id: Identifier unik sesi (dipakai untuk path ChromaDB)

    Returns:
        dict metadata hasil crawling:
        {
            "status": "success",
            "query": str,
            "vector_db_path": str,
            "sources": {nama_sumber: jumlah_docs},
            "total_docs": int,
            "total_chunks": int,
            "message": str,
        }

    Raises:
        ValidationError: Jika query atau session_id kosong
        CrawlerError: Jika semua sumber gagal atau ChromaDB gagal dibangun
    """
    # ── Validasi ────────────────────────────────────────────
    if not query or not query.strip():
        raise ValidationError("Query tidak boleh kosong")
    if not session_id or not session_id.strip():
        raise ValidationError("Session ID tidak boleh kosong")

    query = query.strip()
    vector_db_path = os.path.join(VECTOR_DB_PATH, f"session_{session_id}")

    logger_crawler.info(f"=== Crawler start: '{query}' (session: {session_id}) ===")

    # ── Query Expansion Sederhana ───────────────────────────
    #
    # Perluas query dengan sinonim dan HyDE sebelum fetch ke API.
    # Tujuan: tangkap dokumen yang memakai kata berbeda untuk konsep sama.
    #
    # Contoh untuk query "kecerdasan buatan":
    #   queries = [
    #     "kecerdasan buatan",                    ← query asli
    #     "artificial intelligence",              ← sinonim (bahasa Inggris)
    #     "AI berkembang pesat di berbagai...",   ← HyDE (kalimat hipotetis)
    #   ]
    #
    # Jika LLM gagal → expand_query_for_search() fallback ke [query] saja.
    #
    logger_crawler.info("Query expansion: sinonim + HyDE...")
    llm = get_llm()
    search_queries = expand_query_for_search(query, llm)
    logger_crawler.info(f"Search queries: {search_queries}")

    # ── Jalankan semua sumber ───────────────────────────────
    #
    # Sumber API eksternal → _run_source_multi() dengan semua search_queries
    #   GDELT, NewsAPI, Google Scrape, Twitter, Wikipedia, Wikidata
    #
    # Sumber filter lokal → _run_source() cukup 1x dengan query asli
    #   RSS dan Google News RSS sudah memuat seluruh feed lalu filter di lokal,
    #   jadi tidak ada manfaat mengirim ulang query yang berbeda.
    #
    news_gdelt     = _run_source_multi("GDELT",       fetch_gdelt,       search_queries)
    news_newsapi   = _run_source_multi("NewsAPI",      fetch_newsapi,     search_queries)
    news_rss       = _run_source("RSS",                fetch_rss,         query)   # filter lokal, 1x cukup
    news_google    = _run_source("Google News",        fetch_google_news, query)   # filter lokal, 1x cukup
    scrape_google  = _run_source_multi("Google",       fetch_google,      search_queries)
    social_twitter = _run_source_multi("Twitter/X",    fetch_twitter,     search_queries)
    know_wikipedia = _run_source_multi("Wikipedia",    fetch_wikipedia,   search_queries)
    know_wikidata  = _run_source_multi("Wikidata",     fetch_wikidata,    search_queries)


    # ── Catat jumlah per sumber (untuk metadata return) ────
    sources_count = {
        "gdelt":        len(news_gdelt),
        "newsapi":      len(news_newsapi),
        "rss":          len(news_rss),
        "google_news":  len(news_google),
        "google":       len(scrape_google),
        "twitter":      len(social_twitter),
        "wikipedia":    len(know_wikipedia),
        "wikidata":     len(know_wikidata),
    }

    # ── Cek apakah ada data sama sekali ────────────────────
    total_docs = sum(sources_count.values())
    if total_docs == 0:
        raise CrawlerError(
            "Semua sumber gagal menghasilkan data",
            {"query": query, "sources": sources_count},
        )

    logger_crawler.info(f"Total docs terkumpul: {total_docs}")

    # ── Simpan ke CSV per kategori ──────────────────────────
    #
    # Kenapa dipisah per kategori, bukan satu CSV?
    # Agar ChromaDB bisa di-load ulang per kategori jika perlu,
    # dan debugging lebih mudah (cek data1.csv = news, dst)
    #
    try:
        # Gabungkan per kategori sebelum disimpan
        news_all    = news_gdelt + news_newsapi + news_rss + news_google
        scrape_all  = scrape_google
        social_all  = social_twitter
        know_all    = know_wikipedia + know_wikidata

        saved_files = []

        csv_map = {
            "news.csv":      news_all,
            "scrape.csv":    scrape_all,
            "social.csv":    social_all,
            "knowledge.csv": know_all,
        }

        for filename, data in csv_map.items():
            filepath = os.path.join(CSV_DATA_DIR, filename)
            if save_csv(data, filename):
                saved_files.append(filepath)

        if not saved_files:
            raise CrawlerError("Tidak ada CSV yang berhasil disimpan")

    except CrawlerError:
        raise
    except Exception as e:
        logger_crawler.error(f"Error saving CSVs: {e}", exc_info=True)
        raise CrawlerError(f"Gagal menyimpan CSV: {str(e)}")

    # ── Rebuild ChromaDB ────────────────────────────────────
    total_chunks = rebuild_vectordb(saved_files, vector_db_path)

    # ── Return metadata ─────────────────────────────────────
    result = {
        "status": "success",
        "query": query,
        "vector_db_path": vector_db_path,
        "sources": sources_count,
        "total_docs": total_docs,
        "total_chunks": total_chunks,
        "message": f"Crawling selesai: {total_docs} docs dari {len(saved_files)} kategori",
    }

    logger_crawler.info(f"=== Crawler selesai: {total_docs} docs, {total_chunks} chunks ===")
    return result