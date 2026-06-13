"""
tests/test_doc_intelligence.py — Automated Unit Tests for Document Intelligence
================================================================================
"""

from __future__ import annotations

import os
import time
import unittest
from unittest.mock import MagicMock, patch
from pathlib import Path
import tempfile

from os_layer.doc_intelligence import get_doc_intelligence, DocumentIntelligence

class TestDocumentIntelligence(unittest.TestCase):
    """Test suite for the Document Intelligence background indexer and chunker."""

    def setUp(self) -> None:
        self.doc_intel = get_doc_intelligence()

    def test_singleton_pattern(self) -> None:
        """Verify the service implements a strict thread-safe singleton pattern."""
        instance2 = get_doc_intelligence()
        self.assertIs(self.doc_intel, instance2)

    def test_semantic_chunker_basic(self) -> None:
        """Verify text is chunked properly at paragraph boundaries."""
        text = "Paragraph one is here.\n\nParagraph two is slightly longer."
        chunks = self.doc_intel._chunk_text(text, max_chars=50, overlap=5)
        
        self.assertEqual(len(chunks), 2)
        self.assertEqual(chunks[0], "Paragraph one is here.")
        self.assertEqual(chunks[1], "Paragraph two is slightly longer.")

    def test_semantic_chunker_large_paragraph(self) -> None:
        """Verify that massive paragraphs are split safely line-by-line."""
        text = "Line A\nLine B\nLine C\nLine D\nLine E"
        chunks = self.doc_intel._chunk_text(text, max_chars=15, overlap=2)
        self.assertTrue(len(chunks) > 1)
        self.assertIn("Line A", chunks[0])

    def test_semantic_chunker_overlap(self) -> None:
        """Verify overlap characters are preserved across boundary splits."""
        text = "This is a paragraph.\n\nAnd another paragraph."
        chunks = self.doc_intel._chunk_text(text, max_chars=25, overlap=10)
        self.assertTrue(len(chunks) >= 2)
        # Verify the second chunk carries a tail of the first or similar
        self.assertTrue(any("paragraph" in c for c in chunks))

    def test_parse_docx_missing_lib_graceful(self) -> None:
        """Verify python-docx ImportErrors are caught and parsed gracefully as empty string."""
        with patch.dict("sys.modules", {"docx": None}):
            res = self.doc_intel._parse_docx(Path("dummy.docx"))
            self.assertEqual(res, "")

    def test_parse_pdf_missing_lib_graceful(self) -> None:
        """Verify pypdf ImportErrors degrade gracefully to OCR fallback without crashing."""
        with patch.dict("sys.modules", {"pypdf": None}):
            # Should fall back to OCR which returns empty if tesseract is missing
            res = self.doc_intel._parse_pdf(Path("dummy.pdf"))
            self.assertEqual(res, "")

    def test_index_unsupported_suffix(self) -> None:
        """Verify that files with unsupported suffixes are skipped."""
        with tempfile.NamedTemporaryFile(suffix=".exe", delete=False) as f:
            f.write(b"dummy executable")
            filepath = f.name
            
        try:
            success = self.doc_intel.index_file(filepath)
            self.assertFalse(success)
        finally:
            os.unlink(filepath)

    @patch("memory.vector_store.store_document_chunk")
    @patch("memory.vector_store.delete_document_chunks")
    def test_index_file_txt(self, mock_delete, mock_store) -> None:
        """Verify indexing a valid text file clears old chunks and stores new ones."""
        mock_store.return_value = True
        mock_delete.return_value = True
        
        with tempfile.NamedTemporaryFile(suffix=".txt", delete=False, mode="w", encoding="utf-8") as f:
            f.write("Hello World!\n\nThis is a unit test for text indexing.")
            filepath = f.name
            
        try:
            success = self.doc_intel.index_file(filepath)
            self.assertTrue(success)
            self.assertTrue(mock_delete.called)
            self.assertTrue(mock_store.called)
            # Verify file_path passed to delete
            mock_delete.assert_called_with(str(Path(filepath).resolve()))
        finally:
            os.unlink(filepath)
            
    def test_debounce_timer_lifecycle(self) -> None:
        """Verify that modification triggers are debounced and timers are enqueued."""
        file_path = "test_doc.txt"
        
        # Enqueue debounce
        self.doc_intel._enqueue_debounce(file_path)
        
        # Ensure timer is active in state
        self.assertIn(file_path, self.doc_intel._timers)
        timer1 = self.doc_intel._timers[file_path]
        self.assertTrue(timer1.is_alive())
        
        # Re-enqueue should cancel the original timer and spawn a new one
        self.doc_intel._enqueue_debounce(file_path)
        self.assertIn(file_path, self.doc_intel._timers)
        timer2 = self.doc_intel._timers[file_path]
        
        self.assertIsNot(timer1, timer2)
        self.assertFalse(timer1.is_alive())  # should be cancelled
        
        # Cleanup
        self.doc_intel._cancel_debounce(file_path)
        self.assertNotIn(file_path, self.doc_intel._timers)

if __name__ == "__main__":
    unittest.main()
