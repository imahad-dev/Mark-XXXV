"""
tool_defs.py — Central Tool Definitions
=========================================
Registers ALL JARVIS tools into the ToolRegistry.

Import this module once at startup. Every @tool decorator call
auto-registers the handler into the singleton registry.

Architecture Note:
  - `hand_auth` has a non-standard signature (positional args, no `parameters` dict).
    It gets a thin adapter wrapper to normalize to the standard interface.
  - `save_memory` is a synthetic tool (no action file) — handled inline.
  - `screen_process` is fire-and-forget (runs in a thread, never returns a result
    to the caller). The adapter handles this by returning a fixed string.
  - `agent_task` routes to the TaskQueue, not a direct action module.

Safety Tiers:
  SAFE    — open_app, web_search, weather_report, youtube_video, screen_process,
            save_memory, flight_finder, game_updater
  NOTIFY  — browser_control, file_controller, code_helper, dev_agent,
            computer_settings, desktop_control, computer_control, cmd_control,
            reminder, send_message, agent_task
  CONFIRM — hand_auth
"""

from core.tool_registry import tool, SafetyTier


# ── 1. open_app ───────────────────────────────────────────────────────────────

@tool(
    name="open_app",
    description=(
        "Opens any application on the Windows computer. "
        "Use this whenever the user asks to open, launch, or start any app, "
        "website, or program. Always call this tool — never just say you opened it."
    ),
    parameters={
        "type": "OBJECT",
        "properties": {
            "app_name": {
                "type": "STRING",
                "description": "Exact name of the application (e.g. 'WhatsApp', 'Chrome', 'Spotify')"
            }
        },
        "required": ["app_name"]
    },
    safety=SafetyTier.SAFE,
)
def _open_app(parameters=None, player=None, **kw):
    from actions.open_app import open_app
    return open_app(parameters=parameters, response=None, player=player) or f"Opened {(parameters or {}).get('app_name')}."


# ── 2. web_search ─────────────────────────────────────────────────────────────

@tool(
    name="web_search",
    description="Searches the web for any information.",
    parameters={
        "type": "OBJECT",
        "properties": {
            "query":  {"type": "STRING", "description": "Search query"},
            "mode":   {"type": "STRING", "description": "search (default) or compare"},
            "items":  {"type": "ARRAY", "items": {"type": "STRING"}, "description": "Items to compare"},
            "aspect": {"type": "STRING", "description": "price | specs | reviews"},
        },
        "required": ["query"]
    },
    safety=SafetyTier.SAFE,
)
def _web_search(parameters=None, player=None, **kw):
    from actions.web_search import web_search
    return web_search(parameters=parameters, player=player) or "Done."


# ── 3. weather_report ─────────────────────────────────────────────────────────

@tool(
    name="weather_report",
    description="Gets real-time weather information for a city.",
    parameters={
        "type": "OBJECT",
        "properties": {
            "city": {"type": "STRING", "description": "City name"}
        },
        "required": ["city"]
    },
    safety=SafetyTier.SAFE,
)
def _weather_report(parameters=None, player=None, **kw):
    from actions.weather_report import weather_action
    return weather_action(parameters=parameters, player=player) or "Weather delivered."


# ── 4. send_message ───────────────────────────────────────────────────────────

@tool(
    name="send_message",
    description="Sends a text message via WhatsApp, Telegram, or other messaging platform.",
    parameters={
        "type": "OBJECT",
        "properties": {
            "receiver":     {"type": "STRING", "description": "Recipient contact name"},
            "message_text": {"type": "STRING", "description": "The message to send"},
            "platform":     {"type": "STRING", "description": "Platform: WhatsApp, Telegram, etc."},
        },
        "required": ["receiver", "message_text", "platform"]
    },
    safety=SafetyTier.NOTIFY,
)
def _send_message(parameters=None, player=None, **kw):
    from actions.send_message import send_message
    return send_message(parameters=parameters, response=None, player=player, session_memory=None) or f"Message sent to {(parameters or {}).get('receiver')}."


# ── 5. reminder ───────────────────────────────────────────────────────────────

@tool(
    name="reminder",
    description="Sets a timed reminder using Windows Task Scheduler.",
    parameters={
        "type": "OBJECT",
        "properties": {
            "date":    {"type": "STRING", "description": "Date in YYYY-MM-DD format"},
            "time":    {"type": "STRING", "description": "Time in HH:MM format (24h)"},
            "message": {"type": "STRING", "description": "Reminder message text"},
        },
        "required": ["date", "time", "message"]
    },
    safety=SafetyTier.NOTIFY,
)
def _reminder(parameters=None, player=None, **kw):
    from actions.reminder import reminder
    return reminder(parameters=parameters, response=None, player=player) or "Reminder set."


# ── 6. youtube_video ──────────────────────────────────────────────────────────

@tool(
    name="youtube_video",
    description=(
        "Controls YouTube. Use for: playing videos, summarizing a video's content, "
        "getting video info, or showing trending videos."
    ),
    parameters={
        "type": "OBJECT",
        "properties": {
            "action": {"type": "STRING", "description": "play | summarize | get_info | trending (default: play)"},
            "query":  {"type": "STRING", "description": "Search query for play action"},
            "save":   {"type": "BOOLEAN", "description": "Save summary to Notepad (summarize only)"},
            "region": {"type": "STRING", "description": "Country code for trending e.g. TR, US"},
            "url":    {"type": "STRING", "description": "Video URL for get_info action"},
        },
        "required": []
    },
    safety=SafetyTier.SAFE,
)
def _youtube_video(parameters=None, player=None, **kw):
    from actions.youtube_video import youtube_video
    return youtube_video(parameters=parameters, response=None, player=player) or "Done."


# ── 7. screen_process ─────────────────────────────────────────────────────────

@tool(
    name="screen_process",
    description=(
        "Captures and analyzes the screen or webcam image. "
        "MUST be called when user asks what is on screen, what you see, "
        "analyze my screen, look at camera, etc. "
        "You have NO visual ability without this tool. "
        "After calling this tool, stay SILENT — the vision module speaks directly."
    ),
    parameters={
        "type": "OBJECT",
        "properties": {
            "angle": {"type": "STRING", "description": "'screen' to capture display, 'camera' for webcam. Default: 'screen'"},
            "text":  {"type": "STRING", "description": "The question or instruction about the captured image"},
        },
        "required": ["text"]
    },
    safety=SafetyTier.SAFE,
)
def _screen_process(parameters=None, player=None, **kw):
    import threading
    from actions.screen_processor import screen_process
    threading.Thread(
        target=screen_process,
        kwargs={"parameters": parameters, "response": None,
                "player": player, "session_memory": None},
        daemon=True
    ).start()
    return "Vision module activated. Stay completely silent — vision module will speak directly."


# ── 8. computer_settings ──────────────────────────────────────────────────────

@tool(
    name="computer_settings",
    description=(
        "Controls the computer: volume, brightness, window management, keyboard shortcuts, "
        "typing text on screen, closing apps, fullscreen, dark mode, WiFi, restart, shutdown, "
        "scrolling, tab management, zoom, screenshots, lock screen, refresh/reload page. "
        "Use for ANY single computer control command. NEVER route to agent_task."
    ),
    parameters={
        "type": "OBJECT",
        "properties": {
            "action":      {"type": "STRING", "description": "The action to perform"},
            "description": {"type": "STRING", "description": "Natural language description of what to do"},
            "value":       {"type": "STRING", "description": "Optional value: volume level, text to type, etc."},
        },
        "required": []
    },
    safety=SafetyTier.NOTIFY,
)
def _computer_settings(parameters=None, player=None, **kw):
    from actions.computer_settings import computer_settings
    return computer_settings(parameters=parameters, response=None, player=player) or "Done."


# ── 9. browser_control ───────────────────────────────────────────────────────

@tool(
    name="browser_control",
    description=(
        "Controls the web browser. Use for: opening websites, searching the web, "
        "clicking elements, filling forms, scrolling, any web-based task."
    ),
    parameters={
        "type": "OBJECT",
        "properties": {
            "action":      {"type": "STRING", "description": "go_to | search | click | type | scroll | fill_form | smart_click | smart_type | get_text | press | close"},
            "url":         {"type": "STRING", "description": "URL for go_to action"},
            "query":       {"type": "STRING", "description": "Search query for search action"},
            "selector":    {"type": "STRING", "description": "CSS selector for click/type"},
            "text":        {"type": "STRING", "description": "Text to click or type"},
            "description": {"type": "STRING", "description": "Element description for smart_click/smart_type"},
            "direction":   {"type": "STRING", "description": "up or down for scroll"},
            "key":         {"type": "STRING", "description": "Key name for press action"},
            "incognito":   {"type": "BOOLEAN", "description": "Open in private/incognito mode"},
        },
        "required": ["action"]
    },
    safety=SafetyTier.NOTIFY,
)
def _browser_control(parameters=None, player=None, **kw):
    from actions.browser_control import browser_control
    return browser_control(parameters=parameters, player=player) or "Done."


# ── 10. file_controller ──────────────────────────────────────────────────────

@tool(
    name="file_controller",
    description="Manages files and folders: list, create, delete, move, copy, rename, read, write, find, disk usage.",
    parameters={
        "type": "OBJECT",
        "properties": {
            "action":      {"type": "STRING", "description": "list | create_file | create_folder | delete | move | copy | rename | read | write | find | largest | disk_usage | organize_desktop | info"},
            "path":        {"type": "STRING", "description": "File/folder path or shortcut: desktop, downloads, documents, home"},
            "destination": {"type": "STRING", "description": "Destination path for move/copy"},
            "new_name":    {"type": "STRING", "description": "New name for rename"},
            "content":     {"type": "STRING", "description": "Content for create_file/write"},
            "name":        {"type": "STRING", "description": "File name to search for"},
            "extension":   {"type": "STRING", "description": "File extension to search (e.g. .pdf)"},
            "count":       {"type": "INTEGER", "description": "Number of results for largest"},
        },
        "required": ["action"]
    },
    safety=SafetyTier.NOTIFY,
)
def _file_controller(parameters=None, player=None, **kw):
    from actions.file_controller import file_controller
    return file_controller(parameters=parameters, player=player) or "Done."


# ── 11. cmd_control ──────────────────────────────────────────────────────────

@tool(
    name="cmd_control",
    description=(
        "Runs CMD/terminal commands via natural language: disk space, processes, "
        "system info, network, find files, or anything in the command line."
    ),
    parameters={
        "type": "OBJECT",
        "properties": {
            "task":    {"type": "STRING", "description": "Natural language description of what to do"},
            "visible": {"type": "BOOLEAN", "description": "Open visible CMD window. Default: true"},
            "command": {"type": "STRING", "description": "Optional: exact command if already known"},
        },
        "required": ["task"]
    },
    safety=SafetyTier.NOTIFY,
)
def _cmd_control(parameters=None, player=None, **kw):
    from actions.cmd_control import cmd_control
    return cmd_control(parameters=parameters, player=player) or "Done."


# ── 12. desktop_control ──────────────────────────────────────────────────────

@tool(
    name="desktop_control",
    description="Controls the desktop: wallpaper, organize, clean, list, stats.",
    parameters={
        "type": "OBJECT",
        "properties": {
            "action": {"type": "STRING", "description": "wallpaper | wallpaper_url | organize | clean | list | stats | task"},
            "path":   {"type": "STRING", "description": "Image path for wallpaper"},
            "url":    {"type": "STRING", "description": "Image URL for wallpaper_url"},
            "mode":   {"type": "STRING", "description": "by_type or by_date for organize"},
            "task":   {"type": "STRING", "description": "Natural language desktop task"},
        },
        "required": ["action"]
    },
    safety=SafetyTier.NOTIFY,
)
def _desktop_control(parameters=None, player=None, **kw):
    from actions.desktop import desktop_control
    return desktop_control(parameters=parameters, player=player) or "Done."


# ── 13. code_helper ──────────────────────────────────────────────────────────

@tool(
    name="code_helper",
    description="Writes, edits, explains, runs, or builds code files.",
    parameters={
        "type": "OBJECT",
        "properties": {
            "action":      {"type": "STRING", "description": "write | edit | explain | run | build | auto (default: auto)"},
            "description": {"type": "STRING", "description": "What the code should do or what change to make"},
            "language":    {"type": "STRING", "description": "Programming language (default: python)"},
            "output_path": {"type": "STRING", "description": "Where to save the file"},
            "file_path":   {"type": "STRING", "description": "Path to existing file for edit/explain/run/build"},
            "code":        {"type": "STRING", "description": "Raw code string for explain"},
            "args":        {"type": "STRING", "description": "CLI arguments for run/build"},
            "timeout":     {"type": "INTEGER", "description": "Execution timeout in seconds (default: 30)"},
        },
        "required": ["action"]
    },
    safety=SafetyTier.NOTIFY,
)
def _code_helper(parameters=None, player=None, speak=None, **kw):
    from actions.code_helper import code_helper
    return code_helper(parameters=parameters, player=player, speak=speak) or "Done."


# ── 14. dev_agent ────────────────────────────────────────────────────────────

@tool(
    name="dev_agent",
    description="Builds complete multi-file projects from scratch: plans, writes files, installs deps, opens VSCode, runs and fixes errors.",
    parameters={
        "type": "OBJECT",
        "properties": {
            "description":  {"type": "STRING", "description": "What the project should do"},
            "language":     {"type": "STRING", "description": "Programming language (default: python)"},
            "project_name": {"type": "STRING", "description": "Optional project folder name"},
            "timeout":      {"type": "INTEGER", "description": "Run timeout in seconds (default: 30)"},
        },
        "required": ["description"]
    },
    safety=SafetyTier.NOTIFY,
    timeout=300,  # Projects can take longer
)
def _dev_agent(parameters=None, player=None, speak=None, **kw):
    from actions.dev_agent import dev_agent
    return dev_agent(parameters=parameters, player=player, speak=speak) or "Done."


# ── 15. agent_task ───────────────────────────────────────────────────────────

@tool(
    name="agent_task",
    description=(
        "Executes complex multi-step tasks requiring multiple different tools. "
        "Examples: 'research X and save to file', 'find and organize files'. "
        "DO NOT use for single commands. NEVER use for Steam/Epic — use game_updater."
    ),
    parameters={
        "type": "OBJECT",
        "properties": {
            "goal":     {"type": "STRING", "description": "Complete description of what to accomplish"},
            "priority": {"type": "STRING", "description": "low | normal | high (default: normal)"},
        },
        "required": ["goal"]
    },
    safety=SafetyTier.NOTIFY,
    timeout=300,
)
def _agent_task(parameters=None, player=None, speak=None, **kw):
    from agent.task_queue import get_queue, TaskPriority
    priority_map = {"low": TaskPriority.LOW, "normal": TaskPriority.NORMAL, "high": TaskPriority.HIGH}
    priority = priority_map.get((parameters or {}).get("priority", "normal").lower(), TaskPriority.NORMAL)
    task_id = get_queue().submit(goal=(parameters or {}).get("goal", ""), priority=priority, speak=speak)
    return f"Task started (ID: {task_id})."


# ── 16. computer_control ─────────────────────────────────────────────────────

@tool(
    name="computer_control",
    description="Direct computer control: type, click, hotkeys, scroll, move mouse, screenshots, find elements on screen.",
    parameters={
        "type": "OBJECT",
        "properties": {
            "action":      {"type": "STRING", "description": "type | smart_type | click | double_click | right_click | hotkey | press | scroll | move | copy | paste | screenshot | wait | clear_field | focus_window | screen_find | screen_click | random_data | user_data"},
            "text":        {"type": "STRING", "description": "Text to type or paste"},
            "x":           {"type": "INTEGER", "description": "X coordinate"},
            "y":           {"type": "INTEGER", "description": "Y coordinate"},
            "keys":        {"type": "STRING", "description": "Key combination e.g. 'ctrl+c'"},
            "key":         {"type": "STRING", "description": "Single key e.g. 'enter'"},
            "direction":   {"type": "STRING", "description": "up | down | left | right"},
            "amount":      {"type": "INTEGER", "description": "Scroll amount (default: 3)"},
            "seconds":     {"type": "NUMBER",  "description": "Seconds to wait"},
            "title":       {"type": "STRING",  "description": "Window title for focus_window"},
            "description": {"type": "STRING",  "description": "Element description for screen_find/screen_click"},
            "type":        {"type": "STRING",  "description": "Data type for random_data"},
            "field":       {"type": "STRING",  "description": "Field for user_data: name|email|city"},
            "clear_first": {"type": "BOOLEAN", "description": "Clear field before typing (default: true)"},
            "path":        {"type": "STRING",  "description": "Save path for screenshot"},
        },
        "required": ["action"]
    },
    safety=SafetyTier.NOTIFY,
)
def _computer_control(parameters=None, player=None, **kw):
    from actions.computer_control import computer_control
    return computer_control(parameters=parameters, player=player) or "Done."


# ── 17. game_updater ─────────────────────────────────────────────────────────

@tool(
    name="game_updater",
    description=(
        "THE ONLY tool for ANY Steam or Epic Games request. "
        "Use for: installing, downloading, updating games, listing installed games, "
        "checking download status, scheduling updates. "
        "ALWAYS call directly for any Steam/Epic/game request. "
        "NEVER use agent_task, browser_control, or web_search for Steam/Epic."
    ),
    parameters={
        "type": "OBJECT",
        "properties": {
            "action":    {"type": "STRING",  "description": "update | install | list | download_status | schedule | cancel_schedule | schedule_status (default: update)"},
            "platform":  {"type": "STRING",  "description": "steam | epic | both (default: both)"},
            "game_name": {"type": "STRING",  "description": "Game name (partial match supported)"},
            "app_id":    {"type": "STRING",  "description": "Steam AppID for install (optional)"},
            "hour":      {"type": "INTEGER", "description": "Hour for scheduled update 0-23 (default: 3)"},
            "minute":    {"type": "INTEGER", "description": "Minute for scheduled update 0-59 (default: 0)"},
            "shutdown_when_done": {"type": "BOOLEAN", "description": "Shut down PC when download finishes"},
        },
        "required": []
    },
    safety=SafetyTier.SAFE,
)
def _game_updater(parameters=None, player=None, speak=None, **kw):
    from actions.game_updater import game_updater
    return game_updater(parameters=parameters, player=player, speak=speak) or "Done."


# ── 18. flight_finder ────────────────────────────────────────────────────────

@tool(
    name="flight_finder",
    description="Searches Google Flights and speaks the best options.",
    parameters={
        "type": "OBJECT",
        "properties": {
            "origin":      {"type": "STRING",  "description": "Departure city or airport code"},
            "destination": {"type": "STRING",  "description": "Arrival city or airport code"},
            "date":        {"type": "STRING",  "description": "Departure date (any format)"},
            "return_date": {"type": "STRING",  "description": "Return date for round trips"},
            "passengers":  {"type": "INTEGER", "description": "Number of passengers (default: 1)"},
            "cabin":       {"type": "STRING",  "description": "economy | premium | business | first"},
            "save":        {"type": "BOOLEAN", "description": "Save results to Notepad"},
        },
        "required": ["origin", "destination", "date"]
    },
    safety=SafetyTier.SAFE,
)
def _flight_finder(parameters=None, player=None, speak=None, **kw):
    from actions.flight_finder import flight_finder
    return flight_finder(parameters=parameters, player=player) or "Done."


# ── 19. save_memory (synthetic — no action file) ─────────────────────────────

@tool(
    name="save_memory",
    description=(
        "Save an important personal fact about the user to long-term memory. "
        "Call this silently whenever the user reveals something worth remembering: "
        "name, age, city, job, preferences, hobbies, relationships, projects, or future plans. "
        "Do NOT call for: weather, reminders, searches, or one-time commands. "
        "Do NOT announce that you are saving — just call it silently. "
        "Values must be in English regardless of the conversation language."
    ),
    parameters={
        "type": "OBJECT",
        "properties": {
            "category": {
                "type": "STRING",
                "description": (
                    "identity — name, age, birthday, city, job, language, nationality | "
                    "preferences — favorite food/color/music/film/game/sport, hobbies | "
                    "projects — active projects, goals, things being built | "
                    "relationships — friends, family, partner, colleagues | "
                    "wishes — future plans, things to buy, travel dreams | "
                    "notes — habits, schedule, anything else worth remembering"
                )
            },
            "key":   {"type": "STRING", "description": "Short snake_case key (e.g. name, favorite_food, sister_name)"},
            "value": {"type": "STRING", "description": "Concise value in English (e.g. Fatih, pizza, older sister)"},
        },
        "required": ["category", "key", "value"]
    },
    safety=SafetyTier.SAFE,
    timeout=10,
)
def _save_memory(parameters=None, player=None, **kw):
    from memory.memory_manager import update_memory
    params = parameters or {}
    category = params.get("category", "notes")
    key = params.get("key", "")
    value = params.get("value", "")
    if key and value:
        update_memory({category: {key: {"value": value}}})
        print(f"[Memory] 💾 save_memory: {category}/{key} = {value}")
        return "ok"
    return "Missing key or value."


# ── 20. hand_auth ────────────────────────────────────────────────────────────

@tool(
    name="hand_auth",
    description=(
        "Scans the user's hand via webcam to verify identity before "
        "performing a sensitive action. Call this tool BEFORE: "
        "deleting files or folders, sending emails or messages, "
        "running terminal commands, making purchases, or any time "
        "the user says 'scan my hand', 'verify me', or "
        "'confirm my identity'. Returns authenticated or denied."
    ),
    parameters={
        "type": "OBJECT",
        "properties": {
            "reason": {
                "type": "STRING",
                "description": "Brief reason why authentication is needed.",
            },
            "timeout": {
                "type": "NUMBER",
                "description": "Seconds to wait for hand (default 15).",
            },
        },
        "required": []
    },
    safety=SafetyTier.CONFIRM,
    timeout=30,
)
def _hand_auth(parameters=None, player=None, **kw):
    """Adapter: normalizes the non-standard hand_auth(reason, timeout, theme) signature."""
    from actions.hand_auth import hand_auth
    params = parameters or {}
    theme = "jarvis"
    if player and hasattr(player, "current_theme"):
        theme = getattr(player, "current_theme", "jarvis")
    return hand_auth(
        reason=params.get("reason", ""),
        timeout=float(params.get("timeout", 15.0)),
        theme=theme,
    )


# ── 20. stock_price ───────────────────────────────────────────────────────────

@tool(
    name="stock_price",
    description=(
        "Gets live stock or cryptocurrency prices. "
        "Use when user asks about stock price, market data, "
        "Bitcoin, crypto, or any ticker symbol."
    ),
    parameters={
        "type": "OBJECT",
        "properties": {
            "ticker": {
                "type": "STRING",
                "description": "Stock ticker (e.g. 'AAPL', 'TSLA') or crypto (e.g. 'BTC-USD', 'ETH-USD')"
            }
        },
        "required": ["ticker"]
    },
    safety=SafetyTier.SAFE,
)
def _stock_price(parameters=None, **kw):
    from core.specialists.web_intelligence import WebIntelligence
    params = parameters or {}
    ticker = params.get("ticker", "")
    if not ticker:
        return "Please provide a stock ticker symbol."
    data = WebIntelligence.get_price(ticker)
    if data:
        return WebIntelligence.format_price(data)
    return f"Could not find price data for '{ticker}'."


# ── 21. news_feed ─────────────────────────────────────────────────────────────

@tool(
    name="news_feed",
    description=(
        "Fetches latest news headlines from RSS feeds. "
        "Use when user asks about news, headlines, current events, "
        "or what happened today. Supports categories: world, tech, science, local."
    ),
    parameters={
        "type": "OBJECT",
        "properties": {
            "category": {
                "type": "STRING",
                "description": "News category: 'world', 'tech', 'science', or 'local' (default: world)"
            },
            "count": {
                "type": "NUMBER",
                "description": "Number of headlines to return (default: 5, max: 10)"
            }
        },
        "required": []
    },
    safety=SafetyTier.SAFE,
)
def _news_feed(parameters=None, **kw):
    from core.specialists.web_intelligence import WebIntelligence
    params = parameters or {}
    category = params.get("category", "world")
    count = min(int(params.get("count", 5)), 10)
    items = WebIntelligence.get_news(category=category, limit=count)
    if items:
        return WebIntelligence.format_news(items)
    return f"No news available for category '{category}'."


# ── 22. screen_intelligence ──────────────────────────────────────────────────

@tool(
    name="screen_intelligence",
    description=(
        "Captures and analyzes current screen context: active app, visible text, "
        "window layout, and recent screen history. Use when asked 'what app am I using', "
        "'what's on my screen right now', 'what was I doing earlier', or to provide "
        "context-aware assistance. Returns structured screen data without taking a screenshot."
    ),
    parameters={
        "type": "OBJECT",
        "properties": {
            "action": {
                "type": "STRING",
                "description": (
                    "capture — full screen context snapshot | "
                    "active — just the active window info | "
                    "text — extract visible text from active window | "
                    "layout — list all visible windows | "
                    "history — recent screen contexts | "
                    "summary — human-readable context summary"
                ),
            },
            "app_filter": {
                "type": "STRING",
                "description": "Filter history by app name (only for 'history' action)",
            },
            "limit": {
                "type": "INTEGER",
                "description": "Number of history entries to return (default: 5)",
            },
        },
        "required": [],
    },
    safety=SafetyTier.SAFE,
)
def _screen_intelligence(parameters=None, **kw):
    from os_layer.screen_intel import get_screen_intelligence
    si = get_screen_intelligence()
    params = parameters or {}
    action = params.get("action", "summary")

    if action == "capture":
        ctx = si.capture_context()
        return (
            f"Active: {ctx.active_window.app_name} — {ctx.active_window.title}\n"
            f"Source: {ctx.source}\n"
            f"Windows: {len(ctx.window_layout)}\n"
            f"Text preview: {ctx.visible_text[:300]}"
        )

    if action == "active":
        win = si.get_active_window()
        return (
            f"App: {win.app_name}\n"
            f"Title: {win.title}\n"
            f"PID: {win.process_id}\n"
            f"Position: {win.rect}"
        )

    if action == "text":
        text = si.get_visible_text()
        return text[:2000] if text else "No text could be extracted."

    if action == "layout":
        windows = si.get_window_layout()
        if not windows:
            return "No visible windows found."
        lines = [f"{w.app_name}: {w.title} @ {w.rect}" for w in windows[:15]]
        return "\n".join(lines)

    if action == "history":
        limit = min(int(params.get("limit", 5)), 20)
        app_filter = params.get("app_filter")
        entries = si.get_recent_contexts(limit=limit, app_filter=app_filter)
        if not entries:
            return "No screen history available."
        lines = []
        for e in entries:
            lines.append(
                f"[{e.get('app_name', '?')}] {e.get('window_title', '')[:60]} "
                f"(source: {e.get('source', '?')})"
            )
        return "\n".join(lines)

    # Default: summary
    return si.get_context_summary()


# ── 23. window_manager ───────────────────────────────────────────────────────

@tool(
    name="window_manager",
    description=(
        "Controls windows on the desktop: move, resize, snap to positions, "
        "save/restore window layouts, and manage processes. Use for: "
        "'arrange my windows for coding', 'snap Chrome to the left', "
        "'save this layout', 'restore my coding layout', "
        "'what processes are using the most CPU', 'kill that process'."
    ),
    parameters={
        "type": "OBJECT",
        "properties": {
            "action": {
                "type": "STRING",
                "description": (
                    "snap — snap window to position | "
                    "move — move/resize window | "
                    "close — close a window | "
                    "save_layout — save current arrangement | "
                    "restore_layout — restore saved arrangement | "
                    "list_layouts — list saved layouts | "
                    "delete_layout — delete a saved layout | "
                    "list_processes — list running processes | "
                    "kill_process — terminate a process | "
                    "resource_hogs — find CPU-heavy processes | "
                    "launch — launch an application | "
                    "monitors — list connected monitors"
                ),
            },
            "window_title": {
                "type": "STRING",
                "description": "Partial window title to find the target window",
            },
            "position": {
                "type": "STRING",
                "description": "Snap position: left, right, top, bottom, top_left, top_right, bottom_left, bottom_right, center, maximize, minimize",
            },
            "x": {"type": "INTEGER", "description": "X coordinate for move"},
            "y": {"type": "INTEGER", "description": "Y coordinate for move"},
            "w": {"type": "INTEGER", "description": "Width for move"},
            "h": {"type": "INTEGER", "description": "Height for move"},
            "layout_name": {
                "type": "STRING",
                "description": "Name for save/restore/delete layout",
            },
            "pid": {"type": "INTEGER", "description": "Process ID for kill"},
            "app_path": {"type": "STRING", "description": "Path for launch action"},
            "force": {"type": "BOOLEAN", "description": "Force kill (default: false)"},
        },
        "required": ["action"],
    },
    safety=SafetyTier.NOTIFY,
)
def _window_manager(parameters=None, **kw):
    from os_layer.window_manager import get_window_manager, SnapPosition
    wm = get_window_manager()
    params = parameters or {}
    action = params.get("action", "")

    if action == "snap":
        title = params.get("window_title", "")
        pos_str = params.get("position", "left")
        hwnd = wm.find_window(title) if title else None
        if not hwnd:
            return f"Window '{title}' not found."
        try:
            pos = SnapPosition(pos_str.lower())
        except ValueError:
            return f"Invalid position '{pos_str}'. Use: left, right, top, bottom, center, maximize, minimize."
        ok = wm.snap_window(hwnd, pos)
        return f"Window snapped to {pos_str}." if ok else "Snap failed."

    if action == "move":
        title = params.get("window_title", "")
        hwnd = wm.find_window(title) if title else None
        if not hwnd:
            return f"Window '{title}' not found."
        x = int(params.get("x", 0))
        y = int(params.get("y", 0))
        w = int(params.get("w", 960))
        h = int(params.get("h", 540))
        ok = wm.move_window(hwnd, x, y, w, h)
        return "Window moved." if ok else "Move failed."

    if action == "close":
        title = params.get("window_title", "")
        hwnd = wm.find_window(title) if title else None
        if not hwnd:
            return f"Window '{title}' not found."
        ok = wm.close_window(hwnd)
        return "Window closed." if ok else "Close failed."

    if action == "save_layout":
        name = params.get("layout_name", "default")
        layout = wm.save_layout(name)
        return f"Layout '{name}' saved ({len(layout.windows)} windows)."

    if action == "restore_layout":
        name = params.get("layout_name", "default")
        ok = wm.restore_layout(name)
        return f"Layout '{name}' restored." if ok else f"Layout '{name}' not found or no windows matched."

    if action == "list_layouts":
        layouts = wm.list_layouts()
        return "\n".join(layouts) if layouts else "No saved layouts."

    if action == "delete_layout":
        name = params.get("layout_name", "")
        ok = wm.delete_layout(name)
        return f"Layout '{name}' deleted." if ok else f"Layout '{name}' not found."

    if action == "list_processes":
        procs = wm.list_processes()
        # Sort by memory, show top 15
        procs.sort(key=lambda p: p.memory_mb, reverse=True)
        lines = [f"{p.name}: PID {p.pid}, {p.memory_mb}MB, {p.cpu_percent}% CPU" for p in procs[:15]]
        return "\n".join(lines) if lines else "No processes found."

    if action == "kill_process":
        pid = int(params.get("pid", 0))
        if not pid:
            return "Please provide a process ID (pid)."
        force = params.get("force", False)
        ok = wm.kill_process(pid, force=force)
        return f"Process {pid} terminated." if ok else f"Could not terminate process {pid}."

    if action == "resource_hogs":
        hogs = wm.get_resource_hogs(threshold_cpu=50)
        if not hogs:
            return "No processes consuming excessive CPU."
        lines = [f"{h.name}: PID {h.pid}, {h.cpu_percent}% CPU, {h.memory_mb}MB" for h in hogs[:10]]
        return "\n".join(lines)

    if action == "launch":
        path = params.get("app_path", "")
        if not path:
            return "Please provide an app_path."
        pid = wm.launch_app(path)
        return f"Launched (PID: {pid})." if pid else "Launch failed."

    if action == "monitors":
        monitors = wm.get_monitors()
        if not monitors:
            return "Could not detect monitors."
        lines = [
            f"Monitor {m.index}: {m.width}x{m.height} @ ({m.x},{m.y})"
            + (" [PRIMARY]" if m.is_primary else "")
            for m in monitors
        ]
        return "\n".join(lines)

    return f"Unknown action '{action}'."


# ── 24. workspace_memory ─────────────────────────────────────────────────────

@tool(
    name="workspace_memory",
    description=(
        "Manages workspace state persistence: save current window arrangement, "
        "restore previous session, track incomplete tasks. Use for: "
        "'save my workspace', 'what was I working on', 'restore my last session', "
        "'remind me about my pending tasks', 'mark this task as done'."
    ),
    parameters={
        "type": "OBJECT",
        "properties": {
            "action": {
                "type": "STRING",
                "description": (
                    "save — save current workspace state | "
                    "restore — restore last session | "
                    "brief — get last session summary | "
                    "add_task — mark a task as incomplete | "
                    "complete_task — mark a task as done | "
                    "list_tasks — list incomplete tasks | "
                    "set_context — save what user is working on"
                ),
            },
            "description": {
                "type": "STRING",
                "description": "Task description (for add_task/complete_task/set_context)",
            },
        },
        "required": ["action"],
    },
    safety=SafetyTier.SAFE,
)
def _workspace_memory(parameters=None, **kw):
    from os_layer.workspace_memory import get_workspace_memory
    wm = get_workspace_memory()
    params = parameters or {}
    action = params.get("action", "brief")

    if action == "save":
        snapshot = wm.save_workspace_state()
        return (
            f"Workspace saved: {snapshot.active_app} "
            f"({len(snapshot.open_windows)} windows)"
        )

    if action == "restore":
        result = wm.restore_workspace()
        return result.briefing

    if action == "brief":
        brief = wm.get_last_session_brief()
        return brief or "No previous session data."

    if action == "add_task":
        desc = params.get("description", "")
        if not desc:
            return "Please provide a task description."
        wm.mark_task_incomplete(desc)
        return f"Task tracked: {desc}"

    if action == "complete_task":
        desc = params.get("description", "")
        if not desc:
            return "Please provide the task description to complete."
        wm.complete_task(desc)
        return f"Task completed: {desc}"

    if action == "list_tasks":
        tasks = wm.get_incomplete_tasks()
        if not tasks:
            return "No incomplete tasks."
        return "\n".join(f"• {t}" for t in tasks)

    if action == "set_context":
        desc = params.get("description", "")
        if not desc:
            return "Please describe what you're working on."
        wm.save_active_task_context(desc)
        return f"Context updated: {desc}"

    return f"Unknown action '{action}'."


# ── 25. event_bus ────────────────────────────────────────────────────────────

@tool(
    name="event_bus",
    description=(
        "Query and interact with the OS-level event system. View recent events, "
        "check event history, monitor file system changes. Use for: "
        "'what events happened recently', 'watch this folder for changes', "
        "'show me file change events', 'how many event subscribers are active'."
    ),
    parameters={
        "type": "OBJECT",
        "properties": {
            "action": {
                "type": "STRING",
                "description": (
                    "history — recent events from memory | "
                    "persisted — recent events from database | "
                    "watch — start watching a directory | "
                    "status — event bus status and subscriber count | "
                    "prune — delete old events from database"
                ),
            },
            "event_type": {
                "type": "STRING",
                "description": "Filter by event type (e.g., 'file.modified', 'window.focus_changed')",
            },
            "path": {
                "type": "STRING",
                "description": "Directory path for watch action",
            },
            "patterns": {
                "type": "STRING",
                "description": "Comma-separated glob patterns for watch (e.g., '*.py,*.json')",
            },
            "limit": {
                "type": "INTEGER",
                "description": "Number of events to return (default: 10)",
            },
        },
        "required": ["action"],
    },
    safety=SafetyTier.SAFE,
)
def _event_bus_tool(parameters=None, **kw):
    from os_layer.event_bus import get_event_bus, EventType
    bus = get_event_bus()
    params = parameters or {}
    action = params.get("action", "status")

    if action == "history":
        event_type_str = params.get("event_type")
        limit = min(int(params.get("limit", 10)), 50)
        evt_type = None
        if event_type_str:
            try:
                evt_type = EventType(event_type_str)
            except ValueError:
                return f"Unknown event type '{event_type_str}'."
        events = bus.get_history(event_type=evt_type, limit=limit)
        if not events:
            return "No events in memory."
        lines = [
            f"[{e.event_type.value}] {e.source}: {json.dumps(e.payload)[:80]}"
            for e in events
        ]
        return "\n".join(lines)

    if action == "persisted":
        event_type_str = params.get("event_type")
        limit = min(int(params.get("limit", 10)), 50)
        events = bus.get_persisted_events(event_type=event_type_str, limit=limit)
        if not events:
            return "No events in database."
        lines = [
            f"[{e.get('event_type', '?')}] {e.get('source', '')}: "
            f"{e.get('payload', '')[:80]}"
            for e in events
        ]
        return "\n".join(lines)

    if action == "watch":
        path = params.get("path", "")
        if not path:
            return "Please provide a directory path."
        patterns_str = params.get("patterns", "")
        patterns = [p.strip() for p in patterns_str.split(",") if p.strip()] if patterns_str else None
        ok = bus.watch_directory(path, patterns=patterns)
        return f"Watching: {path}" if ok else "Watch failed (watchdog not installed?)."

    if action == "prune":
        deleted = bus.prune_event_log(max_age_hours=48)
        return f"Pruned {deleted} old events."

    # Default: status
    return (
        f"Event Bus: {'RUNNING' if bus.is_running else 'STOPPED'}\n"
        f"Subscribers: {bus.get_subscriber_count()}\n"
        f"History size: {len(bus._history)}\n"
        f"Watchers: {len(bus._watchers)}"
    )


# ── 26. workflow_recorder ────────────────────────────────────────────────────

@tool(
    name="workflow_recorder",
    description=(
        "Record and replay user workflows — OS-level macros. "
        "Captures window switches, app launches, and layout changes "
        "as replayable sequences. Use for: 'record my workflow', "
        "'start recording', 'stop recording', 'replay my morning setup', "
        "'list my workflows', 'delete a workflow'."
    ),
    parameters={
        "type": "OBJECT",
        "properties": {
            "action": {
                "type": "STRING",
                "description": (
                    "start — start recording a workflow | "
                    "stop — stop and save recording | "
                    "pause — pause recording | "
                    "resume — resume recording | "
                    "discard — discard current recording | "
                    "replay — replay a saved workflow | "
                    "list — list all workflows | "
                    "get — get workflow details | "
                    "delete — delete a workflow | "
                    "add_step — manually add a step | "
                    "status — current recording status"
                ),
            },
            "name": {
                "type": "STRING",
                "description": "Workflow name (for start/replay/get/delete)",
            },
            "description": {
                "type": "STRING",
                "description": "Workflow description (for start)",
            },
            "speed": {
                "type": "NUMBER",
                "description": "Replay speed factor (default: 1.0, 2.0 = 2x fast)",
            },
            "dry_run": {
                "type": "BOOLEAN",
                "description": "If true, log replay steps without executing",
            },
            "step_action": {
                "type": "STRING",
                "description": "Step action for add_step: focus_window, open_app, snap_window, etc.",
            },
            "target": {
                "type": "STRING",
                "description": "Step target for add_step (window title, app path, etc.)",
            },
        },
        "required": ["action"],
    },
    safety=SafetyTier.NOTIFY,
)
def _workflow_recorder_tool(parameters=None, **kw):
    from os_layer.workflow_recorder import (
        get_workflow_recorder, StepAction, RecordingState,
    )
    rec = get_workflow_recorder()
    params = parameters or {}
    action = params.get("action", "status")

    if action == "start":
        name = params.get("name", "")
        if not name:
            return "Please provide a workflow name."
        desc = params.get("description", "")
        ok = rec.start_recording(name, description=desc)
        return f"Recording started: {name}" if ok else "Already recording — stop first."

    if action == "stop":
        wf = rec.stop_recording()
        if wf:
            return f"Workflow '{wf.name}' saved ({wf.step_count} steps)."
        return "No active recording or no steps recorded."

    if action == "pause":
        rec.pause_recording()
        return "Recording paused."

    if action == "resume":
        rec.resume_recording()
        return "Recording resumed."

    if action == "discard":
        rec.discard_recording()
        return "Recording discarded."

    if action == "replay":
        name = params.get("name", "")
        if not name:
            return "Please provide the workflow name to replay."
        speed = float(params.get("speed", 1.0))
        dry_run = params.get("dry_run", False)
        result = rec.replay_workflow(name, speed_factor=speed, dry_run=dry_run)
        msg = (
            f"Replay {'(DRY RUN) ' if dry_run else ''}"
            f"{'complete' if result.success else 'partial'}: "
            f"{result.steps_executed}/{result.steps_total} steps, "
            f"{result.duration_ms}ms"
        )
        if result.errors:
            msg += f"\nErrors: {'; '.join(result.errors[:5])}"
        return msg

    if action == "list":
        workflows = rec.list_workflows()
        if not workflows:
            return "No saved workflows."
        lines = [
            f"• {w['name']} ({w['step_count']} steps) — {w.get('description', '')[:40]}"
            for w in workflows
        ]
        return "\n".join(lines)

    if action == "get":
        name = params.get("name", "")
        if not name:
            return "Please provide a workflow name."
        wf = rec.get_workflow(name)
        if not wf:
            return f"Workflow '{name}' not found."
        lines = [
            f"Workflow: {wf.name}",
            f"Description: {wf.description}",
            f"Steps: {wf.step_count}",
            f"Duration: {wf.total_duration_ms}ms",
            "",
            "Steps:",
        ]
        for s in wf.steps[:20]:
            lines.append(f"  {s.step_index}. {s.action.value} → {s.target}")
        return "\n".join(lines)

    if action == "delete":
        name = params.get("name", "")
        if not name:
            return "Please provide a workflow name to delete."
        ok = rec.delete_workflow(name)
        return f"Deleted: {name}" if ok else f"Workflow '{name}' not found."

    if action == "add_step":
        step_action_str = params.get("step_action", "custom")
        target = params.get("target", "")
        try:
            sa = StepAction(step_action_str)
        except ValueError:
            return f"Unknown step action '{step_action_str}'."
        ok = rec.record_step(sa, target=target)
        return f"Step recorded: {step_action_str} → {target}" if ok else "Not recording."

    # Default: status
    return (
        f"Recording State: {rec.state.value}\n"
        f"Current Steps: {len(rec._current_steps) if rec.is_recording else 0}"
    )


# ── Phase 3: Document Intelligence and Communication Tools ───────────────────

@tool(
    name="document_search",
    description=(
        "Searches local indexed documents, files, PDFs, Word documents, text, "
        "and markdown files semantically for relevant context."
    ),
    parameters={
        "type": "OBJECT",
        "properties": {
            "query": {"type": "STRING", "description": "Natural language query to search for"},
            "limit": {"type": "INTEGER", "description": "Max number of document chunks to return (default 5)"}
        },
        "required": ["query"]
    },
    safety=SafetyTier.SAFE,
)
def _document_search(parameters=None, **kw):
    params = parameters or {}
    query = params.get("query", "")
    limit = params.get("limit", 5)
    if not query:
        return "Please provide a search query."
        
    from memory.vector_store import search_documents
    hits = search_documents(query, k=limit)
    if not hits:
        return f"No matching documents found for: '{query}'."
        
    lines = [f"Found {len(hits)} matching document chunks:"]
    for i, h in enumerate(hits, 1):
        meta = h.get("metadata", {})
        fname = meta.get("file_name", "unknown")
        path = meta.get("source_path", "unknown")
        chunk_idx = meta.get("chunk_index", 0)
        tot_chunks = meta.get("total_chunks", 1)
        lines.append(
            f"\n--- Result #{i} from {fname} (Chunk {chunk_idx + 1}/{tot_chunks}) ---\n"
            f"Path: {path}\n"
            f"Content:\n{h.get('document', '')}"
        )
    return "\n".join(lines)


@tool(
    name="email_control",
    description="Sends emails, lists unread emails, or marks them as read.",
    parameters={
        "type": "OBJECT",
        "properties": {
            "action": {"type": "STRING", "description": "send | list_unread | mark_read"},
            "recipient": {"type": "STRING", "description": "Recipient email (required for 'send')"},
            "subject": {"type": "STRING", "description": "Subject line (required for 'send')"},
            "body": {"type": "STRING", "description": "Email body message (required for 'send')"},
            "email_id": {"type": "STRING", "description": "Email entry ID (required for 'mark_read')"}
        },
        "required": ["action"]
    },
    safety=SafetyTier.NOTIFY,
)
def _email_control(parameters=None, **kw):
    params = parameters or {}
    action = params.get("action", "").lower().strip()
    
    from os_layer.communication import get_communication
    comm = get_communication()
    
    if action == "send":
        recipient = params.get("recipient", "")
        subject = params.get("subject", "")
        body = params.get("body", "")
        if not recipient or not subject or not body:
            return "Error: recipient, subject, and body are required for 'send' action."
        success = comm.email.send_email(recipient, subject, body) if comm.email else False
        return f"Email sent successfully to {recipient}." if success else "Failed to send email."
        
    elif action == "list_unread":
        unread = comm.email.get_unread_emails() if comm.email else []
        if not unread:
            return "No unread emails."
        lines = [f"You have {len(unread)} unread emails:"]
        for mail in unread:
            lines.append(
                f"- [ID: {mail.get('id') or mail.get('EntryID')}] From: {mail.get('sender')}\n"
                f"  Subject: {mail.get('subject')}\n"
                f"  Snippet: {mail.get('body', '')[:100]}..."
            )
        return "\n".join(lines)
        
    elif action == "mark_read":
        email_id = params.get("email_id")
        if not email_id:
            return "Error: email_id is required for 'mark_read' action."
        success = comm.email.mark_as_read(email_id) if comm.email else False
        return f"Email {email_id} marked as read." if success else f"Failed to update email status for ID: {email_id}."
        
    return f"Unknown action: '{action}'."


@tool(
    name="calendar_control",
    description="Creates calendar appointments or lists upcoming appointments.",
    parameters={
        "type": "OBJECT",
        "properties": {
            "action": {"type": "STRING", "description": "create | list"},
            "title": {"type": "STRING", "description": "Event subject (required for 'create')"},
            "start_time": {"type": "STRING", "description": "Start ISO datetime e.g. '2026-06-02T10:00:00' (required for 'create')"},
            "end_time": {"type": "STRING", "description": "End ISO datetime e.g. '2026-06-02T11:00:00' (required for 'create')"},
            "description": {"type": "STRING", "description": "Event description/notes (optional)"}
        },
        "required": ["action"]
    },
    safety=SafetyTier.NOTIFY,
)
def _calendar_control(parameters=None, **kw):
    from datetime import datetime
    params = parameters or {}
    action = params.get("action", "").lower().strip()
    
    from os_layer.communication import get_communication
    comm = get_communication()
    
    if action == "create":
        title = params.get("title", "")
        start_str = params.get("start_time", "")
        end_str = params.get("end_time", "")
        description = params.get("description", "")
        
        if not title or not start_str or not end_str:
            return "Error: title, start_time, and end_time are required for 'create' action."
            
        try:
            # Parse ISO datetime strings into unix timestamps
            start_unix = datetime.fromisoformat(start_str).timestamp()
            end_unix = datetime.fromisoformat(end_str).timestamp()
        except ValueError as err:
            return f"Invalid datetime format. Please use ISO 8601 format (YYYY-MM-DDTHH:MM:SS): {err}"
            
        success = comm.calendar.create_event(title, start_unix, end_unix, description) if comm.calendar else False
        return f"Event '{title}' created successfully." if success else "Failed to create calendar event."
        
    elif action == "list":
        events = comm.calendar.get_upcoming_events(limit=10) if comm.calendar else []
        if not events:
            return "No upcoming events scheduled."
        lines = [f"Upcoming appointments:"]
        for ev in events:
            start_formatted = datetime.fromtimestamp(ev.get("start_time", 0)).strftime("%Y-%m-%d %H:%M")
            end_formatted = datetime.fromtimestamp(ev.get("end_time", 0)).strftime("%Y-%m-%d %H:%M")
            lines.append(
                f"- [ID: {ev.get('id')}] {ev.get('title')}\n"
                f"  Time: {start_formatted} to {end_formatted}\n"
                f"  Desc: {ev.get('description', '')}"
            )
        return "\n".join(lines)
        
    return f"Unknown action: '{action}'."


# ── Phase 4: Knowledge Graph + Parallel Agent Tools ──────────────────────────

import json as _json  # local alias to avoid shadowing param names

@tool(
    name="kg_control",
    description=(
        "Manages the knowledge graph: add/delete nodes and edges, "
        "search for related concepts, find paths between entities. "
        "Use for: 'remember that X is related to Y', 'how is A connected to B', "
        "'what do you know about X', 'forget this entity'."
    ),
    parameters={
        "type": "OBJECT",
        "properties": {
            "action": {
                "type": "STRING",
                "description": (
                    "add_node — create/update a node | "
                    "delete_node — remove a node and its edges | "
                    "add_edge — connect two nodes | "
                    "delete_edge — remove a specific edge | "
                    "neighbors — get connected nodes | "
                    "find_path — shortest path between two nodes | "
                    "search — semantic search for nodes | "
                    "list — list nodes by type | "
                    "stats — node/edge counts"
                ),
            },
            "node_id": {"type": "STRING", "description": "Node ID (auto-generated if omitted for add_node)"},
            "name": {"type": "STRING", "description": "Node name (for add_node)"},
            "node_type": {"type": "STRING", "description": "Node type: person, project, concept, tool, etc."},
            "properties": {"type": "STRING", "description": "JSON string of extra properties"},
            "source_id": {"type": "STRING", "description": "Source node ID (for edges / find_path)"},
            "target_id": {"type": "STRING", "description": "Target node ID (for edges / find_path)"},
            "relationship": {"type": "STRING", "description": "Edge relationship label"},
            "query": {"type": "STRING", "description": "Search query (for search action)"},
            "limit": {"type": "INTEGER", "description": "Max results (default 10)"},
        },
        "required": ["action"],
    },
    safety=SafetyTier.SAFE,
)
def _kg_control(parameters=None, **kw):
    from os_layer.knowledge_graph import get_knowledge_graph
    import uuid as _uuid

    kg = get_knowledge_graph()
    params = parameters or {}
    action = params.get("action", "stats")

    if action == "add_node":
        name = params.get("name", "")
        if not name:
            return "Error: 'name' is required for add_node."
        node_id = params.get("node_id") or str(_uuid.uuid4())[:8]
        node_type = params.get("node_type", "concept")
        try:
            props = _json.loads(params.get("properties", "{}"))
        except _json.JSONDecodeError:
            props = {}
        node = kg.add_node(node_id, name, node_type, props)
        return f"Node created: [{node.id}] {node.name} ({node.type})"

    if action == "delete_node":
        node_id = params.get("node_id", "")
        if not node_id:
            return "Error: 'node_id' is required."
        ok = kg.delete_node(node_id)
        return f"Node '{node_id}' deleted." if ok else f"Node '{node_id}' not found."

    if action == "add_edge":
        source = params.get("source_id", "")
        target = params.get("target_id", "")
        rel = params.get("relationship", "")
        if not source or not target or not rel:
            return "Error: source_id, target_id, and relationship are required."
        try:
            props = _json.loads(params.get("properties", "{}"))
        except _json.JSONDecodeError:
            props = {}
        try:
            edge = kg.add_edge(source, target, rel, props)
            return f"Edge: {edge.source_id} --[{edge.relationship}]--> {edge.target_id}"
        except ValueError as e:
            return f"Error: {e}"

    if action == "delete_edge":
        source = params.get("source_id", "")
        target = params.get("target_id", "")
        rel = params.get("relationship", "")
        if not source or not target or not rel:
            return "Error: source_id, target_id, and relationship are required."
        ok = kg.delete_edge(source, target, rel)
        return "Edge deleted." if ok else "Edge not found."

    if action == "neighbors":
        node_id = params.get("node_id", "")
        if not node_id:
            return "Error: 'node_id' is required."
        neighbors = kg.get_neighbors(node_id)
        if not neighbors:
            return f"No connections found for '{node_id}'."
        lines = []
        for n in neighbors:
            node = n["node"]
            direction = "→" if n["direction"] == "outgoing" else "←"
            lines.append(f"  {direction} [{node.id}] {node.name} ({n['relationship']})")
        return f"Connections for '{node_id}':\n" + "\n".join(lines)

    if action == "find_path":
        source = params.get("source_id", "")
        target = params.get("target_id", "")
        if not source or not target:
            return "Error: source_id and target_id are required."
        path = kg.find_path(source, target)
        if path is None:
            return f"No path found between '{source}' and '{target}'."
        # Resolve node names for readability
        path_names = []
        for nid in path:
            node = kg.get_node(nid)
            path_names.append(f"{node.name}" if node else nid)
        return f"Path ({len(path)} hops): {' → '.join(path_names)}"

    if action == "search":
        query = params.get("query", "")
        if not query:
            return "Error: 'query' is required for search."
        limit = min(int(params.get("limit", 10)), 20)
        nodes = kg.search_nodes(query, limit=limit)
        if not nodes:
            return f"No nodes matching '{query}'."
        lines = [f"  [{n.id}] {n.name} ({n.type})" for n in nodes]
        return f"Found {len(nodes)} nodes:\n" + "\n".join(lines)

    if action == "list":
        node_type = params.get("node_type")
        limit = min(int(params.get("limit", 20)), 50)
        nodes = kg.list_nodes(node_type=node_type, limit=limit)
        if not nodes:
            return "Knowledge graph is empty." if not node_type else f"No nodes of type '{node_type}'."
        lines = [f"  [{n.id}] {n.name} ({n.type})" for n in nodes]
        return f"Nodes ({len(nodes)}):\n" + "\n".join(lines)

    # Default: stats
    s = kg.stats()
    return f"Knowledge Graph: {s['nodes']} nodes, {s['edges']} edges"


@tool(
    name="parallel_orchestrate",
    description=(
        "Runs multiple independent sub-tasks in parallel. "
        "Use when a goal can be decomposed into 2+ independent pieces "
        "that don't depend on each other's results. "
        "Examples: 'research X AND Y simultaneously', "
        "'check weather in 3 cities at once'."
    ),
    parameters={
        "type": "OBJECT",
        "properties": {
            "tasks": {
                "type": "ARRAY",
                "items": {
                    "type": "OBJECT",
                    "properties": {
                        "id": {"type": "STRING", "description": "Unique short task ID"},
                        "description": {"type": "STRING", "description": "What this sub-task should accomplish"},
                    },
                    "required": ["id", "description"],
                },
                "description": "List of independent sub-tasks to run concurrently",
            },
        },
        "required": ["tasks"],
    },
    safety=SafetyTier.NOTIFY,
    timeout=300,
)
def _parallel_orchestrate(parameters=None, speak=None, **kw):
    import threading as _threading
    from os_layer.parallel_engine import ParallelAgentEngine, SubTask

    params = parameters or {}
    raw_tasks = params.get("tasks", [])

    if not raw_tasks:
        return "Error: provide at least one task."
    if len(raw_tasks) > 5:
        return "Error: maximum 5 parallel sub-tasks allowed."

    tasks = [
        SubTask(id=t.get("id", f"t{i}"), description=t.get("description", ""))
        for i, t in enumerate(raw_tasks)
    ]

    # Build the executor function that creates a fresh, isolated ReactAgent per task
    def _executor_fn(task: SubTask, cancel_event: _threading.Event) -> str:
        from agent.react_agent import ReactAgent
        agent = ReactAgent(
            max_steps=10,
            speak=speak,
            cancel_flag=cancel_event,
        )
        return agent.run(goal=task.description)

    # Create engine with a fresh cancel event for this orchestration
    cancel_event = _threading.Event()
    engine = ParallelAgentEngine(cancel_event=cancel_event)

    if speak:
        speak(f"Launching {len(tasks)} parallel sub-tasks, sir.")

    result = engine.execute_concurrently(tasks, executor_fn=_executor_fn)

    # Format results
    lines = [f"Parallel execution complete: {result.success_count}/{len(tasks)} succeeded ({result.total_duration_ms}ms)"]
    if result.cancelled:
        lines.append("⚠️ Execution was cancelled.")
    for r in result.results:
        status = "✅" if r.success else "❌"
        content = r.result[:200] if r.success else r.error[:200]
        lines.append(f"\n{status} [{r.task_id}] ({r.duration_ms}ms):\n{content}")

    return "\n".join(lines)


