import logging
import subprocess
import webbrowser
from os.path import dirname
from os.path import isdir
from os.path import relpath
from sys import stdout
from typing import Any
from typing import Dict
from typing import Optional

import sublime
import sublime_plugin


def get_logger(level: int = logging.INFO) -> logging.Logger:
    logging.basicConfig(
        level=level,
        format="[%(name)s:%(levelname)s] [%(filename)s:%(lineno)d]: %(message)s",
        handlers=[logging.StreamHandler(stdout)],
    )
    return logging.getLogger("GitTools")


# Global logger
log = get_logger()


class UnsupportedURIException(Exception):
    def __init__(self, uri: str) -> None:
        self.uri = uri

    def __str__(self) -> str:
        return f"unsupported remote URI: {self.uri}"


class RowRange:
    __slots__ = "begin", "end"

    def __init__(self, begin: int, end: int):
        self.begin = begin
        self.end = end


# git ls-remote --exit-code --symref remote HEAD
# git -C path config remote.origin.url
class GitBrowse(sublime_plugin.WindowCommand):
    def run(self) -> None:
        view: Optional[sublime.View] = self.window.active_view()
        if view is None or not view.is_valid() or view.is_loading():
            return
        file_name = view.file_name()
        if file_name is None:
            sublime.status_message(f"error: GitBrowse: file not saved to disk")
            return
        row_range = view_selection_rows(view)
        if not row_range:
            return

        # TODO: try with BRANCH then SHA
        rel = repo_relpath(file_name)
        branch = git_branch(file_name)
        if branch.startswith("tags/"):
            branch = removeprefix(branch, "tags/")
        base_url = convert_remote_url(git_remote(file_name, branch))
        log.info("relpath: %s branch: %s base_url: %s", rel, branch, base_url)
        url = f"{base_url}/blob/{branch}/{rel}#L{row_range.begin}-L{row_range.end}"
        open_url(view, url)
        pass

    def repo_url(self, repo: str) -> str:
        return ""


def removeprefix(base: str, prefix: str) -> str:
    if base.startswith(prefix):
        return base[len(prefix) :]
    return base


def removesuffix(base: str, suffix: str) -> str:
    if base.endswith(suffix):
        return base[: len(base) - len(suffix)]
    return base


# TODO: load replacements from settings
def convert_remote_url(u: str, replacements: Optional[Dict[str, str]] = None) -> str:
    if u.startswith("https://github.com"):
        return u
    elif u.startswith("git@github.com"):
        u = removeprefix(u, "git@")
        u = removesuffix(u, ".git")
        u = u.replace(":", "/", 1)
        return "https://" + u
    # TODO: make this configurable
    elif u.startswith("https://go.googlesource.com/"):
        return u.replace(
            "https://go.googlesource.com/", "https://github.com/golang/", 1
        )
    if replacements:
        for k, v in replacements.items():
            if u.startswith(k):
                return u.replace(k, v, 1)
    raise UnsupportedURIException(u)


def open_url(view: sublime.View, url: str) -> None:
    webbrowser.open(url)


def view_row(view: sublime.View, point: int) -> int:
    return 0


def view_selection_rows(view: sublime.View) -> Optional[RowRange]:
    # Use the first selection, if any.
    try:
        sel = view.sel()
        if sel and len(sel) >= 1:
            return RowRange(
                begin=view.rowcol(sel[0].begin())[0] + 1,
                end=view.rowcol(sel[0].end())[0] + 1,
            )
    except IndexError as e:
        # This happens when the file is closed before this can run
        log.exception("index error: %s", e)
    except Exception as e:
        log.exception("calculating offset: {}".format(e))
    return None


def _git(path: str, *cmd: str) -> str:
    if not isdir(path):
        path = dirname(path)
    try:
        proc = subprocess.run(
            ["git", "-C", path, *cmd],
            capture_output=True,
            check=True,
            timeout=5,
            encoding="utf-8",
        )
        if proc.stdout:
            return proc.stdout.strip()
        else:
            return ""
    except subprocess.CalledProcessError as e:
        stderr = e.stderr.decode().strip() if e.stderr else "<NONE>"
        log.exception(
            "command %s exited with %d stderr:\n%s", e.cmd, e.returncode, stderr
        )
        raise e


def git_top_level(path: str) -> str:
    return _git(path, "rev-parse", "--show-toplevel")


def git_commit_sha(path: str) -> str:
    return _git("rev-parse", "HEAD")


def git_branch(path: str) -> str:
    branch = _git(path, "name-rev", "--name-only", "HEAD")
    if branch != "HEAD":
        return branch
    else:
        return git_commit_sha(path)


PREFERRED_BRANCHES = [
    "master",
    "main",
    "remotes/origin/master",
    "remotes/origin/main",
]


def git_commit_branch(path: str, commit: str) -> Optional[str]:
    try:
        branches = _git(
            path, "branch", "--all", "--no-color", "--contains", commit
        ).splitlines()
        if not branches:
            return None
    except subprocess.CalledProcessError:
        return None

    # Try to find the current branch. Since this function should only
    # be used when we're in a detached state this should fail.
    for b in branches:
        if b.startswith("* ") and not "HEAD detached at" in b:
            return removeprefix(b, "* ")

    # Try to find a preferred branch that contains the commit.
    branches = [b.strip() for b in branches]
    for b in PREFERRED_BRANCHES:
        if b in branches:
            return b

    # Try to find a branch with a remote URL
    for b in sorted(branches):
        try:
            remote = _git(path, "config", f"branch.{b}.remote")
            if _git(path, "config", f"remote.{remote}.url"):
                return b
        except subprocess.CalledProcessError:
            pass

    return None


def repo_relpath(path: str) -> str:
    return relpath(path, git_top_level(path))
