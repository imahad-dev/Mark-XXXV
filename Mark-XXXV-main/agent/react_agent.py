"""
agent/react_agent.py
====================
Autonomous ReAct (Reasoning + Acting) loop for JARVIS.

Architecture
------------
Each iteration:
  1. THINK  — LLM examines goal + scratchpad, decides next action
  2. ACT    — Tool dispatched via ToolRegistry
  3. OBSERVE — Result captured, appended to scratchpad, persisted to AgentMemory

The loop terminates when:
  - LLM emits action="FINISH" (goal achieved)
  - max_steps reached (safety guard)
  - cancel_flag is set (user cancellation)
  - Unrecoverable error after retry exhaustion

Integration Points
------------------
  - core.tool_registry  — tool execution + listing
  - memory.agent_memory — durable task/episode/step persistence
  - core.llm_orchestrator — thinking LLM calls
  - agent.planner — optional initial plan sketch (Plan-then-ReAct)
  - agent.error_handler — error recovery decisions
"""

from __future__ import annotations

import json
import re
import threading
import time
from dataclasses import dataclass, field
from typing import Callable, Optional

from core.tool_registry import registry


# ── Constants ────────────────────────────────────────────────────────────────

DEFAULT_MAX_STEPS = 15
THINK_TIMEOUT_SECONDS = 30
SCRATCHPAD_TRUNCATE_CHARS = 3000  # Per-observation cap to prevent prompt explosion


# ── Structured output types ──────────────────────────────────────────────────

@dataclass
class AgentAction:
    """LLM decided to call a tool."""
    thought: str
    tool: str
    tool_input: dict = field(default_factory=dict)


@dataclass
class AgentFinish:
    """LLM decided the goal is complete."""
    thought: str
    summary: str


# ── System prompt ────────────────────────────────────────────────────────────

REACT_SYSTEM_PROMPT = """You are JARVIS, an autonomous AI agent executing a multi-step task.
You operate in a THINK-ACT-OBSERVE loop. Each turn you must:

1. THINK: Analyze the goal, what you know so far, and decide the single best next action.
2. ACT: Call exactly ONE tool, OR signal FINISH if the goal is achieved.

RULES:
- Call ONE tool per turn. Never batch multiple tools.
- Use observations from previous steps to inform your next action.
- If a tool fails, analyze the error and try an alternative approach.
- Do NOT repeat the same tool call with identical arguments — that is a loop.
- When the goal is fully achieved, use action "FINISH".
- Max {max_steps} steps total. Be efficient.

AVAILABLE TOOLS:
{tool_descriptions}

OUTPUT FORMAT — return ONLY valid JSON, no markdown, no explanation:

To call a tool:
{{"thought": "reasoning about what to do next", "action": "<tool_name>", "action_input": {{"param": "value"}}}}

To finish:
{{"thought": "reasoning about why the goal is complete", "action": "FINISH", "action_input": {{"summary": "what was accomplished"}}}}
"""


# ── ReactAgent ───────────────────────────────────────────────────────────────

class ReactAgent:
    """
    Autonomous ReAct loop that thinks, acts, and observes until goal completion.

    Args:
        max_steps:   Hard ceiling on iterations (prevents infinite loops / credit burn).
        speak:       TTS callback for user-facing announcements.
        cancel_flag: Threading event — set externally to abort the loop.
    """

    def __init__(
        self,
        max_steps: int = DEFAULT_MAX_STEPS,
        speak: Optional[Callable] = None,
        cancel_flag: Optional[threading.Event] = None,
    ):
        self._max_steps = max_steps
        self._speak = speak
        self._cancel_flag = cancel_flag or threading.Event()
        self._scratchpad: list[dict] = []  # [{role, content}] conversation history
        self._step_count = 0

    # ── Public entry point ────────────────────────────────────────────────

    def run(self, goal: str, initial_plan: Optional[list[dict]] = None) -> str:
        """
        Execute the ReAct loop for a goal.

        Args:
            goal:         Natural language description of what to accomplish.
            initial_plan: Optional plan sketch from planner (Plan-then-ReAct).

        Returns:
            Summary string describing what was accomplished.
        """
        from memory.agent_memory import get_agent_memory

        mem = get_agent_memory()

        # Persistence: create or resume task
        task = mem.get_or_resume_task(goal, max_steps=self._max_steps)
        episode = mem.start_episode(
            task.id,
            plan=[s.get("description", "") for s in initial_plan] if initial_plan else [],
        )

        # Seed scratchpad with plan context if available
        if initial_plan:
            plan_text = "\n".join(
                f"  {s.get('step', i+1)}. [{s.get('tool', '?')}] {s.get('description', '')}"
                for i, s in enumerate(initial_plan)
            )
            self._scratchpad.append({
                "role": "plan",
                "content": f"Initial plan sketch:\n{plan_text}\n\n"
                           f"Use this as guidance, but adapt based on observations.",
            })

        print(f"\n[ReAct] === Starting loop for: {goal[:80]} ===")
        print(f"[ReAct] Task: {task.id} | Episode: {episode.id} | Max steps: {self._max_steps}")

        result = "Task did not complete."

        try:
            while self._step_count < self._max_steps:
                if self._cancel_flag.is_set():
                    result = "Task cancelled by user."
                    mem.complete_episode(episode.id, status="failed", outcome=result)
                    mem.update_task_status(task.id, "paused", result=result)
                    if self._speak:
                        self._speak("Task cancelled, sir.")
                    return result

                # ── THINK ────────────────────────────────────────────────
                decision = self._think(goal)

                if decision is None:
                    result = "Agent failed to produce a valid decision."
                    break

                # ── FINISH signal ────────────────────────────────────────
                if isinstance(decision, AgentFinish):
                    print(f"[ReAct] FINISH: {decision.summary[:100]}")
                    result = decision.summary
                    mem.complete_episode(episode.id, status="success", outcome=result)
                    mem.update_task_status(task.id, "done", result=result)
                    if self._speak:
                        self._speak(result)
                    return result

                # ── ACT + OBSERVE ────────────────────────────────────────
                self._step_count += 1
                mem.increment_steps(task.id)

                step_record = mem.log_step(
                    episode_id=episode.id,
                    task_id=task.id,
                    step_num=self._step_count,
                    tool_name=decision.tool,
                    tool_args=decision.tool_input,
                )

                observation = self._act(decision)

                # Persist observation
                is_error = observation.startswith("ERROR:")
                mem.complete_step(
                    step_id=step_record.id,
                    result=None if is_error else observation[:2000],
                    error=observation[:2000] if is_error else None,
                    observation=observation[:2000],
                )

                # Append to scratchpad for next think cycle
                self._observe(decision, observation)

            else:
                # Max steps exhausted
                result = f"Reached maximum of {self._max_steps} steps. Partial progress saved."
                if self._speak:
                    self._speak(f"I've hit my step limit of {self._max_steps}, sir. Saving progress.")

            # If we exit the loop without FINISH
            mem.complete_episode(episode.id, status="timeout", outcome=result)
            mem.update_task_status(task.id, "paused", result=result)
            return result

        except Exception as exc:
            error_msg = f"ReAct loop crashed: {exc}"
            print(f"[ReAct] FATAL: {error_msg}")
            mem.complete_episode(episode.id, status="failed", outcome=error_msg)
            mem.update_task_status(task.id, "failed", error=error_msg)
            return error_msg

    # ── THINK ─────────────────────────────────────────────────────────────

    def _think(self, goal: str) -> Optional[AgentAction | AgentFinish]:
        """
        Call the LLM with goal + scratchpad. Parse structured JSON response.
        Returns AgentAction, AgentFinish, or None on parse failure.
        """
        from core.llm_orchestrator import LLMOrchestrator, TaskTier

        orchestrator = LLMOrchestrator()
        tool_descriptions = self._build_tool_descriptions()

        system = REACT_SYSTEM_PROMPT.format(
            max_steps=self._max_steps,
            tool_descriptions=tool_descriptions,
        )

        # Build the user prompt: goal + scratchpad
        user_parts = [f"GOAL: {goal}\n"]

        if self._scratchpad:
            user_parts.append("EXECUTION LOG:")
            for entry in self._scratchpad:
                role = entry["role"].upper()
                content = entry["content"]
                user_parts.append(f"[{role}] {content}")

        user_parts.append(
            f"\nStep {self._step_count + 1}/{self._max_steps}. "
            f"What is your next action? Return JSON only."
        )

        prompt = "\n".join(user_parts)

        try:
            response = orchestrator.generate_content_with_retry(
                TaskTier.LOGIC,
                prompt,
                system_instruction=system,
            )
            raw = response.text.strip()
            return self._parse_llm_output(raw)

        except Exception as exc:
            print(f"[ReAct] Think failed: {exc}")
            # Append error to scratchpad so next iteration knows
            self._scratchpad.append({
                "role": "system",
                "content": f"Thinking error: {exc}. Retrying...",
            })
            return None

    # ── ACT ───────────────────────────────────────────────────────────────

    def _act(self, action: AgentAction) -> str:
        """
        Execute a tool call. Returns the observation string.
        On failure, returns "ERROR: <message>" so the LLM can self-correct.
        """
        tool_name = action.tool
        tool_args = action.tool_input

        print(f"[ReAct] Step {self._step_count}: [{tool_name}] {json.dumps(tool_args)[:100]}")

        # Special case: generated_code passthrough
        if tool_name == "generated_code":
            from agent.executor import _run_generated_code
            try:
                desc = tool_args.get("description", "")
                if not desc:
                    return "ERROR: generated_code requires a 'description' parameter."
                return _run_generated_code(desc, speak=self._speak)
            except Exception as exc:
                return f"ERROR: {exc}"

        # Registry dispatch
        if not registry.has(tool_name):
            msg = f"Unknown tool '{tool_name}'. Available: {', '.join(registry.list_tools())}"
            print(f"[ReAct] {msg}")
            return f"ERROR: {msg}"

        try:
            result = registry.execute(
                tool_name,
                tool_args,
                speak=self._speak,
            )
            truncated = str(result)[:SCRATCHPAD_TRUNCATE_CHARS]
            print(f"[ReAct] Result: {truncated[:120]}...")
            return truncated

        except (TimeoutError, PermissionError, KeyError) as exc:
            return f"ERROR: {type(exc).__name__}: {exc}"

        except Exception as exc:
            # Attempt error analysis for self-correction
            return self._handle_tool_error(action, exc)

    # ── OBSERVE ───────────────────────────────────────────────────────────

    def _observe(self, action: AgentAction, observation: str) -> None:
        """Append the action + observation pair to the scratchpad."""
        self._scratchpad.append({
            "role": "action",
            "content": f"Tool: {action.tool} | Args: {json.dumps(action.tool_input)[:200]}",
        })
        self._scratchpad.append({
            "role": "observation",
            "content": observation[:SCRATCHPAD_TRUNCATE_CHARS],
        })

    # ── Error recovery ────────────────────────────────────────────────────

    def _handle_tool_error(self, action: AgentAction, exc: Exception) -> str:
        """
        Analyze a tool error using the error handler.
        Returns an observation string that includes the error + recovery hint.
        """
        from agent.error_handler import analyze_error, ErrorDecision

        error_msg = str(exc)
        step_dict = {
            "step": self._step_count,
            "tool": action.tool,
            "description": action.thought,
            "parameters": action.tool_input,
        }

        try:
            recovery = analyze_error(step_dict, error_msg, attempt=1)
            decision = recovery.get("decision", ErrorDecision.REPLAN)
            reason = recovery.get("reason", error_msg)
            fix = recovery.get("fix_suggestion", "")

            hint = f"ERROR: {reason}"
            if fix:
                hint += f" | Suggestion: {fix}"

            if decision == ErrorDecision.ABORT:
                hint += " | RECOMMENDATION: This task cannot be completed. Consider FINISH."

            return hint

        except Exception:
            return f"ERROR: {error_msg}"

    # ── LLM output parsing ───────────────────────────────────────────────

    def _parse_llm_output(self, raw: str) -> Optional[AgentAction | AgentFinish]:
        """
        Parse the LLM's JSON response into AgentAction or AgentFinish.
        Handles common LLM quirks: markdown fences, trailing text, etc.
        """
        # Strip markdown code fences
        cleaned = re.sub(r"```(?:json)?", "", raw).strip().rstrip("`").strip()

        # Try to find JSON object in the response
        json_match = re.search(r"\{.*\}", cleaned, re.DOTALL)
        if not json_match:
            print(f"[ReAct] No JSON found in LLM output: {raw[:200]}")
            return None

        try:
            data = json.loads(json_match.group())
        except json.JSONDecodeError as exc:
            print(f"[ReAct] JSON parse error: {exc} | Raw: {raw[:200]}")
            return None

        thought = data.get("thought", "")
        action = data.get("action", "")
        action_input = data.get("action_input", {})

        if not action:
            print(f"[ReAct] Missing 'action' field in: {data}")
            return None

        # Ensure action_input is a dict
        if not isinstance(action_input, dict):
            action_input = {"value": action_input}

        if action.upper() == "FINISH":
            summary = action_input.get("summary", thought)
            print(f"[ReAct] THINK: {thought[:100]}")
            return AgentFinish(thought=thought, summary=summary)

        print(f"[ReAct] THINK: {thought[:100]}")
        return AgentAction(thought=thought, tool=action, tool_input=action_input)

    # ── Tool description builder ──────────────────────────────────────────

    @staticmethod
    def _build_tool_descriptions() -> str:
        """
        Build a compact tool listing from the registry for prompt injection.
        Format: tool_name — description | params: param1, param2
        """
        lines = []
        for name in registry.list_tools():
            spec = registry.get(name)
            if spec is None:
                continue
            params = spec.parameters.get("properties", {})
            param_names = ", ".join(params.keys()) if params else "none"
            required = spec.parameters.get("required", [])
            req_str = f" (required: {', '.join(required)})" if required else ""
            lines.append(f"  {name} -- {spec.description[:120]} | params: {param_names}{req_str}")

        return "\n".join(lines)
