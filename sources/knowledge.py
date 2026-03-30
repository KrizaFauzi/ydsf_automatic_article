"""
sources/knowledge.py — Sumber data knowledge graph.

Berisi 2 fungsi fetch untuk kategori Knowledge Graph:
- fetch_wikipedia() → Wikipedia API (gratis, tanpa batas)
- fetch_wikidata()  → Wikidata SPARQL endpoint (gratis, tanpa batas)

Peran dalam sistem artikel otomatis:
- fetch_wikipedia() → konten naratif untuk seksi Latar Belakang
- fetch_wikidata()  → data terstruktur tokoh & relasi untuk seksi Tokoh Terlibat

Keduanya gratis total, tidak butuh API key apapun.

Format output seragam:
{
    "Judul":   str,   # nama entitas / judul artikel Wikipedia
    "Konten":  str,   # ringkasan / deskripsi
    "Sumber":  str,   # "Wikipedia" atau "Wikidata"
    "URL":     str,   # URL halaman Wikipedia / Wikidata item
}
"""

import time
import urllib.parse

import requests

from core.logger import logger_crawler

# ── Konstanta ─────────────────────────────────────────────────────────────────

WIKIPEDIA_API_URL  = "https://en.wikipedia.org/api/rest_v1/page/summary"
WIKIPEDIA_SEARCH_URL = "https://en.wikipedia.org/w/api.php"
WIKIPEDIA_ID_URL   = "https://id.wikipedia.org/api/rest_v1/page/summary"  # Bahasa Indonesia
WIKIDATA_SPARQL_URL = "https://query.wikidata.org/sparql"

REQUEST_TIMEOUT    = 15
MAX_WIKI_RESULTS   = 5    # maks artikel Wikipedia per query
MAX_CONTENT_CHAR   = 2000 # potong konten panjang


# ─── Helpers ──────────────────────────────────────────────────────────────────


def _clean(text: str) -> str:
    """Bersihkan whitespace berlebih dari teks."""
    if not text:
        return ""
    return " ".join(text.strip().split())


def _truncate(text: str, max_chars: int = MAX_CONTENT_CHAR) -> str:
    """Potong teks jika terlalu panjang."""
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + "..."


def _safe_get(url: str, params: dict = None, headers: dict = None, timeout=REQUEST_TIMEOUT) -> requests.Response | None:
    """HTTP GET dengan timeout dan error handling. Return None jika gagal."""
    try:
        response = requests.get(
            url,
            params=params,
            headers=headers,
            timeout=timeout,
        )
        response.raise_for_status()
        return response
    except requests.Timeout:
        logger_crawler.warning(f"Timeout: {url}")
        return None
    except requests.RequestException as e:
        logger_crawler.warning(f"Request gagal ({url}): {e}")
        return None


# ─── Wikipedia ────────────────────────────────────────────────────────────────


def _search_wikipedia_titles(query: str, limit: int = MAX_WIKI_RESULTS) -> list[str]:
    """
    Cari judul-judul artikel Wikipedia yang relevan dengan query.

    Menggunakan Wikipedia Search API (opensearch) untuk mendapat
    list judul, yang kemudian dipakai untuk ambil konten via REST API.

    Args:
        query: Kata kunci pencarian
        limit: Jumlah judul yang dikembalikan

    Returns:
        list of judul artikel Wikipedia
    """
    params = {
        "action":  "opensearch",
        "search":  query,
        "limit":   limit,
        "format":  "json",
        "redirects": "resolve",
    }

    # Coba Wikipedia Bahasa Indonesia dulu, fallback ke English
    for lang in ["id", "en"]:
        url = f"https://{lang}.wikipedia.org/w/api.php"
        # Di _search_wikipedia_titles(), tambahkan headers:
        headers = {
            "User-Agent": "ArtikelOtomatis/1.0 (contact@youremail.com) python-requests"
        }
        response = _safe_get(url, params=params, headers=headers)

        if response:
            try:
                data   = response.json()
                titles = data[1]  # opensearch format: [query, [titles], [descs], [urls]]
                if titles:
                    logger_crawler.debug(f"Wikipedia {lang}: {len(titles)} judul ditemukan")
                    return titles
            except (ValueError, IndexError):
                continue

    return []


def _fetch_wikipedia_summary(title: str, lang: str = "en") -> dict | None:
    """
    Ambil ringkasan satu artikel Wikipedia berdasarkan judul.

    Menggunakan Wikipedia REST API v1 yang mengembalikan ringkasan
    terstruktur (judul, extract, URL) dalam format JSON bersih.

    Args:
        title: Judul artikel Wikipedia
        lang: Kode bahasa ("id" atau "en")

    Returns:
        dict dengan format seragam, atau None jika gagal
    """
    encoded = urllib.parse.quote(title.replace(" ", "_"))
    url = f"https://{lang}.wikipedia.org/api/rest_v1/page/summary/{encoded}"

    response = _safe_get(url)
    if not response:
        return None

    try:
        data   = response.json()
        judul  = _clean(data.get("title", title))
        konten = _clean(data.get("extract", ""))
        wiki_url = data.get("content_urls", {}).get("desktop", {}).get("page", "")

        if not konten:
            return None

        return {
            "Judul":  judul,
            "Konten": _truncate(konten),
            "Sumber": f"Wikipedia ({lang.upper()})",
            "URL":    wiki_url,
        }

    except (ValueError, KeyError) as e:
        logger_crawler.warning(f"Wikipedia: gagal parse summary '{title}': {e}")
        return None


def fetch_wikipedia(query: str, max_results: int = MAX_WIKI_RESULTS) -> list[dict]:
    """
    Ambil artikel Wikipedia yang relevan dengan query.

    Cara kerja:
    1. Cari judul artikel relevan via Wikipedia Search API
    2. Untuk setiap judul → ambil ringkasan via REST API
    3. Coba bahasa Indonesia dulu, fallback ke Inggris

    Kenapa Wikipedia penting untuk artikel otomatis?
    - Konten naratif berkualitas tinggi untuk seksi Latar Belakang
    - Terverifikasi dan netral
    - Gratis tanpa batas

    Args:
        query: Topik yang dicari
        max_results: Jumlah artikel Wikipedia yang diambil

    Returns:
        list[dict] dengan format seragam, atau [] jika tidak ada hasil
    """
    logger_crawler.debug(f"Wikipedia: fetching '{query}'")

    titles = _search_wikipedia_titles(query, limit=max_results)

    if not titles:
        logger_crawler.warning(f"Wikipedia: tidak ada judul ditemukan untuk '{query}'")
        return []

    results = []

    for title in titles:
        # Coba bahasa Indonesia dulu, fallback ke English
        result = _fetch_wikipedia_summary(title, lang="id")
        if not result:
            result = _fetch_wikipedia_summary(title, lang="en")

        if result:
            results.append(result)

        # Jeda kecil untuk hormati rate limit Wikipedia
        time.sleep(0.3)

    logger_crawler.info(f"Wikipedia: {len(results)} artikel")
    return results


# ─── Wikidata ─────────────────────────────────────────────────────────────────


def _build_sparql_query(query: str) -> str:
    """
    Bangun SPARQL query untuk Wikidata berdasarkan topik.

    Query ini mencari entitas (orang/organisasi) yang berkaitan
    dengan label yang mengandung kata dari query input.

    Hasilnya adalah:
    - Nama entitas
    - Deskripsi singkat
    - Posisi/jabatan (jika ada)
    - Kewarganegaraan (jika ada)
    - URL Wikidata item

    Args:
        query: Topik artikel (dipakai untuk filter label entitas)

    Returns:
        String SPARQL query
    """
    # Ambil kata kunci utama (kata terpanjang, biasanya paling spesifik)
    words   = [w for w in query.split() if len(w) > 3]
    keyword = max(words, key=len) if words else query

    sparql = f"""
SELECT DISTINCT ?item ?itemLabel ?description ?positionLabel ?countryLabel WHERE {{
  ?item rdfs:label ?label .
  FILTER(LANG(?label) = "en" || LANG(?label) = "id")
  FILTER(CONTAINS(LCASE(?label), LCASE("{keyword}")))

  # Hanya ambil manusia (Q5) atau organisasi (Q43229)
  ?item wdt:P31 ?type .
  VALUES ?type {{ wd:Q5 wd:Q43229 wd:Q7278 }}

  OPTIONAL {{ ?item schema:description ?description .
             FILTER(LANG(?description) = "en") }}
  OPTIONAL {{ ?item wdt:P39 ?position . }}
  OPTIONAL {{ ?item wdt:P27 ?country . }}

  SERVICE wikibase:label {{
    bd:serviceParam wikibase:language "id,en" .
  }}
}}
LIMIT 10
"""
    return sparql.strip()


def fetch_wikidata(query: str) -> list[dict]:
    """
    Query Wikidata SPARQL endpoint untuk data tokoh dan entitas.

    Wikidata adalah knowledge graph terstruktur — berisi relasi
    antar entitas (orang, organisasi, negara, peristiwa).

    Kenapa Wikidata penting untuk artikel otomatis?
    - Seksi Tokoh Terlibat: nama, jabatan, afiliasi organisasi
    - Data terstruktur dan terverifikasi
    - Gratis, query via SPARQL endpoint publik

    Docs: https://query.wikidata.org/

    Args:
        query: Topik artikel — dipakai untuk filter entitas relevan

    Returns:
        list[dict] dengan format seragam, atau [] jika gagal/kosong
    """
    logger_crawler.debug(f"Wikidata: querying '{query}'")

    sparql_query = _build_sparql_query(query)

    headers = {
        "Accept":     "application/sparql-results+json",
        "User-Agent": "artikel-otomatis/1.0 (https://github.com/yourusername/artikel-otomatis)",
    }

    params = {
        "query":  sparql_query,
        "format": "json",
    }

    # Ganti _safe_get biasa dengan timeout lebih besar
    response = _safe_get(
        WIKIDATA_SPARQL_URL,
        params=params,
        headers=headers,
        timeout=30  # naikkan dari 15 ke 30 detik
    )
    if not response:
        return []

    try:
        data     = response.json()
        bindings = data.get("results", {}).get("bindings", [])

        if not bindings:
            logger_crawler.warning(f"Wikidata: tidak ada hasil untuk '{query}'")
            return []

        results  = []
        seen     = set()  # deduplicate berdasarkan nama entitas

        for row in bindings:
            nama = _clean(row.get("itemLabel", {}).get("value", ""))
            if not nama or nama in seen:
                continue
            seen.add(nama)

            deskripsi = _clean(row.get("description",   {}).get("value", ""))
            posisi    = _clean(row.get("positionLabel",  {}).get("value", ""))
            negara    = _clean(row.get("countryLabel",   {}).get("value", ""))
            item_url  = row.get("item", {}).get("value", "")

            # Bangun konten dari field yang tersedia
            bagian_konten = [deskripsi]
            if posisi:
                bagian_konten.append(f"Jabatan: {posisi}")
            if negara:
                bagian_konten.append(f"Negara: {negara}")

            konten = ". ".join(filter(None, bagian_konten))

            if not konten:
                konten = nama  # fallback minimal

            results.append({
                "Judul":  nama,
                "Konten": konten,
                "Sumber": "Wikidata",
                "URL":    item_url,
            })

        logger_crawler.info(f"Wikidata: {len(results)} entitas ditemukan")
        return results

    except (ValueError, KeyError) as e:
        logger_crawler.warning(f"Wikidata: gagal parse response: {e}")
        return []