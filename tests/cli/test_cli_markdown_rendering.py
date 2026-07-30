from io import StringIO

from rich.console import Console
from rich.markdown import Markdown

from cli import _render_final_assistant_content


def _render_to_text(renderable) -> str:
    buf = StringIO()
    Console(file=buf, width=80, force_terminal=False, color_system=None).print(renderable)
    return buf.getvalue()


def test_final_assistant_content_uses_markdown_renderable():
    renderable = _render_final_assistant_content("# Title\n\n- one\n- two")

    assert isinstance(renderable, Markdown)
    output = _render_to_text(renderable)
    assert "Title" in output
    assert "one" in output
    assert "two" in output




def test_final_assistant_content_keeps_non_path_markdown_escapes():
    renderable = _render_final_assistant_content(r"1\. Not an ordered list")

    output = _render_to_text(renderable)
    assert "1. Not an ordered list" in output
    assert r"1\." not in output






def test_strip_mode_preserves_lists():
    renderable = _render_final_assistant_content(
        "**Formatting**\n- Ran prettier\n- Files changed\n- Verified clean",
        mode="strip",
    )

    output = _render_to_text(renderable)
    assert "- Ran prettier" in output
    assert "- Files changed" in output
    assert "- Verified clean" in output
    assert "**" not in output




def test_strip_mode_preserves_blockquotes():
    renderable = _render_final_assistant_content(
        "> This is quoted text\n> Another quoted line",
        mode="strip",
    )

    output = _render_to_text(renderable)
    assert "> This is quoted" in output
    assert "> Another quoted" in output






def test_strip_mode_preserves_cron_asterisks_in_plain_text():
    renderable = _render_final_assistant_content("* * * * *", mode="strip")

    output = _render_to_text(renderable)
    assert "* * * * *" in output

    # Still treat the canonical 3-asterisk Markdown horizontal rule as decoration.
    renderable = _render_final_assistant_content("* * *", mode="strip")
    output = _render_to_text(renderable)
    assert "* * *" not in output




def test_strip_mode_preserves_intraword_underscores_in_snake_case_identifiers():
    renderable = _render_final_assistant_content(
        "Let me look at test_case_with_underscores and SOME_CONST "
        "then /tmp/snake_case_dir/file_with_name.py",
        mode="strip",
    )

    output = _render_to_text(renderable)
    assert "test_case_with_underscores" in output
    assert "SOME_CONST" in output
    assert "snake_case_dir" in output
    assert "file_with_name" in output


def test_strip_mode_still_strips_boundary_underscore_emphasis():
    renderable = _render_final_assistant_content(
        "say _hi_ and __bold__ now",
        mode="strip",
    )

    output = _render_to_text(renderable)
    assert "say hi and bold now" in output


def test_strip_mode_drops_code_fence_language_tag():
    # The opening fence's info-string (language tag) must not leak into the
    # plain-text output as visible text.  See issue #73212.
    renderable = _render_final_assistant_content(
        "Here is code:\n\n```python\nprint('hi')\n```\n",
        mode="strip",
    )

    output = _render_to_text(renderable)
    assert "print('hi')" in output
    assert "python" not in output
    assert "```" not in output


def test_strip_mode_preserves_dunder_identifiers_inside_code_fence():
    # Markdown emphasis stripping must not be applied inside fenced code
    # blocks — Python dunder identifiers like __name__ / __class__ survive
    # verbatim so copy-pasted output stays executable.  See issue #73212.
    source = (
        "Run this:\n\n"
        "```python\n"
        "if __name__ == '__main__':\n"
        "    print(__class__)\n"
        "```\n"
    )
    renderable = _render_final_assistant_content(source, mode="strip")

    output = _render_to_text(renderable)
    # The full executable line is preserved verbatim, underscores and all.
    assert "if __name__ == '__main__':" in output
    assert "print(__class__)" in output

    # The mangled forms reported in the issue must not appear.
    assert "name == " not in output
    assert "print(class)" not in output

    # The fence markers and language tag are gone, but the code body remains.
    assert "```" not in output
    assert "python" not in output


def test_strip_mode_preserves_tilde_fence_content_verbatim():
    # Tilde fences (~~~) are also code blocks; inner emphasis markers and
    # the language tag must be preserved / dropped respectively.
    renderable = _render_final_assistant_content(
        "~~~js\nlet x = a * b;\n~~~\n",
        mode="strip",
    )

    output = _render_to_text(renderable)
    assert "let x = a * b;" in output
    assert "~~~" not in output
    assert "js" not in output


def test_strip_mode_still_strips_emphasis_outside_code_fence():
    # Prose adjacent to a code fence is still stripped normally.
    renderable = _render_final_assistant_content(
        "**bold** prose\n\n```\n__name__\n```\n",
        mode="strip",
    )

    output = _render_to_text(renderable)
    assert "bold prose" in output
    assert "**" not in output
    assert "__name__" in output
