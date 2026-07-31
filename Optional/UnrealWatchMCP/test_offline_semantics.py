#!/usr/bin/env python3
"""Offline / status semantics tests for UnrealWatchMCP (no editor required)."""

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


class OfflineSemanticsTests(unittest.TestCase):
    def test_editor_offline_instruction(self) -> None:
        with mock.patch.object(watch, "find_unreal_pids", return_value=[]), mock.patch.object(
            watch, "find_dialogs", return_value=[]
        ), mock.patch.object(
            watch,
            "_http_probe",
            return_value={"listening": False, "responded": False, "error": "not_listening"},
        ), mock.patch.object(
            watch,
            "ensure_http_proxy_sidecar",
            return_value={"ok": False, "error": "skipped"},
        ), mock.patch.object(
            watch,
            "probe_proxy_health",
            return_value={"ok": False, "listening": False},
        ), mock.patch.object(watch, "project_alert_path", return_value=None):
            report = watch.check_unreal()

        self.assertFalse(report["unreal_running"])
        self.assertEqual(report["status"], "editor_offline")
        self.assertTrue(report["likely_blocked"])
        self.assertTrue(report["abort_unreal_mcp"])
        instr = report["agent_instruction"]
        self.assertIn("STOP", instr)
        self.assertIn("not running", instr.lower())
        self.assertNotIn("watch clear", instr.lower())
        self.assertNotIn("may be used", instr.lower())

    def test_ok_instruction_requires_responsive(self) -> None:
        procs = [{"pid": 1, "process": "UnrealEditor", "main_title": "Test", "has_window": True}]
        with mock.patch.object(watch, "find_unreal_pids", return_value=procs), mock.patch.object(
            watch, "find_dialogs", return_value=[]
        ), mock.patch.object(
            watch,
            "_http_probe",
            side_effect=[
                {"listening": True, "responded": True, "preview": "HTTP/1.1 405"},
                {"listening": False, "responded": False, "error": "not_listening"},
            ],
        ), mock.patch.object(
            watch,
            "ensure_http_proxy_sidecar",
            return_value={"ok": True, "already_up": True},
        ), mock.patch.object(
            watch,
            "probe_proxy_health",
            return_value={"ok": True, "listening": True},
        ), mock.patch.object(watch, "project_alert_path", return_value=None):
            report = watch.check_unreal()

        self.assertEqual(report["status"], "ok")
        self.assertFalse(report["abort_unreal_mcp"])
        self.assertIn("watch clear", report["agent_instruction"].lower())

    def test_get_editor_status_keys(self) -> None:
        with mock.patch.object(watch, "find_unreal_pids", return_value=[]), mock.patch.object(
            watch, "find_dialogs", return_value=[]
        ), mock.patch.object(
            watch,
            "_http_probe",
            return_value={"listening": False, "responded": False, "error": "not_listening"},
        ), mock.patch.object(
            watch,
            "ensure_http_proxy_sidecar",
            return_value={"ok": False},
        ), mock.patch.object(
            watch,
            "probe_proxy_health",
            return_value={"ok": False},
        ), mock.patch.object(watch, "project_alert_path", return_value=None):
            status = watch.get_editor_status()

        self.assertEqual(status["status"], "editor_offline")
        self.assertIn("agent_instruction", status)
        # Round-trip JSON for agents
        json.dumps(status)


if __name__ == "__main__":
    unittest.main()
