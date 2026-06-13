"""
tests/test_knowledge_graph.py — KnowledgeGraph Unit Tests
==========================================================
Tests SQLite CRUD, BFS pathfinding, and edge validation.
ChromaDB tests use graceful degradation (skip if unavailable).
"""

import json
import os
import sqlite3
import sys
import threading
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

# Ensure project root is on sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


class TestKnowledgeGraphSQLite(unittest.TestCase):
    """Test KnowledgeGraph SQLite operations with an isolated temp DB."""

    def setUp(self):
        """Redirect DB to a temp file and create a fresh KG instance."""
        self._tmpdir = tempfile.mkdtemp()
        self._db_path = Path(self._tmpdir) / "test_episodes.db"

        import os_layer.knowledge_graph as kg_mod
        self._original_db_path = kg_mod.DB_PATH
        kg_mod.DB_PATH = self._db_path
        kg_mod._schema_initialized = False
        kg_mod._instance = None
        # Clear thread-local connections
        if hasattr(kg_mod._db_local, "kg_conn"):
            delattr(kg_mod._db_local, "kg_conn")

        from os_layer.knowledge_graph import KnowledgeGraph
        # Patch out ChromaDB to test SQLite in isolation
        with patch.object(KnowledgeGraph, "__init__", lambda self: None):
            self.kg = KnowledgeGraph()
        self.kg._chroma = None
        kg_mod._ensure_schema()

    def tearDown(self):
        """Restore original DB path and clean up temp files."""
        import os_layer.knowledge_graph as kg_mod
        kg_mod.DB_PATH = self._original_db_path
        kg_mod._schema_initialized = False
        kg_mod._instance = None
        if hasattr(kg_mod._db_local, "kg_conn"):
            try:
                kg_mod._db_local.kg_conn.close()
            except Exception:
                pass
            delattr(kg_mod._db_local, "kg_conn")
        import shutil
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    # ── Node CRUD ────────────────────────────────────────────────────────

    def test_add_node_basic(self):
        node = self.kg.add_node("n1", "Test Node", "concept")
        self.assertEqual(node.id, "n1")
        self.assertEqual(node.name, "Test Node")
        self.assertEqual(node.type, "concept")

    def test_add_node_with_properties(self):
        props = {"role": "engineer", "level": 5}
        node = self.kg.add_node("n2", "Alice", "person", props)
        self.assertEqual(node.properties["role"], "engineer")

    def test_add_node_upsert(self):
        """Adding a node with same ID should update, not duplicate."""
        self.kg.add_node("n1", "Original", "concept")
        self.kg.add_node("n1", "Updated", "person")
        retrieved = self.kg.get_node("n1")
        self.assertEqual(retrieved.name, "Updated")
        self.assertEqual(retrieved.type, "person")

    def test_get_node_exists(self):
        self.kg.add_node("n1", "Test", "concept")
        node = self.kg.get_node("n1")
        self.assertIsNotNone(node)
        self.assertEqual(node.name, "Test")

    def test_get_node_missing(self):
        node = self.kg.get_node("nonexistent")
        self.assertIsNone(node)

    def test_delete_node(self):
        self.kg.add_node("n1", "Doomed", "concept")
        ok = self.kg.delete_node("n1")
        self.assertTrue(ok)
        self.assertIsNone(self.kg.get_node("n1"))

    def test_delete_node_missing(self):
        ok = self.kg.delete_node("nonexistent")
        self.assertFalse(ok)

    def test_list_nodes_all(self):
        self.kg.add_node("n1", "A", "concept")
        self.kg.add_node("n2", "B", "person")
        nodes = self.kg.list_nodes()
        self.assertEqual(len(nodes), 2)

    def test_list_nodes_by_type(self):
        self.kg.add_node("n1", "A", "concept")
        self.kg.add_node("n2", "B", "person")
        self.kg.add_node("n3", "C", "concept")
        nodes = self.kg.list_nodes(node_type="concept")
        self.assertEqual(len(nodes), 2)

    # ── Edge CRUD ────────────────────────────────────────────────────────

    def test_add_edge_basic(self):
        self.kg.add_node("n1", "A", "person")
        self.kg.add_node("n2", "B", "project")
        edge = self.kg.add_edge("n1", "n2", "works_on")
        self.assertEqual(edge.source_id, "n1")
        self.assertEqual(edge.relationship, "works_on")

    def test_add_edge_missing_node_raises(self):
        self.kg.add_node("n1", "A", "person")
        with self.assertRaises(ValueError):
            self.kg.add_edge("n1", "nonexistent", "knows")

    def test_add_edge_upsert(self):
        """Same (source, target, relationship) should update properties, not duplicate."""
        self.kg.add_node("n1", "A", "person")
        self.kg.add_node("n2", "B", "person")
        self.kg.add_edge("n1", "n2", "knows", {"since": 2020})
        self.kg.add_edge("n1", "n2", "knows", {"since": 2025})
        # Should not raise, and only one edge should exist
        neighbors = self.kg.get_neighbors("n1")
        knows_edges = [n for n in neighbors if n["relationship"] == "knows"]
        self.assertEqual(len(knows_edges), 1)

    def test_delete_edge(self):
        self.kg.add_node("n1", "A", "person")
        self.kg.add_node("n2", "B", "person")
        self.kg.add_edge("n1", "n2", "knows")
        ok = self.kg.delete_edge("n1", "n2", "knows")
        self.assertTrue(ok)

    def test_delete_edge_missing(self):
        ok = self.kg.delete_edge("x", "y", "z")
        self.assertFalse(ok)

    def test_delete_node_cascades_edges(self):
        """Deleting a node should remove all its edges."""
        self.kg.add_node("n1", "A", "person")
        self.kg.add_node("n2", "B", "person")
        self.kg.add_edge("n1", "n2", "knows")
        self.kg.delete_node("n1")
        neighbors = self.kg.get_neighbors("n2")
        self.assertEqual(len(neighbors), 0)

    # ── Query ────────────────────────────────────────────────────────────

    def test_get_neighbors_outgoing(self):
        self.kg.add_node("n1", "A", "person")
        self.kg.add_node("n2", "B", "project")
        self.kg.add_edge("n1", "n2", "created")
        neighbors = self.kg.get_neighbors("n1")
        self.assertEqual(len(neighbors), 1)
        self.assertEqual(neighbors[0]["direction"], "outgoing")
        self.assertEqual(neighbors[0]["node"].id, "n2")

    def test_get_neighbors_incoming(self):
        self.kg.add_node("n1", "A", "person")
        self.kg.add_node("n2", "B", "project")
        self.kg.add_edge("n1", "n2", "created")
        neighbors = self.kg.get_neighbors("n2")
        self.assertEqual(len(neighbors), 1)
        self.assertEqual(neighbors[0]["direction"], "incoming")
        self.assertEqual(neighbors[0]["node"].id, "n1")

    def test_get_neighbors_empty(self):
        self.kg.add_node("n1", "Alone", "concept")
        neighbors = self.kg.get_neighbors("n1")
        self.assertEqual(len(neighbors), 0)

    # ── BFS Pathfinding ──────────────────────────────────────────────────

    def test_find_path_direct(self):
        self.kg.add_node("n1", "A", "person")
        self.kg.add_node("n2", "B", "person")
        self.kg.add_edge("n1", "n2", "knows")
        path = self.kg.find_path("n1", "n2")
        self.assertEqual(path, ["n1", "n2"])

    def test_find_path_multi_hop(self):
        for i in range(1, 5):
            self.kg.add_node(f"n{i}", f"Node{i}", "concept")
        self.kg.add_edge("n1", "n2", "links")
        self.kg.add_edge("n2", "n3", "links")
        self.kg.add_edge("n3", "n4", "links")
        path = self.kg.find_path("n1", "n4")
        self.assertEqual(path, ["n1", "n2", "n3", "n4"])

    def test_find_path_reverse_direction(self):
        """BFS should traverse edges regardless of direction."""
        self.kg.add_node("n1", "A", "concept")
        self.kg.add_node("n2", "B", "concept")
        self.kg.add_edge("n2", "n1", "links")  # edge goes n2 → n1
        path = self.kg.find_path("n1", "n2")  # search n1 → n2
        self.assertEqual(path, ["n1", "n2"])

    def test_find_path_self(self):
        self.kg.add_node("n1", "Self", "concept")
        path = self.kg.find_path("n1", "n1")
        self.assertEqual(path, ["n1"])

    def test_find_path_no_connection(self):
        self.kg.add_node("n1", "Isolated1", "concept")
        self.kg.add_node("n2", "Isolated2", "concept")
        path = self.kg.find_path("n1", "n2")
        self.assertIsNone(path)

    def test_find_path_missing_node(self):
        path = self.kg.find_path("nonexistent1", "nonexistent2")
        self.assertIsNone(path)

    # ── SQL Fallback Search ──────────────────────────────────────────────

    def test_search_nodes_sql_fallback(self):
        """When ChromaDB is None, search should use SQL LIKE fallback."""
        self.kg.add_node("n1", "Machine Learning", "concept")
        self.kg.add_node("n2", "Deep Learning", "concept")
        self.kg.add_node("n3", "Cooking", "concept")
        results = self.kg.search_nodes("Learning")
        self.assertEqual(len(results), 2)
        names = {r.name for r in results}
        self.assertIn("Machine Learning", names)
        self.assertIn("Deep Learning", names)

    def test_search_nodes_no_results(self):
        results = self.kg.search_nodes("nonexistent")
        self.assertEqual(len(results), 0)

    # ── Stats ────────────────────────────────────────────────────────────

    def test_stats_empty(self):
        s = self.kg.stats()
        self.assertEqual(s["nodes"], 0)
        self.assertEqual(s["edges"], 0)

    def test_stats_populated(self):
        self.kg.add_node("n1", "A", "person")
        self.kg.add_node("n2", "B", "person")
        self.kg.add_edge("n1", "n2", "knows")
        s = self.kg.stats()
        self.assertEqual(s["nodes"], 2)
        self.assertEqual(s["edges"], 1)

    # ── Thread Safety ────────────────────────────────────────────────────

    def test_concurrent_node_creation(self):
        """Multiple threads creating nodes should not corrupt the DB."""
        errors = []

        def create_nodes(start, count):
            try:
                for i in range(start, start + count):
                    self.kg.add_node(f"t{i}", f"Thread Node {i}", "concept")
            except Exception as e:
                errors.append(e)

        threads = [
            threading.Thread(target=create_nodes, args=(i * 10, 10))
            for i in range(3)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(len(errors), 0, f"Thread errors: {errors}")
        stats = self.kg.stats()
        self.assertEqual(stats["nodes"], 30)


class TestKnowledgeGraphSingleton(unittest.TestCase):
    """Test singleton accessor."""

    def test_singleton_returns_same_instance(self):
        import os_layer.knowledge_graph as kg_mod
        kg_mod._instance = None
        with patch.object(kg_mod, "_get_kg_collection", return_value=None):
            kg1 = kg_mod.get_knowledge_graph()
            kg2 = kg_mod.get_knowledge_graph()
            self.assertIs(kg1, kg2)
        kg_mod._instance = None


if __name__ == "__main__":
    unittest.main()
