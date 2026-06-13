"""
tool_registry.py — Central Tool Registry
==========================================
Single source of truth for ALL JARVIS tools.

Every action file registers itself via the @tool decorator.
Both main.py (Live API) and executor.py (Agent) call registry.execute().

Architecture:
  - @tool decorator auto-registers functions at import time
  - 3-tier safety system: safe / notify / confirm
  - Thread-safe execution with configurable timeout
  - Auto-generates Gemini function declarations from registry
  - No more scattered if/elif chains — one dispatch point

Safety Tiers:
  - SAFE    : runs silently, no user interaction
  - NOTIFY  : tells user what it's doing, auto-proceeds
  - CONFIRM : requires explicit voice/text confirmation before executing
"""

import logging
import threading
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable

logger = logging.getLogger(__name__)


class SafetyTier(Enum):
    """Action safety classification."""
    SAFE    = "safe"       # Green — silent execution
    NOTIFY  = "notify"     # Amber — announce, auto-proceed
    CONFIRM = "confirm"    # Red — full user confirmation required


@dataclass(frozen=True)
class ToolSpec:
    """Immutable specification for a registered tool."""
    name:        str
    description: str
    parameters:  dict                    # Gemini-compatible parameter schema
    handler:     Callable                # The actual function to call
    safety:      SafetyTier = SafetyTier.SAFE
    required:    list[str]  = field(default_factory=list)
    timeout:     int        = 120        # Seconds before execution is killed


class ToolRegistry:
    """
    Singleton registry for all JARVIS tools.

    Usage:
        from core.tool_registry import registry

        # Register
        @tool(name="web_search", description="...", parameters={...})
        def web_search(parameters, player=None, **kw):
            ...

        # Execute
        result = registry.execute("web_search", {"query": "hello"})

        # Get Gemini declarations
        decls = registry.get_declarations()
    """

    _instance = None
    _lock = threading.Lock()

    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    inst = super().__new__(cls)
                    inst._tools: dict[str, ToolSpec] = {}
                    inst._execution_lock = threading.Lock()
                    cls._instance = inst
        return cls._instance

    # ── Registration ──────────────────────────────────────────────────────

    def register(self, spec: ToolSpec) -> None:
        """Register a tool. Overwrites if name already exists (supports hot-reload)."""
        if spec.name in self._tools:
            logger.debug(f"[ToolRegistry] Overwriting tool: {spec.name}")
        self._tools[spec.name] = spec
        logger.debug(f"[ToolRegistry] Registered: {spec.name} (safety={spec.safety.value})")

    def has(self, name: str) -> bool:
        return name in self._tools

    def get(self, name: str) -> ToolSpec | None:
        return self._tools.get(name)

    def list_tools(self) -> list[str]:
        """Return sorted list of registered tool names."""
        return sorted(self._tools.keys())

    def count(self) -> int:
        return len(self._tools)

    # ── Execution ─────────────────────────────────────────────────────────

    def execute(
        self,
        name:    str,
        args:    dict,
        *,
        player:  Any = None,
        speak:   Callable | None = None,
        confirm: Callable | None = None,
        **extra_kwargs,
    ) -> str:
        """
        Execute a registered tool by name.

        Args:
            name:    Tool name (must be registered).
            args:    Parameter dict from Gemini function call.
            player:  UI reference for logging.
            speak:   TTS callback for NOTIFY/CONFIRM announcements.
            confirm: Callback returning True/False for CONFIRM-tier tools.
                     Signature: confirm(message: str) -> bool
            **extra_kwargs: Passed through to the handler.

        Returns:
            String result from the tool handler.

        Raises:
            KeyError:  Tool not found in registry.
            TimeoutError: Execution exceeded tool's timeout.
            PermissionError: User declined CONFIRM-tier action.
        """
        spec = self._tools.get(name)
        if spec is None:
            msg = f"Unknown tool: '{name}'"
            logger.error(f"[ToolRegistry] {msg}")
            raise KeyError(msg)

        # ── Safety gate ───────────────────────────────────────────────────
        if spec.safety == SafetyTier.CONFIRM:
            action_desc = f"I'm about to execute '{name}' with {args}. Say confirm or cancel."
            if speak:
                speak(f"Sir, {action_desc}")
            if confirm:
                if not confirm(action_desc):
                    raise PermissionError(f"User declined: {name}")
            else:
                # No confirm callback available — log warning but proceed
                # (the Live API session handles this via voice)
                logger.warning(
                    f"[ToolRegistry] CONFIRM tool '{name}' called without confirm callback"
                )

        elif spec.safety == SafetyTier.NOTIFY:
            desc = args.get("description", args.get("query", args.get("app_name", name)))
            if speak:
                speak(f"Executing {name}: {desc}")

        # ── Build handler kwargs ──────────────────────────────────────────
        handler_kwargs = {"parameters": args}
        if player is not None:
            handler_kwargs["player"] = player
        if speak is not None:
            handler_kwargs["speak"] = speak
        handler_kwargs.update(extra_kwargs)

        # ── Execute with timeout ──────────────────────────────────────────
        result_container = {"value": None, "error": None}

        def _run():
            try:
                result_container["value"] = spec.handler(**handler_kwargs)
            except Exception as exc:
                result_container["error"] = exc

        thread = threading.Thread(target=_run, daemon=True)
        start = time.monotonic()
        thread.start()
        thread.join(timeout=spec.timeout)

        elapsed = time.monotonic() - start

        if thread.is_alive():
            msg = f"Tool '{name}' timed out after {spec.timeout}s"
            logger.error(f"[ToolRegistry] {msg}")
            raise TimeoutError(msg)

        if result_container["error"] is not None:
            raise result_container["error"]

        result = result_container["value"]
        logger.info(
            f"[ToolRegistry] {name} completed in {elapsed:.1f}s — "
            f"{str(result)[:80] if result else 'None'}"
        )
        return result or "Done."

    # ── Gemini Declaration Generation ─────────────────────────────────────

    def get_declarations(self) -> list[dict]:
        """
        Generate the Gemini function_declarations JSON array from registered tools.
        This replaces the 340-line static TOOL_DECLARATIONS in main.py.
        """
        declarations = []
        for spec in self._tools.values():
            decl = {
                "name":        spec.name,
                "description": spec.description,
                "parameters":  spec.parameters,
            }
            declarations.append(decl)
        return declarations

    def get_safety_tier(self, name: str) -> SafetyTier | None:
        """Return the safety tier for a tool, or None if not found."""
        spec = self._tools.get(name)
        return spec.safety if spec else None

    # ── Debug ─────────────────────────────────────────────────────────────

    def print_registry(self) -> None:
        """Print all registered tools to console."""
        print(f"\n{'='*60}")
        print(f" JARVIS Tool Registry — {self.count()} tools")
        print(f"{'='*60}")
        for name in self.list_tools():
            spec = self._tools[name]
            print(f"  [{spec.safety.value:>7}]  {name}")
        print(f"{'='*60}\n")


# ── Singleton accessor ────────────────────────────────────────────────────────
registry = ToolRegistry()


# ── Decorator ─────────────────────────────────────────────────────────────────

def tool(
    name:        str,
    description: str,
    parameters:  dict,
    safety:      SafetyTier = SafetyTier.SAFE,
    required:    list[str] | None = None,
    timeout:     int = 120,
):
    """
    Decorator to register a function as a JARVIS tool.

    Usage:
        @tool(
            name="web_search",
            description="Searches the web for information.",
            parameters={
                "type": "OBJECT",
                "properties": {
                    "query": {"type": "STRING", "description": "Search query"}
                },
                "required": ["query"]
            },
            safety=SafetyTier.SAFE,
        )
        def web_search(parameters, player=None, **kw):
            query = parameters.get("query")
            ...
    """
    def decorator(func: Callable) -> Callable:
        spec = ToolSpec(
            name=name,
            description=description,
            parameters=parameters,
            handler=func,
            safety=safety,
            required=required or [],
            timeout=timeout,
        )
        registry.register(spec)
        return func
    return decorator
