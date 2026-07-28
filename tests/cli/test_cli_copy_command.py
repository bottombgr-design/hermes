"""Tests for CLI /copy command and /copy-code command."""

from unittest.mock import MagicMock, patch

from cli import HermesCLI


def _make_cli() -> HermesCLI:
    cli_obj = HermesCLI.__new__(HermesCLI)
    cli_obj.config = {}
    cli_obj.console = MagicMock()
    cli_obj.agent = None
    cli_obj.conversation_history = []
    cli_obj.session_id = "sess-copy-test"
    cli_obj._pending_input = MagicMock()
    cli_obj._app = None
    return cli_obj


def test_copy_copies_latest_assistant_message():
    cli_obj = _make_cli()
    cli_obj.conversation_history = [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "first"},
        {"role": "assistant", "content": "latest"},
    ]

    with patch.object(cli_obj, "_write_osc52_clipboard") as mock_copy:
        result = cli_obj.process_command("/copy")

    assert result is True
    mock_copy.assert_called_once_with("latest")


def test_copy_with_index_uses_requested_assistant_message():
    cli_obj = _make_cli()
    cli_obj.conversation_history = [
        {"role": "assistant", "content": "one"},
        {"role": "assistant", "content": "two"},
    ]

    with patch.object(cli_obj, "_write_osc52_clipboard") as mock_copy:
        cli_obj.process_command("/copy 1")

    mock_copy.assert_called_once_with("one")


def test_copy_strips_reasoning_blocks_before_copy():
    cli_obj = _make_cli()
    cli_obj.conversation_history = [
        {
            "role": "assistant",
            "content": "<REASONING_SCRATCHPAD>internal</REASONING_SCRATCHPAD>\nVisible answer",
        }
    ]

    with patch.object(cli_obj, "_write_osc52_clipboard") as mock_copy:
        cli_obj.process_command("/copy")

    mock_copy.assert_called_once_with("Visible answer")


def test_copy_invalid_index_does_not_copy():
    cli_obj = _make_cli()
    cli_obj.conversation_history = [{"role": "assistant", "content": "only"}]

    with patch.object(cli_obj, "_write_osc52_clipboard") as mock_copy, patch("cli._cprint") as mock_print:
        cli_obj.process_command("/copy 99")

    mock_copy.assert_not_called()
    assert any("Invalid response number" in str(call) for call in mock_print.call_args_list)


# ---- /copy-code tests ----


def _make_cli_with_code_block(code: str, language: str = "python") -> HermesCLI:
    """Return a HermesCLI with a single assistant message containing a fenced code block."""
    cli_obj = _make_cli()
    fence = f"```{language}\n{code}\n```\n"
    cli_obj.conversation_history = [
        {"role": "assistant", "content": fence},
    ]
    return cli_obj


def test_copy_code_picks_block_by_index():
    cli_obj = _make_cli_with_code_block("x = 1")

    with patch.object(cli_obj, "_write_osc52_clipboard") as mock_copy:
        cli_obj.process_command("/copy-code 1")

    mock_copy.assert_called_once_with("x = 1")


def test_copy_code_no_blocks_shows_hint():
    cli_obj = _make_cli()
    cli_obj.conversation_history = [
        {"role": "assistant", "content": "no code here"}
    ]

    with patch.object(cli_obj, "_write_osc52_clipboard") as mock_copy, \
         patch("cli._cprint") as mock_print:
        cli_obj.process_command("/copy-code")

    mock_copy.assert_not_called()
    assert any("No code blocks found" in str(call) for call in mock_print.call_args_list)


def test_copy_code_multiple_blocks():
    src = "```py\nfirst\n```\n\n```js\nsecond\n```\n"
    cli_obj = _make_cli()
    cli_obj.conversation_history = [
        {"role": "assistant", "content": src},
    ]

    # Block 1
    with patch.object(cli_obj, "_write_osc52_clipboard") as mock_copy:
        cli_obj.process_command("/copy-code 1")
    mock_copy.assert_called_once_with("first")


def test_copy_code_invalid_index():
    cli_obj = _make_cli_with_code_block("x = 1")

    with patch.object(cli_obj, "_write_osc52_clipboard") as mock_copy, \
         patch("cli._cprint") as mock_print:
        cli_obj.process_command("/copy-code 99")

    mock_copy.assert_not_called()
    assert any("Invalid block number" in str(call) for call in mock_print.call_args_list)


# ---- /cc alias tests ----


def test_cc_alias_copies_by_index():
    """Test that /cc N works as an alias for /copy-code N."""
    cli_obj = _make_cli_with_code_block("y = 2", language="js")

    with patch.object(cli_obj, "_write_osc52_clipboard") as mock_copy:
        cli_obj.process_command("/cc 1")

    mock_copy.assert_called_once_with("y = 2")


def test_cc_alias_no_blocks_shows_hint():
    """Test /cc without blocks when none exist."""
    cli_obj = _make_cli()
    cli_obj.conversation_history = [
        {"role": "assistant", "content": "no code here"}
    ]

    with patch.object(cli_obj, "_write_osc52_clipboard") as mock_copy, \
         patch("cli._cprint") as mock_print:
        cli_obj.process_command("/cc")

    mock_copy.assert_not_called()
    assert any("No code blocks found" in str(call) for call in mock_print.call_args_list)


# ---- Single-block immediate copy ----


def test_copy_code_single_block_copies_immediately():
    """When there's exactly one code block, /copy-code copies without preview."""
    cli_obj = _make_cli_with_code_block("single block")

    with patch.object(cli_obj, "_write_osc52_clipboard") as mock_copy:
        cli_obj.process_command("/copy-code")

    mock_copy.assert_called_once_with("single block")


# ---- Assistant content as list ----


def test_copy_code_handles_list_content():
    """Assistant content can be a list of content-part dicts."""
    cli_obj = _make_cli()
    cli_obj.conversation_history = [
        {
            "role": "assistant",
            "content": [
                {"type": "text", "text": "Here's the code:"},
                {"type": "text", "text": "```python\ndef hello():\n    pass\n```"},
            ],
        }
    ]

    with patch.object(cli_obj, "_write_osc52_clipboard") as mock_copy:
        cli_obj.process_command("/copy-code 1")

    mock_copy.assert_called_once()


def test_copy_code_handles_none_content():
    """Assistant content can be None."""
    cli_obj = _make_cli()
    cli_obj.conversation_history = [
        {"role": "assistant", "content": None},
    ]

    with patch.object(cli_obj, "_write_osc52_clipboard") as mock_copy, \
         patch("cli._cprint") as mock_print:
        cli_obj.process_command("/copy-code")

    mock_copy.assert_not_called()
    assert any("Nothing to copy" in str(call) or "No code blocks" in str(call)
               for call in mock_print.call_args_list)


# ---- _print_code_copy_hint tests ----


def test_print_code_copy_hint_single():
    from cli import _print_code_copy_hint

    with patch("cli._cprint") as mock_print:
        _print_code_copy_hint("```python\nx = 1\n```")

    assert any("Single code block" in str(call) for call in mock_print.call_args_list)


def test_print_code_copy_hint_multiple():
    from cli import _print_code_copy_hint

    src = "```py\na\n```\n\n```js\nb\n```"
    with patch("cli._cprint") as mock_print:
        _print_code_copy_hint(src)

    assert any("2 code blocks" in str(call) for call in mock_print.call_args_list)


def test_print_code_copy_hint_no_code():
    from cli import _print_code_copy_hint

    with patch("cli._cprint") as mock_print:
        _print_code_copy_hint("just plain text")

    mock_print.assert_not_called()


def test_print_code_copy_hint_none():
    from cli import _print_code_copy_hint

    with patch("cli._cprint") as mock_print:
        _print_code_copy_hint(None)

    mock_print.assert_not_called()


# ---- Backward search ----


def test_copy_code_searches_backward_when_last_has_no_code():
    """When the last assistant message has no code, search earlier messages."""
    cli_obj = _make_cli()
    cli_obj.conversation_history = [
        {"role": "assistant", "content": "```python\nearly code\n```"},
        {"role": "user", "content": "what about this?"},
        {"role": "assistant", "content": "no code here"},
    ]

    with patch.object(cli_obj, "_write_osc52_clipboard") as mock_copy:
        cli_obj.process_command("/copy-code 1")

    mock_copy.assert_called_once_with("early code")


# ---- Numbered list preview ----


def test_copy_code_multiple_blocks_shows_numbered_list():
    """Multiple blocks without index prints a numbered list."""
    src = "```python\ndef foo(): pass\n```\n\n```bash\nsudo systemctl restart nginx\n```\n\n```json\n{\n  \"key\": \"val\"\n}\n```"
    cli_obj = _make_cli()
    cli_obj.conversation_history = [
        {"role": "assistant", "content": src},
    ]

    with patch("cli._cprint") as mock_print:
        cli_obj.process_command("/copy-code")

    called_text = " ".join(str(call) for call in mock_print.call_args_list)
    assert "Code blocks in the last response:" in called_text
    assert "python" in called_text
    assert "bash" in called_text
    assert "json" in called_text
    assert "def foo()" in called_text
    assert "systemctl restart nginx" in called_text
    assert "Run /cc <1-3>" in called_text
