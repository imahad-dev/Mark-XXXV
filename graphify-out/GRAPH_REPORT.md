# Graph Report - .  (2026-04-30)

## Corpus Check
- 53 files · ~50,464 words
- Verdict: corpus is large enough that graph structure adds value.

## Summary
- 811 nodes · 1584 edges · 28 communities detected
- Extraction: 77% EXTRACTED · 23% INFERRED · 0% AMBIGUOUS · INFERRED: 362 edges (avg confidence: 0.63)
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
- [[_COMMUNITY_Community 28|Community 28]]
- [[_COMMUNITY_Community 29|Community 29]]

## God Nodes (most connected - your core abstractions)
1. `LLMOrchestrator` - 121 edges
2. `TaskTier` - 79 edges
3. `AgentMemory` - 23 edges
4. `computer_control()` - 22 edges
5. `ErrorDecision` - 21 edges
6. `JarvisUI` - 20 edges
7. `_BrowserThread` - 20 edges
8. `JarvisUI` - 18 edges
9. `browser_control()` - 18 edges
10. `JarvisLive` - 17 edges

## Surprising Connections (you probably didn't know these)
- `_browser_control()` --calls--> `browser_control()`  [INFERRED]
  core\tool_defs.py → actions\browser_control.py
- `_code_helper()` --calls--> `code_helper()`  [INFERRED]
  core\tool_defs.py → actions\code_helper.py
- `_computer_control()` --calls--> `computer_control()`  [INFERRED]
  core\tool_defs.py → actions\computer_control.py
- `_desktop_control()` --calls--> `desktop_control()`  [INFERRED]
  core\tool_defs.py → actions\desktop.py
- `_flight_finder()` --calls--> `flight_finder()`  [INFERRED]
  core\tool_defs.py → actions\flight_finder.py

## Communities

### Community 0 - "Community 0"
Cohesion: 0.03
Nodes (112): Called from main.py.      parameters:         action      : write | edit | ex, Types text at the current cursor position., Clicks at coordinates or on a screen image.     If image path given, locates it, Presses a single key., Scrolls in the specified direction., Moves mouse to coordinates., Drags from (x1,y1) to (x2,y2)., Gets current clipboard content. (+104 more)

### Community 1 - "Community 1"
Cohesion: 0.04
Nodes (12): computer_settings(), copy(), paste(), press_key(), refresh_page(), reload_page_n(), scroll_down(), scroll_up() (+4 more)

### Community 2 - "Community 2"
Cohesion: 0.04
Nodes (40): hand_auth(), actions/hand_auth.py ==================== Jarvis tool wrapper around the hand, Display the hand-scanner window and block until verified or timed out.      Pa, _run_scan(), _normalize(), open_app(), Sets a timed reminder using Windows Task Scheduler.      parameters:, reminder() (+32 more)

### Community 3 - "Community 3"
Cohesion: 0.07
Nodes (29): Enum, AgentMemory, _ConnectionPool, Episode, EpisodeStatus, _format_age(), get_agent_memory(), memory/agent_memory.py ====================== SQLite-backed persistence for mu (+21 more)

### Community 4 - "Community 4"
Cohesion: 0.06
Nodes (26): Config, get_all_gemini_keys(), next_gemini_key(), validate_env(), warn_optional(), start_telegram_daemon(), Exception, _build_boot_prompt() (+18 more)

### Community 5 - "Community 5"
Cohesion: 0.07
Nodes (18): get_scheduler(), JarvisScheduler, agent/scheduler.py ================== Background Task Scheduler for JARVIS.  Run, Register all whitelisted jobs and start the scheduler., Graceful shutdown — waits for running jobs to finish., Register a single whitelisted task as an APScheduler job., Submit a scheduled task to the TaskQueue for execution.          Safety rules:, Return a snapshot of all scheduled jobs for status display. (+10 more)

### Community 6 - "Community 6"
Cohesion: 0.09
Nodes (36): copy_file(), create_file(), create_folder(), delete_file(), file_controller(), find_files(), _format_size(), _get_desktop() (+28 more)

### Community 7 - "Community 7"
Cohesion: 0.16
Nodes (29): _ask_gemini_for_desktop_action(), clean_desktop(), desktop_control(), _execute_generated_code(), get_current_wallpaper(), _get_desktop(), get_desktop_stats(), _is_safe_code() (+21 more)

### Community 8 - "Community 8"
Cohesion: 0.13
Nodes (11): browser_control(), _BrowserThread, _ensure_started(), _find_browser_executable(), _get_default_browser_id(), _get_opera_executable(), Launch Playwright's built-in Chromium with a dedicated Jarvis profile., Returns raw default browser identifier string for current OS. (+3 more)

### Community 9 - "Community 9"
Cohesion: 0.17
Nodes (26): _cancel_scheduled_update(), _click_button(), _click_first_profile_by_screenshot(), _ensure_steam_running(), _find_best_drive(), _find_epic_path(), _find_steam_path(), game_updater() (+18 more)

### Community 10 - "Community 10"
Cohesion: 0.15
Nodes (5): _ac(), JarvisUI, Sol alt köşeye mute butonu yerleştirir., Log'un hemen altına tek satır metin giriş alanı., main.py'den çağrılır.         state: LISTENING | SPEAKING | THINKING | MUTED |

### Community 11 - "Community 11"
Cohesion: 0.2
Nodes (23): _analyze_screen_for_element(), _clear_field(), _click(), _clipboard_copy(), _clipboard_set(), computer_control(), _drag(), _ensure_pyautogui() (+15 more)

### Community 12 - "Community 12"
Cohesion: 0.12
Nodes (9): Api, get_base_dir(), webview_ui.py – PyWebView frontend controller for JARVIS MARK XXXV.  Collects re, Run in a daemon thread to avoid blocking the UI on first load., Called every 3 s from JS. Returns all telemetry (slow data is internally cached), Switch active theme and return the theme config to JS., Gathers and caches system metrics at appropriate intervals., SystemDataCollector (+1 more)

### Community 13 - "Community 13"
Cohesion: 0.16
Nodes (22): authenticate(), _draw_corner_brackets(), _draw_hud(), _draw_progress_arc(), _draw_skeleton(), enroll_hand(), _get_camera_backend(), _get_model_path() (+14 more)

### Community 14 - "Community 14"
Cohesion: 0.14
Nodes (20): bootstrap_vector_migration(), _empty_memory(), forget(), load_memory(), _recursive_update(), remember(), save_memory(), _truncate_value() (+12 more)

### Community 15 - "Community 15"
Cohesion: 0.23
Nodes (19): _build(), _clean_code(), code_helper(), _detect_intent(), _edit_action(), _explain_action(), _fix_code(), _has_error() (+11 more)

### Community 16 - "Community 16"
Cohesion: 0.13
Nodes (6): QOpenGLWidget, QWidget, DraggableHologramWindow, generate_sphere_wireframe(), HologramWidget, main()

### Community 17 - "Community 17"
Cohesion: 0.17
Nodes (9): _capture_camera(), _capture_screenshot(), _ensure_started(), _get_api_key(), _get_camera_index(), _LiveSession, screen_process(), _to_jpeg() (+1 more)

### Community 18 - "Community 18"
Cohesion: 0.2
Nodes (16): _ask_for_url(), _extract_video_id(), find_video_thumbnails(), _get_default_browser_display_name(), _get_default_browser_name(), _get_transcript(), _handle_get_info(), _handle_play() (+8 more)

### Community 19 - "Community 19"
Cohesion: 0.16
Nodes (17): _open_app(), Sends a Telegram message via Windows desktop app., Opens an app via Windows search., For any other platform not explicitly supported.     Opens the app, searches fo, Called from main.py.      parameters:         receiver     : Contact name to, Searches for a contact inside the messaging app.     Uses Ctrl+F (universal sea, Types message and sends it., Sends a WhatsApp message via the Windows desktop app.     Steps: Open WhatsApp (+9 more)

### Community 20 - "Community 20"
Cohesion: 0.14
Nodes (8): Execute a registered tool by name.          Args:             name:    Tool name, Generate the Gemini function_declarations JSON array from registered tools., Return the safety tier for a tool, or None if not found., Print all registered tools to console., Singleton registry for all JARVIS tools.      Usage:         from core.tool_regi, Register a tool. Overwrites if name already exists (supports hot-reload)., Return sorted list of registered tool names., ToolRegistry

### Community 21 - "Community 21"
Cohesion: 0.36
Nodes (8): _ask_gemini(), cmd_control(), _find_hardcoded(), _get_platform(), _is_safe(), _run_silent(), _run_visible(), _cmd_control()

### Community 22 - "Community 22"
Cohesion: 0.33
Nodes (2): applyTheme(), updateOrbState()

### Community 23 - "Community 23"
Cohesion: 0.33
Nodes (5): elevenlabs_tts.py — ElevenLabs Text-to-Speech for JARVIS personas. =============, Non-blocking wrapper — runs speak_elevenlabs in a daemon thread.     Returns the, Synthesize `text` via ElevenLabs and play it through speakers.      Args:, speak_elevenlabs(), speak_elevenlabs_async()

### Community 24 - "Community 24"
Cohesion: 0.6
Nodes (3): create_plan(), _fallback_plan(), replan()

### Community 25 - "Community 25"
Cohesion: 1.0
Nodes (1): Quick smoke test for Gemini search grounding.

### Community 28 - "Community 28"
Cohesion: 1.0
Nodes (1): Return the full pool of available Gemini API keys.

### Community 29 - "Community 29"
Cohesion: 1.0
Nodes (1): Rotate to the next available Gemini key. Returns None if exhausted.

## Knowledge Gaps
- **96 isolated node(s):** `Quick smoke test for Gemini search grounding.`, `Sol alt köşeye mute butonu yerleştirir.`, `Log'un hemen altına tek satır metin giriş alanı.`, `main.py'den çağrılır.         state: LISTENING | SPEAKING | THINKING | MUTED |`, `webview_ui.py – PyWebView frontend controller for JARVIS MARK XXXV.  Collects re` (+91 more)
  These have ≤1 connection - possible missing edges or undocumented components.
- **Thin community `Community 22`** (7 nodes): `$()`, `animate()`, `applyTheme()`, `app.js`, `sendCommand()`, `updateOrbState()`, `updateSystemData()`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 25`** (2 nodes): `Quick smoke test for Gemini search grounding.`, `test_search.py`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 28`** (1 nodes): `Return the full pool of available Gemini API keys.`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 29`** (1 nodes): `Rotate to the next available Gemini key. Returns None if exhausted.`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `LLMOrchestrator` connect `Community 0` to `Community 2`, `Community 7`, `Community 11`, `Community 15`, `Community 18`, `Community 21`, `Community 24`?**
  _High betweenness centrality (0.217) - this node is a cross-community bridge._
- **Why does `JarvisUI` connect `Community 4` to `Community 12`?**
  _High betweenness centrality (0.069) - this node is a cross-community bridge._
- **Why does `JarvisLive` connect `Community 4` to `Community 18`?**
  _High betweenness centrality (0.063) - this node is a cross-community bridge._
- **Are the 107 inferred relationships involving `LLMOrchestrator` (e.g. with `Called from main.py.      parameters:         action      : write | edit | ex` and `Load user profile from long_term.json for form filling.`) actually correct?**
  _`LLMOrchestrator` has 107 INFERRED edges - model-reasoned connections that need verification._
- **Are the 77 inferred relationships involving `TaskTier` (e.g. with `Called from main.py.      parameters:         action      : write | edit | ex` and `Load user profile from long_term.json for form filling.`) actually correct?**
  _`TaskTier` has 77 INFERRED edges - model-reasoned connections that need verification._
- **Are the 65 inferred relationships involving `str` (e.g. with `_ensure_vosk_model()` and `._init_vosk()`) actually correct?**
  _`str` has 65 INFERRED edges - model-reasoned connections that need verification._
- **What connects `Quick smoke test for Gemini search grounding.`, `Sol alt köşeye mute butonu yerleştirir.`, `Log'un hemen altına tek satır metin giriş alanı.` to the rest of the system?**
  _96 weakly-connected nodes found - possible documentation gaps or missing edges._