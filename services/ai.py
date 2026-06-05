"""
services/ai.py — Layanan integrasi AI (LLM, Embedding, RAG)

Fungsi utama:
- Initialize dan caching LLM (Groq) & Embedding Model (HuggingFace)
- Retrieval-Augmented Generation (RAG) untuk generate artikel dan tanya jawab
- Query expansion (Sinonim & HyDE) untuk meningkatkan kualitas pencarian
"""

import os
import json
import asyncio
import re
from typing import Optional, Any
from dotenv import load_dotenv
from datetime import datetime

from langchain_groq import ChatGroq
from langchain_chroma import Chroma
from langchain_huggingface import HuggingFaceEmbeddings

from services.reranker import rerank_to_docs
from schemas.chat import ArticleConfig

from core.logger import logger_ai
from core.exceptions import (
    AIServiceError,
    VectorDBNotFoundError,
    LLMInitializationError,
    ValidationError,
)
from core.constants import (
    DEFAULT_LLM_MODEL,
    DEFAULT_LLM_MODEL_FAST,
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
LLM_MODEL_FAST = os.getenv("LLM_MODEL_FAST", DEFAULT_LLM_MODEL_FAST)

# Validate required env vars
if not GROQ_API_KEY:
    raise EnvironmentError("GROQ_API_KEY tidak ditemukan di .env")
if not HF_TOKEN:
    raise EnvironmentError("HF_TOKEN tidak ditemukan di .env")

os.environ["GROQ_API_KEY"] = GROQ_API_KEY
os.environ["HF_TOKEN"] = HF_TOKEN

logger_ai.info(f"AI Service initialized: Complex={LLM_MODEL}, Fast={LLM_MODEL_FAST}")


# ─── Singleton LLM ────────────────────────────────────────────────────────────

_llm: Optional[ChatGroq] = None
_llm_fast: Optional[ChatGroq] = None
_embedding: Optional[HuggingFaceEmbeddings] = None


def get_llm(model_name: Optional[str] = None) -> ChatGroq:
    """Complex model (70B) untuk generation."""
    global _llm
    target_model = model_name or LLM_MODEL
    
    # Jika model_name diberikan dan berbeda dari singleton saat ini, buat baru
    if model_name and _llm and _llm.model_name != model_name:
        try:
            new_llm = ChatGroq(model=model_name, temperature=LLM_TEMPERATURE)
            logger_ai.info(f"Custom LLM loaded: {model_name}")
            return new_llm
        except Exception as e:
            logger_ai.error(f"Failed to load custom LLM {model_name}: {e}")
            return _llm # Fallback ke default singleton

    if _llm is None:
        try:
            _llm = ChatGroq(model=target_model, temperature=LLM_TEMPERATURE)
            logger_ai.info(f"Complex LLM loaded: {target_model}")
        except Exception as e:
            logger_ai.error(f"Failed to load complex LLM: {e}")
            raise LLMInitializationError(target_model, str(e))
    return _llm


def get_llm_fast() -> ChatGroq:
    """Fast model (8B) untuk overhead tasks (expansion, etc)."""
    global _llm_fast
    if _llm_fast is None:
        try:
            _llm_fast = ChatGroq(model=LLM_MODEL_FAST, temperature=LLM_TEMPERATURE)
            logger_ai.info(f"Fast LLM loaded: {LLM_MODEL_FAST}")
        except Exception as e:
            logger_ai.error(f"Failed to load fast LLM: {e}")
            raise LLMInitializationError(LLM_MODEL_FAST, str(e))
    return _llm_fast


def get_embedding() -> HuggingFaceEmbeddings:
    global _embedding
    if _embedding is None:
        _embedding = HuggingFaceEmbeddings(model_name=EMBED_MODEL)
    return _embedding


# ─── Utilities ────────────────────────────────────────────────────────────────

def parse_json_from_text(text: str) -> dict[str, Any]:
    """Robust JSON extraction from LLM response."""
    try:
        # 1. Clean markdown blocks
        clean_text = text.strip()
        if "```" in clean_text:
            match = re.search(r"```(?:json)?\s*(.*?)\s*```", clean_text, re.DOTALL)
            if match:
                clean_text = match.group(1)

        # 2. Extract first { or [ object
        start_idx = clean_text.find('{')
        end_idx = clean_text.rfind('}')
        if start_idx != -1 and end_idx != -1:
            clean_text = clean_text[start_idx : end_idx + 1]

        return json.loads(clean_text)
    except Exception as e:
        logger_ai.error(f"JSON parsing failed: {e}. Raw text: {text[:200]}...")
        raise AIServiceError(f"Gagal memproses data AI: JSON invalid")


# ─── Load ChromaDB ────────────────────────────────────────────────────────────


def load_vectordb(vector_db_path: str) -> Chroma:
    if not vector_db_path or not vector_db_path.strip():
        raise ValidationError("Path ke ChromaDB tidak boleh kosong")

    if not os.path.exists(vector_db_path) or not os.path.isdir(vector_db_path):
        raise VectorDBNotFoundError(vector_db_path)

    try:
        embedding = get_embedding()
        return Chroma(
            persist_directory=vector_db_path,
            embedding_function=embedding,
        )
    except Exception as e:
        logger_ai.error(f"ChromaDB load failed: {e}")
        raise AIServiceError(f"Gagal load ChromaDB: {str(e)}")


# ─── Prompt Builder ───────────────────────────────────────────────────────────

async def expand_query(question: str, llm, seo_keywords: Optional[list[str]] = None) -> dict[str, list[str]]:
    month_year = datetime.now().strftime("%B %Y")
    seo_text = f"\nOptimasi untuk keywords SEO: {', '.join(seo_keywords)}" if seo_keywords else ""
    
    prompt = f"""Kamu membantu sistem yang membuat artikel otomatis.
Dari topik berikut, buat query pencarian yang dikelompokkan per seksi artikel.{seo_text}
Topik: "{question}"
Bulan sekarang: {month_year}
Balas HANYA dengan JSON objek yang berisi list query untuk: universal, latar_belakang, ringkasan, tokoh, konflik, prediksi."""

    try:
        response = await llm.ainvoke(prompt)
        result = parse_json_from_text(response.content)

        if question not in result.get("universal", []):
            result.setdefault("universal", []).insert(0, question)
        
        # Inject SEO keywords into universal queries if provided
        if seo_keywords:
            for kw in seo_keywords:
                if kw not in result["universal"]:
                    result["universal"].append(f"{question} {kw}")
                    
        return result
    except Exception:
        logger_ai.warning("Query expansion fallback")
        return {k: [question] for k in ["universal", "latar_belakang", "ringkasan", "tokoh", "konflik", "prediksi"]}


async def expand_query_for_search(query: str, llm) -> list[str]:
    prompt = f"""Topik: "{query}"
Bantu perluas pencarian. Balas HANYA dengan JSON: {{"sinonim": ["list synonym/alias"], "hyde": "1 fakta singkat"}}.
HyDE adalah kalimat imajiner yang seolah-olah menjawab topik tersebut."""

    try:
        response = await llm.ainvoke(prompt)
        data = parse_json_from_text(response.content)
        queries: list[str] = [query]
        for syn in data.get("sinonim", []):
            if syn and syn.lower() != query.lower(): queries.append(syn)
        if data.get("hyde"): queries.append(data["hyde"])
        return list(dict.fromkeys(queries))
    except Exception:
        return [query]


def build_prompt(context: str, history: list, question: str) -> str:
    history_text = "\n".join([f"{'User' if m['role']=='user' else 'Bot'}: {m['content']}" for m in history])
    return f"""Context:
{context}

History:
{history_text}

Question: {question}

Answer (Always respond in rich Markdown format with bold text, lists, and clear structure):"""


def build_article_prompt(context: str, config: ArticleConfig) -> str:
    month_year = datetime.now().strftime("%B %Y")
    
    # Length mapping
    length_desc = {
        "short": "singkat dan padat (sekitar 500 kata)",
        "medium": "komprehensif (sekitar 1200 kata)",
        "long": "mendalam dan detail (sekitar 2500 kata)"
    }.get(config.length, "komprehensif")

    # Determine Mode
    has_blueprint = bool(config.additional_info or config.key_points)

    prompt = f"""Role: Senior Philanthropy Journalist & Editor.
Topic: "{config.topic}" ({month_year})
Target Length: {length_desc}

<INSTRUCTIONS>
Tugas Anda adalah menulis artikel berkualitas tinggi dan profesional. 
Gunakan Bahasa Indonesia yang mengalir, elegan, dan informatif.
Anda diberikan data riset di bawah dalam format [ID] Judul | Sumber.
PENTING: Anda WAJIB menyertakan sitasi berupa nomor ID di akhir kalimat atau paragraf yang menggunakan informasi tersebut, contoh: "Data menunjukkan peningkatan donasi sebesar 20% [1]."
Hanya gunakan data yang benar-benar relevan dan dibutuhkan untuk membangun argumen yang kuat.
</INSTRUCTIONS>
"""

    # Mode A: Strict Blueprint Mode
    if has_blueprint:
        prompt += "\n<USER_BLUEPRINT>\n"
        prompt += "Anda WAJIB mengikuti struktur persis di bawah ini. Setiap poin harus menjadi bagian utama (Heading H2).\n"
        
        if config.additional_info:
            prompt += f"Fakta/Konteks Utama yang WAJIB dimasukkan: {config.additional_info}\n"
        
        if config.key_points:
            prompt += "Outline yang WAJIB digunakan (H2):\n- " + "\n- ".join(config.key_points) + "\n"
        
        prompt += "</USER_BLUEPRINT>\n"
    
    # Mode B: Creative Expert Mode (Standard Structure)
    else:
        prompt += "\n<STANDARD_STRUCTURE>\n"
        prompt += "Gunakan struktur jurnalistik profesional berikut:\n"
        prompt += "1. Latar Belakang & Urgensi (H2)\n"
        prompt += "2. Pembahasan Utama & Analisis (H2)\n"
        prompt += "3. Studi Kasus / Contoh Nyata (H2)\n"
        prompt += "4. Kesimpulan & Rekomendasi (H2)\n"
        prompt += "</STANDARD_STRUCTURE>\n"

    # Integration of Research Data (RAG)
    if context.strip():
        prompt += f"\n<RESEARCH_DATA>\n"
        prompt += "Gunakan data riset berikut. JANGAN gunakan informasi yang tidak relevan.\n"
        prompt += f"{context}\n"
        prompt += "</RESEARCH_DATA>\n"
    else:
        prompt += "\n<RESEARCH_DATA>\n"
        prompt += "Tidak ada data riset eksternal. Gunakan pengetahuan ahli Anda.\n"
        prompt += "</RESEARCH_DATA>\n"

    # Constraints & Final Output Rules
    prompt += "\n<CONSTRAINTS>\n"
    prompt += "1. Output WAJIB dalam format Markdown yang rapi.\n"
    if config.seo_keywords:
        prompt += f"2. Integrasikan keywords SEO secara natural: {', '.join(config.seo_keywords)}\n"
    else:
        prompt += "2. Pastikan alur tulisan logis dan profesional.\n"
    prompt += "3. JANGAN mengarang statistik atau data yang tidak ada di RESEARCH_DATA.\n"
    prompt += "4. DILARANG KERAS membuat seksi 'Referensi', 'Sumber', atau 'Daftar Pustaka' di akhir artikel. Tulis isi artikel saja. Sistem akan menambahkan referensi secara terpisah.\n"
    prompt += "5. Pastikan sitasi nomor ID [X] muncul di dalam teks jika Anda mengambil data dari riset.\n"
    prompt += "6. Batasi hingga maksimal 10 seksi utama.\n"
    prompt += "</CONSTRAINTS>\n"

    return prompt


def strip_reference_section(content: str) -> str:
    """Hapus seksi referensi/sumber yang mungkin dibuat oleh LLM secara redundan dengan regex yang lebih agresif."""
    # Cari heading yang mirip referensi atau daftar pustaka, atau garis pemisah diikuti referensi
    patterns = [
        r"\n\s*(?:##+|###+)\s*(?:Referensi|Sumber|Daftar Pustaka|Bibliography|References|Sources).*",
        r"\n\s*---\s*\n\s*(?:Referensi|Sumber|Daftar Pustaka|Bibliography).*",
        r"\n\s*\*\*(?:Referensi|Sumber|Daftar Pustaka|Bibliography|Sources)\*\*.*",
        r"\n\s*Sources:.*",
        r"\n\s*References:.*",
    ]
    for pattern in patterns:
        # Gunakan re.split dan ambil bagian pertama saja untuk memotong teks
        content = re.split(pattern, content, flags=re.IGNORECASE | re.DOTALL)[0]
    
    return content.strip()


# ─── Article Generator ────────────────────────────────────────────────────────


async def generate_article(config: ArticleConfig, vector_db_path: Optional[str] = None) -> dict[str, Any]:
    topic = config.topic
    logger_ai.info(f"Generating advanced article: {topic[:50]}")
    try:
        context = ""
        sources = []
        
        # Only use RAG if vector_db_path is provided
        if vector_db_path and vector_db_path.strip():
            vectordb = load_vectordb(vector_db_path)
            retriever = vectordb.as_retriever(search_kwargs={"k": VECTOR_RETRIEVAL_K})
            llm_fast = get_llm_fast()

            # 1. Expand (Async) with SEO consideration
            query_map = await expand_query(topic, llm_fast, config.seo_keywords)

            # 2. Parallel Retrieval
            all_queries = []
            for queries in query_map.values(): all_queries.extend(queries)

            # Execute retrievals in parallel
            retrieval_tasks = [retriever.ainvoke(q) for q in all_queries]
            docs_results = await asyncio.gather(*retrieval_tasks)

            # Flatten and Deduplicate by content hash
            all_docs = []
            seen_contents = set()
            for doc_list in docs_results:
                for doc in doc_list:
                    # Case-insensitive content deduplication
                    content_normalized = doc.page_content.strip().lower()
                    content_hash = hash(content_normalized)
                    if content_hash not in seen_contents:
                        seen_contents.add(content_hash)
                        all_docs.append(doc)

            # 3. Rerank
            # Perkecil pool rerank ke top 10 agar lebih fokus
            reranked_docs = rerank_to_docs(topic, all_docs, top_n=10)
            
            # 4. Build Context with Citations
            context_parts = []
            temp_sources = []
            seen_urls = set()
            
            for i, doc in enumerate(reranked_docs, 1):
                meta = doc.metadata or {}
                url = meta.get('URL', '').strip()
                if not url: continue
                
                # Normalize URL to prevent duplicates
                norm_url = url.lower().strip().rstrip('/')
                if '://' in norm_url:
                    norm_url = norm_url.split('://', 1)[1]
                
                if norm_url not in seen_urls:
                    seen_urls.add(norm_url)
                    judul = meta.get('Judul', 'Untitled').strip()
                    sumber = meta.get('Sumber', 'Unknown').strip()
                    
                    context_parts.append(f"[{i}] {judul} | {sumber}\n{doc.page_content}")
                    temp_sources.append({
                        "id": i,
                        "judul": judul,
                        "sumber": sumber,
                        "url": url
                    })
            
            context = "\n\n".join(context_parts)
            sources = temp_sources
        else:
            logger_ai.info(f"No vector_db_path provided for '{topic}', generating from LLM knowledge only.")

        # 5. Generation
        # Use custom model if requested
        llm = get_llm(config.model_choice)
        response = await llm.ainvoke(build_article_prompt(context, config))
        article_text = response.content.strip()
        
        # 6. Filtering Sources by Citations
        # Cari angka di dalam kurung siku, misal [1], [2]
        cited_ids = set(re.findall(r"\[(\d+)\]", article_text))
        final_sources = [s for s in sources if str(s.get("id")) in cited_ids]
        
        # Strip redundant reference section from LLM
        clean_content = strip_reference_section(article_text)

        # Jika LLM lupa sitasi tapi kita punya sources, berikan top 3 sebagai fallback
        if not final_sources and sources:
            final_sources = sources[:3]

        return {"article": clean_content, "sources": final_sources}

    except Exception as e:
        logger_ai.error(f"Error in generate_article: {e}", exc_info=True)
        raise AIServiceError(f"Gagal generate artikel: {str(e)}")


async def ask(question: str, vector_db_path: Optional[str] = None, history: Optional[list] = None, model_name: Optional[str] = None) -> str:
    try:
        context = ""
        
        # Only use RAG if vector_db_path is provided
        if vector_db_path and vector_db_path.strip():
            vectordb = load_vectordb(vector_db_path)
            retriever = vectordb.as_retriever(search_kwargs={"k": VECTOR_RETRIEVAL_K})
            llm_fast = get_llm_fast()

            # Expand & Retrieve (Parallel)
            queries = await expand_query_for_search(question, llm_fast)
            retrieval_tasks = [retriever.ainvoke(q) for q in queries]
            docs_results = await asyncio.gather(*retrieval_tasks)

            all_docs = []
            seen = set()
            for dl in docs_results:
                for d in dl:
                    if d.page_content not in seen:
                        seen.add(d.page_content); all_docs.append(d)

            # Rerank and Build Context with ID citations
            reranked = rerank_to_docs(question, all_docs, top_n=5)
            context_parts = []
            for i, d in enumerate(reranked, 1):
                meta = d.metadata or {}
                sumber = meta.get('Sumber', 'Unknown').strip()
                context_parts.append(f"[{i}] Sumber: {sumber}\n{d.page_content}")
            
            context = "\n\n".join(context_parts)
        else:
            logger_ai.info(f"No vector_db_path provided for question, generating from LLM knowledge only.")

        llm = get_llm(model_name)
        
        # Build prompt with citation instruction
        history_text = "\n".join([f"{'User' if m['role']=='user' else 'Bot'}: {m['content']}" for m in history or []])
        system_prompt = f"""Context:
{context}

History:
{history_text}

Question: {question}

Answer:
(Berikan jawaban informatif dalam Bahasa Indonesia. Gunakan sitasi [nomor ID] jika mengambil informasi dari Context. Balas dalam format Markdown yang rapi.)"""

        response = await llm.ainvoke(system_prompt)
        return response.content.strip()
    except Exception as e:
        logger_ai.error(f"Error in ask: {e}")
        raise AIServiceError(f"Gagal generate jawaban: {str(e)}")


# ─── Helper: Sources ──────────────────────────────────────────────────────────

def _extract_sources(documents: list) -> list[dict]:
    seen_urls = set()
    sources = []
    for doc in documents:
        meta = doc.metadata or {}
        url = meta.get('URL', '').strip()
        if not url: continue
        
        # Super robust normalization to prevent duplicates
        norm_url = url.lower().strip().rstrip('/')
        if '://' in norm_url:
            norm_url = norm_url.split('://', 1)[1]
        
        if norm_url not in seen_urls:
            seen_urls.add(norm_url)
            sources.append({
                "judul": meta.get('Judul', 'Untitled').strip(),
                "sumber": meta.get('Sumber', 'Unknown').strip(),
                "url": url,
            })
    return sources


def _format_sources_section(sources: list[dict]) -> str:
    """Format sources list into APA-style markdown with [link] text."""
    if not sources: return ""
    md = "\n\n## Sumber & Referensi\n\n"
    for s in sources:
        # APA style: Author/Source. (Year/n.d.). Title. [link](url)
        md += f"- {s['judul']}. (n.d.). *{s['sumber']}*. [link]({s['url']})\n"
    return md
