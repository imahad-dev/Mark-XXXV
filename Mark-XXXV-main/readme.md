# 🤖 MARK XXXV — Enterprise Autonomous Voice & OS Intelligence System

### Next-Generation Autonomous AI Assistant — By [imahad-dev](https://github.com/imahad-dev)

**MARK XXXV** is a real-time, voice-driven autonomous AI agent and OS intelligence system designed to hear, see, reason, plan, and control your Windows computer.

Built with enterprise-grade multi-agent concurrency, persistent task memory, automatic context compression, and continuous background goal execution, MARK XXXV operates with near-zero latency while maintaining strict cost ceilings and safety gates.

---

## ✨ System Architecture & Key Capabilities

### 🧠 1. Long-Term Goal Loop & Background Orchestration (`GoalOrchestrator`)
- **Continuous Multi-Hour Goal Execution**: Automatically decomposes high-level user goals into structured sub-tasks and executes them sequentially in the background.
- **Dedicated Dispatch Queue**: Background goals bypass interactive user queues to guarantee zero latency during live voice interaction.
- **Safety Ceilings & Human-in-the-Loop**: Enforces per-goal cost ceilings (`GOAL_MAX_COST_USD`), maximum sub-goal limits (`GOAL_MAX_SUB_GOALS`), and recursive delegation depth caps (`GOAL_MAX_DELEGATION_DEPTH`).
- **Suspension & Resumption**: Automatically pauses goals for human approval when safety thresholds or confirmation gates are hit, resuming cleanly via EventBus events.

### ⚡ 2. Autonomous ReAct Agent & Context Compression (`ReactAgent`)
- **Reasoning + Acting Loop**: Step-by-step THINK-ACT-OBSERVE execution cycle with structured JSON output and automated self-correction error handling.
- **Dynamic Fact Pinning**: Preserves critical task facts (goals, task IDs, key discoveries) across infinite turns without truncation.
- **Map-Reduce Context Compression**: Summarizes execution scratchpads exceeding token thresholds, persisting compressed context checkpoints to SQLite for crash recovery and cross-session resumption.

### 🛡️ 3. Global Concurrency & Resource Pooling (`ParallelAgentEngine`)
- **Global Concurrency Ceiling**: Thread-safe singleton enforcing system-wide agent concurrency ceilings (`MAX_CONCURRENT_AGENTS`) to guarantee API rate-limit headroom.
- **Deadlock-Free Execution**: Automatically isolates nested and delegated sub-tasks into dedicated worker pools.
- **Exponential Backoff**: Resilient retry strategies with jitter for network and API rate-limiting recovery.

### 💾 4. Multi-Day Persistent Memory & Cost Accounting (`AgentMemory` & `CreditTracker`)
- **WAL-Mode SQLite Persistence**: Durable database tracking for tasks, episodes, steps, and execution outcomes (`agent_episodes.db`).
- **Per-Goal USD Budget Ledger**: Real-time token usage attribution and persistent goal cost tracking.
- **Session Workspace State**: Save and restore active workspace states, tasks, and memory briefings across reboots.

### 🐚 5. Persistent Agentic Shell (`AgenticShell`)
- **Background PowerShell Session**: Persistent, state-retaining terminal runner for executing system commands, scripts, and build tools.
- **Command Security Classification**: Automatic risk categorization (File Operations, Build Tools, Network Calls) with security validation.

### 👁️ 6. Screen & OS Intelligence (`ScreenIntel` & `WindowManager`)
- **Visual Awareness & OCR**: Full screen context analysis, layout mapping, and active window monitoring.
- **Window Management**: Process discovery, layout saving/restoration, and resource-hog identification.

### 🎙️ 7. Real-time Voice & Multimodal Interaction (`JarvisLive` & HUD)
- **Zero-Latency Gemini Live Integration**: Bidirectional WebSocket audio streaming for natural conversation.
- **Dual VAD & Speech Recognition**: Silero VAD coupled with Faster Whisper (and Vosk offline fallback).
- **Interactive Cybernetic HUD**: Pygame and Webview interfaces displaying real-time agent status (`LISTENING`, `SPEAKING`, `THINKING`, `MUTED`), visual telemetry, Mute toggle (`F4`), and instant text input console.

---

## ⚡ Quick Start Guide

### Prerequisites
* **Operating System**: Windows 10 / 11
* **Python**: Python 3.11 or 3.12
* **Hardware**: Microphone & Speakers
* **API Key**: Free [Gemini API Key](https://aistudio.google.com/apikey)

### Installation

```bash
# Clone the repository
git clone https://github.com/imahad-dev/Mark-XXXV.git
cd Mark-XXXV

# Install dependencies
pip install -r requirements.txt
playwright install

# Launch MARK XXXV
python main.py
```

Enter your Gemini API key when prompted on first launch.

---

## 🎮 Key Controls & Hotkeys

| Control | Action | Description |
| :--- | :--- | :--- |
| **F4 / UI Click** | Mute Microphone | Instantly silences audio input to prevent background conversation triggers |
| **Console Bar** | Text Input | Type direct text commands to the agent without speaking |
| **GUI Dashboard** | Status HUD | Monitor live agent states (`LISTENING`, `THINKING`, `SPEAKING`, `MUTED`) and cost tracking |

---

## 🔒 Security & Safety Design

- **Local Storage**: All session data, task logs, SQLite databases, and encrypted credentials remain 100% on your local machine.
- **Delegation Guardrails**: Hard limits on delegation depth prevent runaway sub-agent recursion.
- **Budget Protection**: Per-goal cost limits automatically suspend background tasks before exceeding dollar budgets.

---

## 🧑‍💻 Author & Support

Developed, expanded, and maintained by **imahad-dev**.

* **GitHub**: [https://github.com/imahad-dev](https://github.com/imahad-dev)
* **Instagram**: [https://www.instagram.com/mahad_.x1/](https://www.instagram.com/mahad_.x1/)

⭐ **Star the repository** to support ongoing development!
