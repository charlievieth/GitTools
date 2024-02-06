import base64
from re import sub
from sre_compile import BRANCH
import subprocess
import webbrowser
from os.path import relpath
from os.path import isdir
from os.path import dirname
from typing import Optional
from typing import Tuple

from .plugin.logger import get_logger

import sublime
import sublime_plugin

# Global logger
# TODO: maybe just initialize this here
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
    # sublime.status_message
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


def convert_remote_url(u: str) -> str:
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
    raise UnsupportedURIException(u)


def open_url(view: sublime.View, url: str) -> None:
    webbrowser.open(url)


def view_row(view: sublime.View, point: int) -> int:
    return 0


def view_selection_rows(view: sublime.View) -> Optional[RowRange]:
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
    return


# def open_in_browser(uri: str) -> None:
#     # NOTE: Remove this check when on py3.8.
#     if not uri.lower().startswith(("http://", "https://")):
#         uri = "https://" + uri
#     if not webbrowser.open(uri):
#         sublime.status_message("failed to open: " + uri)


def _git(path: str, *cmd: str) -> str:
    if not isdir(path):
        path = dirname(path)
    return subprocess.check_output(("git", "-C", path, *cmd)).decode().strip()


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


def git_remote(path: str, branch: Optional[str] = None) -> str:
    try:
        # Check if the branch has a different remote than origin
        branch = git_branch(path)
        remote = _git(path, "config", f"branch.{branch}.remote")
        if remote != "origin":
            return remote
    except subprocess.CalledProcessError:
        pass
    return _git(path, "config", "remote.origin.url")


def repo_relpath(path: str) -> str:
    return relpath(path, git_top_level(path))
