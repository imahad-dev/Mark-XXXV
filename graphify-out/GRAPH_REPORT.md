# Graph Report - Jarvis_Mark-69  (2026-07-17)

## Corpus Check
- 94 files · ~113,601 words
- Verdict: corpus is large enough that graph structure adds value.

## Summary
- 2487 nodes · 5137 edges · 71 communities detected
- Extraction: 61% EXTRACTED · 39% INFERRED · 0% AMBIGUOUS · INFERRED: 2004 edges (avg confidence: 0.57)
- Token cost: 0 input · 0 output

## Community Hubs (Navigation)
- [[_COMMUNITY_Community 0|Community 0]]
- [[_COMMUNITY_Community 1|Community 1]]
- [[_COMMUNITY_Community 2|Community 2]]
- [[_COMMUNITY_Community 3|Community 3]]
- [[_COMMUNITY_Community 4|Community 4]]
- [[_COMMUNITY_Community 5|Community 5]]
- [[_COMMUNITY_Community 6|Community 6]]
- [[_COMMUNITY_Community 7|Community 7]]
- [[_COMMUNITY_Community 8|Community 8]]
- [[_COMMUNITY_Community 9|Community 9]]
- [[_COMMUNITY_Community 10|Community 10]]
- [[_COMMUNITY_Community 11|Community 11]]
- [[_COMMUNITY_Community 12|Community 12]]
- [[_COMMUNITY_Community 13|Community 13]]
- [[_COMMUNITY_Community 14|Community 14]]
- [[_COMMUNITY_Community 15|Community 15]]
- [[_COMMUNITY_Community 16|Community 16]]
- [[_COMMUNITY_Community 17|Community 17]]
- [[_COMMUNITY_Community 18|Community 18]]
- [[_COMMUNITY_Community 19|Community 19]]
- [[_COMMUNITY_Community 20|Community 20]]
- [[_COMMUNITY_Community 21|Community 21]]
- [[_COMMUNITY_Community 22|Community 22]]
- [[_COMMUNITY_Community 23|Community 23]]
- [[_COMMUNITY_Community 24|Community 24]]
- [[_COMMUNITY_Community 25|Community 25]]
- [[_COMMUNITY_Community 26|Community 26]]
- [[_COMMUNITY_Community 27|Community 27]]
- [[_COMMUNITY_Community 28|Community 28]]
- [[_COMMUNITY_Community 29|Community 29]]
- [[_COMMUNITY_Community 30|Community 30]]
- [[_COMMUNITY_Community 31|Community 31]]
- [[_COMMUNITY_Community 32|Community 32]]
- [[_COMMUNITY_Community 35|Community 35]]
- [[_COMMUNITY_Community 36|Community 36]]
- [[_COMMUNITY_Community 37|Community 37]]
- [[_COMMUNITY_Community 38|Community 38]]
- [[_COMMUNITY_Community 39|Community 39]]
- [[_COMMUNITY_Community 40|Community 40]]
- [[_COMMUNITY_Community 42|Community 42]]
- [[_COMMUNITY_Community 43|Community 43]]
- [[_COMMUNITY_Community 44|Community 44]]
- [[_COMMUNITY_Community 45|Community 45]]
- [[_COMMUNITY_Community 46|Community 46]]
- [[_COMMUNITY_Community 47|Community 47]]
- [[_COMMUNITY_Community 48|Community 48]]
- [[_COMMUNITY_Community 49|Community 49]]
- [[_COMMUNITY_Community 50|Community 50]]
- [[_COMMUNITY_Community 53|Community 53]]
- [[_COMMUNITY_Community 54|Community 54]]
- [[_COMMUNITY_Community 55|Community 55]]
- [[_COMMUNITY_Community 56|Community 56]]
- [[_COMMUNITY_Community 57|Community 57]]
- [[_COMMUNITY_Community 58|Community 58]]
- [[_COMMUNITY_Community 59|Community 59]]
- [[_COMMUNITY_Community 60|Community 60]]
- [[_COMMUNITY_Community 61|Community 61]]
- [[_COMMUNITY_Community 62|Community 62]]
- [[_COMMUNITY_Community 63|Community 63]]
- [[_COMMUNITY_Community 64|Community 64]]
- [[_COMMUNITY_Community 65|Community 65]]
- [[_COMMUNITY_Community 66|Community 66]]
- [[_COMMUNITY_Community 67|Community 67]]
- [[_COMMUNITY_Community 68|Community 68]]
- [[_COMMUNITY_Community 69|Community 69]]
- [[_COMMUNITY_Community 70|Community 70]]
- [[_COMMUNITY_Community 71|Community 71]]
- [[_COMMUNITY_Community 72|Community 72]]
- [[_COMMUNITY_Community 73|Community 73]]
- [[_COMMUNITY_Community 74|Community 74]]
- [[_COMMUNITY_Community 75|Community 75]]

## God Nodes (most connected - your core abstractions)
1. `EventType` - 244 edges
2. `LLMOrchestrator` - 138 edges
3. `CreditTracker` - 113 edges
4. `Event` - 99 edges
5. `TaskTier` - 95 edges
6. `AgenticShell` - 85 edges
7. `ActuationManager` - 72 edges
8. `ScreenIntelligence` - 64 edges
9. `JarvisUI` - 61 edges
10. `ActionPayload` - 54 edges

## Surprising Connections (you probably didn't know these)
- `Called by pywebview after the window is shown.` --uses--> `CreditTracker`  [INFERRED]
  webview_ui.py → core\credit_tracker.py
- `Phase 1: YuNet+ArcFace scan → Phase 2: Video → navigate to HUD.` --uses--> `CreditTracker`  [INFERRED]
  webview_ui.py → core\credit_tracker.py
- `Run one scan attempt. Returns True if face matches and liveness checks pass.` --uses--> `CreditTracker`  [INFERRED]
  webview_ui.py → core\credit_tracker.py
- `Transition from auth to video, then navigate to JARVIS HUD.` --uses--> `CreditTracker`  [INFERRED]
  webview_ui.py → core\credit_tracker.py
- `Hardware failure path \u2014 always require PIN verification.          Even if k` --uses--> `CreditTracker`  [INFERRED]
  webview_ui.py → core\credit_tracker.py

## Communities

### Community 0 - "Community 0"
Cohesion: 0.01
Nodes (182): Config, ActionPayload, ActionResult, ActionType, ActuationManager, _capture_pip_diff(), _dispatch_browser_action(), _ensure_schema() (+174 more)

### Community 1 - "Community 1"
Cohesion: 0.02
Nodes (171): Called from main.py.      parameters:         action      : write | edit | ex, _analyze_screen_for_element(), _clear_field(), _click(), _clipboard_copy(), _clipboard_set(), computer_control(), _drag() (+163 more)

### Community 2 - "Community 2"
Cohesion: 0.02
Nodes (142): analyze_file(), analyze_screen_capture(), analyze_webcam_frame(), _get_gemini_client(), _guess_mime(), _local_fallback(), file_analyzer.py – Multimodal file analysis via Gemini.  Accepts base64-encoded, Analyze a screenshot via Gemini vision. (+134 more)

### Community 3 - "Community 3"
Cohesion: 0.02
Nodes (115): _calendar_control(), _email_control(), _event_bus_tool(), delete_document_chunks(), Delete all stored chunks associated with a specific file path.     Uses metadata, AmbientModeManager, get_ambient_mode(), _LASTINPUTINFO (+107 more)

### Community 4 - "Community 4"
Cohesion: 0.02
Nodes (114): tool_defs.py — Central Tool Definitions ========================================, Adapter: normalizes the non-standard hand_auth(reason, timeout, theme) signature, Adapter: normalizes the non-standard hand_auth(reason, timeout, theme) signature, _workflow_recorder_tool(), tool_registry.py — Central Tool Registry =======================================, Decorator to register a function as a JARVIS tool.      Usage:         @tool(, Action safety classification., Immutable specification for a registered tool. (+106 more)

### Community 5 - "Community 5"
Cohesion: 0.03
Nodes (71): _classify_risk(), _ensure_schema(), _get_conn(), get_predictive_engine(), _normalize_state(), Prediction, PredictiveEngine, os_layer/predictive.py — Usage Pattern Analysis & Predictive Execution ========= (+63 more)

### Community 6 - "Community 6"
Cohesion: 0.03
Nodes (69): get_all_gemini_keys(), _get_env_float(), _get_env_int(), next_gemini_key(), Parse an integer from env, falling back to *default* on ValueError., Parse a float from env, falling back to *default* on ValueError., reload(), validate_env() (+61 more)

### Community 7 - "Community 7"
Cohesion: 0.04
Nodes (52): _screen_intelligence(), _ensure_schema(), _extract_app_name(), _get_conn(), get_screen_intelligence(), Notification, os_layer/screen_intel.py — Screen Intelligence Engine ==========================, Full screen context snapshot. (+44 more)

### Community 8 - "Community 8"
Cohesion: 0.03
Nodes (55): _ask_gemini(), cmd_control(), _find_hardcoded(), _get_platform(), _is_safe(), _run_silent(), _run_visible(), _normalize() (+47 more)

### Community 9 - "Community 9"
Cohesion: 0.03
Nodes (37): _kg_control(), _ensure_schema(), _get_conn(), _get_kg_collection(), get_knowledge_graph(), KGEdge, KGNode, KnowledgeGraph (+29 more)

### Community 10 - "Community 10"
Cohesion: 0.05
Nodes (37): _window_manager(), _ensure_schema(), _get_conn(), get_window_manager(), LayoutConfig, MonitorInfo, ProcessInfo, os_layer/window_manager.py — Window & Process Orchestrator ===================== (+29 more)

### Community 11 - "Community 11"
Cohesion: 0.05
Nodes (34): _ensure_schema(), EventBus, _get_conn(), is_running(), _matches_subscription(), os_layer/event_bus.py — Event-Driven Reactive System ===========================, A registered event subscriber., Central event bus with typed dispatch, priority ordering,     and persistent eve (+26 more)

### Community 12 - "Community 12"
Cohesion: 0.04
Nodes (12): computer_settings(), copy(), paste(), press_key(), refresh_page(), reload_page_n(), scroll_down(), scroll_up() (+4 more)

### Community 13 - "Community 13"
Cohesion: 0.08
Nodes (57): _ask_gemini_for_desktop_action(), clean_desktop(), desktop_control(), _execute_generated_code(), get_current_wallpaper(), _get_desktop(), get_desktop_stats(), _is_safe_code() (+49 more)

### Community 14 - "Community 14"
Cohesion: 0.1
Nodes (36): _parallel_orchestrate(), OrchestrateResult, ParallelAgentEngine, os_layer/parallel_engine.py — Multi-Agent Concurrent Execution =================, Run tasks in parallel using ThreadPoolExecutor.          Args:             tasks, Signal all workers to stop., Execute a single task with exponential backoff on rate-limit errors., A single sub-task to execute in parallel. (+28 more)

### Community 15 - "Community 15"
Cohesion: 0.06
Nodes (43): _build(), _clean_code(), code_helper(), _detect_intent(), _edit_action(), _explain_action(), _fix_code(), _has_error() (+35 more)

### Community 16 - "Community 16"
Cohesion: 0.07
Nodes (28): AgentMemory, _ConnectionPool, Episode, EpisodeStatus, _format_age(), get_agent_memory(), memory/agent_memory.py ====================== SQLite-backed persistence for mu, Thread-local SQLite connection pool with WAL mode. (+20 more)

### Community 17 - "Community 17"
Cohesion: 0.07
Nodes (33): authenticate_existing(), capture_face_embeddings(), check_prerequisites(), create_golden_signature(), create_recovery_bundle(), main(), print_banner(), core/auth/enroll_face.py ========================= One-time enrollment script -- (+25 more)

### Community 18 - "Community 18"
Cohesion: 0.07
Nodes (19): get_scheduler(), JarvisScheduler, agent/scheduler.py ================== Background Task Scheduler for JARVIS.  Run, Register all whitelisted jobs and start the scheduler., Graceful shutdown — waits for running jobs to finish., Graceful shutdown — waits for running jobs to finish., Register a single whitelisted task as an APScheduler job., Register a single whitelisted task as an APScheduler job. (+11 more)

### Community 19 - "Community 19"
Cohesion: 0.09
Nodes (36): copy_file(), create_file(), create_folder(), delete_file(), file_controller(), find_files(), _format_size(), _get_desktop() (+28 more)

### Community 20 - "Community 20"
Cohesion: 0.13
Nodes (11): browser_control(), _BrowserThread, _ensure_started(), _find_browser_executable(), _get_default_browser_id(), _get_opera_executable(), Launch Playwright's built-in Chromium with a dedicated Jarvis profile., Returns raw default browser identifier string for current OS. (+3 more)

### Community 21 - "Community 21"
Cohesion: 0.11
Nodes (27): hand_auth(), actions/hand_auth.py ==================== Jarvis tool wrapper around the hand, Display the hand-scanner window and block until verified or timed out.      Pa, _run_scan(), _hand_auth(), authenticate(), _draw_corner_brackets(), _draw_hud() (+19 more)

### Community 22 - "Community 22"
Cohesion: 0.15
Nodes (5): _ac(), JarvisUI, Sol alt köşeye mute butonu yerleştirir., Log'un hemen altına tek satır metin giriş alanı., main.py'den çağrılır.         state: LISTENING | SPEAKING | THINKING | MUTED |

### Community 23 - "Community 23"
Cohesion: 0.09
Nodes (12): get_session_security(), core/security.py — Session Security Manager ====================================, Write the token to JSON and lock the file to the current user's SID., Thread-safe singleton accessor., Manages the per-session authentication token for local IPC.      Usage:, Generate a new session token and write it to disk with owner-only ACLs., Check if the provided token matches the current session token., Return the current session token (None if not initialized). (+4 more)

### Community 24 - "Community 24"
Cohesion: 0.12
Nodes (22): _document_search(), _get_collection(), _get_documents_collection(), _get_knowledge_collection(), get_relevant_context(), vector_store.py — ChromaDB Semantic Memory Layer ===============================, Search conversation history by semantic similarity.      Args:         query: Na, Convenience wrapper: returns a formatted string of past conversations     releva (+14 more)

### Community 25 - "Community 25"
Cohesion: 0.14
Nodes (16): $(), applyTheme(), drawGlobe(), loadSettingsData(), navigateTo(), pollDashboardNow(), project(), renderDashboard() (+8 more)

### Community 26 - "Community 26"
Cohesion: 0.13
Nodes (6): QOpenGLWidget, QWidget, DraggableHologramWindow, generate_sphere_wireframe(), HologramWidget, main()

### Community 27 - "Community 27"
Cohesion: 0.17
Nodes (9): _capture_camera(), _capture_screenshot(), _ensure_started(), _get_api_key(), _get_camera_index(), _LiveSession, screen_process(), _to_jpeg() (+1 more)

### Community 28 - "Community 28"
Cohesion: 0.14
Nodes (17): decrypt_env_to_memory(), encrypt_env_file(), _import_dpapi(), inject_env_from_decrypted(), load_fernet_key(), protect_key(), core/auth/keystore.py ====================== Secure keystore using Windows DPAPI, Encrypt a Fernet key with DPAPI and save to master.key. (+9 more)

### Community 29 - "Community 29"
Cohesion: 0.38
Nodes (6): download_file(), main(), core/auth/download_models.py ============================= Downloads the require, Download a file with progress indication., Verify SHA-256 hash if provided., verify_hash()

### Community 30 - "Community 30"
Cohesion: 0.33
Nodes (5): elevenlabs_tts.py — ElevenLabs Text-to-Speech for JARVIS personas. =============, Non-blocking wrapper — runs speak_elevenlabs in a daemon thread.     Returns the, Synthesize `text` via ElevenLabs and play it through speakers.      Args:, speak_elevenlabs(), speak_elevenlabs_async()

> **Note on Counting Rules**: Section header node counts reflect direct AST-parsed definition nodes, while summary sections count total nodes (including implicit module roots like `__init__.py`). Isolated-node metrics (368 total) represent unlinked definition nodes at snapshot time.

### Community 31 - "Community 31"
Cohesion: 1.0
Nodes (1): Quick smoke test for Gemini search grounding.

### Community 32 - "Community 32"
Cohesion: 1.0
Nodes (1): os_layer — OS-Level Intelligence System ======================================== (2 nodes including package root)

### Community 35 - "Community 35"
Cohesion: 1.0
Nodes (1): Return the full pool of available Gemini API keys.

### Community 36 - "Community 36"
Cohesion: 1.0
Nodes (1): Rotate to the next available Gemini key. Returns None if exhausted.

### Community 37 - "Community 37"
Cohesion: 1.0
Nodes (1): Re-read environment variables from os.environ and update all class fields.

### Community 38 - "Community 38"
Cohesion: 1.0
Nodes (1): Restrict file access to the current user's SID only.          Uses win32security

### Community 39 - "Community 39"
Cohesion: 1.0
Nodes (1): Cosine similarity between two L2-normalized embeddings.

### Community 40 - "Community 40"
Cohesion: 1.0
Nodes (1): Check if two embeddings belong to the same person.

### Community 42 - "Community 42"
Cohesion: 1.0
Nodes (1): Web search via DuckDuckGo — wraps existing _ddg_search.         Returns list of

### Community 43 - "Community 43"
Cohesion: 1.0
Nodes (1): Weather report — delegates to existing weather_action.         Handles auto-dete

### Community 44 - "Community 44"
Cohesion: 1.0
Nodes (1): Live stock/crypto prices via yfinance (free, no API key).          Args:

### Community 45 - "Community 45"
Cohesion: 1.0
Nodes (1): Format price dict into readable string.

### Community 46 - "Community 46"
Cohesion: 1.0
Nodes (1): Fetch headlines from RSS feeds (zero API cost).          Args:             categ

### Community 47 - "Community 47"
Cohesion: 1.0
Nodes (1): Format news list into readable string (HTML stripped).

### Community 48 - "Community 48"
Cohesion: 1.0
Nodes (1): Scrape and extract text content from a URL.          Returns cleaned text (scrip

### Community 49 - "Community 49"
Cohesion: 1.0
Nodes (1): Fetch Wikipedia summary for a topic (free, no key).          Uses the maintained

### Community 50 - "Community 50"
Cohesion: 1.0
Nodes (1): Fast keyword check — does this query suit Wolfram?

### Community 53 - "Community 53"
Cohesion: 1.0
Nodes (1): Check if a filepath matches any of the glob patterns.

### Community 54 - "Community 54"
Cohesion: 1.0
Nodes (1): Check if an event matches a subscription's filters.

### Community 55 - "Community 55"
Cohesion: 1.0
Nodes (1): Human-readable time gap from timestamp to now.

### Community 56 - "Community 56"
Cohesion: 1.0
Nodes (1): Generate a unique session identifier.

### Community 57 - "Community 57"
Cohesion: 1.0
Nodes (1): Build a natural-language briefing for boot sequence.

### Community 58 - "Community 58"
Cohesion: 1.0
Nodes (1): Called every 3 s from JS. Returns all telemetry (slow data is internally cached)

### Community 59 - "Community 59"
Cohesion: 1.0
Nodes (1): Called every 3 s from JS. Returns all telemetry (slow data is internally cached)

### Community 60 - "Community 60"
Cohesion: 1.0
Nodes (1): Gathers and caches system metrics at appropriate intervals.

### Community 61 - "Community 61"
Cohesion: 1.0
Nodes (1): Run in a daemon thread to avoid blocking the UI on first load.

### Community 62 - "Community 62"
Cohesion: 1.0
Nodes (1): Called every 3 s from JS. Returns all telemetry (slow data is internally cached)

### Community 63 - "Community 63"
Cohesion: 1.0
Nodes (1): Switch active theme and return the theme config to JS.

### Community 64 - "Community 64"
Cohesion: 1.0
Nodes (1): Return the full pool of available Gemini API keys.

### Community 65 - "Community 65"
Cohesion: 1.0
Nodes (1): Rotate to the next available Gemini key. Returns None if exhausted.

### Community 66 - "Community 66"
Cohesion: 1.0
Nodes (1): Attempt to rotate to a fresh Gemini API key. Returns True if successful.

### Community 67 - "Community 67"
Cohesion: 1.0
Nodes (1): Returns the model name string for the given tier.

### Community 68 - "Community 68"
Cohesion: 1.0
Nodes (1): Returns the types.LiveConnectConfig for google.genai asynchronous websocket API.

### Community 69 - "Community 69"
Cohesion: 1.0
Nodes (1): Executes generation using REST API with transparent fallback.

### Community 70 - "Community 70"
Cohesion: 1.0
Nodes (1): For testing purposes only.

### Community 71 - "Community 71"
Cohesion: 1.0
Nodes (1): Lazy-initialize ChromaDB client and collection on first use.

### Community 72 - "Community 72"
Cohesion: 1.0
Nodes (1): Store a single conversation turn as a vector embedding.      Args:         user_

### Community 73 - "Community 73"
Cohesion: 1.0
Nodes (1): Search conversation history by semantic similarity.      Args:         query: Na

### Community 74 - "Community 74"
Cohesion: 1.0
Nodes (1): Convenience wrapper: returns a formatted string of past conversations     releva

### Community 75 - "Community 75"
Cohesion: 1.0
Nodes (1): One-time migration: converts existing long_term.json facts into     vector embed

## Knowledge Gaps
- **368 isolated node(s):** `Quick smoke test for Gemini search grounding.`, `Sol alt köşeye mute butonu yerleştirir.`, `Log'un hemen altına tek satır metin giriş alanı.`, `main.py'den çağrılır.         state: LISTENING | SPEAKING | THINKING | MUTED |`, `Called every 3 s from JS. Returns all telemetry (slow data is internally cached)` (+363 more)
  These have ≤1 connection - possible missing edges or undocumented components.
- **Thin community `Community 31`** (2 nodes): `Quick smoke test for Gemini search grounding.`, `test_search.py`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 32`** (2 nodes): `__init__.py`, `os_layer — OS-Level Intelligence System ========================================`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 35`** (1 nodes): `Return the full pool of available Gemini API keys.`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 36`** (1 nodes): `Rotate to the next available Gemini key. Returns None if exhausted.`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 37`** (1 nodes): `Re-read environment variables from os.environ and update all class fields.`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 38`** (1 nodes): `Restrict file access to the current user's SID only.          Uses win32security`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 39`** (1 nodes): `Cosine similarity between two L2-normalized embeddings.`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 40`** (1 nodes): `Check if two embeddings belong to the same person.`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 42`** (1 nodes): `Web search via DuckDuckGo — wraps existing _ddg_search.         Returns list of`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 43`** (1 nodes): `Weather report — delegates to existing weather_action.         Handles auto-dete`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 44`** (1 nodes): `Live stock/crypto prices via yfinance (free, no API key).          Args:`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 45`** (1 nodes): `Format price dict into readable string.`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 46`** (1 nodes): `Fetch headlines from RSS feeds (zero API cost).          Args:             categ`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 47`** (1 nodes): `Format news list into readable string (HTML stripped).`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 48`** (1 nodes): `Scrape and extract text content from a URL.          Returns cleaned text (scrip`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 49`** (1 nodes): `Fetch Wikipedia summary for a topic (free, no key).          Uses the maintained`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 50`** (1 nodes): `Fast keyword check — does this query suit Wolfram?`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 53`** (1 nodes): `Check if a filepath matches any of the glob patterns.`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 54`** (1 nodes): `Check if an event matches a subscription's filters.`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 55`** (1 nodes): `Human-readable time gap from timestamp to now.`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 56`** (1 nodes): `Generate a unique session identifier.`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 57`** (1 nodes): `Build a natural-language briefing for boot sequence.`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 58`** (1 nodes): `Called every 3 s from JS. Returns all telemetry (slow data is internally cached)`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 59`** (1 nodes): `Called every 3 s from JS. Returns all telemetry (slow data is internally cached)`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 60`** (1 nodes): `Gathers and caches system metrics at appropriate intervals.`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 61`** (1 nodes): `Run in a daemon thread to avoid blocking the UI on first load.`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 62`** (1 nodes): `Called every 3 s from JS. Returns all telemetry (slow data is internally cached)`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 63`** (1 nodes): `Switch active theme and return the theme config to JS.`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 64`** (1 nodes): `Return the full pool of available Gemini API keys.`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 65`** (1 nodes): `Rotate to the next available Gemini key. Returns None if exhausted.`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 66`** (1 nodes): `Attempt to rotate to a fresh Gemini API key. Returns True if successful.`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 67`** (1 nodes): `Returns the model name string for the given tier.`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 68`** (1 nodes): `Returns the types.LiveConnectConfig for google.genai asynchronous websocket API.`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 69`** (1 nodes): `Executes generation using REST API with transparent fallback.`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 70`** (1 nodes): `For testing purposes only.`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 71`** (1 nodes): `Lazy-initialize ChromaDB client and collection on first use.`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 72`** (1 nodes): `Store a single conversation turn as a vector embedding.      Args:         user_`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 73`** (1 nodes): `Search conversation history by semantic similarity.      Args:         query: Na`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 74`** (1 nodes): `Convenience wrapper: returns a formatted string of past conversations     releva`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 75`** (1 nodes): `One-time migration: converts existing long_term.json facts into     vector embed`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `EventType` connect `Community 4` to `Community 0`, `Community 3`, `Community 5`, `Community 7`, `Community 11`, `Community 13`?**
  _High betweenness centrality (0.321) - this node is a cross-community bridge._
- **Why does `CreditTracker` connect `Community 2` to `Community 13`, `Community 6`?**
  _High betweenness centrality (0.068) - this node is a cross-community bridge._
- **Why does `AgenticShell` connect `Community 0` to `Community 4`?**
  _High betweenness centrality (0.066) - this node is a cross-community bridge._
- **Are the 240 inferred relationships involving `EventType` (e.g. with `tool_defs.py — Central Tool Definitions ========================================` and `Adapter: normalizes the non-standard hand_auth(reason, timeout, theme) signature`) actually correct?**
  _`EventType` has 240 INFERRED edges - model-reasoned connections that need verification._
- **Are the 124 inferred relationships involving `LLMOrchestrator` (e.g. with `Called from main.py.      parameters:         action      : write | edit | ex` and `Load user profile from long_term.json for form filling.`) actually correct?**
  _`LLMOrchestrator` has 124 INFERRED edges - model-reasoned connections that need verification._
- **Are the 103 inferred relationships involving `str` (e.g. with `_ensure_vosk_model()` and `._init_vosk_fallback()`) actually correct?**
  _`str` has 103 INFERRED edges - model-reasoned connections that need verification._
- **Are the 105 inferred relationships involving `CreditTracker` (e.g. with `SystemDataCollector` and `Api`) actually correct?**
  _`CreditTracker` has 105 INFERRED edges - model-reasoned connections that need verification._