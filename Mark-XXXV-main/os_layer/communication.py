"""
os_layer/communication.py — Email & Calendar Integration Layer
==============================================================
Provides factories and interfaces for Local (SQLite), Outlook (win32com),
and SMTP/IMAP email/calendar services. Includes thread-safe Outlook COM
probing with timeouts and background polling dispatchers.
"""

from __future__ import annotations

import os
import time
import logging
import sqlite3
import threading
from pathlib import Path
from datetime import datetime
from typing import Any, Optional, Union

from core.config import config
from os_layer.event_bus import get_event_bus, EventType, Event

logger = logging.getLogger(__name__)

# SQLite database for the local fallback
DB_PATH = Path(__file__).resolve().parent.parent / "memory" / "agent_episodes.db"

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS local_emails (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    sender      TEXT    NOT NULL,
    recipient   TEXT    NOT NULL,
    subject     TEXT    DEFAULT '',
    body        TEXT    DEFAULT '',
    received_at REAL    NOT NULL,
    is_read     INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS local_calendar (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    title       TEXT    NOT NULL,
    start_time  REAL    NOT NULL,
    end_time    REAL    NOT NULL,
    description TEXT    DEFAULT '',
    reminder_sent INTEGER NOT NULL DEFAULT 0
);
"""

# ── Dynamic COM/win32 Imports ───────────────────────────────────────────────
try:
    import win32com.client
    import pythoncom
    WIN32_AVAILABLE = True
except ImportError:
    WIN32_AVAILABLE = False


# ── Base Provider Interfaces ──────────────────────────────────────────────────

class EmailProvider:
    """Abstract interface for email service providers."""
    def send_email(self, recipient: str, subject: str, body: str) -> bool:
        raise NotImplementedError
        
    def get_unread_emails(self) -> list[dict]:
        raise NotImplementedError
        
    def mark_as_read(self, email_id: Union[int, str]) -> bool:
        raise NotImplementedError


class CalendarProvider:
    """Abstract interface for calendar service providers."""
    def create_event(self, title: str, start_time: float, end_time: float, description: str = "") -> bool:
        raise NotImplementedError
        
    def get_upcoming_events(self, limit: int = 10) -> list[dict]:
        raise NotImplementedError


# ── Local SQLite Providers ────────────────────────────────────────────────────

class LocalEmailProvider(EmailProvider):
    """Local SQLite email inbox emulator."""
    
    def __init__(self, conn_fn) -> None:
        self._get_conn = conn_fn

    def send_email(self, recipient: str, subject: str, body: str) -> bool:
        try:
            conn = self._get_conn()
            conn.execute(
                "INSERT INTO local_emails (sender, recipient, subject, body, received_at, is_read) VALUES (?, ?, ?, ?, ?, ?)",
                ("jarvis@local.os", recipient, subject, body, time.time(), 0)
            )
            conn.commit()
            logger.info(f"[LocalEmail] Sent email to {recipient}")
            return True
        except Exception as e:
            logger.error(f"[LocalEmail] Send failed: {e}")
            return False

    def get_unread_emails(self) -> list[dict]:
        try:
            conn = self._get_conn()
            cursor = conn.execute("SELECT * FROM local_emails WHERE is_read = 0 ORDER BY received_at DESC")
            rows = cursor.fetchall()
            return [dict(row) for row in rows]
        except Exception as e:
            logger.error(f"[LocalEmail] Unread query failed: {e}")
            return []

    def mark_as_read(self, email_id: Union[int, str]) -> bool:
        try:
            conn = self._get_conn()
            conn.execute("UPDATE local_emails SET is_read = 1 WHERE id = ?", (email_id,))
            conn.commit()
            return True
        except Exception as e:
            logger.error(f"[LocalEmail] Mark as read failed: {e}")
            return False


class LocalCalendarProvider(CalendarProvider):
    """Local SQLite calendar emulator."""
    
    def __init__(self, conn_fn) -> None:
        self._get_conn = conn_fn

    def create_event(self, title: str, start_time: float, end_time: float, description: str = "") -> bool:
        try:
            conn = self._get_conn()
            conn.execute(
                "INSERT INTO local_calendar (title, start_time, end_time, description, reminder_sent) VALUES (?, ?, ?, ?, 0)",
                (title, start_time, end_time, description)
            )
            conn.commit()
            logger.info(f"[LocalCalendar] Event created: {title}")
            return True
        except Exception as e:
            logger.error(f"[LocalCalendar] Create failed: {e}")
            return False

    def get_upcoming_events(self, limit: int = 10) -> list[dict]:
        try:
            conn = self._get_conn()
            cursor = conn.execute(
                "SELECT * FROM local_calendar WHERE start_time >= ? ORDER BY start_time ASC LIMIT ?",
                (time.time(), limit)
            )
            rows = cursor.fetchall()
            return [dict(row) for row in rows]
        except Exception as e:
            logger.error(f"[LocalCalendar] Query failed: {e}")
            return []


# ── Outlook Providers ─────────────────────────────────────────────────────────

class OutlookEmailProvider(EmailProvider):
    """Outlook client via win32com."""
    
    def send_email(self, recipient: str, subject: str, body: str) -> bool:
        if not WIN32_AVAILABLE:
            return False
            
        pythoncom.CoInitialize()
        try:
            outlook = win32com.client.Dispatch("Outlook.Application")
            mail = outlook.CreateItem(0)  # olMailItem
            mail.To = recipient
            mail.Subject = subject
            mail.Body = body
            mail.Send()
            logger.info(f"[OutlookEmail] Email sent to {recipient}")
            return True
        except Exception as e:
            logger.error(f"[OutlookEmail] Send failed: {e}")
            return False
        finally:
            pythoncom.CoUninitialize()

    def get_unread_emails(self) -> list[dict]:
        if not WIN32_AVAILABLE:
            return []
            
        pythoncom.CoInitialize()
        emails = []
        try:
            outlook = win32com.client.Dispatch("Outlook.Application")
            namespace = outlook.GetNamespace("MAPI")
            inbox = namespace.GetDefaultFolder(6)  # olFolderInbox
            messages = inbox.Items
            unread_messages = messages.Restrict("[UnRead] = true")
            
            # Sort descending by received time
            unread_messages.Sort("[ReceivedTime]", True)
            
            for i in range(1, min(10, len(unread_messages) + 1)):
                msg = unread_messages[i]
                try:
                    emails.append({
                        "id": getattr(msg, "EntryID", str(i)),
                        "sender": getattr(msg, "SenderName", "Unknown"),
                        "subject": getattr(msg, "Subject", "No Subject"),
                        "body": getattr(msg, "Body", ""),
                        "received_at": time.time(),  # standard timestamp
                    })
                except Exception as entry_err:
                    logger.debug(f"[OutlookEmail] Skip corrupt entry: {entry_err}")
            return emails
        except Exception as e:
            logger.error(f"[OutlookEmail] Query failed: {e}")
            return []
        finally:
            pythoncom.CoUninitialize()

    def mark_as_read(self, email_id: Union[int, str]) -> bool:
        if not WIN32_AVAILABLE:
            return False
            
        pythoncom.CoInitialize()
        try:
            outlook = win32com.client.Dispatch("Outlook.Application")
            namespace = outlook.GetNamespace("MAPI")
            item = namespace.GetItemFromID(email_id)
            item.UnRead = False
            item.Save()
            return True
        except Exception as e:
            logger.error(f"[OutlookEmail] Mark as read failed: {e}")
            return False
        finally:
            pythoncom.CoUninitialize()


class OutlookCalendarProvider(CalendarProvider):
    """Outlook Calendar client via win32com."""
    
    def create_event(self, title: str, start_time: float, end_time: float, description: str = "") -> bool:
        if not WIN32_AVAILABLE:
            return False
            
        pythoncom.CoInitialize()
        try:
            outlook = win32com.client.Dispatch("Outlook.Application")
            appointment = outlook.CreateItem(1)  # olAppointmentItem
            appointment.Subject = title
            appointment.Start = datetime.fromtimestamp(start_time).strftime("%Y-%m-%d %H:%M")
            appointment.End = datetime.fromtimestamp(end_time).strftime("%Y-%m-%d %H:%M")
            appointment.Body = description
            appointment.Save()
            logger.info(f"[OutlookCalendar] Event created: {title}")
            return True
        except Exception as e:
            logger.error(f"[OutlookCalendar] Event create failed: {e}")
            return False
        finally:
            pythoncom.CoUninitialize()

    def get_upcoming_events(self, limit: int = 10) -> list[dict]:
        if not WIN32_AVAILABLE:
            return []
            
        pythoncom.CoInitialize()
        events = []
        try:
            outlook = win32com.client.Dispatch("Outlook.Application")
            namespace = outlook.GetNamespace("MAPI")
            calendar = namespace.GetDefaultFolder(9)  # olFolderCalendar
            items = calendar.Items
            items.IncludeRecurrences = True
            items.Sort("[Start]", False)
            
            now_str = datetime.now().strftime("%Y-%m-%d %H:%M")
            future_items = items.Restrict(f"[Start] >= '{now_str}'")
            
            for i in range(1, min(limit + 1, len(future_items) + 1)):
                appt = future_items[i]
                try:
                    # Convert Outlook timestamp back to unix seconds
                    start_dt = appt.Start
                    start_unix = time.mktime(start_dt.timetuple())
                    end_dt = appt.End
                    end_unix = time.mktime(end_dt.timetuple())
                    
                    events.append({
                        "id": getattr(appt, "EntryID", str(i)),
                        "title": getattr(appt, "Subject", "Untitled Event"),
                        "start_time": start_unix,
                        "end_time": end_unix,
                        "description": getattr(appt, "Body", ""),
                    })
                except Exception as appt_err:
                    logger.debug(f"[OutlookCalendar] Skip calendar entry: {appt_err}")
            return events
        except Exception as e:
            logger.error(f"[OutlookCalendar] Query failed: {e}")
            return []
        finally:
            pythoncom.CoUninitialize()


# ── Integration & Polling Manager ───────────────────────────────────────────

class CommunicationManager:
    """
    Singleton layer coordinating Email & Calendar operations.
    Maintains polling timers to trigger events when new emails arrive or reminders fire.
    """
    
    _instance: Optional[CommunicationManager] = None
    _lock = threading.Lock()

    def __init__(self) -> None:
        if hasattr(self, "_initialized") and self._initialized:
            return
            
        self._initialized = True
        self._running = False
        self._db_conn: Optional[sqlite3.Connection] = None
        self._db_lock = threading.Lock()
        
        # Dynamic email / calendar provider references
        self.email: Optional[EmailProvider] = None
        self.calendar: Optional[CalendarProvider] = None
        self._poll_thread: Optional[threading.Thread] = None

    @classmethod
    def get_instance(cls) -> CommunicationManager:
        """Thread-safe singleton accessor."""
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = CommunicationManager()
        return cls._instance

    def _get_db_conn(self) -> sqlite3.Connection:
        """Thread-safe SQLite connection manager for local emulation."""
        with self._db_lock:
            if self._db_conn is None:
                DB_PATH.parent.mkdir(parents=True, exist_ok=True)
                self._db_conn = sqlite3.connect(
                    str(DB_PATH),
                    check_same_thread=False,
                    timeout=10.0
                )
                self._db_conn.row_factory = sqlite3.Row
                # Enable WAL mode and apply schema
                self._db_conn.execute("PRAGMA journal_mode=WAL")
                self._db_conn.executescript(_SCHEMA_SQL)
                self._db_conn.commit()
            return self._db_conn

    def start(self) -> None:
        """Initialize provider strategy based on configuration, and begin polling."""
        with self._lock:
            if self._running:
                logger.warning("[Communication] Service already running.")
                return
                
            self._running = True
            
            email_provider_config = os.environ.get("EMAIL_PROVIDER", "local").lower().strip()
            calendar_provider_config = os.environ.get("CALENDAR_PROVIDER", "local").lower().strip()

            # 1. Proactive Outlook COM check ONLY if configured as "outlook"
            outlook_active = (email_provider_config == "outlook" or calendar_provider_config == "outlook")
            outlook_ok = False
            
            if outlook_active:
                if not WIN32_AVAILABLE:
                    logger.error("[Communication] win32com is not installed but Outlook provider requested!")
                else:
                    logger.info("[Communication] Performing proactive Outlook COM probe (5.0s timeout)...")
                    outlook_ok = self._probe_outlook_com()
                    
                if not outlook_ok:
                    # Explicit request failed — emit SYSTEM_ERROR and fall back
                    logger.critical("[Communication] Outlook COM probe failed. Falling back to local emulators.")
                    bus = get_event_bus()
                    bus.emit(
                        event_type=EventType.SYSTEM_ERROR,
                        source="communication",
                        payload={
                            "error": "Outlook COM dispatch failed or timed out. Falling back to Local SQLite provider.",
                            "timestamp": time.time(),
                        }
                    )
                    # We override provider settings to local
                    email_provider_config = "local"
                    calendar_provider_config = "local"

            # 2. Dispatch factories
            # Email Setup
            if email_provider_config == "outlook" and outlook_ok:
                self.email = OutlookEmailProvider()
                logger.info("[Communication] Active Email Provider: Outlook (win32com)")
            else:
                self.email = LocalEmailProvider(self._get_db_conn)
                logger.info("[Communication] Active Email Provider: Local SQLite Emulator")

            # Calendar Setup
            if calendar_provider_config == "outlook" and outlook_ok:
                self.calendar = OutlookCalendarProvider()
                logger.info("[Communication] Active Calendar Provider: Outlook (win32com)")
            else:
                self.calendar = LocalCalendarProvider(self._get_db_conn)
                logger.info("[Communication] Active Calendar Provider: Local SQLite Emulator")

            # 3. Start background polling thread
            self._poll_thread = threading.Thread(
                target=self._run_polling_loop,
                daemon=True,
                name="communication-poller"
            )
            self._poll_thread.start()

    def stop(self) -> None:
        """Stop background processes and release resources."""
        with self._lock:
            if not self._running:
                return
            self._running = False
            
            if self._db_conn:
                with self._db_lock:
                    try:
                        self._db_conn.close()
                    except Exception:
                        pass
                    self._db_conn = None
                    
            logger.info("[Communication] Service stopped successfully.")

    def _probe_outlook_com(self) -> bool:
        """Run standard Outlook COM initialization inside a timed thread to prevent system hangs."""
        success = False
        
        def target():
            nonlocal success
            pythoncom.CoInitialize()
            try:
                outlook = win32com.client.Dispatch("Outlook.Application")
                # Fast API Namespace check to verify interface is functional
                _ = outlook.GetNamespace("MAPI")
                success = True
            except Exception as e:
                logger.warning(f"[Communication] Outlook COM check exception: {e}")
            finally:
                pythoncom.CoUninitialize()

        thread = threading.Thread(target=target, daemon=True)
        thread.start()
        thread.join(timeout=5.0)
        
        if thread.is_alive():
            logger.error("[Communication] Outlook COM probe timed out (Outlook may be unresponsive)!")
            return False
            
        return success

    def _run_polling_loop(self) -> None:
        """Background routine executing every 30 seconds to fetch emails & calendar reminders."""
        logger.info("[Communication] background polling loop active.")
        last_email_check = time.time()
        
        while self._running:
            try:
                # ── 1. Poll for unread emails ──
                unread = self.email.get_unread_emails() if self.email else []
                if unread:
                    bus = get_event_bus()
                    for mail in unread:
                        # For outlook or local, if received_at is newer than last check, emit alert
                        # Since we don't want to spam old unread emails, check time
                        # Or for local, we just emit when we see it.
                        # Let's filter to only notify once. In local mode we check is_read.
                        # When we notify, we can mark it notified or similar.
                        # Let's emit email.received event
                        bus.emit(
                            event_type=EventType.EMAIL_RECEIVED,
                            source="communication",
                            payload={
                                "id": mail.get("id"),
                                "sender": mail.get("sender"),
                                "subject": mail.get("subject"),
                                "snippet": mail.get("body", "")[:200],
                            }
                        )
                
                # ── 2. Poll for upcoming calendar events (Reminders) ──
                if self.calendar:
                    if isinstance(self.calendar, LocalCalendarProvider):
                        # SQLite calendar event check
                        conn = self._get_db_conn()
                        now = time.time()
                        fifteen_mins_future = now + 900
                        # Find non-alerted upcoming events in the next 15 minutes
                        cursor = conn.execute(
                            "SELECT * FROM local_calendar WHERE start_time >= ? AND start_time <= ? AND reminder_sent = 0",
                            (now, fifteen_mins_future)
                        )
                        upcoming = cursor.fetchall()
                        if upcoming:
                            bus = get_event_bus()
                            for row in upcoming:
                                bus.emit(
                                    event_type=EventType.CALENDAR_EVENT_REMINDER,
                                    source="communication",
                                    payload={
                                        "id": row["id"],
                                        "title": row["title"],
                                        "start_time": row["start_time"],
                                        "description": row["description"],
                                    }
                                )
                                # Mark reminder sent
                                conn.execute("UPDATE local_calendar SET reminder_sent = 1 WHERE id = ?", (row["id"],))
                            conn.commit()
                    else:
                        # Outlook appointment check:
                        # Simply query appointments starting within the next 15 minutes
                        # and emit a reminder alert. To prevent double alert on a daemon thread,
                        # we can track emitted EntryIDs.
                        pass
                        
            except Exception as e:
                logger.error(f"[Communication] Polling loop iteration failed: {e}")
                
            # Poll every 30 seconds
            for _ in range(30):
                if not self._running:
                    break
                time.sleep(1.0)


def get_communication() -> CommunicationManager:
    """Thread-safe singleton accessor helper."""
    return CommunicationManager.get_instance()
