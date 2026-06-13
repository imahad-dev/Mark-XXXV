"""
tests/test_communication.py — Automated Unit Tests for Communication Layer
===========================================================================
"""

from __future__ import annotations

import os
import time
import sqlite3
import unittest
from unittest.mock import MagicMock, patch
from pathlib import Path

from os_layer.communication import (
    get_communication,
    CommunicationManager,
    LocalEmailProvider,
    LocalCalendarProvider,
    OutlookEmailProvider,
    OutlookCalendarProvider
)

class TestCommunicationLayer(unittest.TestCase):
    """Test suite for Email, Calendar, factories, and the Outlook COM probe."""

    def setUp(self) -> None:
        self.manager = get_communication()
        # Initialize SQLite emulated database connection for test scope
        self.db_path = Path(__file__).resolve().parent.parent / "memory" / "agent_episodes.db"

    def test_singleton_accessor(self) -> None:
        """Verify the service implements a strict thread-safe singleton pattern."""
        instance2 = get_communication()
        self.assertIs(self.manager, instance2)

    def test_local_email_provider(self) -> None:
        """Verify the Local SQLite email emulator registers, queries, and marks reads correctly."""
        # Use manager's db connection helper
        provider = LocalEmailProvider(self.manager._get_db_conn)
        
        # 1. Send local mock email
        ok = provider.send_email(recipient="tony@stark.com", subject="Armor upgrade", body="Mark 85 is ready.")
        self.assertTrue(ok)
        
        # 2. Query unread emails
        unread = provider.get_unread_emails()
        self.assertGreater(len(unread), 0)
        
        target_mail = [m for m in unread if m.get("recipient") == "tony@stark.com"][0]
        self.assertEqual(target_mail["subject"], "Armor upgrade")
        self.assertEqual(target_mail["is_read"], 0)
        
        # 3. Mark as read
        mark_ok = provider.mark_as_read(target_mail["id"])
        self.assertTrue(mark_ok)
        
        # 4. Verify unread excludes marked entries
        unread_after = provider.get_unread_emails()
        self.assertNotIn(target_mail["id"], [m["id"] for m in unread_after])

    def test_local_calendar_provider(self) -> None:
        """Verify Local SQLite calendar emulator can create events and query upcoming appointments."""
        provider = LocalCalendarProvider(self.manager._get_db_conn)
        
        title = "Stark Expo Meeting"
        now = time.time()
        start = now + 3600  # 1 hour from now
        end = now + 7200    # 2 hours from now
        
        # 1. Create event
        ok = provider.create_event(title=title, start_time=start, end_time=end, description="Discuss clean energy.")
        self.assertTrue(ok)
        
        # 2. Query upcoming
        upcoming = provider.get_upcoming_events(limit=5)
        self.assertGreater(len(upcoming), 0)
        
        target_event = [e for e in upcoming if e.get("title") == title][0]
        self.assertEqual(target_event["description"], "Discuss clean energy.")
        self.assertEqual(target_event["reminder_sent"], 0)

    @patch("os_layer.communication.WIN32_AVAILABLE", False)
    def test_outlook_factories_fail_gracefully_when_win32_missing(self) -> None:
        """Verify Outlook providers fail gracefully when win32com is absent from OS."""
        email_prov = OutlookEmailProvider()
        calendar_prov = OutlookCalendarProvider()
        
        self.assertFalse(email_prov.send_email("a@b.com", "Sub", "Body"))
        self.assertEqual(email_prov.get_unread_emails(), [])
        self.assertFalse(calendar_prov.create_event("E", time.time(), time.time()))

    @patch("os_layer.communication.CommunicationManager._probe_outlook_com")
    def test_factory_dispatches_probe_only_on_outlook_request(self, mock_probe) -> None:
        """Verify factory does NOT probe COM unless Outlook provider is explicitly requested."""
        mock_probe.return_value = True
        
        with patch.dict(os.environ, {"EMAIL_PROVIDER": "local", "CALENDAR_PROVIDER": "local"}):
            self.manager.start()
            self.assertFalse(mock_probe.called)
            self.manager.stop()
            
        mock_probe.reset_mock()
        
        with patch.dict(os.environ, {"EMAIL_PROVIDER": "outlook", "CALENDAR_PROVIDER": "outlook"}):
            with patch("os_layer.communication.WIN32_AVAILABLE", True):
                self.manager.start()
                self.assertTrue(mock_probe.called)
                self.manager.stop()

    def test_probe_outlook_com_timeout_protection(self) -> None:
        """Verify COM probe times out after 5.0 seconds and does not block background initialization."""
        def slow_dispatch(*args, **kw):
            time.sleep(10.0)  # Sleep longer than the 5s join timeout
            
        with patch("win32com.client.Dispatch", side_effect=slow_dispatch):
            with patch("os_layer.communication.WIN32_AVAILABLE", True):
                # Run probe and verify it returns False (fails gracefully due to timeout protection)
                start_time = time.time()
                res = self.manager._probe_outlook_com()
                end_time = time.time()
                
                self.assertFalse(res)
                # The elapsed time should be bounded closely to 5.0 seconds (not 10.0 seconds)
                self.assertLess(end_time - start_time, 7.0)

if __name__ == "__main__":
    unittest.main()
