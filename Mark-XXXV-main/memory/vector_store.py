"""
vector_store.py — ChromaDB Semantic Memory Layer
=================================================
Stores raw conversation turns as vector embeddings for deep semantic recall.
Works alongside the existing JSON-based long_term.json fact store.

Architecture:
  - ChromaDB PersistentClient stores embeddings locally (zero cloud dependency)
  - Default embedding: ChromaDB's built-in all-MiniLM-L6-v2
  - Each document = one conversation turn (user + jarvis)
  - Metadata tags: timestamp, turn_index, has_tool_call
"""

import time
import logging
from datetime import datetime
from pathlib import Path
from threading import Lock

logger = logging.getLogger(__name__)

_DB_DIR = Path(__file__).resolve().parent / "chroma_db"
_COLLECTION_NAME = "jarvis_conversations"
_KNOWLEDGE_COLLECTION_NAME = "jarvis_knowledge"
_DOCUMENTS_COLLECTION_NAME = "jarvis_documents"
_lock = Lock()

# Lazy-loaded singletons
_client = None
_collection = None
_knowledge_collection = None
_documents_collection = None


def _get_collection():
    """Lazy-initialize ChromaDB client and collection on first use."""
    global _client, _collection
    if _collection is not None:
        return _collection

    with _lock:
        # Double-check after acquiring lock
        if _collection is not None:
            return _collection

        try:
            import chromadb

            _DB_DIR.mkdir(parents=True, exist_ok=True)
            _client = chromadb.PersistentClient(path=str(_DB_DIR))
            _collection = _client.get_or_create_collection(
                name=_COLLECTION_NAME,
                metadata={"hnsw:space": "cosine"},
            )
            doc_count = _collection.count()
            logger.info(f"[VectorStore] ChromaDB ready -- {doc_count} documents")
            print(f"[VectorStore] ChromaDB ready -- {doc_count} documents")
            return _collection
        except ImportError:
            logger.warning("[VectorStore] chromadb not installed -- vector memory disabled")
            print("[VectorStore] WARN: chromadb not installed -- pip install chromadb")
            return None
        except Exception as e:
            logger.error(f"[VectorStore] Init failed: {e}")
            print(f"[VectorStore] ERROR: Init failed: {e}")
            return None


def _get_knowledge_collection():
    """Lazy-initialize knowledge collection (used by intelligence router cache)."""
    global _client, _knowledge_collection
    if _knowledge_collection is not None:
        return _knowledge_collection

    # Ensure main client is initialized first
    _get_collection()

    with _lock:
        if _knowledge_collection is not None:
            return _knowledge_collection
        if _client is None:
            return None

        try:
            _knowledge_collection = _client.get_or_create_collection(
                name=_KNOWLEDGE_COLLECTION_NAME,
                metadata={"hnsw:space": "cosine"},
            )
            count = _knowledge_collection.count()
            logger.info(f"[VectorStore] Knowledge collection ready -- {count} entries")
            return _knowledge_collection
        except Exception as e:
            logger.error(f"[VectorStore] Knowledge init failed: {e}")
            return None


def store_dialogue(user_text: str, jarvis_text: str, has_tool_call: bool = False) -> bool:
    """
    Store a single conversation turn as a vector embedding.

    Args:
        user_text: What the user said.
        jarvis_text: What JARVIS responded.
        has_tool_call: Whether the turn involved a tool execution.

    Returns:
        True if stored successfully, False otherwise.
    """
    collection = _get_collection()
    if collection is None:
        return False

    user_text = (user_text or "").strip()
    jarvis_text = (jarvis_text or "").strip()

    # Skip trivial turns (greetings, single words, empty)
    if len(user_text) < 10:
        return False

    # Combine both sides into a single searchable document
    document = f"User: {user_text}\nJarvis: {jarvis_text}"

    # Truncate to prevent embedding model overflow (max ~512 tokens)
    if len(document) > 2000:
        document = document[:2000]

    doc_id = f"turn_{int(time.time() * 1000)}"
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M")

    try:
        collection.add(
            documents=[document],
            ids=[doc_id],
            metadatas=[{
                "timestamp": now_str,
                "user_text_preview": user_text[:100],
                "has_tool_call": str(has_tool_call),
            }],
        )
        return True
    except Exception as e:
        logger.error(f"[VectorStore] Store failed: {e}")
        print(f"[VectorStore] WARN: Store failed: {e}")
        return False


def semantic_search(query: str, k: int = 5) -> list[dict]:
    """
    Search conversation history by semantic similarity.

    Args:
        query: Natural language search query.
        k: Number of results to return.

    Returns:
        List of dicts with keys: document, metadata, distance.
        Sorted by relevance (lowest distance = most relevant).
    """
    collection = _get_collection()
    if collection is None:
        return []

    query = (query or "").strip()
    if not query:
        return []

    try:
        doc_count = collection.count()
        if doc_count == 0:
            return []

        # Don't request more results than we have documents
        n_results = min(k, doc_count)

        results = collection.query(
            query_texts=[query],
            n_results=n_results,
        )

        hits = []
        if results and results["documents"] and results["documents"][0]:
            docs = results["documents"][0]
            metas = results["metadatas"][0] if results.get("metadatas") else [{}] * len(docs)
            dists = results["distances"][0] if results.get("distances") else [0.0] * len(docs)

            for doc, meta, dist in zip(docs, metas, dists):
                hits.append({
                    "document": doc,
                    "metadata": meta,
                    "distance": dist,
                })

        return hits
    except Exception as e:
        logger.error(f"[VectorStore] Search failed: {e}")
        print(f"[VectorStore] WARN: Search failed: {e}")
        return []


def get_relevant_context(query: str, k: int = 5, max_chars: int = 1500) -> str:
    """
    Convenience wrapper: returns a formatted string of past conversations
    relevant to the current query, ready to inject into a system prompt.

    Args:
        query: The user's current input.
        k: Max number of past conversations to retrieve.
        max_chars: Truncate the total output to this length.

    Returns:
        Formatted string block, or empty string if nothing relevant.
    """
    hits = semantic_search(query, k=k)
    if not hits:
        return ""

    # Filter out low-relevance results (cosine distance > 0.8 = barely related)
    relevant = [h for h in hits if h["distance"] < 0.8]
    if not relevant:
        return ""

    lines = ["[PAST CONVERSATIONS — use for context, do not recite verbatim]"]
    for i, hit in enumerate(relevant, 1):
        ts = hit["metadata"].get("timestamp", "unknown")
        lines.append(f"\n--- Memory #{i} ({ts}) ---")
        lines.append(hit["document"][:400])

    result = "\n".join(lines)
    if len(result) > max_chars:
        result = result[:max_chars] + "\n..."

    return result + "\n"


def migrate_from_json(json_memory: dict) -> int:
    """
    One-time migration: converts existing long_term.json facts into
    vector embeddings for hybrid search.

    Args:
        json_memory: The loaded long_term.json dict.

    Returns:
        Number of facts migrated.
    """
    collection = _get_collection()
    if collection is None:
        return 0

    count = 0
    for category, entries in json_memory.items():
        if not isinstance(entries, dict):
            continue
        for key, entry in entries.items():
            val = entry.get("value") if isinstance(entry, dict) else str(entry)
            if not val:
                continue

            doc = f"Known fact — {category}/{key}: {val}"
            doc_id = f"migrated_{category}_{key}"

            try:
                collection.upsert(
                    documents=[doc],
                    ids=[doc_id],
                    metadatas=[{
                        "timestamp": entry.get("updated", "migrated") if isinstance(entry, dict) else "migrated",
                        "source": "long_term_json_migration",
                        "category": category,
                        "key": key,
                    }],
                )
                count += 1
            except Exception as e:
                logger.warning(f"[VectorStore] Migration skip {category}/{key}: {e}")

    print(f"[VectorStore] Migrated {count} facts from long_term.json")
    return count


# ── Knowledge Cache (used by Intelligence Router) ────────────────────────────

def store_knowledge(topic: str, content: str, source: str = "unknown") -> bool:
    """
    Store a factual answer in the knowledge collection for future cache hits.

    Called by the intelligence router when Wolfram/Wikipedia/etc return
    a successful result — enables instant retrieval on similar future queries.
    """
    collection = _get_knowledge_collection()
    if collection is None:
        return False

    topic = (topic or "").strip()
    content = (content or "").strip()
    if len(topic) < 3 or len(content) < 10:
        return False

    document = f"Q: {topic}\nA: {content}"
    if len(document) > 2000:
        document = document[:2000]

    doc_id = f"knowledge_{source}_{int(time.time() * 1000)}"
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M")

    try:
        collection.upsert(
            documents=[document],
            ids=[doc_id],
            metadatas=[{
                "timestamp": now_str,
                "source": source,
                "topic": topic[:100],
            }],
        )
        return True
    except Exception as e:
        logger.error(f"[VectorStore] Knowledge store failed: {e}")
        return False


def search_knowledge(query: str, k: int = 3) -> list[dict]:
    """
    Search the knowledge collection for cached factual answers.

    Returns list of dicts with keys: document, metadata, distance.
    Lower distance = more relevant.
    """
    collection = _get_knowledge_collection()
    if collection is None:
        return []

    query = (query or "").strip()
    if not query:
        return []

    try:
        doc_count = collection.count()
        if doc_count == 0:
            return []

        n_results = min(k, doc_count)
        results = collection.query(
            query_texts=[query],
            n_results=n_results,
        )

        hits = []
        if results and results["documents"] and results["documents"][0]:
            docs = results["documents"][0]
            metas = results["metadatas"][0] if results.get("metadatas") else [{}] * len(docs)
            dists = results["distances"][0] if results.get("distances") else [0.0] * len(docs)

            for doc, meta, dist in zip(docs, metas, dists):
                hits.append({
                    "document": doc,
                    "metadata": meta,
                    "distance": dist,
                })

        return hits
    except Exception as e:
        logger.error(f"[VectorStore] Knowledge search failed: {e}")
        return []


# ── Document Intelligence Collection (Phase 3) ───────────────────────────────

def _get_documents_collection():
    """Lazy-initialize documents collection (used by Document Intelligence background indexer)."""
    global _client, _documents_collection
    if _documents_collection is not None:
        return _documents_collection

    # Ensure main client is initialized first
    _get_collection()

    with _lock:
        if _documents_collection is not None:
            return _documents_collection
        if _client is None:
            return None

        try:
            _documents_collection = _client.get_or_create_collection(
                name=_DOCUMENTS_COLLECTION_NAME,
                metadata={"hnsw:space": "cosine"},
            )
            count = _documents_collection.count()
            logger.info(f"[VectorStore] Documents collection ready -- {count} entries")
            return _documents_collection
        except Exception as e:
            logger.error(f"[VectorStore] Documents init failed: {e}")
            return None


def store_document_chunk(
    file_path: str,
    chunk_index: int,
    total_chunks: int,
    chunk_text: str,
    extra_metadata: dict = None
) -> bool:
    """
    Store a document chunk with its metadata.

    Args:
        file_path: Absolute path to the source file.
        chunk_index: 0-indexed position of this chunk.
        total_chunks: Total number of chunks in the source file.
        chunk_text: Text content of the chunk.
        extra_metadata: Optional additional metadata fields to store.

    Returns:
        True if stored successfully, False otherwise.
    """
    collection = _get_documents_collection()
    if collection is None:
        return False

    file_path = str(file_path).strip()
    chunk_text = (chunk_text or "").strip()
    if not file_path or not chunk_text:
        return False

    import hashlib
    file_hash = hashlib.sha256(file_path.encode("utf-8")).hexdigest()
    doc_id = f"doc_{file_hash}_chunk_{chunk_index}"
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M")

    # source_path is critical for metadata-based batch deletion
    metadata = {
        "source_path": file_path,
        "chunk_index": chunk_index,
        "total_chunks": total_chunks,
        "timestamp": now_str,
    }
    if extra_metadata:
        metadata.update(extra_metadata)

    try:
        collection.upsert(
            documents=[chunk_text],
            ids=[doc_id],
            metadatas=[metadata],
        )
        return True
    except Exception as e:
        logger.error(f"[VectorStore] Document chunk store failed for {file_path}: {e}")
        return False


def delete_document_chunks(file_path: str) -> bool:
    """
    Delete all stored chunks associated with a specific file path.
    Uses metadata filtering for reliable atomic deletion across collection sizes.

    Args:
        file_path: Absolute path to the source file.

    Returns:
        True if deleted successfully, False otherwise.
    """
    collection = _get_documents_collection()
    if collection is None:
        return False

    file_path = str(file_path).strip()
    if not file_path:
        return False

    try:
        collection.delete(where={"source_path": file_path})
        logger.info(f"[VectorStore] Purged all chunks for file: {file_path}")
        return True
    except Exception as e:
        logger.error(f"[VectorStore] Failed to delete chunks for {file_path}: {e}")
        return False


def search_documents(query: str, k: int = 5) -> list[dict]:
    """
    Search indexed documents by semantic similarity.

    Args:
        query: Natural language query.
        k: Number of chunks to return.

    Returns:
        List of dicts with keys: document, metadata, distance.
    """
    collection = _get_documents_collection()
    if collection is None:
        return []

    query = (query or "").strip()
    if not query:
        return []

    try:
        doc_count = collection.count()
        if doc_count == 0:
            return []

        n_results = min(k, doc_count)
        results = collection.query(
            query_texts=[query],
            n_results=n_results,
        )

        hits = []
        if results and results["documents"] and results["documents"][0]:
            docs = results["documents"][0]
            metas = results["metadatas"][0] if results.get("metadatas") else [{}] * len(docs)
            dists = results["distances"][0] if results.get("distances") else [0.0] * len(docs)

            for doc, meta, dist in zip(docs, metas, dists):
                hits.append({
                    "document": doc,
                    "metadata": meta,
                    "distance": dist,
                })

        return hits
    except Exception as e:
        logger.error(f"[VectorStore] Document search failed: {e}")
        return []


