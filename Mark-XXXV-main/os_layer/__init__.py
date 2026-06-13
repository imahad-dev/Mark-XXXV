"""
os_layer — OS-Level Intelligence System
=========================================
Extends JARVIS from a voice-command assistant to a full OS-aware system
with continuous screen awareness, window control, workspace persistence,
and event-driven reactivity.

Architectural Constraints
-------------------------
- **Do NOT import from core.llm_orchestrator** — it is a high-betweenness
  god-node (121 edges). Integrate via ToolRegistry and IntelligenceRouter.
- All heavy imports are lazy-loaded to keep startup fast.
- Every component is independently toggleable via `.env` config flags.
- Target hardware: GTX 960M (2GB VRAM), 16GB RAM — CPU-first design.

Module Inventory
----------------
Phase 1:
    screen_intel      – Continuous screen awareness (UIA → OCR fallback)
    window_manager    – Window & process orchestration (win32gui)
    workspace_memory  – Persistent workspace state across sessions

Phase 2:
    event_bus         – Event-driven reactive system (asyncio + watchdog)
    workflow_recorder – Record → store → replay user workflows

Phase 3:
    doc_intelligence  – Background document indexing & semantic search

Phase 4:
    parallel_engine   – Multi-agent concurrent execution
    knowledge_graph   – SQLite-backed relationship graph

Phase 5:
    ambient_mode      – Low-power passive context awareness
    predictive        – Usage pattern analysis & pre-computation

Phase 6 (OS Layer 2):
    actuation         – Universal actuation engine (UI, FS, Shell, Browser)
"""

__all__ = [
    "screen_intel",
    "window_manager",
    "workspace_memory",
    "event_bus",
    "workflow_recorder",
    "doc_intelligence",
    "communication",
    "parallel_engine",
    "knowledge_graph",
    "ambient_mode",
    "predictive",
    "actuation",
]
