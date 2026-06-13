# Project Plan: Phase 5 - Ambient Intelligence & Predictive Execution

## Overview
Phase 5 introduces passive, low-power ambient awareness and statistical pattern-driven predictive capabilities to the JARVIS OS-level integration layer. It optimizes CPU/VRAM usage during user inactivity and pre-empts tasks to enhance real-time responsive interaction.

## Project Type
BACKEND / OS LAYER (Standalone Python System Integration)

## Success Criteria
1. **Low-Power Idle Transition:** Transition from active screen/file watching to reduced-frequency captures (or complete pause) within 5 seconds of idle detection.
2. **Idle Detection Accuracy:** Correctly dispatch `user.idle` and `user.active` events based on Windows `GetLastInputInfo` with 32-bit tick count wraparound safety.
3. **輕量級 Sequence Prediction:** Record sequence transitions and output prediction candidates with > 80% confidence logic under 10ms execution time.
4. **State Space Normalization:** Collapse variable window titles (e.g. paths, issue hashes) to distinct state names.
5. **Zero Regressions:** Maintain 100% pass rate on all Phase 1-4 tests (163+ tests).

## Tech Stack
- **Python standard libraries** (`ctypes`, `threading`, `queue`, `json`, `re`)
- **SQLite** (`sqlite3` for local transition matrices)
- **win32 API** (via `ctypes` directly to avoid heavy imports on startup)

## File Structure
```plaintext
Mark-XXXV-main/
├── os_layer/
│   ├── ambient_mode.py       # [NEW] Low-power idle state manager
│   ├── predictive.py         # [NEW] Transition logs & prediction matrix
│   └── __init__.py           # [MODIFY] Export new modules
├── tests/
│   ├── test_ambient_mode.py  # [NEW] Mocks input & monitors transitions
│   └── test_predictive.py    # [NEW] Tests SQLite Markov probability calculations
└── core/
    └── config.py             # [MODIFY] Added configurations
```

---

## Task Breakdown

### Task 1: Core Configuration Setup
- **ID:** `TASK_1`
- **Agent:** `backend-specialist`
- **Skills:** `python-patterns`
- **Priority:** High
- **Dependencies:** None
- **INPUT:** `.env` variables and `config.py` definitions.
- **OUTPUT:** Configuration values for `AMBIENT_MODE_ENABLED`, `PREDICTIVE_ENABLED`, `AMBIENT_IDLE_THRESHOLD_SEC`, `AMBIENT_MONITOR_INTERVAL_SEC`, `PREDICTIVE_HISTORY_DAYS`, and `PREDICTIVE_CONFIDENCE_THRESHOLD`.
- **VERIFY:** Config properties are loaded with expected default values under fallback checks.

### Task 2: Windows Idle Detection Hook (`ambient_mode.py`)
- **ID:** `TASK_2`
- **Agent:** `backend-specialist`
- **Skills:** `python-patterns`, `powershell-windows`
- **Priority:** High
- **Dependencies:** `TASK_1`
- **INPUT:** Windows ctypes binding to `user32.dll`'s `GetLastInputInfo`.
- **OUTPUT:** Functional polling loop checking time since last user input with rollover safety:
  `idle_ms = (GetTickCount() - last_input.dwTime) & 0xFFFFFFFF`.
- **VERIFY:** Verify that keyboard or mouse input updates last input timestamp correctly, and exceeding threshold triggers `user.idle` event.

### Task 3: Low-Power Lifecycle Integration (Subscribers)
- **ID:** `TASK_3`
- **Agent:** `backend-specialist`
- **Skills:** `python-patterns`, `clean-code`
- **Priority:** High
- **Dependencies:** `TASK_2`
- **INPUT:** `screen_intel.py` and `doc_intelligence.py` subscribing to `user.idle` and `user.active` events.
- **OUTPUT:** Dynamic speed scaling and crawl inversion:
  - On `user.idle` event: Decrease screen capture frequency (to 300s) and resume document crawler indexing.
  - On `user.active` event: Restore screen capture frequency (to 30s) and pause the document crawler indexing.
- **VERIFY:** Trigger event logs manually and verify capture interval and crawl loops change state dynamically.

### Task 4: SQLite Predictive Logger & Normalization
- **ID:** `TASK_4`
- **Agent:** `database-architect`
- **Skills:** `database-design`
- **Priority:** High
- **Dependencies:** `TASK_1`
- **INPUT:** `agent_episodes.db` connection.
- **OUTPUT:** Database schemas for `user_action_history` and `state_transitions`. Normalizer function `_normalize_state` using regex.
- **VERIFY:** Dynamic dynamic titles collapse into unified states (e.g. `VS Code - main.py` -> `Code::VS Code`). Database tables are correctly provisioned on module init.

### Task 5: Transition Tracker & Inference Engine
- **ID:** `TASK_5`
- **Agent:** `backend-specialist`
- **Skills:** `python-patterns`
- **Priority:** High
- **Dependencies:** `TASK_4`
- **INPUT:** `window.focus_changed` and `file.modified` events.
- **OUTPUT:** Markov transition probability builder. `predict_next_action()` returns top predictions with calculated weights. Nightly cleanup prunes records > `PREDICTIVE_HISTORY_DAYS` and rebuilds the transitions matrix.
- **VERIFY:** Inject a series of mock focus switch sequences (e.g. `App A -> App B`, `App A -> App B`, `App A -> App C`) and assert that predictions select `App B` with $P = 2/3$. Verify history pruning operates as expected.

### Task 6: Pre-computation Dispatch & Risk-Gated UI integration
- **ID:** `TASK_6`
- **Agent:** `backend-specialist`
- **Skills:** `clean-code`
- **Priority:** Medium
- **Dependencies:** `TASK_5`
- **INPUT:** Highly confident predictions (> threshold) and 60s trigger deduplication.
- **OUTPUT:** Action dispatch:
  - `risk_level == "safe"`: Silently warm cache / search / url context at >= 80% confidence.
  - `risk_level == "side_effect"`: Display UI recommendation button in Webview UI at >= 85% confidence.
- **VERIFY:** Assert that high confidence outputs generate correct notifications, UI button actions, or silent background logs.

---

## Phase X: Verification

### 1. Run Automated Tests
```powershell
pytest tests/test_ambient_mode.py tests/test_predictive.py -v
```

### 2. Manual Checklist Verification
- [ ] No purple/violet color hex codes added.
- [ ] Socratic Gate was fully respected.
- [ ] Low-power mode keeps CPU overhead < 0.5% during user activity and < 0.1% during idle state.

### 3. Build Verification
```powershell
python -m py_compile os_layer/ambient_mode.py os_layer/predictive.py
```

---

## Phase X Completion Marker
(Will be filled upon completing tasks)
