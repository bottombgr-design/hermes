"""Focused regressions for the Copilot ACP shim safety layer."""

from __future__ import annotations

import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import agent.copilot_acp_client as acp_client
from agent.copilot_acp_client import (
    CopilotACPClient,
    _build_subprocess_env,
    is_acp_process_runtime,
)


class _FakeProcess:
    def __init__(self) -> None:
        self.stdin = io.StringIO()


class CopilotACPClientSafetyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = CopilotACPClient(acp_cwd="/tmp")

    def test_remote_acp_tcp_runtime_is_not_a_local_process(self) -> None:
        self.assertFalse(is_acp_process_runtime("custom", "acp+tcp://host:1234"))
        self.assertFalse(is_acp_process_runtime("copilot-acp", "acp+tcp://host:1234"))
        self.assertFalse(is_acp_process_runtime("qoder-acp", "acp+tcp://host:1234"))

    def test_local_acp_runtime_still_uses_the_process_client(self) -> None:
        self.assertTrue(is_acp_process_runtime("copilot-acp", None))
        self.assertTrue(is_acp_process_runtime("qoder-acp", None))
        self.assertTrue(is_acp_process_runtime("custom", "acp://custom"))

    def test_extracted_tool_calls_match_openai_sdk_shape(self) -> None:
        tool_response = (
            "I'll inspect that.\n"
            "<tool_call>"
            '{"id":"call_read","type":"function",'
            '"function":{"name":"read_file","arguments":"{\\"path\\":\\"README.md\\"}"}}'
            "</tool_call>"
        )

        with patch.object(self.client, "_run_prompt", return_value=(tool_response, "")):
            response = self.client._create_chat_completion(
                model="copilot-acp",
                messages=[{"role": "user", "content": "read README.md"}],
                tools=[
                    {
                        "type": "function",
                        "function": {"name": "read_file", "parameters": {}},
                    }
                ],
            )

        choice = response.choices[0]
        self.assertEqual(choice.finish_reason, "tool_calls")
        tool_call = choice.message.tool_calls[0]
        self.assertEqual(tool_call.id, "call_read")
        self.assertEqual(tool_call.function.name, "read_file")
        self.assertEqual(
            json.loads(tool_call.function.arguments),
            {"path": "README.md"},
        )
        self.assertEqual(dict(tool_call)["id"], "call_read")
        self.assertEqual(dict(tool_call.function)["name"], "read_file")
        self.assertEqual(choice.message.content, "I'll inspect that.")

    def test_stream_true_returns_iterable_text_chunks(self) -> None:
        with patch.object(self.client, "_run_prompt", return_value=("Hello from ACP", "")):
            stream = self.client._create_chat_completion(
                model="copilot-acp",
                messages=[{"role": "user", "content": "hello"}],
                stream=True,
            )

        chunks = list(stream)
        self.assertEqual(len(chunks), 2)
        self.assertEqual(chunks[0].choices[0].delta.content, "Hello from ACP")
        self.assertIsNone(chunks[0].choices[0].delta.tool_calls)
        self.assertEqual(chunks[0].choices[0].finish_reason, "stop")
        self.assertEqual(chunks[1].choices, [])
        self.assertEqual(chunks[1].usage.total_tokens, 0)

    def test_stream_true_preserves_tool_call_deltas(self) -> None:
        tool_response = (
            "<tool_call>"
            '{"id":"call_read","type":"function",'
            '"function":{"name":"read_file","arguments":"{\\"path\\":\\"README.md\\"}"}}'
            "</tool_call>"
        )

        with patch.object(self.client, "_run_prompt", return_value=(tool_response, "")):
            stream = self.client._create_chat_completion(
                model="copilot-acp",
                messages=[{"role": "user", "content": "read README.md"}],
                stream=True,
            )

        chunks = list(stream)
        delta = chunks[0].choices[0].delta
        self.assertIsNone(delta.content)
        self.assertEqual(chunks[0].choices[0].finish_reason, "tool_calls")
        self.assertEqual(len(delta.tool_calls), 1)
        tool_delta = delta.tool_calls[0]
        self.assertEqual(tool_delta.index, 0)
        self.assertEqual(tool_delta.id, "call_read")
        self.assertEqual(tool_delta.function.name, "read_file")
        self.assertEqual(
            json.loads(tool_delta.function.arguments),
            {"path": "README.md"},
        )
        self.assertEqual(chunks[1].choices, [])

    def test_timeout_object_is_coerced_for_streaming_requests(self) -> None:
        captured: dict[str, float] = {}

        def fake_run_prompt(prompt_text: str, *, timeout_seconds: float) -> tuple[str, str]:
            captured["timeout"] = timeout_seconds
            return "ok", ""

        timeout = type(
            "TimeoutLike",
            (),
            {"read": 12.0, "write": 5.0, "connect": 3.0, "pool": 1.0},
        )()

        with patch.object(self.client, "_run_prompt", side_effect=fake_run_prompt):
            list(
                self.client._create_chat_completion(
                    model="copilot-acp",
                    messages=[{"role": "user", "content": "hello"}],
                    timeout=timeout,
                    stream=True,
                )
            )

        self.assertEqual(captured["timeout"], 12.0)

    def _dispatch(self, message: dict, *, cwd: str) -> dict:
        process = _FakeProcess()
        handled = self.client._handle_server_message(
            message,
            process=process,
            cwd=cwd,
            text_parts=[],
            reasoning_parts=[],
        )
        self.assertTrue(handled)
        payload = process.stdin.getvalue().strip()
        self.assertTrue(payload)
        return json.loads(payload)

    def test_request_permission_is_not_auto_allowed(self) -> None:
        response = self._dispatch(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "session/request_permission",
                "params": {},
            },
            cwd="/tmp",
        )

        outcome = (((response.get("result") or {}).get("outcome") or {}).get("outcome"))
        self.assertEqual(outcome, "cancelled")

    def test_read_text_file_rejects_path_outside_session_cwd(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            workspace = root / "workspace"
            workspace.mkdir()
            outside = root / "outside.txt"
            outside.write_text("must-not-be-read")

            response = self._dispatch(
                {
                    "jsonrpc": "2.0",
                    "id": 6,
                    "method": "fs/read_text_file",
                    "params": {"path": str(outside)},
                },
                cwd=str(workspace),
            )

        self.assertEqual((response.get("error") or {}).get("code"), -32602)
        self.assertIn("outside the session cwd", str(response.get("error")))
        self.assertNotIn("must-not-be-read", json.dumps(response))

    @unittest.skipUnless(os.name == "posix" and hasattr(os, "O_NOFOLLOW"), "POSIX symlink race")
    def test_read_text_file_rejects_target_swapped_to_symlink_after_policy_check(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            target = root / "note.txt"
            target.write_text("inside", encoding="utf-8")
            outside = root.parent / f"{root.name}-outside-read.txt"
            outside.write_text("must-not-leak", encoding="utf-8")

            def swap_target(_path: str) -> None:
                target.unlink()
                target.symlink_to(outside)
                return None

            try:
                with patch(
                    "agent.copilot_acp_client.get_read_block_error",
                    side_effect=swap_target,
                ):
                    response = self._dispatch(
                        {
                            "jsonrpc": "2.0",
                            "id": 12,
                            "method": "fs/read_text_file",
                            "params": {"path": str(target)},
                        },
                        cwd=str(root),
                    )
            finally:
                outside.unlink(missing_ok=True)

        self.assertEqual((response.get("error") or {}).get("code"), -32602)
        self.assertNotIn("must-not-leak", json.dumps(response))

    @unittest.skipUnless(os.name == "posix" and hasattr(os, "O_NOFOLLOW"), "POSIX symlink race")
    def test_read_text_file_rejects_parent_swapped_to_symlink_after_policy_check(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            parent = root / "subdir"
            parent.mkdir()
            target = parent / "note.txt"
            target.write_text("inside", encoding="utf-8")
            outside_dir = root.parent / f"{root.name}-outside-read-dir"
            outside_dir.mkdir()
            outside = outside_dir / "note.txt"
            outside.write_text("must-not-leak-parent", encoding="utf-8")

            def swap_parent(_path: str) -> None:
                target.unlink()
                parent.rmdir()
                parent.symlink_to(outside_dir, target_is_directory=True)
                return None

            try:
                with patch(
                    "agent.copilot_acp_client.get_read_block_error",
                    side_effect=swap_parent,
                ):
                    response = self._dispatch(
                        {
                            "jsonrpc": "2.0",
                            "id": 13,
                            "method": "fs/read_text_file",
                            "params": {"path": str(target)},
                        },
                        cwd=str(root),
                    )
            finally:
                outside.unlink(missing_ok=True)
                outside_dir.rmdir()

        self.assertEqual((response.get("error") or {}).get("code"), -32602)
        self.assertNotIn("must-not-leak-parent", json.dumps(response))

    @unittest.skipUnless(os.name == "posix" and hasattr(os, "link"), "POSIX hardlink")
    def test_read_text_file_rejects_a_hardlinked_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            outside = root.parent / f"{root.name}-outside-hardlink.txt"
            outside.write_text("must-not-leak-hardlink", encoding="utf-8")
            target = root / "note.txt"
            os.link(outside, target)

            try:
                response = self._dispatch(
                    {
                        "jsonrpc": "2.0",
                        "id": 14,
                        "method": "fs/read_text_file",
                        "params": {"path": str(target)},
                    },
                    cwd=str(root),
                )
            finally:
                outside.unlink(missing_ok=True)

        self.assertEqual((response.get("error") or {}).get("code"), -32602)
        self.assertNotIn("must-not-leak-hardlink", json.dumps(response))

    def test_read_text_file_keeps_a_secure_windows_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            target = root / "note.txt"
            target.write_text("inside", encoding="utf-8")

            with (
                patch("agent.copilot_acp_client.sys.platform", "win32"),
                patch(
                    "agent.copilot_acp_client._read_text_file_secure_windows",
                    return_value="inside",
                    create=True,
                ) as secure_windows_read,
            ):
                response = self._dispatch(
                    {
                        "jsonrpc": "2.0",
                        "id": 14,
                        "method": "fs/read_text_file",
                        "params": {"path": str(target)},
                    },
                    cwd=str(root),
                )

        self.assertNotIn("error", response)
        self.assertEqual((response.get("result") or {}).get("content"), "inside")
        secure_windows_read.assert_called_once_with(target.resolve(), str(root))

    def test_windows_secure_reader_rejects_an_escaped_handle_before_reading(self) -> None:
        class FakeWin32Con:
            GENERIC_READ = 0x80000000
            OPEN_EXISTING = 3
            FILE_ATTRIBUTE_NORMAL = 0x80
            FILE_ATTRIBUTE_DIRECTORY = 0x10
            FILE_SHARE_READ = 1
            FILE_SHARE_WRITE = 2
            FILE_SHARE_DELETE = 4

        class FakeWin32File:
            def __init__(self) -> None:
                self.read_called = False
                self.closed = False

            def CreateFile(self, *_args):
                return object()

            def GetFinalPathNameByHandle(self, _handle, _flags):
                return str(Path(tempfile.gettempdir()) / "escaped" / "secret.txt")

            def GetFileInformationByHandle(self, _handle):
                return (0, None, None, None, 0, 0, 0, 1, 0, 0)

            def ReadFile(self, _handle, _size):
                self.read_called = True
                return 0, b"must-not-leak"

            def CloseHandle(self, _handle):
                self.closed = True

        fake_file = FakeWin32File()
        with tempfile.TemporaryDirectory() as tmpdir:
            target = Path(tmpdir) / "note.txt"
            target.write_text("inside", encoding="utf-8")
            with patch.object(
                acp_client,
                "_win32_file_api",
                return_value=(Exception, FakeWin32Con, fake_file),
                create=True,
            ):
                with self.assertRaisesRegex(PermissionError, "escaped"):
                    acp_client._read_text_file_secure_windows(target, tmpdir)

        self.assertFalse(fake_file.read_called)
        self.assertTrue(fake_file.closed)

    def test_windows_secure_reader_decodes_utf8_from_a_verified_handle(self) -> None:
        class FakePyWinTypes:
            error = OSError

        class FakeWin32Con:
            GENERIC_READ = 0x80000000
            OPEN_EXISTING = 3
            FILE_ATTRIBUTE_NORMAL = 0x80
            FILE_ATTRIBUTE_DIRECTORY = 0x10
            FILE_SHARE_READ = 1
            FILE_SHARE_WRITE = 2
            FILE_SHARE_DELETE = 4

        class FakeWin32File:
            def __init__(self, expected: Path) -> None:
                self.expected = expected
                self.chunks = ["中文 — ok".encode(), b""]
                self.closed = False

            def CreateFile(self, *_args):
                return object()

            def GetFinalPathNameByHandle(self, _handle, _flags):
                return str(self.expected)

            def GetFileInformationByHandle(self, _handle):
                return (0, None, None, None, 0, 0, 0, 1, 0, 0)

            def ReadFile(self, _handle, _size):
                return 0, self.chunks.pop(0)

            def CloseHandle(self, _handle):
                self.closed = True

        with tempfile.TemporaryDirectory() as tmpdir:
            target = (Path(tmpdir) / "note.txt").resolve()
            target.write_text("ignored by fake handle", encoding="utf-8")
            fake_file = FakeWin32File(target)
            with patch.object(
                acp_client,
                "_win32_file_api",
                return_value=(FakePyWinTypes, FakeWin32Con, fake_file),
            ):
                content = acp_client._read_text_file_secure_windows(target, tmpdir)

        self.assertEqual(content, "中文 — ok")
        self.assertTrue(fake_file.closed)

    def test_windows_secure_reader_rejects_a_multilink_handle_before_reading(self) -> None:
        class FakePyWinTypes:
            error = OSError

        class FakeWin32Con:
            GENERIC_READ = 0x80000000
            OPEN_EXISTING = 3
            FILE_ATTRIBUTE_NORMAL = 0x80
            FILE_ATTRIBUTE_DIRECTORY = 0x10
            FILE_SHARE_READ = 1
            FILE_SHARE_WRITE = 2
            FILE_SHARE_DELETE = 4

        class FakeWin32File:
            def __init__(self, expected: Path) -> None:
                self.expected = expected
                self.read_called = False
                self.closed = False

            def CreateFile(self, *_args):
                return object()

            def GetFinalPathNameByHandle(self, _handle, _flags):
                return str(self.expected)

            def GetFileInformationByHandle(self, _handle):
                return (0, None, None, None, 0, 0, 0, 2, 0, 0)

            def ReadFile(self, _handle, _size):
                self.read_called = True
                return 0, b"must-not-leak"

            def CloseHandle(self, _handle):
                self.closed = True

        with tempfile.TemporaryDirectory() as tmpdir:
            target = (Path(tmpdir) / "note.txt").resolve()
            target.write_text("inside", encoding="utf-8")
            fake_file = FakeWin32File(target)
            with patch.object(
                acp_client,
                "_win32_file_api",
                return_value=(FakePyWinTypes, FakeWin32Con, fake_file),
            ):
                with self.assertRaisesRegex(PermissionError, "single-link"):
                    acp_client._read_text_file_secure_windows(target, tmpdir)

        self.assertFalse(fake_file.read_called)
        self.assertTrue(fake_file.closed)

    def test_shell_and_network_methods_are_rejected_as_unsupported(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            marker = Path(tmpdir) / "should-not-run"
            for request_id, method in enumerate(
                ("terminal/execute", "network/request"),
                start=7,
            ):
                with self.subTest(method=method):
                    response = self._dispatch(
                        {
                            "jsonrpc": "2.0",
                            "id": request_id,
                            "method": method,
                            "params": {
                                "command": f"touch {marker}",
                                "url": "https://example.com",
                            },
                        },
                        cwd=tmpdir,
                    )

                    self.assertEqual((response.get("error") or {}).get("code"), -32601)
                    self.assertIn("not supported by Hermes", str(response.get("error")))
                    self.assertFalse(marker.exists())

    def test_read_text_file_blocks_internal_hermes_hub_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            home = Path(tmpdir) / "home"
            blocked = home / ".hermes" / "skills" / ".hub" / "index-cache" / "entry.json"
            blocked.parent.mkdir(parents=True, exist_ok=True)
            blocked.write_text('{"token":"sk-test-secret-1234567890"}')

            with patch.dict(
                os.environ,
                {"HOME": str(home), "HERMES_HOME": str(home / ".hermes")},
                clear=False,
            ):
                response = self._dispatch(
                    {
                        "jsonrpc": "2.0",
                        "id": 2,
                        "method": "fs/read_text_file",
                        "params": {"path": str(blocked)},
                    },
                    cwd=str(home),
                )

        self.assertIn("error", response)

    def test_read_text_file_redacts_sensitive_content(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            secret_file = root / "config.env"
            secret_file.write_text("OPENAI_API_KEY=sk-proj-abc123def456ghi789jkl012")

            # agent.redact snapshots HERMES_REDACT_SECRETS at import time into
            # _REDACT_ENABLED, so patching os.environ is a no-op. Flip the
            # module-level constant directly for the duration of the call.
            with patch("agent.redact._REDACT_ENABLED", True):
                response = self._dispatch(
                    {
                        "jsonrpc": "2.0",
                        "id": 3,
                        "method": "fs/read_text_file",
                        "params": {"path": str(secret_file)},
                    },
                    cwd=str(root),
                )

        content = ((response.get("result") or {}).get("content") or "")
        self.assertNotIn("abc123def456", content)
        self.assertIn("OPENAI_API_KEY=", content)

    def test_fs_read_text_file_decodes_as_utf8_under_non_utf8_locale(self) -> None:
        """Regression for #18637 (bug 2): fs/read_text_file used
        ``path.read_text()`` with no explicit encoding, so on Windows
        GBK/CP932/CP949 locales the Copilot read_file tool crashed on any
        source file with non-ASCII content (e.g. a CJK comment, an em dash,
        or UTF-8 BOM)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            target = root / "note.md"
            target.write_text("# 中文标题\nem dash — here\n", encoding="utf-8")

            original_read_text = Path.read_text

            def strict_read_text(self, encoding=None, errors=None, **kwargs):
                if self == target and encoding != "utf-8":
                    raise UnicodeDecodeError(
                        "gbk", b"\x94", 0, 1, "illegal multibyte sequence"
                    )
                return original_read_text(
                    self, encoding=encoding, errors=errors, **kwargs
                )

            with patch.object(Path, "read_text", strict_read_text):
                response = self._dispatch(
                    {
                        "jsonrpc": "2.0",
                        "id": 10,
                        "method": "fs/read_text_file",
                        "params": {"path": str(target)},
                    },
                    cwd=str(root),
                )

        self.assertNotIn("error", response)
        content = ((response.get("result") or {}).get("content") or "")
        self.assertIn("中文标题", content)
        self.assertIn("em dash —", content)

    def test_fs_write_text_file_encodes_as_utf8(self) -> None:
        """Regression for #18637 (bug 2): fs/write_text_file used
        ``path.write_text()`` with no explicit encoding, so on non-UTF-8
        locales the Copilot write tool could not emit code/config files
        containing any char outside the platform codec."""
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            target = root / "out.md"
            payload = "# 中文标题\nem dash — here\n"

            original_write_text = Path.write_text

            def strict_write_text(
                self, data, encoding=None, errors=None, **kwargs
            ):
                if self == target and encoding != "utf-8":
                    raise UnicodeEncodeError(
                        "gbk", data, 0, 1, "illegal multibyte sequence"
                    )
                return original_write_text(
                    self, data, encoding=encoding, errors=errors, **kwargs
                )

            with patch.object(Path, "write_text", strict_write_text):
                response = self._dispatch(
                    {
                        "jsonrpc": "2.0",
                        "id": 11,
                        "method": "fs/write_text_file",
                        "params": {
                            "path": str(target),
                            "content": payload,
                        },
                    },
                    cwd=str(root),
                )

            self.assertNotIn("error", response)
            self.assertEqual(target.read_text(encoding="utf-8"), payload)

    def test_write_text_file_reuses_write_denylist(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            home = Path(tmpdir) / "home"
            target = home / ".ssh" / "id_rsa"
            target.parent.mkdir(parents=True, exist_ok=True)

            with patch(
                "agent.copilot_acp_client.get_write_denied_error",
                return_value="Write denied: protected",
                create=True,
            ):
                response = self._dispatch(
                    {
                        "jsonrpc": "2.0",
                        "id": 4,
                        "method": "fs/write_text_file",
                        "params": {
                            "path": str(target),
                            "content": "fake-private-key",
                        },
                    },
                    cwd=str(home),
                )

        self.assertIn("error", response)
        self.assertFalse(target.exists())

    def test_write_text_file_respects_safe_root(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            safe_root = root / "workspace"
            safe_root.mkdir()
            outside = root / "outside.txt"

            with patch.dict(os.environ, {"HERMES_WRITE_SAFE_ROOT": str(safe_root)}, clear=False):
                response = self._dispatch(
                    {
                        "jsonrpc": "2.0",
                        "id": 5,
                        "method": "fs/write_text_file",
                        "params": {
                            "path": str(outside),
                            "content": "should-not-write",
                        },
                    },
                    cwd=str(root),
                )

        self.assertIn("error", response)
        self.assertIn("HERMES_WRITE_SAFE_ROOT", str(response["error"]))
        self.assertFalse(outside.exists())


if __name__ == "__main__":
    unittest.main()


# ── HOME env propagation tests (from PR #11285) ─────────────────────

from unittest.mock import patch as _patch
import pytest


def _make_home_client(tmp_path):
    return CopilotACPClient(
        api_key="copilot-acp",
        base_url="acp://copilot",
        acp_command="copilot",
        acp_args=["--acp", "--stdio"],
        acp_cwd=str(tmp_path),
    )


def _fake_popen_capture(captured):
    def _fake(cmd, **kwargs):
        captured["cmd"] = cmd
        captured["kwargs"] = kwargs
        raise FileNotFoundError("copilot not found")
    return _fake


def test_run_prompt_preserves_real_home_when_profile_home_available(monkeypatch, tmp_path):
    hermes_home = tmp_path / "hermes"
    (hermes_home / "home").mkdir(parents=True)
    real_home = tmp_path / "real-home"
    real_home.mkdir()

    monkeypatch.setenv("HOME", str(real_home))
    monkeypatch.setenv("HERMES_HOME", str(hermes_home))
    # The contract under test is host-mode ``auto``. Keep it deterministic on
    # CI runners that themselves execute inside a container, where ``auto``
    # intentionally selects the profile home instead.
    monkeypatch.setattr("hermes_constants.is_container", lambda: False)

    captured = {}
    client = _make_home_client(tmp_path)

    with _patch("agent.copilot_acp_client.subprocess.Popen", side_effect=_fake_popen_capture(captured)):
        with pytest.raises(RuntimeError, match="Could not start Copilot ACP command"):
            client._run_prompt("hello", timeout_seconds=1)

    assert captured["kwargs"]["env"]["HOME"] == str(real_home)
    assert captured["kwargs"]["env"]["HERMES_REAL_HOME"] == str(real_home)


def test_run_prompt_passes_home_when_parent_env_is_clean(monkeypatch, tmp_path):
    monkeypatch.delenv("HOME", raising=False)
    monkeypatch.delenv("HERMES_HOME", raising=False)

    captured = {}
    client = _make_home_client(tmp_path)

    with _patch("agent.copilot_acp_client.subprocess.Popen", side_effect=_fake_popen_capture(captured)):
        with pytest.raises(RuntimeError, match="Could not start Copilot ACP command"):
            client._run_prompt("hello", timeout_seconds=1)

    assert "env" in captured["kwargs"]
    assert captured["kwargs"]["env"]["HOME"]


def test_acp_subprocess_env_excludes_unrelated_provider_credentials(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "openai-parent-secret")
    monkeypatch.setenv("KIMI_API_KEY", "kimi-parent-secret")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "anthropic-parent-secret")
    monkeypatch.setenv("QODER_CLI_PATH", "/opt/qoder/bin/qodercli")

    env = _build_subprocess_env()

    assert "OPENAI_API_KEY" not in env
    assert "KIMI_API_KEY" not in env
    assert "ANTHROPIC_API_KEY" not in env
    assert env["QODER_CLI_PATH"] == "/opt/qoder/bin/qodercli"
