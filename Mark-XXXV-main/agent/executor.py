import json
import re
import sys
import threading
import subprocess
import tempfile
import os
from pathlib import Path
from typing import Callable

from agent.planner       import create_plan, replan
from agent.error_handler import analyze_error, generate_fix, ErrorDecision

# Load all tool definitions into the central registry at import time
import core.tool_defs  # noqa: F401 — side-effect import registers all @tool handlers


def get_base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent


def _run_generated_code(description: str, speak: Callable | None = None) -> str:
    from core.llm_orchestrator import LLMOrchestrator, TaskTier
    orchestrator = LLMOrchestrator()

    if speak:
        speak("Writing custom code for this task, sir.")

    home      = Path.home()
    desktop   = home / "Desktop"
    downloads = home / "Downloads"
    documents = home / "Documents"

    if not desktop.exists():
        try:
            import winreg
            key     = winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Explorer\Shell Folders")
            desktop = Path(winreg.QueryValueEx(key, "Desktop")[0])
        except Exception:
            pass

    try:
        prompt = (
            "You are an expert Python developer. "
            "Write clean, complete, working Python code. "
            "Use standard library + common packages. "
            "Install missing packages with subprocess + pip if needed. "
            "Return ONLY the Python code. No explanation, no markdown, no backticks.\n\n"
            f"SYSTEM PATHS:\n"
            f"  Desktop   = r'{desktop}'\n"
            f"  Downloads = r'{downloads}'\n"
            f"  Documents = r'{documents}'\n"
            f"  Home      = r'{home}'\n\n"
            f"Write Python code to accomplish this task:\n\n{description}"
        )
        response = orchestrator.generate_content_with_retry(TaskTier.COMPLEX, prompt)
        code = response.text.strip()
        code = re.sub(r"```(?:python)?", "", code).strip().rstrip("`").strip()

        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".py", delete=False, encoding="utf-8"
        ) as f:
            f.write(code)
            tmp_path = f.name

        print(f"[Executor] 🐍 Running generated code: {tmp_path}")

        result = subprocess.run(
            [sys.executable, tmp_path],
            capture_output=True, text=True,
            timeout=120, cwd=str(Path.home())
        )

        try:
            os.unlink(tmp_path)
        except Exception:
            pass

        output = result.stdout.strip()
        error  = result.stderr.strip()

        if result.returncode == 0 and output:
            return output
        elif result.returncode == 0:
            return "Task completed successfully."
        elif error:
            raise RuntimeError(f"Code error: {error[:400]}")
        return "Completed."

    except subprocess.TimeoutExpired:
        raise RuntimeError("Generated code timed out after 120 seconds.")
    except RuntimeError:
        raise
    except Exception as e:
        raise RuntimeError(f"Generated code failed: {e}")

def _inject_context(params: dict, tool: str, step_results: dict, goal: str = "") -> dict:
    if not step_results:
        return params

    params = dict(params)

    if tool == "file_controller" and params.get("action") in ("write", "create_file"):
        content = params.get("content", "")
        if not content or len(content) < 50:
            all_results = [
                v for v in step_results.values()
                if v and len(v) > 100 and v not in ("Done.", "Completed.")
            ]
            if all_results:
                combined = "\n\n---\n\n".join(all_results)
                translated = _translate_to_goal_language(combined, goal)
                params["content"] = translated
                print(f"[Executor] 💉 Injected + translated content")

    return params
def _detect_language(text: str) -> str:
    from core.llm_orchestrator import LLMOrchestrator, TaskTier
    orchestrator = LLMOrchestrator()
    try:
        response = orchestrator.generate_content_with_retry(
            TaskTier.ROUTING,
            f"What language is this text written in? "
            f"Reply with ONLY the language name in English (e.g. Turkish, English, French).\n\n"
            f"Text: {text[:200]}"
        )
        return response.text.strip()
    except Exception:
        return "English"


def _translate_to_goal_language(content: str, goal: str) -> str:
    if not goal:
        return content
    try:
        from core.llm_orchestrator import LLMOrchestrator, TaskTier
        orchestrator = LLMOrchestrator()

        target_lang = _detect_language(goal)
        print(f"[Executor] 🌐 Translating to: {target_lang}")

        prompt = (
            f"You are a professional translator. "
            f"Translate the following text into {target_lang}.\n"
            f"IMPORTANT:\n"
            f"- Translate EVERYTHING, leave nothing in English\n"
            f"- Keep all facts, numbers, and data intact\n"
            f"- Keep the structure and formatting\n"
            f"- Output ONLY the translated text, nothing else\n\n"
            f"Text to translate:\n{content[:4000]}"
        )
        response = orchestrator.generate_content_with_retry(TaskTier.LOGIC, prompt)
        translated = response.text.strip()
        print(f"[Executor] ✅ Translation done ({target_lang})")
        return translated
    except Exception as e:
        print(f"[Executor] ⚠️ Translation failed: {e}")
        return content

def _call_tool(tool: str, parameters: dict, speak: Callable | None) -> str:
    """Dispatch a tool call through the central registry (or fallback to generated code)."""
    from core.tool_registry import registry

    # Direct generated_code path — planner can emit this as a synthetic tool
    if tool == "generated_code":
        description = parameters.get("description", "")
        if not description:
            raise ValueError("generated_code requires a 'description' parameter.")
        return _run_generated_code(description, speak=speak)

    # Registry lookup — covers all 19 registered tools
    if registry.has(tool):
        return registry.execute(tool, parameters, speak=speak)

    # Unknown tool — last resort: generate code to accomplish it
    print(f"[Executor] ⚠️ Unknown tool '{tool}' — falling back to generated_code")
    return _run_generated_code(f"Accomplish this task: {parameters}", speak=speak)

class AgentExecutor:
    """
    High-level executor that delegates to the ReAct agent loop.

    The TaskQueue calls executor.execute(goal) — this method creates an
    initial plan sketch (Plan-then-ReAct) and hands off to ReactAgent
    for autonomous Thought-Action-Observation execution.
    """

    MAX_PLAN_STEPS = 5  # Cap initial plan sketch length

    def execute(
        self,
        goal:        str,
        speak:       Callable | None        = None,
        cancel_flag: threading.Event | None = None,
    ) -> str:
        print(f"\n[Executor] Target: {goal}")

        # ── Phase 0: Intelligence Router (free sources first) ─────────────
        try:
            from core.intelligence_router import get_router
            router = get_router()
            result = router.route(goal)
            if result is not None:
                print(
                    f"[Executor] Router handled via '{result.source}' "
                    f"({result.duration_ms}ms, ${result.cost:.4f})"
                )
                answer = result.answer
                if speak:
                    speak(answer[:500])
                return answer
        except Exception as exc:
            print(f"[Executor] Router skipped ({exc}), falling through to ReAct")

        # ── Phase 1: Plan sketch (optional, for structure) ────────────────
        initial_plan = None
        try:
            plan = create_plan(goal)
            steps = plan.get("steps", [])
            if steps:
                initial_plan = steps[:self.MAX_PLAN_STEPS]
                print(f"[Executor] Plan sketch: {len(initial_plan)} steps")
        except Exception as exc:
            print(f"[Executor] Planning failed ({exc}), proceeding with pure ReAct")

        # ── Phase 2: ReAct autonomous loop ────────────────────────────────
        from agent.react_agent import ReactAgent

        agent = ReactAgent(
            max_steps=15,
            speak=speak,
            cancel_flag=cancel_flag,
        )

        result = agent.run(goal=goal, initial_plan=initial_plan)

        # ── Phase 3: Summarize ────────────────────────────────────────────
        if result and not result.startswith(("Task cancelled", "ReAct loop crashed")):
            return self._summarize(goal, result, speak)

        return result

    def _summarize(self, goal: str, react_result: str, speak: Callable | None) -> str:
        """Generate a polished, user-facing summary from the ReAct result."""
        fallback = react_result or f"All done, sir. Completed task: {goal[:60]}."
        try:
            from core.llm_orchestrator import LLMOrchestrator, TaskTier
            orchestrator = LLMOrchestrator()
            prompt = (
                f'User goal: "{goal}"\n'
                f"Agent result: {react_result[:1000]}\n\n"
                "Write a single natural sentence summarizing what was accomplished. "
                "Address the user as 'sir'. Be direct and positive."
            )
            response = orchestrator.generate_content_with_retry(TaskTier.ROUTING, prompt)
            summary = response.text.strip()
            if speak:
                speak(summary)
            return summary
        except Exception:
            if speak:
                speak(fallback)
            return fallback