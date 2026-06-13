"""
os_layer/doc_intelligence.py — Background Document Intelligence & Indexer
========================================================================
Handles background crawling, debounced change monitoring, lazy file parsing
(TXT, MD, PDF, DOCX, and OCR fallback for images), semantic chunking, and
vector database indexing.
"""

from __future__ import annotations

import os
import time
import logging
import threading
from pathlib import Path
from typing import Optional, Union

from core.config import config
from os_layer.event_bus import get_event_bus, EventType, Event

logger = logging.getLogger(__name__)

class DocumentIntelligence:
    """
    Singleton service that indexes documents in the background, listens
    to file system changes, debounces modifications, and exposes search capability.
    """
    
    _instance: Optional[DocumentIntelligence] = None
    _lock = threading.Lock()

    def __init__(self) -> None:
        # Avoid re-initialization if singleton pattern is bypassed
        if hasattr(self, "_initialized") and self._initialized:
            return
            
        self._initialized = True
        self._running = False
        self._debounce_lock = threading.Lock()
        self._timers: dict[str, threading.Timer] = {}
        self._crawler_thread: Optional[threading.Thread] = None
        self._subscription = None

        # Ambient mode: crawler paused during active use, running during idle
        self._crawl_paused = threading.Event()
        self._crawl_paused.set()  # Start paused — wait for user.idle to crawl
        
        # Determine the target index directory (default to current workspace root)
        self.index_dir = Path(os.environ.get("DOC_INTEL_INDEX_DIR", ".")).resolve()

    @classmethod
    def get_instance(cls) -> DocumentIntelligence:
        """Thread-safe singleton accessor."""
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = DocumentIntelligence()
        return cls._instance

    def start(self) -> None:
        """Start the background crawler and listen for file system events."""
        with self._lock:
            if self._running:
                logger.warning("[DocIntel] Service already running.")
                return
                
            self._running = True
            logger.info(f"[DocIntel] Starting Document Intelligence service for: {self.index_dir}")

            # 1. Subscribe to event bus for file changes
            bus = get_event_bus()
            self._subscription = bus.subscribe(
                callback=self._handle_file_event,
                event_types={
                    EventType.FILE_CREATED,
                    EventType.FILE_MODIFIED,
                    EventType.FILE_DELETED,
                    EventType.FILE_MOVED,
                },
                name="doc_intelligence_watcher",
            )
            
            # Start directory watcher in the event bus if watchdog is installed
            bus.watch_directory(
                str(self.index_dir),
                recursive=True,
                patterns=["*.txt", "*.md", "*.pdf", "*.docx", "*.png", "*.jpg", "*.jpeg"]
            )

            # 2. Spawn directory crawler in a daemon background thread
            self._crawler_thread = threading.Thread(
                target=self._run_boot_crawler,
                daemon=True,
                name="doc-intel-crawler"
            )
            self._crawler_thread.start()

            # 3. Subscribe to ambient idle/active events for crawl gating
            self._subscribe_ambient()

    def _subscribe_ambient(self) -> None:
        """Subscribe to user.idle / user.active events for crawl gating."""
        try:
            bus = get_event_bus()
            bus.subscribe(
                callback=self._on_ambient_idle,
                event_types={EventType.USER_IDLE},
                name="doc_intel_idle_resume",
            )
            bus.subscribe(
                callback=self._on_ambient_active,
                event_types={EventType.USER_ACTIVE},
                name="doc_intel_active_pause",
            )
            logger.debug("[DocIntel] Subscribed to ambient idle/active events")
        except Exception as e:
            logger.warning(f"[DocIntel] Ambient subscription failed: {e}")

    def _on_ambient_idle(self, event) -> None:
        """Resume background crawling when user goes idle."""
        self._crawl_paused.clear()
        logger.info("[DocIntel] 💤 User idle — resuming background indexing")

    def _on_ambient_active(self, event) -> None:
        """Pause background crawling when user returns."""
        self._crawl_paused.set()
        logger.info("[DocIntel] ⚡ User active — pausing background indexing")

    def stop(self) -> None:
        """Stop background activities and clean up timers."""
        with self._lock:
            if not self._running:
                return
            self._running = False
            
            # 1. Unsubscribe from event bus
            if self._subscription:
                bus = get_event_bus()
                bus.unsubscribe(self._subscription)
                self._subscription = None
                
            # 2. Cancel all pending debounce timers
            with self._debounce_lock:
                for timer in self._timers.values():
                    timer.cancel()
                self._timers.clear()
                
            logger.info("[DocIntel] Service stopped successfully.")

    def _handle_file_event(self, event: Event) -> None:
        """Callback for file-system events received via the EventBus."""
        if not self._running:
            return
            
        payload = event.payload
        file_path = payload.get("path")
        if not file_path:
            return
            
        path = Path(file_path).resolve()
        
        # Skip internal files (git, cache, sqlite DB, venv)
        if any(part.startswith(".") or part in ["__pycache__", "chroma_db", "venv", "node_modules"] for part in path.parts):
            return

        ext = path.suffix.lower()
        if ext not in [".txt", ".md", ".pdf", ".docx", ".png", ".jpg", ".jpeg"]:
            return

        if event.event_type in [EventType.FILE_CREATED, EventType.FILE_MODIFIED]:
            self._enqueue_debounce(str(path))
        elif event.event_type == EventType.FILE_DELETED:
            self._cancel_debounce(str(path))
            # Delete from vector store instantly
            from memory.vector_store import delete_document_chunks
            delete_document_chunks(str(path))
        elif event.event_type == EventType.FILE_MOVED:
            dest_path = payload.get("dest_path")
            self._cancel_debounce(str(path))
            
            # Delete old chunks
            from memory.vector_store import delete_document_chunks
            delete_document_chunks(str(path))
            
            # Index new path if valid
            if dest_path:
                dest = Path(dest_path).resolve()
                if dest.suffix.lower() in [".txt", ".md", ".pdf", ".docx", ".png", ".jpg", ".jpeg"]:
                    self._enqueue_debounce(str(dest))

    def _enqueue_debounce(self, file_path: str) -> None:
        """Queue or reset a debounced parsing timer for a modified file."""
        with self._debounce_lock:
            # Cancel existing timer for this file
            if file_path in self._timers:
                self._timers[file_path].cancel()
                
            # Schedule a new indexing task
            timer = threading.Timer(
                interval=config.DOC_INTEL_DEBOUNCE_SEC,
                function=self._debounced_index_callback,
                args=[file_path]
            )
            self._timers[file_path] = timer
            timer.daemon = True
            timer.start()

    def _cancel_debounce(self, file_path: str) -> None:
        """Remove and cancel any pending debounce timers for a file."""
        with self._debounce_lock:
            if file_path in self._timers:
                self._timers[file_path].cancel()
                del self._timers[file_path]

    def _debounced_index_callback(self, file_path: str) -> None:
        """Callback executed when the debounce timer fires."""
        # Clean up the timer reference
        with self._debounce_lock:
            if file_path in self._timers:
                del self._timers[file_path]
                
        try:
            self.index_file(file_path)
        except Exception as e:
            logger.error(f"[DocIntel] Debounced index callback failed for {file_path}: {e}")

    def _run_boot_crawler(self) -> None:
        """Boot sequence directory walker. Indices up to DOC_INTEL_MAX_FILES_ON_BOOT files."""
        logger.info("[DocIntel] Crawling directory in background...")
        try:
            # Walk directory finding matching files
            files_to_index = []
            
            for root, dirs, files in os.walk(self.index_dir):
                # Prune internal and hidden folders in-place
                dirs[:] = [
                    d for d in dirs
                    if not d.startswith(".") 
                    and d not in ["__pycache__", "chroma_db", "venv", "node_modules", "tests", ".gemini", ".shared", ".agent"]
                ]
                
                for f in files:
                    ext = Path(f).suffix.lower()
                    if ext in [".txt", ".md", ".pdf", ".docx", ".png", ".jpg", ".jpeg"]:
                        full_path = Path(root) / f
                        files_to_index.append(full_path)
            
            # Sort by modified time (newest first)
            files_to_index.sort(key=lambda x: x.stat().st_mtime if x.exists() else 0, reverse=True)
            
            # Limit the number of files indexed on boot for low-power performance
            limit = config.DOC_INTEL_MAX_FILES_ON_BOOT
            crawl_list = files_to_index[:limit]
            
            logger.info(f"[DocIntel] Found {len(files_to_index)} supported files. Boot indexing capped at {len(crawl_list)}")
            
            count = 0
            for file_path in crawl_list:
                if not self._running:
                    break
                # Respect ambient mode: block while user is active
                if self._crawl_paused.is_set():
                    self._crawl_paused.wait(timeout=5.0)
                    if self._crawl_paused.is_set():
                        continue  # Still paused, check again
                try:
                    if self.index_file(str(file_path)):
                        count += 1
                except Exception as e:
                    logger.error(f"[DocIntel] Boot indexing failed for {file_path}: {e}")
                # Yield control to prevent CPU starvation on low-spec hardware
                time.sleep(0.05)
                
            logger.info(f"[DocIntel] Boot crawler finished. Index count: {count}/{len(crawl_list)}")
        except Exception as e:
            logger.error(f"[DocIntel] Background boot crawler crashed: {e}")

    def index_file(self, file_path: str) -> bool:
        """
        Extract text, segment into semantic chunks, and upload to vector database.
        
        Args:
            file_path: Absolute string path to the file.
            
        Returns:
            True if indexing succeeded and chunks were saved, False otherwise.
        """
        path = Path(file_path).resolve()
        if not path.exists() or path.is_dir():
            return False
            
        ext = path.suffix.lower()
        
        # 1. Parse text based on suffix
        text = ""
        try:
            if ext in [".txt", ".md"]:
                # Try UTF-8 first, fallback to CP1252/latin-1 for legacy text systems
                try:
                    text = path.read_text(encoding="utf-8")
                except UnicodeDecodeError:
                    text = path.read_text(encoding="latin-1")
            elif ext == ".pdf":
                text = self._parse_pdf(path)
            elif ext == ".docx":
                text = self._parse_docx(path)
            elif ext in [".png", ".jpg", ".jpeg"]:
                text = self._parse_image_ocr(path)
        except Exception as e:
            logger.error(f"[DocIntel] Parse failed for {path}: {e}")
            return False
            
        text = text.strip()
        if not text:
            logger.debug(f"[DocIntel] No text extracted from {path}, skipping index.")
            return False

        # 2. Clear out existing chunks from the collection
        from memory.vector_store import delete_document_chunks, store_document_chunk
        delete_document_chunks(str(path))

        # 3. Create semantic chunks
        chunks = self._chunk_text(text)
        if not chunks:
            return False

        # 4. Insert chunks into ChromaDB
        success = True
        total = len(chunks)
        
        for i, chunk in enumerate(chunks):
            stored = store_document_chunk(
                file_path=str(path),
                chunk_index=i,
                total_chunks=total,
                chunk_text=chunk,
                extra_metadata={
                    "file_name": path.name,
                    "file_extension": ext,
                }
            )
            if not stored:
                success = False

        if success:
            logger.info(f"[DocIntel] Indexed {path.name} -- {total} chunks stored.")
            # Notify systems of document intelligence update
            bus = get_event_bus()
            bus.emit(
                event_type=EventType.DOC_INDEXED,
                source="doc_intelligence",
                payload={
                    "path": str(path),
                    "file_name": path.name,
                    "chunks": total,
                }
            )
            
        return success

    # ── Lazy Document Parsers ────────────────────────────────────────────────

    def _parse_pdf(self, file_path: Path) -> str:
        """Parse native text from PDF, falling back to Tesseract OCR if text extraction yields nothing."""
        try:
            import pypdf
            reader = pypdf.PdfReader(str(file_path))
            parts = []
            for page in reader.pages:
                txt = page.extract_text()
                if txt:
                    parts.append(txt)
            text = "\n".join(parts).strip()
            
            if text:
                return text
                
            logger.info(f"[DocIntel] Native PDF text extraction empty for {file_path}. Falling back to OCR.")
            return self._parse_pdf_via_ocr(file_path)
            
        except ImportError:
            logger.warning(f"[DocIntel] pypdf library is not installed. Attempting OCR fallback for {file_path}.")
            return self._parse_pdf_via_ocr(file_path)
        except Exception as e:
            logger.error(f"[DocIntel] PDF native read failed for {file_path}: {e}")
            return self._parse_pdf_via_ocr(file_path)

    def _parse_pdf_via_ocr(self, file_path: Path) -> str:
        """Best-effort OCR fallback for scanned PDFs. Currently requires external rasterizer or yields warning."""
        # Genuine scanned PDFs require pdf2image to convert pages to images.
        # Since pdf2image requires poppler installed, we log a degradation notice rather than failing.
        logger.info(f"[DocIntel] Scanned PDF requires pdf2image + poppler to rasterize: {file_path}")
        return ""

    def _parse_docx(self, file_path: Path) -> str:
        """Parse text content from Microsoft Word document (.docx)."""
        try:
            import docx
            doc = docx.Document(str(file_path))
            return "\n".join(p.text for p in doc.paragraphs)
        except ImportError:
            logger.warning(f"[DocIntel] python-docx not installed, docx parsing skipped for {file_path}")
            return ""
        except Exception as e:
            logger.error(f"[DocIntel] Word document parse failed for {file_path}: {e}")
            return ""

    def _parse_image_ocr(self, file_path: Path) -> str:
        """Perform Tesseract OCR text extraction on image files with tight error categorization."""
        try:
            from PIL import Image
            import pytesseract
            
            # 1. Catch binary configuration failure separately (TesseractNotFoundError)
            try:
                pytesseract.get_tesseract_version()
            except pytesseract.TesseractNotFoundError as t_err:
                logger.error(f"[DocIntel] Tesseract OCR binary not found in system PATH. OCR fallback disabled: {t_err}")
                raise t_err
                
            # 2. General OCR parsing
            img = Image.open(file_path)
            text = pytesseract.image_to_string(img)
            return text
            
        except pytesseract.TesseractNotFoundError:
            # Catches missing OS binary error
            return ""
        except Exception as e:
            # Catches PIL image read failure, corrupt file, or general OCR exceptions
            logger.error(f"[DocIntel] General OCR execution failed for image {file_path}: {e}")
            return ""

    # ── Semantic Segmenter (Chunker) ──────────────────────────────────────────

    def _chunk_text(self, text: str, max_chars: int = 1000, overlap: int = 100) -> list[str]:
        """
        Splits text into chunks of roughly max_chars based on semantic boundaries
        like double newlines (paragraphs), single newlines (lines), and spaces.
        """
        if not text:
            return []

        # Split by double newline (paragraphs) first
        paragraphs = text.split("\n\n")
        chunks = []
        current_chunk = ""

        for paragraph in paragraphs:
            paragraph = paragraph.strip()
            if not paragraph:
                continue

            # If adding the paragraph does not exceed the chunk size
            if len(current_chunk) + len(paragraph) + 2 <= max_chars:
                current_chunk = f"{current_chunk}\n\n{paragraph}" if current_chunk else paragraph
            else:
                # Store the current chunk if it exists
                if current_chunk:
                    chunks.append(current_chunk)
                    # Overlap: keep trailing characters
                    current_chunk = current_chunk[-overlap:] if len(current_chunk) > overlap else current_chunk

                # Handle massive paragraphs by splitting them line-by-line
                if len(paragraph) > max_chars:
                    lines = paragraph.split("\n")
                    for line in lines:
                        line = line.strip()
                        if not line:
                            continue
                        if len(current_chunk) + len(line) + 1 <= max_chars:
                            current_chunk = f"{current_chunk}\n{line}" if current_chunk else line
                        else:
                            if current_chunk:
                                chunks.append(current_chunk)
                                current_chunk = current_chunk[-overlap:] if len(current_chunk) > overlap else current_chunk
                            
                            # If a single line is wider than max_chars, split by character sequence
                            if len(line) > max_chars:
                                for offset in range(0, len(line), max_chars - overlap):
                                    chunks.append(line[offset:offset + max_chars])
                                current_chunk = ""
                            else:
                                current_chunk = line
                else:
                    current_chunk = paragraph

        if current_chunk:
            chunks.append(current_chunk)

        return [c.strip() for c in chunks if c.strip()]


def get_doc_intelligence() -> DocumentIntelligence:
    """Thread-safe singleton accessor helper."""
    return DocumentIntelligence.get_instance()
