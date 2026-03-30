"""
sources/news.py — Sumber data berita.

Berisi 4 fungsi fetch untuk kategori News:
- fetch_gdelt()      → GDELT Project (100% gratis, berita global)
- fetch_newsapi()    → NewsAPI (free tier, 100 req/hari)
- fetch_rss()        → RSS feed media lokal & internasional
- fetch_google_news()→ Google News RSS (gratis, tanpa API key)

Semua fungsi:
- Menerima: query (str)
- Mengembalikan: list[dict] dengan key "Judul", "Konten", "Sumber", "URL"
- Jika gagal: kembalikan [] (jangan raise, biarkan crawler.py yang handle)

Format output seragam (WAJIB diikuti semua fungsi di file ini):
{
    "Judul":   str,   # judul artikel/berita
    "Konten":  str,   # isi/deskripsi/ringkasan
    "Sumber":  str,   # nama sumber (misal: "GDELT", "Kompas", dst)
    "URL":     str,   # URL artikel (bisa kosong string jika tidak ada)
}
"""

import os
import time
import urllib.parse
from datetime import datetime, timedelta

import requests
import feedparser
from dotenv import load_dotenv

from core.logger import logger_crawler

load_dotenv()

NEWSAPI_KEY = os.getenv("NEWSAPI_KEY", "")

# ── RSS feed media yang dipantau ──────────────────────────────────────────────
#
# Tambah atau kurangi sesuai kebutuhan.
# Format: ("Nama Sumber", "URL RSS feed")
#
RSS_FEEDS = [
    ("Detik",       "https://rss.detik.com/index.php/detikcom"),
    ("Kompas",      "https://rss.kompas.com/"),
    ("BBC Indonesia","https://feeds.bbci.co.uk/indonesia/rss.xml"),
    ("Reuters",     "https://feeds.reuters.com/reuters/topNews"),
    ("Al Jazeera",  "https://www.aljazeera.com/xml/rss/all.xml"),
    ("CNN Indonesia","https://www.cnnindonesia.com/rss"),
]

# ── Konstanta ─────────────────────────────────────────────────────────────────

GDELT_API_URL   = "https://api.gdeltproject.org/api/v2/doc/doc"
NEWSAPI_URL     = "https://newsapi.org/v2/everything"
GOOGLE_NEWS_URL = "https://news.google.com/rss/search"

REQUEST_TIMEOUT = 15   # detik
MAX_RESULTS     = 10   # max artikel per sumber


# ─── Helpers ──────────────────────────────────────────────────────────────────


def _safe_get(url: str, params: dict = None, headers: dict = None) -> requests.Response | None:
    """
    HTTP GET dengan timeout dan error handling.
    Mengembalikan Response atau None jika gagal.
    Tidak raise — biarkan caller yang putuskan.
    """
    try:
        response = requests.get(
            url,
            params=params,
            headers=headers,
            timeout=REQUEST_TIMEOUT,
        )
        response.raise_for_status()
        return response
    except requests.Timeout:
        logger_crawler.warning(f"Timeout: {url}")
        return None
    except requests.RequestException as e:
        logger_crawler.warning(f"Request gagal ({url}): {e}")
        return None


def _clean(text: str) -> str:
    """Bersihkan whitespace berlebih dari teks."""
    if not text:
        return ""
    return " ".join(text.strip().split())


# ─── GDELT ────────────────────────────────────────────────────────────────────


def fetch_gdelt(query: str, max_results: int = MAX_RESULTS) -> list[dict]:
    """
    Ambil berita dari GDELT Project API v2.

    GDELT adalah database berita global terbesar — gratis total,
    tidak perlu API key, mencakup ribuan media dari seluruh dunia.

    Docs: https://blog.gdeltproject.org/gdelt-2-0-our-global-archive-of-the-english-web/

    Args:
        query: Kata kunci pencarian
        max_results: Jumlah maksimal artikel yang diambil

    Returns:
        list[dict] dengan format seragam, atau [] jika gagal
    """
    logger_crawler.debug(f"GDELT: fetching '{query}'")

    params = {
        "query":      query,
        "mode":       "ArtList",        # mode daftar artikel
        "maxrecords": max_results,
        "format":     "json",
        "sort":       "DateDesc",       # terbaru dulu
        "timespan":   "7d",             # 7 hari terakhir
    }

    response = _safe_get(GDELT_API_URL, params=params)
    if not response:
        return []

    # Guard: respons body kosong (misal GDELT mengembalikan 200 tapi empty)
    if not response.text.strip():
        logger_crawler.warning("GDELT: response kosong (empty body)")
        return []

    try:
        data = response.json()
        articles = data.get("articles", [])

        results = []
        for art in articles:
            judul   = _clean(art.get("title", ""))
            konten  = _clean(art.get("seendescription", "") or art.get("title", ""))
            sumber  = _clean(art.get("domain", "GDELT"))
            url     = art.get("url", "")

            if judul:
                results.append({
                    "Judul":  judul,
                    "Konten": konten,
                    "Sumber": sumber,
                    "URL":    url,
                })

        logger_crawler.info(f"GDELT: {len(results)} artikel")
        return results

    except (ValueError, KeyError) as e:
        logger_crawler.warning(f"GDELT: gagal parse response: {e}")
        return []


# ─── NewsAPI ──────────────────────────────────────────────────────────────────


def fetch_newsapi(query: str, max_results: int = MAX_RESULTS) -> list[dict]:
    """
    Ambil berita dari NewsAPI.org.

    Free tier: 100 request/hari, artikel dari 7 hari terakhir.
    Butuh NEWSAPI_KEY di .env (daftar gratis di newsapi.org).

    Args:
        query: Kata kunci pencarian
        max_results: Jumlah maksimal artikel

    Returns:
        list[dict] dengan format seragam, atau [] jika gagal/key kosong
    """
    if not NEWSAPI_KEY:
        logger_crawler.warning("NewsAPI: NEWSAPI_KEY tidak ditemukan, skip")
        return []

    logger_crawler.debug(f"NewsAPI: fetching '{query}'")

    # Rentang tanggal: 7 hari ke belakang
    date_from = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")

    params = {
        "q":          query,
        "from":       date_from,
        "sortBy":     "publishedAt",
        "pageSize":   max_results,
        "language":   "id",             # Bahasa Indonesia (ganti "en" jika perlu)
        "apiKey":     NEWSAPI_KEY,
    }

    response = _safe_get(NEWSAPI_URL, params=params)
    if not response:
        return []

    try:
        data = response.json()

        # NewsAPI mengembalikan status error dalam JSON
        if data.get("status") != "ok":
            logger_crawler.warning(f"NewsAPI error: {data.get('message', 'unknown')}")
            return []

        articles = data.get("articles", [])

        results = []
        for art in articles:
            judul  = _clean(art.get("title", ""))
            konten = _clean(
                art.get("description", "") or
                art.get("content", "") or
                judul
            )
            sumber = art.get("source", {}).get("name", "NewsAPI")
            url    = art.get("url", "")

            if judul:
                results.append({
                    "Judul":  judul,
                    "Konten": konten,
                    "Sumber": sumber,
                    "URL":    url,
                })

        logger_crawler.info(f"NewsAPI: {len(results)} artikel")
        return results

    except (ValueError, KeyError) as e:
        logger_crawler.warning(f"NewsAPI: gagal parse response: {e}")
        return []


# ─── RSS Feed ─────────────────────────────────────────────────────────────────


def fetch_rss(query: str, max_per_feed: int = 5, max_total: int = MAX_RESULTS) -> list[dict]:
    """
    Ambil dan filter artikel dari RSS feed media.

    Cara kerja:
    1. Fetch semua feed yang ada di RSS_FEEDS
    2. Filter entry yang judulnya mengandung kata dari query
    3. Kembalikan hasil yang relevan

    Gratis total — tidak butuh API key apapun.
    feedparser adalah library Python standar untuk RSS/Atom.

    Args:
        query: Kata kunci untuk filter relevansi
        max_per_feed: Maks artikel per feed (hindari satu feed dominasi)
        max_total: Batas total artikel dari semua feed gabungan

    Returns:
        list[dict] dengan format seragam, atau [] jika semua feed gagal
    """
    logger_crawler.debug(f"RSS: fetching '{query}' dari {len(RSS_FEEDS)} feeds")

    # Siapkan kata kunci untuk filter (lowercase, split per kata)
    keywords = [k.lower() for k in query.split() if len(k) > 2]

    results = []

    for nama_sumber, feed_url in RSS_FEEDS:
        try:
            feed = feedparser.parse(
                feed_url,
                agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
                )

            if feed.bozo and not feed.entries:
                # bozo = ada error parsing, tapi masih coba jika ada entries
                logger_crawler.warning(f"RSS: feed error untuk {nama_sumber}")
                continue

            count = 0
            for entry in feed.entries:
                # Hentikan jika sudah mencapai batas per-feed atau batas total keseluruhan
                if count >= max_per_feed or len(results) >= max_total:
                    break

                judul  = _clean(entry.get("title", ""))
                konten = _clean(
                    entry.get("summary", "") or
                    entry.get("description", "") or
                    judul
                )
                url = entry.get("link", "")

                # Filter relevansi: minimal satu keyword ada di judul/konten
                gabungan = (judul + " " + konten).lower()
                relevan  = any(kw in gabungan for kw in keywords)

                if relevan and judul:
                    results.append({
                        "Judul":  judul,
                        "Konten": konten,
                        "Sumber": nama_sumber,
                        "URL":    url,
                    })
                    count += 1

            # Jeda kecil antar feed untuk hindari rate-limit
            time.sleep(0.2)

            # Hentikan iterasi feed jika total sudah cukup
            if len(results) >= max_total:
                logger_crawler.debug(f"RSS: batas total {max_total} tercapai, berhenti fetch feed berikutnya")
                break

        except Exception as e:
            logger_crawler.warning(f"RSS: gagal fetch {nama_sumber}: {e}")
            continue

    logger_crawler.info(f"RSS: {len(results)} artikel relevan")
    return results


# ─── Google News RSS ──────────────────────────────────────────────────────────


def fetch_google_news(query: str, max_results: int = MAX_RESULTS) -> list[dict]:
    """
    Ambil berita dari Google News via RSS feed-nya.

    Google News punya endpoint RSS publik yang bisa diakses gratis
    tanpa API key. Ini berbeda dari Google Search API (berbayar).
    Hasilnya adalah headline + link ke artikel asli.

    Catatan: ini hanya judul + link, bukan full content.
    Untuk full content, URL-nya diteruskan ke Jina Reader
    di sources/scraper.py (fetch_full_content).

    Args:
        query: Kata kunci pencarian
        max_results: Jumlah maksimal artikel

    Returns:
        list[dict] dengan format seragam, atau [] jika gagal
    """
    logger_crawler.debug(f"Google News RSS: fetching '{query}'")

    # Encode query untuk URL
    encoded_query = urllib.parse.quote(query)
    url = f"{GOOGLE_NEWS_URL}?q={encoded_query}&hl=id&gl=ID&ceid=ID:id"

    try:
        feed = feedparser.parse(
                url,
                agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        )

        if not feed.entries:
            logger_crawler.warning("Google News RSS: tidak ada hasil")
            return []

        results = []
        for entry in feed.entries[:max_results]:
            judul  = _clean(entry.get("title", ""))
            konten = _clean(entry.get("summary", "") or judul)
            url    = entry.get("link", "")

            # Google News menambahkan nama media di judul: "Judul - Media"
            # Ekstrak nama media dari bagian terakhir
            sumber = "Google News"
            if " - " in judul:
                parts  = judul.rsplit(" - ", 1)
                judul  = _clean(parts[0])
                sumber = _clean(parts[1]) if len(parts) > 1 else "Google News"

            if judul:
                results.append({
                    "Judul":  judul,
                    "Konten": konten,
                    "Sumber": sumber,
                    "URL":    url,
                })

        logger_crawler.info(f"Google News RSS: {len(results)} artikel")
        return results

    except Exception as e:
        logger_crawler.warning(f"Google News RSS: gagal: {e}")
        return []