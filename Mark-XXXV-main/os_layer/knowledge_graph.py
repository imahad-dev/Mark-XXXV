"""
os_layer/knowledge_graph.py — Relationship Graph
==================================================
Hybrid knowledge graph combining:
  - SQLite (structured storage, pathfinding, CRUD)
  - ChromaDB (semantic search over node descriptions)

Nodes represent entities (people, projects, concepts, tools).
Edges represent typed relationships between entities.

Pathfinding uses BFS — all edge weights are uniform (user constraint).

Thread Safety:
    Per-thread SQLite connections via threading.local (same pattern
    as event_bus.py). ChromaDB collection is thread-safe internally.

Architecture Constraint:
    Does NOT import from core.llm_orchestrator. Node creation is
    driven exclusively through tool calls (kg_control) or the
    kg.suggest event on the EventBus.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
import uuid
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

# ── Constants ────────────────────────────────────────────────────────────────

DB_PATH = Path(__file__).resolve().parent.parent / "memory" / "agent_episodes.db"
_KG_COLLECTION_NAME = "jarvis_kg_nodes"

_KG_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS kg_nodes (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    type        TEXT NOT NULL,
    properties  TEXT DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS kg_edges (
    source_id       TEXT NOT NULL,
    target_id       TEXT NOT NULL,
    relationship    TEXT NOT NULL,
    properties      TEXT DEFAULT '{}',
    PRIMARY KEY (source_id, target_id, relationship),
    FOREIGN KEY (source_id) REFERENCES kg_nodes(id) ON DELETE CASCADE,
    FOREIGN KEY (target_id) REFERENCES kg_nodes(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_kg_edges_source ON kg_edges(source_id);
CREATE INDEX IF NOT EXISTS idx_kg_edges_target ON kg_edges(target_id);
CREATE INDEX IF NOT EXISTS idx_kg_nodes_type   ON kg_nodes(type);
"""


# ── Data Classes ─────────────────────────────────────────────────────────────

@dataclass
class KGNode:
    """A single node in the knowledge graph."""
    id: str
    name: str
    type: str
    properties: dict = field(default_factory=dict)


@dataclass
class KGEdge:
    """A directed edge between two nodes."""
    source_id: str
    target_id: str
    relationship: str
    properties: dict = field(default_factory=dict)


# ── SQLite Helpers (thread-local connections) ────────────────────────────────

_db_local = threading.local()
_schema_initialized = False
_schema_lock = threading.Lock()


def _get_conn() -> sqlite3.Connection:
    conn = getattr(_db_local, "kg_conn", None)
    if conn is None:
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(
            str(DB_PATH),
            check_same_thread=False,
            timeout=10.0,
        )
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.row_factory = sqlite3.Row
        _db_local.kg_conn = conn
    return conn


def _ensure_schema() -> None:
    global _schema_initialized
    if _schema_initialized:
        return
    with _schema_lock:
        if _schema_initialized:
            return
        conn = _get_conn()
        conn.executescript(_KG_SCHEMA_SQL)
        conn.commit()
        _schema_initialized = True


# ── ChromaDB helpers ─────────────────────────────────────────────────────────

def _get_kg_collection():
    """Lazy-load the ChromaDB collection for semantic node search."""
    try:
        import chromadb
        db_dir = Path(__file__).resolve().parent.parent / "memory" / "chroma_db"
        db_dir.mkdir(parents=True, exist_ok=True)
        client = chromadb.PersistentClient(path=str(db_dir))
        return client.get_or_create_collection(
            name=_KG_COLLECTION_NAME,
            metadata={"hnsw:space": "cosine"},
        )
    except ImportError:
        logger.warning("[KnowledgeGraph] chromadb not installed — semantic search disabled")
        return None
    except Exception as e:
        logger.warning(f"[KnowledgeGraph] ChromaDB init failed: {e}")
        return None


# ── Knowledge Graph ──────────────────────────────────────────────────────────

class KnowledgeGraph:
    """
    Hybrid knowledge graph: SQLite structure + ChromaDB semantics.

    Usage:
        kg = KnowledgeGraph()
        kg.add_node("p1", "Tony Stark", "person", {"role": "CEO"})
        kg.add_node("p2", "JARVIS", "ai")
        kg.add_edge("p1", "p2", "created")
        path = kg.find_path("p1", "p2")
        results = kg.search_nodes("AI assistant")
    """

    def __init__(self):
        _ensure_schema()
        self._chroma = _get_kg_collection()
        logger.info("[KnowledgeGraph] ✅ Initialized (SQLite + ChromaDB)")

    # ── Node CRUD ────────────────────────────────────────────────────────

    def add_node(
        self,
        node_id: str,
        name: str,
        node_type: str,
        properties: dict | None = None,
    ) -> KGNode:
        """
        Insert or update a node. Upserts into both SQLite and ChromaDB.

        Args:
            node_id:    Unique identifier (caller-provided or auto-generated).
            name:       Human-readable node name.
            node_type:  Category (person, project, concept, tool, etc.).
            properties: Arbitrary JSON-serializable metadata.

        Returns:
            The created/updated KGNode.
        """
        props = properties or {}
        props_json = json.dumps(props, ensure_ascii=False)

        conn = _get_conn()
        conn.execute(
            """INSERT INTO kg_nodes (id, name, type, properties)
               VALUES (?, ?, ?, ?)
               ON CONFLICT(id) DO UPDATE SET
                   name = excluded.name,
                   type = excluded.type,
                   properties = excluded.properties""",
            (node_id, name, node_type, props_json),
        )
        conn.commit()

        # Upsert semantic representation in ChromaDB
        if self._chroma is not None:
            doc_text = f"{name} ({node_type})"
            if props.get("description"):
                doc_text += f": {props['description']}"
            try:
                self._chroma.upsert(
                    ids=[node_id],
                    documents=[doc_text],
                    metadatas=[{"name": name, "type": node_type}],
                )
            except Exception as e:
                logger.warning(f"[KnowledgeGraph] ChromaDB upsert failed: {e}")

        logger.debug(f"[KnowledgeGraph] Node upserted: {node_id} ({name})")
        return KGNode(id=node_id, name=name, type=node_type, properties=props)

    def get_node(self, node_id: str) -> KGNode | None:
        """Retrieve a node by ID. Returns None if not found."""
        conn = _get_conn()
        row = conn.execute(
            "SELECT id, name, type, properties FROM kg_nodes WHERE id = ?",
            (node_id,),
        ).fetchone()
        if not row:
            return None
        return KGNode(
            id=row["id"],
            name=row["name"],
            type=row["type"],
            properties=json.loads(row["properties"] or "{}"),
        )

    def delete_node(self, node_id: str) -> bool:
        """
        Delete a node and all its connected edges.
        Also removes from ChromaDB.

        Returns:
            True if the node existed and was deleted.
        """
        conn = _get_conn()
        cursor = conn.execute("DELETE FROM kg_nodes WHERE id = ?", (node_id,))
        # CASCADE handles edge deletion via foreign keys
        conn.commit()
        deleted = cursor.rowcount > 0

        if deleted and self._chroma is not None:
            try:
                self._chroma.delete(ids=[node_id])
            except Exception as e:
                logger.warning(f"[KnowledgeGraph] ChromaDB delete failed: {e}")

        return deleted

    def list_nodes(self, node_type: str | None = None, limit: int = 50) -> list[KGNode]:
        """List nodes, optionally filtered by type."""
        conn = _get_conn()
        if node_type:
            rows = conn.execute(
                "SELECT id, name, type, properties FROM kg_nodes WHERE type = ? LIMIT ?",
                (node_type, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT id, name, type, properties FROM kg_nodes LIMIT ?",
                (limit,),
            ).fetchall()
        return [
            KGNode(
                id=r["id"],
                name=r["name"],
                type=r["type"],
                properties=json.loads(r["properties"] or "{}"),
            )
            for r in rows
        ]

    # ── Edge CRUD ────────────────────────────────────────────────────────

    def add_edge(
        self,
        source_id: str,
        target_id: str,
        relationship: str,
        properties: dict | None = None,
    ) -> KGEdge:
        """
        Create a directed edge between two nodes.
        Raises ValueError if either node does not exist.
        """
        conn = _get_conn()
        # Validate both nodes exist
        for nid in (source_id, target_id):
            row = conn.execute("SELECT 1 FROM kg_nodes WHERE id = ?", (nid,)).fetchone()
            if not row:
                raise ValueError(f"Node '{nid}' does not exist. Create it first.")

        props = properties or {}
        props_json = json.dumps(props, ensure_ascii=False)

        conn.execute(
            """INSERT INTO kg_edges (source_id, target_id, relationship, properties)
               VALUES (?, ?, ?, ?)
               ON CONFLICT(source_id, target_id, relationship) DO UPDATE SET
                   properties = excluded.properties""",
            (source_id, target_id, relationship, props_json),
        )
        conn.commit()
        logger.debug(f"[KnowledgeGraph] Edge: {source_id} --[{relationship}]--> {target_id}")
        return KGEdge(source_id=source_id, target_id=target_id,
                      relationship=relationship, properties=props)

    def delete_edge(self, source_id: str, target_id: str, relationship: str) -> bool:
        """Remove a specific edge. Returns True if it existed."""
        conn = _get_conn()
        cursor = conn.execute(
            "DELETE FROM kg_edges WHERE source_id = ? AND target_id = ? AND relationship = ?",
            (source_id, target_id, relationship),
        )
        conn.commit()
        return cursor.rowcount > 0

    # ── Query ────────────────────────────────────────────────────────────

    def get_neighbors(self, node_id: str) -> list[dict]:
        """
        Get all nodes directly connected to the given node (both directions).

        Returns:
            List of dicts: {node: KGNode, relationship: str, direction: "outgoing"|"incoming"}
        """
        conn = _get_conn()
        results: list[dict] = []

        # Outgoing edges
        rows = conn.execute(
            """SELECT e.relationship, e.properties AS edge_props,
                      n.id, n.name, n.type, n.properties
               FROM kg_edges e
               JOIN kg_nodes n ON n.id = e.target_id
               WHERE e.source_id = ?""",
            (node_id,),
        ).fetchall()
        for r in rows:
            results.append({
                "node": KGNode(
                    id=r["id"], name=r["name"], type=r["type"],
                    properties=json.loads(r["properties"] or "{}"),
                ),
                "relationship": r["relationship"],
                "direction": "outgoing",
            })

        # Incoming edges
        rows = conn.execute(
            """SELECT e.relationship, e.properties AS edge_props,
                      n.id, n.name, n.type, n.properties
               FROM kg_edges e
               JOIN kg_nodes n ON n.id = e.source_id
               WHERE e.target_id = ?""",
            (node_id,),
        ).fetchall()
        for r in rows:
            results.append({
                "node": KGNode(
                    id=r["id"], name=r["name"], type=r["type"],
                    properties=json.loads(r["properties"] or "{}"),
                ),
                "relationship": r["relationship"],
                "direction": "incoming",
            })

        return results

    def find_path(self, source_id: str, target_id: str) -> list[str] | None:
        """
        BFS shortest path from source to target (undirected traversal).

        Returns:
            List of node IDs forming the path, or None if no path exists.
        """
        if source_id == target_id:
            return [source_id]

        conn = _get_conn()

        # Verify both nodes exist
        for nid in (source_id, target_id):
            if not conn.execute("SELECT 1 FROM kg_nodes WHERE id = ?", (nid,)).fetchone():
                return None

        # BFS traversal
        visited: set[str] = {source_id}
        queue: deque[list[str]] = deque([[source_id]])

        while queue:
            path = queue.popleft()
            current = path[-1]

            # Get adjacent node IDs (both directions)
            outgoing = conn.execute(
                "SELECT target_id FROM kg_edges WHERE source_id = ?",
                (current,),
            ).fetchall()
            incoming = conn.execute(
                "SELECT source_id FROM kg_edges WHERE target_id = ?",
                (current,),
            ).fetchall()

            neighbors = {r[0] for r in outgoing} | {r[0] for r in incoming}

            for neighbor_id in neighbors:
                if neighbor_id == target_id:
                    return path + [neighbor_id]
                if neighbor_id not in visited:
                    visited.add(neighbor_id)
                    queue.append(path + [neighbor_id])

        return None  # No path exists

    def search_nodes(self, query: str, limit: int = 10) -> list[KGNode]:
        """
        Semantic search: finds nodes whose names/descriptions match the query.
        Falls back to SQL LIKE search if ChromaDB is unavailable.
        """
        if self._chroma is not None:
            try:
                results = self._chroma.query(
                    query_texts=[query],
                    n_results=min(limit, 20),
                )
                node_ids = results.get("ids", [[]])[0]
                if node_ids:
                    return [
                        node for nid in node_ids
                        if (node := self.get_node(nid)) is not None
                    ]
            except Exception as e:
                logger.warning(f"[KnowledgeGraph] Semantic search failed: {e}")

        # Fallback: SQL LIKE search
        conn = _get_conn()
        rows = conn.execute(
            "SELECT id, name, type, properties FROM kg_nodes WHERE name LIKE ? LIMIT ?",
            (f"%{query}%", limit),
        ).fetchall()
        return [
            KGNode(
                id=r["id"], name=r["name"], type=r["type"],
                properties=json.loads(r["properties"] or "{}"),
            )
            for r in rows
        ]

    # ── Stats ────────────────────────────────────────────────────────────

    def stats(self) -> dict[str, int]:
        """Return node/edge counts."""
        conn = _get_conn()
        node_count = conn.execute("SELECT COUNT(*) FROM kg_nodes").fetchone()[0]
        edge_count = conn.execute("SELECT COUNT(*) FROM kg_edges").fetchone()[0]
        return {"nodes": node_count, "edges": edge_count}


# ── Singleton ────────────────────────────────────────────────────────────────

_instance: Optional[KnowledgeGraph] = None
_instance_lock = threading.Lock()


def get_knowledge_graph() -> KnowledgeGraph:
    """Thread-safe singleton accessor."""
    global _instance
    if _instance is None:
        with _instance_lock:
            if _instance is None:
                _instance = KnowledgeGraph()
    return _instance
