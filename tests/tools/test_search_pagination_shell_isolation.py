"""Regression coverage for search pagination in restored Bash sessions."""

from pathlib import Path
import shutil
import subprocess

import pytest

from tools.file_operations import ShellFileOperations


@pytest.mark.skipif(
    not (shutil.which("bash") and shutil.which("rg")),
    reason="requires bash and ripgrep",
)
class TestSearchPaginationShellIsolation:
    """Search pagination must not inherit a user's ``head`` definition."""

    class _HeadShadowingEnvironment:
        def __init__(self, cwd):
            self.cwd = str(cwd)

        def execute(self, command, cwd=None, **kwargs):
            completed = subprocess.run(
                [
                    "bash",
                    "-c",
                    "head() { command head -n 1; }\n" + command,
                ],
                cwd=cwd or self.cwd,
                text=True,
                encoding="utf-8",
                errors="replace",
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                check=False,
            )
            return {"output": completed.stdout, "returncode": completed.returncode}

    def test_file_and_content_search_ignore_shadowed_head(self, tmp_path):
        for index in range(3):
            (tmp_path / f"note-{index}.md").write_text(
                "date: 2026-07\n",
                encoding="utf-8",
            )

        ops = ShellFileOperations(self._HeadShadowingEnvironment(tmp_path))

        files = ops.search("*.md", str(tmp_path), target="files", limit=50)
        if files.error and files.error.startswith("Path not found:"):
            pytest.skip("bash cannot search the absolute temporary path")
        assert files.error is None
        assert {Path(path).name for path in files.files} == {
            "note-0.md",
            "note-1.md",
            "note-2.md",
        }
        assert files.total_count == 3

        matches = ops.search("date: 2026-07", str(tmp_path), target="content", limit=50)
        assert matches.error is None
        assert len(matches.matches) == 3
        assert matches.total_count == 3

        invalid = ops.search("[", str(tmp_path), target="content", limit=50)
        assert invalid.error is not None
        assert invalid.error.startswith("Search failed:")
