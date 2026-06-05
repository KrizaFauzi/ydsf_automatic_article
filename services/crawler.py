"""
services/crawler.py — Pipeline pengumpulan data dan indexing.

Mensinergikan data dari berbagai sumber (News, Wikipedia, Wikidata, Twitter, Google)
kemudian memproses dan menyimpannya ke vector database (ChromaDB) untuk RAG.
"""

import os
import csv
import shutil
import asyncio
from typing import Optional, Callable, Any
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
from services.ai import expand_query_for_search, get_llm, get_llm_fast, get_embedding

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


async def run_crawler(
    query: str, 
    session_id: str, 
    progress_callback: Optional[Callable[[], Any]] = None,
    seo_keywords: Optional[list[str]] = None
) -> dict:
    """
    Orkestrator utama (Async) — kumpulkan data dari semua sumber, bangun ChromaDB.
    Mendukung progress_callback untuk anti-timeout heartbeat.
    """
    if not query or not query.strip():
        raise ValidationError("Query tidak boleh kosong")
    if not session_id or not session_id.strip():
        raise ValidationError("Session ID tidak boleh kosong")

    query = query.strip()
    vector_db_path = os.path.join(VECTOR_DB_PATH, f"session_{session_id}")

    logger_crawler.info(f"=== Async Crawler start: '{query}' (session: {session_id}) ===")

    # 1. Query Expansion (Awaited)
    logger_crawler.info("Query expansion: sinonim + HyDE...")
    llm_fast = get_llm_fast()
    search_queries = await expand_query_for_search(query, llm_fast)
    
    # ── UPGRADE: SEO-Guided Anchors ──────────────────────────────────
    # Tambahkan kombinasi [Topic + SEO Keyword] ke list pencarian
    if seo_keywords:
        logger_crawler.info(f"Adding SEO anchors: {seo_keywords}")
        for kw in seo_keywords:
            anchor = f"{query} {kw}"
            if anchor not in search_queries:
                search_queries.append(anchor)
                
    logger_crawler.info(f"Search queries: {search_queries}")
    if progress_callback: progress_callback() # Heartbeat 1

    # 2. Sequential Category Execution with Heartbeats
    # News Category
    news_gdelt     = _run_source_multi("GDELT",       fetch_gdelt,       search_queries)
    news_newsapi   = _run_source_multi("NewsAPI",      fetch_newsapi,     search_queries)
    news_rss       = _run_source("RSS",                fetch_rss,         query)
    news_google    = _run_source("Google News",        fetch_google_news, query)
    if progress_callback: progress_callback() # Heartbeat 2

    # Scrape Category
    scrape_google  = _run_source_multi("Google",       fetch_google,      search_queries)
    if progress_callback: progress_callback() # Heartbeat 3

    # Social Category
    social_twitter = _run_source_multi("Twitter/X",    fetch_twitter,     search_queries)
    if progress_callback: progress_callback() # Heartbeat 4

    # Knowledge Category
    know_wikipedia = _run_source_multi("Wikipedia",    fetch_wikipedia,   search_queries)
    know_wikidata  = _run_source_multi("Wikidata",     fetch_wikidata,    search_queries)
    if progress_callback: progress_callback() # Heartbeat 5

    # 3. Sum & Validate
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

    total_docs = sum(sources_count.values())
    if total_docs == 0:
        raise CrawlerError("Semua sumber gagal menghasilkan data")

    # 4. Save CSVs
    try:
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
            if save_csv(data, filename):
                saved_files.append(os.path.join(CSV_DATA_DIR, filename))

        if not saved_files:
            raise CrawlerError("Tidak ada CSV yang berhasil disimpan")

        if progress_callback: progress_callback() # Heartbeat 6
    except Exception as e:
        logger_crawler.error(f"Error saving CSVs: {e}")
        raise CrawlerError(f"Gagal menyimpan CSV: {str(e)}")

    # 5. Rebuild ChromaDB
    total_chunks = rebuild_vectordb(saved_files, vector_db_path)
    if progress_callback: progress_callback() # Heartbeat 7 (Final)

    return {
        "status": "success",
        "query": query,
        "vector_db_path": vector_db_path,
        "sources": sources_count,
        "total_docs": total_docs,
        "total_chunks": total_chunks,
        "message": f"Crawling selesai: {total_docs} docs",
    }