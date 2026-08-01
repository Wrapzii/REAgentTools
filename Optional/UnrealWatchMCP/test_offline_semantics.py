#!/usr/bin/env python3
"""Offline / modal / crash status semantics tests for UnrealWatchMCP (no editor required)."""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import watch  # noqa: E402


def _probe_down() -> dict:
    return {"listening": False, "responded": False, "error": "not_listening"}


def _probe_up() -> dict:
    return {"listening": True, "responded": True, "preview": "HTTP/1.1 405"}


def _patch_base(**overrides):
    """Common check_unreal dependency patches."""
    patches = {
        "find_unreal_pids": mock.patch.object(watch, "find_unreal_pids", return_value=[]),
        "find_crash_reporter_pids": mock.patch.object(
            watch, "find_crash_reporter_pids", return_value=[]
        ),
        "find_dialogs": mock.patch.object(watch, "find_dialogs", return_value=[]),
        "_http_probe": mock.patch.object(watch, "_http_probe", return_value=_probe_down()),
        "ensure_http_proxy_sidecar": mock.patch.object(
            watch, "ensure_http_proxy_sidecar", return_value={"ok": False, "error": "skipped"}
        ),
        "probe_proxy_health": mock.patch.object(
            watch, "probe_proxy_health", return_value={"ok": False, "listening": False}
        ),
        "project_alert_path": mock.patch.object(watch, "project_alert_path", return_value=None),
    }
    patches.update(overrides)
    return patches


class OfflineSemanticsTests(unittest.TestCase):
    def test_editor_offline_instruction(self) -> None:
        with mock.patch.object(watch, "find_unreal_pids", return_value=[]), mock.patch.object(
            watch, "find_crash_reporter_pids", return_value=[]
        ), mock.patch.object(watch, "find_dialogs", return_value=[]), mock.patch.object(
            watch, "_http_probe", return_value=_probe_down()
        ), mock.patch.object(
            watch, "ensure_http_proxy_sidecar", return_value={"ok": False, "error": "skipped"}
        ), mock.patch.object(
            watch, "probe_proxy_health", return_value={"ok": False, "listening": False}
        ), mock.patch.object(watch, "project_alert_path", return_value=None):
            report = watch.check_unreal()

        self.assertFalse(report["unreal_running"])
        self.assertEqual(report["status"], "editor_offline")
        self.assertTrue(report["likely_blocked"])
        self.assertTrue(report["abort_unreal_mcp"])
        self.assertEqual(report["recover_tool"], "wait_for_editor")
        instr = report["agent_instruction"]
        self.assertIn("STOP", instr)
        self.assertIn("not running", instr.lower())
        self.assertIn("RECOVER", instr)
        self.assertNotIn("watch clear", instr.lower())
        self.assertNotIn("may be used", instr.lower())

    def test_ok_instruction_requires_responsive(self) -> None:
        procs = [
            {
                "pid": 1,
                "process": "UnrealEditor",
                "main_title": "Test",
                "has_window": True,
            }
        ]
        with mock.patch.object(watch, "find_unreal_pids", return_value=procs), mock.patch.object(
            watch, "find_crash_reporter_pids", return_value=[]
        ), mock.patch.object(watch, "find_dialogs", return_value=[]), mock.patch.object(
            watch,
            "_http_probe",
            side_effect=[_probe_up(), _probe_down()],
        ), mock.patch.object(
            watch, "ensure_http_proxy_sidecar", return_value={"ok": True, "already_up": True}
        ), mock.patch.object(
            watch, "probe_proxy_health", return_value={"ok": True, "listening": True}
        ), mock.patch.object(watch, "project_alert_path", return_value=None):
            report = watch.check_unreal()

        self.assertEqual(report["status"], "ok")
        self.assertFalse(report["abort_unreal_mcp"])
        self.assertIn("watch clear", report["agent_instruction"].lower())
        self.assertIn("get_editor_status", report["agent_instruction"])

    def test_modal_blocked_instruction(self) -> None:
        procs = [{"pid": 1, "process": "UnrealEditor", "main_title": "Ed", "has_window": True}]
        dialogs = [
            {
                "hwnd": 42,
                "pid": 1,
                "process": "UnrealEditor",
                "class": "UnrealWindow",
                "title": "Blueprint Compile Error",
                "kind": "slate",
                "blocker_kind": "modal",
                "size": [400, 200],
                "owner_hwnd": 1,
                "buttons": [
                    {"text": "OK", "enabled": True, "source": "uia"},
                    {"text": "OK/Enter (keyboard)", "enabled": True, "source": "keyboard"},
                ],
                "_button_hwnds": {},
                "_slate": True,
            }
        ]
        with mock.patch.object(watch, "find_unreal_pids", return_value=procs), mock.patch.object(
            watch, "find_crash_reporter_pids", return_value=[]
        ), mock.patch.object(watch, "find_dialogs", return_value=dialogs), mock.patch.object(
            watch, "_http_probe", return_value=_probe_down()
        ), mock.patch.object(
            watch, "ensure_http_proxy_sidecar", return_value={"ok": False}
        ), mock.patch.object(
            watch, "probe_proxy_health", return_value={"ok": False}
        ), mock.patch.object(watch, "project_alert_path", return_value=None):
            report = watch.check_unreal()

        self.assertEqual(report["status"], "modal_blocked")
        self.assertTrue(report["modal"]["blocking"])
        self.assertTrue(report["abort_unreal_mcp"])
        self.assertEqual(report["recover_tool"], "dismiss_unreal_blocker")
        instr = report["agent_instruction"]
        self.assertIn("STOP", instr)
        self.assertIn("modal_blocked", instr)
        self.assertIn("dismiss_unreal_blocker", instr)
        self.assertIn("RECOVER", instr)
        self.assertNotIn("watch clear", instr.lower())

    def test_crash_reporter_instruction(self) -> None:
        crc = [{"pid": 99, "process": "CrashReportClient", "main_title": "Crash", "has_window": True}]
        with mock.patch.object(watch, "find_unreal_pids", return_value=[]), mock.patch.object(
            watch, "find_crash_reporter_pids", return_value=crc
        ), mock.patch.object(watch, "find_dialogs", return_value=[]), mock.patch.object(
            watch, "_http_probe", return_value=_probe_down()
        ), mock.patch.object(
            watch, "ensure_http_proxy_sidecar", return_value={"ok": False}
        ), mock.patch.object(
            watch, "probe_proxy_health", return_value={"ok": False}
        ), mock.patch.object(watch, "project_alert_path", return_value=None):
            report = watch.check_unreal()

        self.assertEqual(report["status"], "crash_reporter")
        self.assertTrue(report["abort_unreal_mcp"])
        self.assertEqual(report["recover_tool"], "dismiss_unreal_blocker")
        instr = report["agent_instruction"]
        self.assertIn("STOP", instr)
        self.assertIn("crash_reporter", instr)
        self.assertIn("dismiss_unreal_blocker", instr)
        self.assertNotIn("watch clear", instr.lower())

    def test_restore_packages_instruction(self) -> None:
        procs = [{"pid": 1, "process": "UnrealEditor", "main_title": "Ed", "has_window": True}]
        dialogs = [
            {
                "hwnd": 7,
                "pid": 1,
                "process": "UnrealEditor",
                "class": "#32770",
                "title": "Restore Packages",
                "kind": "win32",
                "blocker_kind": "restore_packages",
                "size": [500, 300],
                "owner_hwnd": 1,
                "buttons": [
                    {"text": "Don't Restore", "enabled": True, "source": "win32"},
                    {"text": "Restore", "enabled": True, "source": "win32"},
                ],
                "_button_hwnds": {"don't restore": 8, "restore": 9},
                "_slate": False,
            }
        ]
        with mock.patch.object(watch, "find_unreal_pids", return_value=procs), mock.patch.object(
            watch, "find_crash_reporter_pids", return_value=[]
        ), mock.patch.object(watch, "find_dialogs", return_value=dialogs), mock.patch.object(
            watch,
            "_http_probe",
            side_effect=[_probe_up(), _probe_down()],
        ), mock.patch.object(
            watch, "ensure_http_proxy_sidecar", return_value={"ok": True}
        ), mock.patch.object(
            watch, "probe_proxy_health", return_value={"ok": True}
        ), mock.patch.object(watch, "project_alert_path", return_value=None):
            report = watch.check_unreal()

        self.assertEqual(report["status"], "restore_packages")
        self.assertTrue(report["modal"]["blocking"])
        self.assertIn("restore_packages", report["modal"]["blocker_kinds"])
        instr = report["agent_instruction"]
        self.assertIn("STOP", instr)
        self.assertIn("restore_packages", instr)
        self.assertIn("dont_restore", instr.lower().replace("'", "").replace("\u2019", ""))
        self.assertIn("allow_destructive", instr)

    def test_import_content_title_and_instruction(self) -> None:
        """Interchange Import Content → import_dialog STOP/RECOVER (always blocking)."""
        self.assertEqual(watch._classify_blocker_kind("Import Content"), "import_dialog")
        self.assertEqual(watch._classify_blocker_kind("FBX Import Options"), "import_dialog")
        self.assertTrue(watch._title_matches("Import Content", watch._IMPORT_TITLE_HINTS))

        procs = [{"pid": 1, "process": "UnrealEditor", "main_title": "Ed", "has_window": True}]
        dialogs = [
            {
                "hwnd": 55,
                "pid": 1,
                "process": "UnrealEditor",
                "class": "UnrealWindow",
                "title": "Import Content",
                "kind": "import_dialog",
                "blocker_kind": "import_dialog",
                "size": [1000, 700],
                "owner_hwnd": 1,
                "buttons": [
                    {"text": "Import", "enabled": True, "source": "uia"},
                    {"text": "Cancel", "enabled": True, "source": "uia"},
                    {"text": "OK/Enter (keyboard)", "enabled": True, "source": "keyboard"},
                ],
                "_button_hwnds": {},
                "_slate": True,
            }
        ]
        with mock.patch.object(watch, "find_unreal_pids", return_value=procs), mock.patch.object(
            watch, "find_crash_reporter_pids", return_value=[]
        ), mock.patch.object(watch, "find_dialogs", return_value=dialogs), mock.patch.object(
            watch,
            "_http_probe",
            side_effect=[_probe_up(), _probe_down()],
        ), mock.patch.object(
            watch, "ensure_http_proxy_sidecar", return_value={"ok": True}
        ), mock.patch.object(
            watch, "probe_proxy_health", return_value={"ok": True}
        ), mock.patch.object(watch, "project_alert_path", return_value=None):
            report = watch.check_unreal()

        self.assertEqual(report["status"], "import_dialog")
        self.assertNotEqual(report["status"], "ok")
        self.assertTrue(report["modal"]["blocking"])
        self.assertTrue(report["abort_unreal_mcp"])
        self.assertIn("import_dialog", report["modal"]["blocker_kinds"])
        self.assertEqual(report["recover_tool"], "dismiss_unreal_blocker")
        instr = report["agent_instruction"]
        self.assertIn("STOP", instr)
        self.assertIn("import_dialog", instr)
        self.assertIn("dismiss_unreal_blocker", instr)
        self.assertIn("Import", instr)
        self.assertIn("RECOVER", instr)
        self.assertNotIn("watch clear", instr.lower())

    def test_dismiss_import_content_clicks_import(self) -> None:
        dialogs = [
            {
                "hwnd": 55,
                "pid": 1,
                "process": "UnrealEditor",
                "class": "UnrealWindow",
                "title": "Import Content",
                "kind": "import_dialog",
                "blocker_kind": "import_dialog",
                "size": [1000, 700],
                "owner_hwnd": 1,
                "buttons": [
                    {"text": "Import", "enabled": True, "source": "uia"},
                    {"text": "Cancel", "enabled": True, "source": "uia"},
                ],
                "_button_hwnds": {},
                "_slate": True,
            }
        ]
        with mock.patch.object(watch, "find_dialogs", return_value=dialogs), mock.patch.object(
            watch, "find_crash_reporter_pids", return_value=[]
        ), mock.patch.object(
            watch,
            "get_editor_status",
            return_value={"status": "ok", "agent_instruction": "clear"},
        ), mock.patch.object(watch, "click_button") as click:
            click.return_value = {"ok": True, "clicked": "Import", "method": "uia_invoke"}
            result = watch.dismiss_unreal_blocker(policy="safe_cancel")
            self.assertTrue(result["ok"])
            self.assertEqual(result["blocker_kind"], "import_dialog")
            click.assert_called_with(dialogs[0], "import")

            click.reset_mock()
            click.return_value = {"ok": True, "clicked": "Cancel", "method": "uia_invoke"}
            cancelled = watch.dismiss_unreal_blocker(policy="cancel")
            self.assertTrue(cancelled["ok"])
            click.assert_called_with(dialogs[0], "cancel")

    def test_dismiss_refuses_delete_without_flag(self) -> None:
        dialogs = [
            {
                "hwnd": 11,
                "pid": 1,
                "process": "UnrealEditor",
                "class": "#32770",
                "title": "Delete Assets",
                "kind": "win32",
                "blocker_kind": "modal",
                "size": [300, 150],
                "owner_hwnd": 1,
                "buttons": [
                    {"text": "Delete", "enabled": True, "source": "win32"},
                    {"text": "Cancel", "enabled": True, "source": "win32"},
                ],
                "_button_hwnds": {"delete": 12, "cancel": 13},
                "_slate": False,
            }
        ]
        with mock.patch.object(watch, "find_dialogs", return_value=dialogs), mock.patch.object(
            watch, "find_crash_reporter_pids", return_value=[]
        ), mock.patch.object(
            watch, "get_editor_status", return_value={"status": "modal_blocked", "agent_instruction": "STOP"}
        ), mock.patch.object(watch, "click_button") as click:
            refused = watch.dismiss_unreal_blocker(policy="accept", allow_destructive=False)
            self.assertFalse(refused["ok"])
            self.assertIn("destructive", refused["error"].lower())
            click.assert_not_called()

            click.return_value = {"ok": True, "clicked": "Delete", "method": "win32_bm_click"}
            allowed = watch.dismiss_unreal_blocker(policy="accept", allow_destructive=True)
            self.assertTrue(allowed["ok"])
            click.assert_called()

    def test_wait_for_editor_returns_on_blocker(self) -> None:
        blocked = {
            "status": "modal_blocked",
            "unreal_running": True,
            "editor_responsive": False,
            "abort_unreal_mcp": True,
            "_dialogs_raw": [],
        }
        with mock.patch.object(watch, "check_unreal", return_value=blocked):
            result = watch.wait_for_editor(timeout_s=5.0, poll_s=0.2, return_on_blocker=True)
        self.assertEqual(result["wait_result"], "blocker:modal_blocked")
        self.assertEqual(result["status"], "modal_blocked")

    def test_orphan_crash_reporter_ignored_when_editor_ok(self) -> None:
        """Headless leftover CrashReportClientEditor must not block a healthy editor."""
        procs = [
            {"pid": 1, "process": "UnrealEditor", "main_title": "Ed", "has_window": True}
        ]
        crc = [
            {
                "pid": 99,
                "process": "CrashReportClientEditor",
                "main_title": "",
                "has_window": False,
                "source": "process_snapshot",
            }
        ]
        with mock.patch.object(watch, "find_unreal_pids", return_value=procs), mock.patch.object(
            watch, "find_crash_reporter_pids", return_value=crc
        ), mock.patch.object(watch, "find_dialogs", return_value=[]), mock.patch.object(
            watch,
            "_http_probe",
            side_effect=[_probe_up(), _probe_down()],
        ), mock.patch.object(
            watch, "ensure_http_proxy_sidecar", return_value={"ok": True, "already_up": True}
        ), mock.patch.object(
            watch, "probe_proxy_health", return_value={"ok": True, "listening": True}
        ), mock.patch.object(watch, "project_alert_path", return_value=None):
            report = watch.check_unreal()

        self.assertEqual(report["status"], "ok")
        self.assertFalse(report["abort_unreal_mcp"])
        self.assertEqual(len(report["crash_reporter_processes"]), 1)

    def test_classify_blocker_kinds(self) -> None:
        self.assertEqual(watch._classify_blocker_kind("Restore Packages"), "restore_packages")
        self.assertEqual(
            watch._classify_blocker_kind("Crash Report", "CrashReportClient"), "crash_reporter"
        )
        self.assertEqual(watch._classify_blocker_kind("Context Menu"), "context_menu")
        self.assertEqual(watch._classify_blocker_kind("Import Content"), "import_dialog")
        self.assertEqual(watch._classify_blocker_kind("Interchange Pipeline"), "import_dialog")

    def test_get_editor_status_keys(self) -> None:
        with mock.patch.object(watch, "find_unreal_pids", return_value=[]), mock.patch.object(
            watch, "find_crash_reporter_pids", return_value=[]
        ), mock.patch.object(watch, "find_dialogs", return_value=[]), mock.patch.object(
            watch, "_http_probe", return_value=_probe_down()
        ), mock.patch.object(watch, "ensure_http_proxy_sidecar", return_value={"ok": False}), mock.patch.object(
            watch, "probe_proxy_health", return_value={"ok": False}
        ), mock.patch.object(watch, "project_alert_path", return_value=None):
            status = watch.get_editor_status()

        self.assertEqual(status["status"], "editor_offline")
        self.assertIn("agent_instruction", status)
        self.assertIn("recover_tool", status)
        json.dumps(status)

    def test_instruction_helper_covers_enum(self) -> None:
        for st in (
            watch.STATUS_OK,
            watch.STATUS_EDITOR_OFFLINE,
            watch.STATUS_MODAL_BLOCKED,
            watch.STATUS_CRASH_REPORTER,
            watch.STATUS_RESTORE_PACKAGES,
            watch.STATUS_IMPORT_DIALOG,
            watch.STATUS_PORTS_WEDGED,
            watch.STATUS_PROXY_UNHEALTHY,
        ):
            text = watch._instruction_for_status(st, titles=["t"])
            self.assertTrue(text)
            if st != watch.STATUS_OK:
                self.assertNotIn("watch clear", text.lower())
            if st == watch.STATUS_IMPORT_DIALOG:
                self.assertIn("dismiss_unreal_blocker", text)
                self.assertIn("Import", text)


if __name__ == "__main__":
    unittest.main()
