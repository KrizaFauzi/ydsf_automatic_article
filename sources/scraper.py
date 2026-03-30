"""
sources/scraper.py — Web scraping untuk full content artikel.

Berisi 2 fungsi fetch untuk kategori Web Scraping:
- fetch_full_content() → Ambil full content dari list URL via Jina Reader
- fetch_google()       → Google Search via RapidAPI, lalu full content via Jina

Hubungan antara keduanya:
- fetch_google() → dapat list URL dari Google Search
- Setiap URL kemudian di-scrape oleh _scrape_one_url() via Jina Reader
- fetch_full_content() juga bisa menerima URL langsung (misal dari Google News RSS)

Jina Reader:
- Endpoint: https://r.jina.ai/{url}
- Gratis, tidak perlu signup, tidak perlu API key
- Mengembalikan teks bersih dari halaman web (strip HTML, iklan, dll)

Format output seragam (sama dengan sources/news.py):
{
    "Judul":   str,
    "Konten":  str,
    "Sumber":  str,
    "URL":     str,
}
"""

import os
import time

import requests
from dotenv import load_dotenv

from core.logger import logger_crawler
from core.constants import (
    GOOGLE_SEARCH_API_URL,
    GOOGLE_SEARCH_API_HOST,
    GOOGLE_SEARCH_PARAMS,
)

load_dotenv()

RAPIDAPI_KEY = os.getenv("RAPIDAPI_KEY", "")

# ── Konstanta ─────────────────────────────────────────────────────────────────

JINA_BASE_URL    = "https://r.jina.ai/"
REQUEST_TIMEOUT  = 20    # Jina butuh lebih lama dari news API biasa
MAX_CONTENT_CHAR = 3000  # Potong konten panjang agar tidak membebani ChromaDB
MAX_RESULTS      = 8     # Maks URL yang di-scrape per pemanggilan
SCRAPE_DELAY     = 0.5   # Jeda antar request ke Jina (detik)


# ─── Helpers ──────────────────────────────────────────────────────────────────


def _clean(text: str) -> str:
    """Bersihkan whitespace berlebih dari teks."""
    if not text:
        return ""
    return " ".join(text.strip().split())


def _truncate(text: str, max_chars: int = MAX_CONTENT_CHAR) -> str:
    """Potong teks jika terlalu panjang, tambah penanda di akhir."""
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + "..."


def _extract_domain(url: str) -> str:
    """
    Ekstrak nama domain dari URL sebagai nama sumber.
    Contoh: "https://www.kompas.com/..." → "kompas.com"
    """
    try:
        from urllib.parse import urlparse
        domain = urlparse(url).netloc
        # Buang "www." jika ada
        return domain.replace("www.", "") if domain else "Web"
    except Exception:
        return "Web"


# ─── Jina Reader ──────────────────────────────────────────────────────────────


def _scrape_one_url(url: str) -> dict | None:
    """
    Scrape satu URL menggunakan Jina Reader.

    Jina Reader bekerja dengan cara:
    - Kamu hit: https://r.jina.ai/https://url-yang-mau-discrape.com
    - Jina mengambil halaman tersebut, strip HTML/iklan/navigasi
    - Mengembalikan teks bersih dalam format Markdown

    Gratis, tidak perlu API key, tidak perlu signup.

    Args:
        url: URL artikel yang akan di-scrape

    Returns:
        dict dengan format seragam, atau None jika gagal
    """
    if not url or not url.startswith("http"):
        return None

    jina_url = f"{JINA_BASE_URL}{url}"

    try:
        headers = {
            "Accept": "text/plain",   # minta plain text, bukan HTML
            "X-Return-Format": "text" # instruksi ke Jina untuk return text
        }

        response = requests.get(
            jina_url,
            headers=headers,
            timeout=REQUEST_TIMEOUT,
        )

        if response.status_code != 200:
            logger_crawler.warning(f"Jina: status {response.status_code} untuk {url}")
            return None

        raw_text = response.text.strip()

        if not raw_text or len(raw_text) < 100:
            # Konten terlalu pendek — kemungkinan halaman error atau block
            logger_crawler.warning(f"Jina: konten terlalu pendek dari {url}")
            return None

        # Baris pertama Jina biasanya adalah judul halaman
        lines  = raw_text.split("\n", 1)
        judul  = _clean(lines[0].lstrip("# "))  # strip markdown heading
        konten = _clean(lines[1]) if len(lines) > 1 else judul
        sumber = _extract_domain(url)

        return {
            "Judul":  judul[:200],              # batasi panjang judul
            "Konten": _truncate(konten),
            "Sumber": sumber,
            "URL":    url,
        }

    except requests.Timeout:
        logger_crawler.warning(f"Jina: timeout untuk {url}")
        return None
    except requests.RequestException as e:
        logger_crawler.warning(f"Jina: request error untuk {url}: {e}")
        return None


def fetch_full_content(urls: list[str]) -> list[dict]:
    """
    Scrape full content dari list URL menggunakan Jina Reader.

    Fungsi ini biasanya dipanggil dengan URL hasil dari sumber lain:
    - URL dari Google News RSS (sources/news.py → fetch_google_news)
    - URL dari Google Search (fetch_google di bawah)
    - URL apapun yang sudah diketahui relevan

    Args:
        urls: List URL yang akan di-scrape

    Returns:
        list[dict] dengan format seragam — hanya URL yang berhasil
    """
    if not urls:
        logger_crawler.warning("fetch_full_content: tidak ada URL yang diberikan")
        return []

    logger_crawler.debug(f"Jina Reader: scraping {len(urls)} URLs")

    results = []

    for i, url in enumerate(urls[:MAX_RESULTS]):
        result = _scrape_one_url(url)

        if result:
            results.append(result)
            logger_crawler.debug(f"Jina: OK ({i+1}/{min(len(urls), MAX_RESULTS)}) {url[:60]}")
        else:
            logger_crawler.debug(f"Jina: skip ({i+1}/{min(len(urls), MAX_RESULTS)}) {url[:60]}")

        # Jeda antar request — hindari rate limit Jina
        if i < len(urls) - 1:
            time.sleep(SCRAPE_DELAY)

    logger_crawler.info(f"Jina Reader: {len(results)} halaman berhasil di-scrape")
    return results


# ─── Google Search (via RapidAPI) + Jina ──────────────────────────────────────


def fetch_google(query: str, max_results: int = MAX_RESULTS) -> list[dict]:
    """
    Ambil hasil Google Search lalu scrape full content tiap URL via Jina.

    Ini adalah upgrade dari preprocess_web() lama yang hanya mengambil
    judul dan deskripsi. Sekarang setelah dapat URL dari Google,
    kita scrape full content-nya via Jina Reader.

    Flow:
    1. Hit Google Search API (via RapidAPI) → dapat list URL
    2. Untuk setiap URL → _scrape_one_url() via Jina
    3. Return full content, bukan hanya header

    Catatan: Butuh RAPIDAPI_KEY di .env.
    Jika tidak ada key → skip dengan warning, kembalikan [].

    Args:
        query: Kata kunci pencarian
        max_results: Jumlah URL yang akan diambil dan di-scrape

    Returns:
        list[dict] dengan format seragam, atau [] jika gagal
    """
    if not RAPIDAPI_KEY:
        logger_crawler.warning("Google Search: RAPIDAPI_KEY tidak ditemukan, skip")
        return []

    logger_crawler.debug(f"Google Search: fetching '{query}'")

    # ── Step 1: Ambil URL dari Google Search API ───────────
    headers = {
        "x-rapidapi-key":  RAPIDAPI_KEY,
        "x-rapidapi-host": GOOGLE_SEARCH_API_HOST,
    }

    params = {
        **GOOGLE_SEARCH_PARAMS,
        "query": query,
    }

    try:
        response = requests.get(
            GOOGLE_SEARCH_API_URL,
            headers=headers,
            params=params,
            timeout=15,
        )
        response.raise_for_status()
        data = response.json()

    except requests.Timeout:
        logger_crawler.warning("Google Search: timeout")
        return []
    except requests.RequestException as e:
        logger_crawler.warning(f"Google Search: request error: {e}")
        return []
    except ValueError as e:
        logger_crawler.warning(f"Google Search: gagal parse JSON: {e}")
        return []

    # ── Step 2: Ekstrak URL dari response ──────────────────
    raw_results = data.get("results", [])

    if not raw_results:
        logger_crawler.warning("Google Search: tidak ada hasil")
        return []

    # Ambil URL saja dari hasil Google
    urls = [
        item.get("url") or item.get("link", "")
        for item in raw_results[:max_results]
        if item.get("url") or item.get("link")
    ]

    if not urls:
        logger_crawler.warning("Google Search: tidak ada URL yang valid")
        return []

    logger_crawler.debug(f"Google Search: {len(urls)} URL → scraping via Jina")

    # ── Step 3: Scrape full content tiap URL via Jina ──────
    results = fetch_full_content(urls)

    # Override sumber menjadi "Google" untuk traceability
    for item in results:
        item["Sumber"] = f"Google ({item['Sumber']})"

    logger_crawler.info(f"Google + Jina: {len(results)} artikel dengan full content")
    return results