<div align="center">

# 🤖 MARK XXXV
### Enterprise Autonomous Voice & OS Intelligence System
**Engineered & Maintained by [@imahad-dev](https://github.com/imahad-dev)**

[![Python Version](https://img.shields.io/badge/Python-3.11%20%7C%203.12-3776AB?style=for-the-badge&logo=python&logoColor=white)](https://www.python.org/)
[![AI Engine](https://img.shields.io/badge/Google%20GenAI-Gemini%202.5%20Live-4285F4?style=for-the-badge&logo=google&logoColor=white)](https://aistudio.google.com/)
[![Speech Pipeline](https://img.shields.io/badge/VAD%20%26%20STT-Silero%20%7C%20Faster--Whisper-FF6F00?style=for-the-badge)](https://github.com/SYSTRAN/faster-whisper)
[![Architecture](https://img.shields.io/badge/Engine-ReAct%20%2B%20Goal%20Loop-8E24AA?style=for-the-badge)]()
[![License](https://img.shields.io/badge/License-CC%20BY--NC%204.0-lightgrey?style=for-the-badge)](LICENSE)
[![Instagram](https://img.shields.io/badge/Instagram-@mahad_.x1-E4405F?style=for-the-badge&logo=instagram&logoColor=white)](https://www.instagram.com/mahad_.x1/)

<p align="center">
  <b>A real-time, bidirectional voice AI paired with a continuous multi-hour autonomous agent loop capable of hearing, seeing, reasoning, and fully controlling Windows systems.</b>
</p>

[Key Features](#-key-features) •
[Architecture](#-system-architecture) •
[Quick Start](#-quick-start) •
[System Controls](#-system-controls--hotkeys) •
[Security & Guardrails](#-security--safety-guardrails) •
[Roadmap](#-development-roadmap) •
[Author](#-author--connect)

---

</div>

## 📖 About The Project

**MARK XXXV** is an enterprise-grade autonomous desktop AI assistant and operating-system intelligence platform. Unlike standard voice assistants that are restricted to question answering and static macros, MARK XXXV bridges ultra-low-latency conversational audio streaming with an industrial-strength autonomous agent execution loop.

Built from the ground up for power users, engineers, and enterprise automation, MARK XXXV listens to conversational voice input in any language, perceives what is on your screen in real time, plans and breaks down multi-step objectives, executes system actions via an isolated persistent PowerShell shell, navigates the web autonomously, and runs long-term background goals for hours without user babysitting.

### 🌟 Why MARK XXXV?
* **Zero Conversational Latency**: Utilizes Google's Gemini Live bidirectional WebSocket protocol combined with Silero Voice Activity Detection (VAD) and local Faster-Whisper transcription for natural, interruptible speech.
* **Continuous Multi-Hour Goal Loop**: Powered by the custom `GoalOrchestrator`, decomposing complex enterprise objectives into sequential sub-tasks that run autonomously in the background.
* **Smart Memory & Context Compression**: Automatically captures execution scratchpads, generates map-reduce summaries to bypass token context ceilings, and saves persistent state into WAL-mode SQLite databases.
* **Guaranteed System Safety**: Enforces hard per-goal dollar cost ceilings (`GOAL_MAX_COST_USD`), maximum recursive delegation depths, and human-in-the-loop approval triggers before executing high-risk system commands.
* **100% Local Data Privacy**: Epistemic memory, biometrics, credentials, and execution histories stay stored securely on your machine.

---

## 🏗️ System Architecture

```
                                  +---------------------------------------+
                                  |            USER INTERACTION           |
                                  |  (Microphone / Screen / Keyboard HUD) |
                                  +-------------------+-------------------+
                                                      |
                         +----------------------------+----------------------------+
                         |                                                         |
                         v                                                         v
          +-------------------------------+                         +-------------------------------+
          |       SPEECH & VISION         |                         |        CYBERNETIC HUD         |
          | - Silero VAD (Dual Trigger)   |                         | - Real-time Status Indicator  |
          | - Faster-Whisper (CUDA / CPU) |                         | - Direct Text Command Bar     |
          | - PyAutoGUI / Screen Intel    |                         | - Hardware Mute Toggle (F4)   |
          +---------------+---------------+                         +---------------+---------------+
                          |                                                         |
                          +----------------------------+----------------------------+
                                                       |
                                                       v
                                    +-------------------------------------+
                                    |     DISPATCHER & EVENT BUS          |
                                    |     (os_layer/event_bus.py)         |
                                    +------------------+------------------+
                                                       |
                             +-------------------------+-------------------------+
                             |                                                   |
                             v                                                   v
              +------------------------------+                    +------------------------------+
              |      JARVIS LIVE STREAM      |                    |      GOAL ORCHESTRATOR       |
              |   (Bidirectional WebSocket)  |                    |  (Continuous Multi-Hour Loop)|
              |   - Gemini 2.5 Flash Audio   |                    |   - High-Level Task Planner  |
              |   - Ultra-Low Latency Duplex |                    |   - Autonomous Sub-Tasking   |
              |   - Instant Interruption     |                    |   - Pinned Memory Extraction |
              +--------------+---------------+                    +--------------+---------------+
                             |                                                   |
                             +-------------------------+-------------------------+
                                                       |
                                                       v
                                    +-------------------------------------+
                                    |       AUTONOMOUS REACT AGENT        |
                                    |        (agent/react_agent.py)       |
                                    |   THINK  -->  ACT  -->  OBSERVE     |
                                    |   - Map-Reduce Context Compression  |
                                    |   - Dynamic Fact Pinning            |
                                    +------------------+------------------+
                                                       |
                             +-------------------------+-------------------------+
                             |                                                   |
                             v                                                   v
              +------------------------------+                    +------------------------------+
              |     PARALLEL AGENT ENGINE    |                    |    PERSISTENT STORAGE & WAL  |
              | - Thread-Safe Singleton Pool |                    | - SQLite (agent_episodes.db) |
              | - Global Concurrency Ceiling |                    | - ChromaDB Vector Store      |
              | - Deadlock-Free Sub-Pools    |                    | - Real-Time Credit Tracker   |
              +--------------+---------------+                    +--------------+---------------+
                             |
                             v
              +------------------------------------------------------------------+
              |                       ACTUATION & TOOL SYSTEM                    |
              |  - AgenticShell: Persistent background PowerShell session        |
              |  - WindowManager: Process lifecycle, layout restore & snappers   |
              |  - BrowserAutomation: Headless/Incognito Playwright integration  |
              |  - DocumentIntelligence: Semantic PDF, CSV, and code parsing    |
              +------------------------------------------------------------------+
```

---

## ✨ Key Features

### 1. 🧠 Long-Term Goal Loop & Background Daemon (`GoalOrchestrator`)
* **Unattended Execution**: Launches multi-hour objectives that continue executing in the background without tying up your live conversation queue.
* **Dedicated Task Queue**: Interactive user speech commands receive immediate priority; background goals process asynchronously on dedicated worker pools.
* **Hierarchical Sub-Goal Decomposition**: Automatically uses high-tier LLM reasoning to decompose macro-goals into discrete milestones.
* **Safety Ceilings & Human Confirmation**: Pauses goals and prompts for authorization if safety-tier thresholds or budget limits are approached.

### 2. ⚡ Autonomous ReAct Loop with Context Compression
* **Structured Reasoning**: Executes the industry-standard `Thought -> Action -> Observation` cognitive cycle with strict JSON schema validation.
* **Map-Reduce Context Compression**: When execution histories approach token capacity, past steps are automatically chunked, summarized with high semantic density, and preserved to avoid silent context loss.
* **Fact Pinning**: User-defined anchors, session goals, and mission-critical variables are permanently pinned above the scratchpad, immune to truncation.

### 3. 🛡️ Global Concurrency & Deadlock-Free Pooling (`ParallelAgentEngine`)
* **Global Rate-Limit Shield**: Single system-wide concurrency ceiling ensures your Gemini API quota is never exhausted by background threads.
* **Isolated Sub-Agent Contexts**: Delegated and recursive sub-tasks run in dedicated secondary pools, completely preventing thread starvation and deadlock conditions.
* **Jittered Exponential Backoff**: Automatic retry policies gracefully absorb transient network anomalies or API rate limits.

### 4. 🐚 Persistent Agentic Shell (`AgenticShell`)
* **Stateful PowerShell Session**: Maintains environment variables, working directories, and interactive states between separate command turns.
* **Security Classification**: Classifies every command against security rulebooks (file mutation, network egress, build tools) before execution.

### 5. 💾 Multi-Day Memory & Real-Time Cost Accounting
* **SQLite WAL-Mode Architecture**: Ultra-fast atomic logging of every task, step, tool call, and token cost into `agent_episodes.db`.
* **Live USD Cost Attribution**: Real-time tracking of input and output token consumption calculated down to fractional cents per goal.
* **Crash-Resilient State**: Can reboot or restart anytime; active tasks and compressed scratchpads resume exactly where they left off.

### 6. 🎙️ Real-Time Voice Pipeline & Cybernetic HUD
* **Instant Conversational Voice**: Talk naturally without awkward pauses or push-to-talk delays.
* **Voice Interruption Support**: Start speaking at any time to immediately silence JARVIS and steer execution.
* **Dual Display Options**: Clean Pygame cyberpunk HUD and rich HTML/JS pywebview interface with real-time audio waveforms, status badges, and telemetry graphs.

---

## ⚡ Quick Start

### 📋 Prerequisites
* **Operating System**: Windows 10 or 11 (64-bit)
* **Python**: Python 3.11 or 3.12 installed
* **Hardware**: Microphone & working audio output
* **API Key**: A free [Google Gemini API Key](https://aistudio.google.com/apikey)

### 🚀 Installation Steps

1. **Clone the Repository**
   ```bash
   git clone https://github.com/imahad-dev/Mark-XXXV.git
   cd Mark-XXXV/Mark-XXXV-main
   ```

2. **Create and Activate a Virtual Environment**
   ```bash
   python -m venv venv
   .\venv\Scripts\activate
   ```

3. **Install Dependencies**
   ```bash
   pip install -r requirements.txt
   playwright install
   ```

4. **Launch MARK XXXV**
   ```bash
   python main.py
   ```

5. **First-Time Setup**:
   Enter your free Gemini API key in the cybernetic setup modal on first launch. Keys are stored locally with enterprise encryption.

---

## 🎮 System Controls & Hotkeys

| Input / Shortcut | Target Component | Purpose & Description |
| :--- | :--- | :--- |
| **`F4` Key** | Audio Pipeline | **Instant Mic Mute**: Instantly silences audio intake when someone enters your room |
| **GUI Mute Button** | Status HUD | Clickable indicator toggling microphone listening state |
| **Bottom Input Bar** | Command Console | **Text Fallback**: Type explicit instructions without speaking aloud |
| **HUD State Badge** | Telemetry | Visual feedback: `LISTENING` (Green), `THINKING` (Cyan), `SPEAKING` (Blue), `MUTED` (Red) |

---

## 🔒 Security & Safety Guardrails

* **100% Local Data Isolation**: All vector embeddings, SQLite task records, session transcripts, and biometric hashes remain on your local disk.
* **Recursive Delegation Depth Cap**: Hard ceiling (`GOAL_MAX_DELEGATION_DEPTH = 3`) prevents infinite agent recursion loops.
* **Per-Goal USD Budget Cap**: Hard limit (`GOAL_MAX_COST_USD = $2.00` by default) suspends any background task before it incurs unexpected API costs.
* **Hardware & Auth Lockout**: PBKDF2 PIN hashing with rate-limited brute-force lockout protection.

---

## 🗺️ Development Roadmap

- [x] **Phase 1: Real-time Multimodal Voice Engine** (Silero VAD + Faster-Whisper + Gemini Live WebSockets)
- [x] **Phase 2: OS Actuation & Persistent Shell** (AgenticShell + WindowManager + Screen Intelligence)
- [x] **Phase 3: Parallel Agent Engine & Concurrency Shield** (Global pool + SubTask parallelization)
- [x] **Phase 4: Long-Term Goal Loop & Memory Compression** (GoalOrchestrator + Map-Reduce Compression + Cost Ledger)
- [ ] **Phase 5: Ambient Mode & Predictive Intelligence** (Proactive workflow automation + Markov behavioral triggers)
- [ ] **Phase 6: Multi-Device Remote HUD & Mobile Pairing** (Encrypted local WebRTC pairing with mobile companion)

---

## 🧪 Testing & Validation

MARK XXXV includes a comprehensive test suite covering all core engines, persistence layers, and concurrency controls:

```bash
# Run the entire test suite
cd Mark-XXXV-main
python -m pytest -v

# Run Phase 4 Goal Orchestration suite specifically
python -m pytest tests/test_goal_orchestrator.py -v
```

> **Current Suite Status**: `297 passed, 5 subtests passed (100% PASS)`

---

## 🧑‍💻 Author & Connect

Developed and maintained by **Mahad** ([@imahad-dev](https://github.com/imahad-dev)).

* **GitHub**: [@imahad-dev](https://github.com/imahad-dev)
* **Instagram**: [@mahad_.x1](https://www.instagram.com/mahad_.x1/)
* **Project Repository**: [https://github.com/imahad-dev/Mark-XXXV](https://github.com/imahad-dev/Mark-XXXV)

---

<div align="center">

**Star ⭐ this repository to follow the ongoing enterprise evolution of JARVIS MARK XXXV!**

</div>
