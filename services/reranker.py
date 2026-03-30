import time
from dataclasses import dataclass, field

import numpy as np
from langchain_core.documents import Document
from rank_bm25 import BM25Okapi
from sentence_transformers import CrossEncoder, SentenceTransformer

from core.logger import logger_crawler as logger

# ── Konstanta ──────────────────────────────────────────────────────────────────

# Model cross-encoder — gratis, lokal, multilingual
# Alternatif lebih ringan : "cross-encoder/ms-marco-MiniLM-L-6-v2" (~70MB)
# Alternatif lebih akurat : "BAAI/bge-reranker-large" (~560MB, butuh lebih RAM)
CROSS_ENCODER_MODEL = "BAAI/bge-reranker-base"

# Model embedding untuk cosine similarity
# Harus SAMA dengan model yang dipakai saat build ChromaDB
# agar vector-nya comparable
EMBEDDING_MODEL = "sentence-transformers/all-MiniLM-L6-v2"

# Bobot tiap metode — total harus = 1.0
WEIGHT_BM25         = 0.3
WEIGHT_COSINE       = 0.3
WEIGHT_CROSS_ENCODER = 0.4

# Jumlah dokumen yang dikembalikan setelah reranking
TOP_N_DEFAULT = 20


# ── Data class untuk hasil reranking ──────────────────────────────────────────


@dataclass
class RankedDocument:
    """
    Satu dokumen beserta skor-skor reranking-nya.

    Dipakai untuk transparansi — kamu bisa lihat skor per metode,
    tidak hanya final score. Berguna untuk debugging dan evaluasi.
    """
    document:     Document          # dokumen asli dari ChromaDB
    bm25_score:   float = 0.0       # skor dari BM25 (sudah dinormalisasi)
    cosine_score: float = 0.0       # skor dari cosine similarity
    cross_score:  float = 0.0       # skor dari cross-encoder
    final_score:  float = 0.0       # skor gabungan final


# ── Model loader (singleton pattern) ──────────────────────────────────────────
#
# Model di-load sekali saja saat pertama dipakai, lalu disimpan
# di variabel module-level. Pemanggilan berikutnya tidak perlu
# load ulang — menghemat waktu dan memori.
#

_cross_encoder_model: CrossEncoder | None = None
_embedding_model: SentenceTransformer | None = None


def _get_cross_encoder() -> CrossEncoder:
    """
    Load cross-encoder model (lazy loading, singleton).
    Model hanya di-load saat pertama kali fungsi ini dipanggil.
    """
    global _cross_encoder_model
    if _cross_encoder_model is None:
        logger.info(f"Loading cross-encoder: {CROSS_ENCODER_MODEL}")
        _cross_encoder_model = CrossEncoder(CROSS_ENCODER_MODEL)
        logger.info("Cross-encoder loaded")
    return _cross_encoder_model


def _get_embedding_model() -> SentenceTransformer:
    """
    Load embedding model untuk cosine similarity (lazy loading, singleton).
    Harus model yang sama dengan saat build ChromaDB.
    """
    global _embedding_model
    if _embedding_model is None:
        logger.info(f"Loading embedding model: {EMBEDDING_MODEL}")
        _embedding_model = SentenceTransformer(EMBEDDING_MODEL)
        logger.info("Embedding model loaded")
    return _embedding_model


# ── Helper: normalisasi skor ───────────────────────────────────────────────────


def _normalize(scores: list[float]) -> list[float]:
    """
    Normalisasi list skor ke rentang 0.0–1.0 menggunakan min-max scaling.

    Kenapa perlu dinormalisasi?
    - BM25 menghasilkan skor 0–tak terbatas (tergantung panjang dokumen)
    - Cosine similarity sudah 0.0–1.0
    - Cross-encoder menghasilkan logit (-inf hingga +inf)
    Tanpa normalisasi, cross-encoder akan selalu dominasi karena skalanya berbeda.

    Rumus: normalized = (x - min) / (max - min)
    Jika semua skor sama (max == min), kembalikan semua 0.0.

    Args:
        scores: List skor mentah dari satu metode

    Returns:
        List skor yang sudah dinormalisasi ke 0.0–1.0
    """
    arr = np.array(scores, dtype=float)
    min_val = arr.min()
    max_val = arr.max()

    if max_val == min_val:
        # Semua skor sama — tidak bisa dinormalisasi, kembalikan semua 0
        return [0.0] * len(scores)

    normalized = (arr - min_val) / (max_val - min_val)
    return normalized.tolist()


# ── Scorer 1: BM25 ────────────────────────────────────────────────────────────


def _score_bm25(query: str, docs: list[Document]) -> list[float]:
    """
    Hitung skor BM25 untuk setiap dokumen terhadap query.

    BM25 (Best Match 25) adalah algoritma ranking berbasis frekuensi kata.
    Cara kerjanya:
    1. Tokenisasi semua dokumen (split per kata, lowercase)
    2. Bangun indeks BM25 dari semua dokumen
    3. Tokenisasi query dengan cara yang sama
    4. Query indeks → dapat skor per dokumen

    Kelebihan: sangat cepat, menangkap keyword eksak
    Kelemahan: tidak mengerti sinonim atau konteks

    Args:
        query: Query string
        docs: List dokumen yang akan di-scoring

    Returns:
        List skor BM25 mentah (belum dinormalisasi), panjang = len(docs)
    """
    # Tokenisasi: lowercase + split per spasi
    # Untuk hasil lebih baik bisa pakai NLTK/spaCy, tapi ini sudah cukup
    tokenized_docs  = [doc.page_content.lower().split() for doc in docs]
    tokenized_query = query.lower().split()

    # Bangun model BM25 dari semua dokumen
    bm25 = BM25Okapi(tokenized_docs)

    # Dapatkan skor untuk semua dokumen sekaligus
    scores = bm25.get_scores(tokenized_query)

    return scores.tolist()


# ── Scorer 2: Cosine Similarity ───────────────────────────────────────────────


def _score_cosine(query: str, docs: list[Document]) -> list[float]:
    """
    Hitung cosine similarity antara query dan setiap dokumen.

    Cara kerja:
    1. Embed query menjadi vector (1 × embedding_dim)
    2. Embed semua dokumen menjadi matrix (n_docs × embedding_dim)
    3. Hitung dot product query_vec · doc_vec untuk setiap dokumen
       (karena sentence-transformers sudah normalize ke unit length,
        dot product == cosine similarity)

    Kelebihan: menangkap makna semantik, sinonim, parafrase
    Kelemahan: bisa miss keyword eksak yang penting

    Args:
        query: Query string
        docs: List dokumen yang akan di-scoring

    Returns:
        List cosine similarity score (0.0–1.0), panjang = len(docs)
    """
    model = _get_embedding_model()

    # Embed query — hasilnya 1D array
    query_vec = model.encode(query, normalize_embeddings=True)

    # Embed semua dokumen sekaligus (lebih efisien dari satu per satu)
    doc_texts = [doc.page_content for doc in docs]
    doc_vecs  = model.encode(doc_texts, normalize_embeddings=True, show_progress_bar=False)

    # Hitung cosine similarity via dot product
    # query_vec shape: (dim,)  →  reshape ke (1, dim) untuk matmul
    # doc_vecs shape: (n_docs, dim)
    # hasil shape: (n_docs,)
    scores = np.dot(doc_vecs, query_vec).tolist()

    return scores


# ── Scorer 3: Cross-encoder ───────────────────────────────────────────────────


def _score_cross_encoder(query: str, docs: list[Document]) -> list[float]:
    """
    Hitung relevansi menggunakan cross-encoder model.

    Ini berbeda fundamental dari cosine similarity.

    Cosine similarity (bi-encoder):
    - Embed query TERPISAH
    - Embed dokumen TERPISAH
    - Bandingkan hasilnya
    → Cepat, tapi query dan dokumen tidak "berinteraksi"

    Cross-encoder:
    - Input: [query, dokumen] DIGABUNG dalam satu forward pass
    - Model membaca keduanya bersamaan
    - Output: satu skor relevansi
    → Lebih lambat, tapi jauh lebih akurat karena model
      benar-benar "memahami" hubungan query dengan dokumen

    Args:
        query: Query string
        docs: List dokumen yang akan di-scoring

    Returns:
        List skor cross-encoder (logit, belum dinormalisasi), panjang = len(docs)
    """
    model = _get_cross_encoder()

    # Format input: list of [query, doc_text] pairs
    # Cross-encoder butuh pasangan query-dokumen, bukan keduanya terpisah
    pairs  = [[query, doc.page_content] for doc in docs]
    scores = model.predict(pairs, show_progress_bar=False)

    return scores.tolist()


# ── Main Reranker ─────────────────────────────────────────────────────────────


def rerank(
    query: str,
    docs: list[Document],
    top_n: int = TOP_N_DEFAULT,
    weights: dict | None = None,
) -> list[RankedDocument]:
    """
    Rerank dokumen menggunakan hybrid scoring (BM25 + Cosine + Cross-encoder).

    Ini adalah fungsi utama yang dipanggil dari luar file ini.
    Menggabungkan ketiga metode dengan bobot yang bisa dikustomisasi.

    Algorithm:
    1. Hitung skor BM25 untuk semua dokumen
    2. Hitung cosine similarity untuk semua dokumen
    3. Hitung cross-encoder score untuk semua dokumen
    4. Normalisasi ketiga skor ke 0.0–1.0
    5. Gabungkan dengan weighted sum
    6. Sort descending, ambil top_n

    Args:
        query: Query/topik artikel
        docs: List dokumen dari ChromaDB (hasil retrieval)
        top_n: Jumlah dokumen terbaik yang dikembalikan
        weights: Override bobot default. Format:
                 {"bm25": 0.3, "cosine": 0.3, "cross": 0.4}

    Returns:
        List RankedDocument, diurutkan dari skor tertinggi.
        Panjang = min(top_n, len(docs))

    Raises:
        Tidak raise — jika ada error di salah satu scorer,
        scorer itu di-skip dan bobot didistribusikan ulang.
    """
    if not docs:
        logger.warning("Reranker: tidak ada dokumen untuk di-rerank")
        return []

    if not query or not query.strip():
        logger.warning("Reranker: query kosong, skip reranking")
        # Kembalikan docs apa adanya tanpa reranking
        return [RankedDocument(document=d) for d in docs[:top_n]]

    # Ambil bobot — pakai default jika tidak di-override
    w_bm25   = weights.get("bm25",   WEIGHT_BM25)          if weights else WEIGHT_BM25
    w_cosine = weights.get("cosine", WEIGHT_COSINE)        if weights else WEIGHT_COSINE
    w_cross  = weights.get("cross",  WEIGHT_CROSS_ENCODER) if weights else WEIGHT_CROSS_ENCODER

    logger.info(f"Reranking {len(docs)} docs untuk query: '{query[:50]}'")
    t_start = time.time()

    # ── Step 1: Hitung semua skor ──────────────────────────────
    #
    # Setiap scorer di-wrap try/except secara independen.
    # Jika satu scorer gagal (misal model tidak bisa di-load),
    # bobot-nya dibagi ke scorer lain — proses tetap jalan.
    #

    bm25_raw   = None
    cosine_raw = None
    cross_raw  = None

    try:
        bm25_raw = _score_bm25(query, docs)
        logger.debug(f"BM25 selesai: {time.time() - t_start:.2f}s")
    except Exception as e:
        logger.warning(f"BM25 scorer gagal: {e} — bobot didistribusikan ulang")
        w_bm25 = 0.0

    try:
        cosine_raw = _score_cosine(query, docs)
        logger.debug(f"Cosine selesai: {time.time() - t_start:.2f}s")
    except Exception as e:
        logger.warning(f"Cosine scorer gagal: {e} — bobot didistribusikan ulang")
        w_cosine = 0.0

    try:
        cross_raw = _score_cross_encoder(query, docs)
        logger.debug(f"Cross-encoder selesai: {time.time() - t_start:.2f}s")
    except Exception as e:
        logger.warning(f"Cross-encoder scorer gagal: {e} — bobot didistribusikan ulang")
        w_cross = 0.0

    # ── Step 2: Normalisasi ke 0.0–1.0 ────────────────────────

    n = len(docs)
    bm25_norm   = _normalize(bm25_raw)   if bm25_raw   is not None else [0.0] * n
    cosine_norm = _normalize(cosine_raw) if cosine_raw is not None else [0.0] * n
    cross_norm  = _normalize(cross_raw)  if cross_raw  is not None else [0.0] * n

    # ── Step 3: Normalkan total bobot ─────────────────────────
    #
    # Jika ada scorer yang gagal (bobotnya jadi 0.0),
    # re-normalisasi sisa bobot agar totalnya tetap 1.0
    #
    total_weight = w_bm25 + w_cosine + w_cross
    if total_weight == 0:
        logger.error("Semua scorer gagal — kembalikan docs tanpa reranking")
        return [RankedDocument(document=d) for d in docs[:top_n]]

    w_bm25   /= total_weight
    w_cosine /= total_weight
    w_cross  /= total_weight

    # ── Step 4: Hitung final score + buat RankedDocument ──────

    ranked = []
    for i, doc in enumerate(docs):
        bm25_s   = bm25_norm[i]
        cosine_s = cosine_norm[i]
        cross_s  = cross_norm[i]

        final = (w_bm25 * bm25_s) + (w_cosine * cosine_s) + (w_cross * cross_s)

        ranked.append(RankedDocument(
            document=doc,
            bm25_score=round(bm25_s,   4),
            cosine_score=round(cosine_s, 4),
            cross_score=round(cross_s,  4),
            final_score=round(final,    4),
        ))

    # ── Step 5: Sort descending + ambil top_n ─────────────────

    ranked.sort(key=lambda x: x.final_score, reverse=True)
    result = ranked[:top_n]

    elapsed = time.time() - t_start
    logger.info(
        f"Reranking selesai: {len(result)} docs dipilih dari {len(docs)} "
        f"dalam {elapsed:.2f}s"
    )

    # Log top 3 untuk debugging
    for i, r in enumerate(result[:3]):
        logger.debug(
            f"  #{i+1} final={r.final_score} "
            f"(bm25={r.bm25_score}, cos={r.cosine_score}, cross={r.cross_score}) "
            f"— {r.document.page_content[:60]}..."
        )

    return result


# ── Helper: ekstrak dokumen saja dari hasil rerank ────────────────────────────


def rerank_to_docs(
    query: str,
    docs: list[Document],
    top_n: int = TOP_N_DEFAULT,
) -> list[Document]:
    """
    Shortcut: rerank + langsung kembalikan list[Document] (tanpa skor).

    Pakai fungsi ini jika kamu tidak butuh skor detail —
    hanya butuh dokumen yang sudah diurutkan.

    Cocok untuk langsung di-pass ke build_prompt() di ai.py.

    Args:
        query: Query/topik artikel
        docs: List dokumen dari ChromaDB
        top_n: Jumlah dokumen terbaik yang dikembalikan

    Returns:
        List Document, diurutkan dari paling relevan
    """
    ranked = rerank(query, docs, top_n=top_n)
    return [r.document for r in ranked]