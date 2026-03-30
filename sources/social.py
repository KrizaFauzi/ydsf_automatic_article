import os

import requests
from dotenv import load_dotenv

from core.logger import logger_crawler
from core.constants import (
    TWITTER_API_URL,
    TWITTER_API_HOST,
    TWITTER_SEARCH_PARAMS,
)

load_dotenv()

RAPIDAPI_KEY = os.getenv("RAPIDAPI_KEY", "")

# ── Konstanta ─────────────────────────────────────────────────────────────────

MAX_RESULTS     = 20
REQUEST_TIMEOUT = 15


# ─── Helpers ──────────────────────────────────────────────────────────────────


def _clean(text: str) -> str:
    """Bersihkan whitespace berlebih dan newline dari teks."""
    if not text:
        return ""
    return " ".join(text.strip().split())


# ─── Twitter / X ──────────────────────────────────────────────────────────────


def _parse_twitter(data: dict) -> list[dict]:
    """
    Parse response JSON dari Twitter RapidAPI menjadi list[dict] seragam.

    Dipindah dari preprocess_twitter() di crawler.py lama.
    Logika parsing tidak berubah — hanya format output disesuaikan
    dengan format seragam (Judul, Konten, Sumber, URL).

    Args:
        data: Raw JSON response dari Twitter API

    Returns:
        list[dict] dengan format seragam
    """
    hasil = []
    try:
        entries = data["data"]["search_by_raw_query"]["search_timeline"][
            "timeline"
        ]["instructions"][0]["entries"]

        for entri in entries:
            konten = entri.get("content", {})
            if konten.get("entryType") != "TimelineTimelineItem":
                continue

            item = konten.get("itemContent", {})
            if item.get("itemType") != "TimelineTweet":
                continue

            tweet = (
                item.get("tweet_results", {})
                .get("result", {})
                .get("legacy", {})
            )

            teks = _clean(tweet.get("full_text", ""))
            if not teks:
                continue

            tweet_id = tweet.get("id_str", "")
            url = f"https://twitter.com/i/web/status/{tweet_id}" if tweet_id else ""
            judul = teks[:80] + ("..." if len(teks) > 80 else "")

            hasil.append({
                "Judul":  judul,
                "Konten": teks,
                "Sumber": "Twitter",
                "URL":    url,
            })

    except KeyError as e:
        logger_crawler.warning(f"Twitter: JSON structure mismatch: {e}")
    except Exception as e:
        logger_crawler.error(f"Twitter: error parsing response: {e}")

    return hasil


def fetch_twitter(query: str, max_results: int = MAX_RESULTS) -> list[dict]:
    """
    Ambil tweets dari Twitter/X via RapidAPI.

    Args:
        query: Kata kunci pencarian tweet
        max_results: Jumlah tweet yang diambil

    Returns:
        list[dict] dengan format seragam, atau [] jika gagal
    """
    if not RAPIDAPI_KEY:
        logger_crawler.warning("Twitter: RAPIDAPI_KEY tidak ditemukan, skip")
        return []

    logger_crawler.debug(f"Twitter: fetching '{query}'")

    headers = {
        "x-rapidapi-key":  RAPIDAPI_KEY,
        "x-rapidapi-host": TWITTER_API_HOST,
    }

    params = {
        **TWITTER_SEARCH_PARAMS,
        "query": query,
        "count": max_results,
    }

    try:
        response = requests.get(
            TWITTER_API_URL,
            headers=headers,
            params=params,
            timeout=REQUEST_TIMEOUT,
        )

        if response.status_code != 200:
            logger_crawler.warning(f"Twitter: status {response.status_code}")
            return []

        data    = response.json()
        results = _parse_twitter(data)

        logger_crawler.info(f"Twitter: {len(results)} tweets")
        return results

    except requests.Timeout:
        logger_crawler.warning("Twitter: timeout")
        return []
    except requests.RequestException as e:
        logger_crawler.warning(f"Twitter: request error: {e}")
        return []
    except ValueError as e:
        logger_crawler.warning(f"Twitter: gagal parse JSON: {e}")
        return []