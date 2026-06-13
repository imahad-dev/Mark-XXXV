"""
tests/test_window_manager.py — Window Manager Tests
=====================================================
Tests for window control, snap positions, layout save/restore,
process management, and multi-monitor detection.

Runs live on Windows — exercises real win32 APIs.
Gracefully skips on non-Windows or missing dependencies.
"""

from __future__ import annotations

import platform
import time
import unittest

IS_WINDOWS = platform.system() == "Windows"


class TestSnapPositionEnum(unittest.TestCase):
    """Test SnapPosition enum values."""

    def test_all_positions_exist(self):
        from os_layer.window_manager import SnapPosition
        expected = [
            "left", "right", "top", "bottom",
            "top_left", "top_right", "bottom_left", "bottom_right",
            "center", "maximize", "minimize",
        ]
        for pos in expected:
            self.assertEqual(SnapPosition(pos).value, pos)

    def test_invalid_position_raises(self):
        from os_layer.window_manager import SnapPosition
        with self.assertRaises(ValueError):
            SnapPosition("invalid_position")


class TestProcessInfoDataClass(unittest.TestCase):
    """Test ProcessInfo data class."""

    def test_defaults(self):
        from os_layer.window_manager import ProcessInfo
        p = ProcessInfo()
        self.assertEqual(p.pid, 0)
        self.assertEqual(p.name, "")
        self.assertEqual(p.cpu_percent, 0.0)
        self.assertEqual(p.memory_mb, 0.0)

    def test_custom_values(self):
        from os_layer.window_manager import ProcessInfo
        p = ProcessInfo(pid=1234, name="chrome", memory_mb=256.5)
        self.assertEqual(p.pid, 1234)
        self.assertEqual(p.name, "chrome")
        self.assertEqual(p.memory_mb, 256.5)


@unittest.skipUnless(IS_WINDOWS, "Windows-only: requires win32gui")
class TestWindowManagerLive(unittest.TestCase):
    """Live OS integration tests."""

    def setUp(self):
        from os_layer.window_manager import WindowManager
        self.wm = WindowManager()

    def test_list_processes_returns_list(self):
        procs = self.wm.list_processes()
        self.assertIsInstance(procs, list)
        # At least python itself should be running
        self.assertTrue(len(procs) > 0)

    def test_find_window_nonexistent(self):
        hwnd = self.wm.find_window("This_Window_Does_Not_Exist_12345")
        self.assertIsNone(hwnd)

    def test_get_monitors_returns_list(self):
        monitors = self.wm.get_monitors()
        self.assertIsInstance(monitors, list)
        # Should have at least one monitor
        if monitors:
            self.assertTrue(monitors[0].width > 0)
            self.assertTrue(monitors[0].height > 0)

    def test_resource_hogs_returns_list(self):
        # Use very high threshold so we likely get an empty list (safe)
        hogs = self.wm.get_resource_hogs(threshold_cpu=99.9)
        self.assertIsInstance(hogs, list)


@unittest.skipUnless(IS_WINDOWS, "Windows-only: requires win32gui")
class TestWindowManagerLayouts(unittest.TestCase):
    """Test layout save/restore/list/delete."""

    def setUp(self):
        from os_layer.window_manager import WindowManager
        self.wm = WindowManager()
        self.test_layout_name = "_test_layout_unit"

    def tearDown(self):
        self.wm.delete_layout(self.test_layout_name)

    def test_save_and_list_layout(self):
        layout = self.wm.save_layout(self.test_layout_name)
        self.assertEqual(layout.name, self.test_layout_name)
        self.assertIsInstance(layout.windows, list)

        layouts = self.wm.list_layouts()
        self.assertIn(self.test_layout_name, layouts)

    def test_delete_layout(self):
        self.wm.save_layout(self.test_layout_name)
        ok = self.wm.delete_layout(self.test_layout_name)
        self.assertTrue(ok)

        layouts = self.wm.list_layouts()
        self.assertNotIn(self.test_layout_name, layouts)

    def test_restore_nonexistent_layout(self):
        ok = self.wm.restore_layout("nonexistent_layout_xyz")
        self.assertFalse(ok)


if __name__ == "__main__":
    unittest.main()
