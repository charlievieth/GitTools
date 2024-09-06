import logging
import subprocess
import webbrowser
from os.path import dirname
from os.path import isdir
from os.path import relpath
from sys import stdout
from typing import Any
from typing import Dict
from typing import List
from typing import Optional

import sublime
import sublime_plugin

PREFERRED_BRANCHES = [
    "master",
    "main",
    "remotes/origin/master",
    "remotes/origin/main",
]


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


class UnsupportedHostException(Exception):
    def __init__(self, uri: str) -> None:
        self.uri = uri

    def __str__(self) -> str:
        return f"unsupported remote host: {self.uri}"


class RowRange:
    __slots__ = "begin", "end"

    def __init__(self, begin: int, end: int):
        self.begin = begin
        self.end = end


# TODO: fallback to the commit if we can't find a branch
class GitBrowse(sublime_plugin.WindowCommand):
    def run(self, **kwargs: Dict[str, Any]) -> None:
        view: Optional[sublime.View] = self.window.active_view()
        if view is None or not view.is_valid() or view.is_loading():
            return
        file_name = view.file_name()
        if file_name is None:
            sublime.status_message("error: GitBrowse: file not saved to disk")
            return
        row_range = view_selection_rows(view)

        # TODO: check if the remote exists in GitHub
        branch = git_branch(file_name)
        remote = git_branch_remote_url(file_name, branch)
        if not remote:
            resolved_branch = git_commit_branch(file_name, branch)
            if not resolved_branch:
                log.info("failed to find a branch for commit %s", branch)
                return
            branch = resolved_branch
            remote = git_branch_remote_url(file_name, branch)
            if not remote:
                log.info("failed to find a remote for commit %s", branch)
                return

        base_url = convert_remote_url(remote)
        rel = repo_relpath(file_name)
        log.info("relpath: %s branch: %s base_url: %s", rel, branch, base_url)

        # WARN: this is GitHub specific
        url = f"{base_url}/blob/{branch}/{rel}"
        if row_range:
            # Add "?plain=1" so that we open the code for things like Markdown
            # which by default are opened in a "pretty" view.
            url += f"?plain=1#L{row_range.begin}-L{row_range.end}"
        webbrowser.open(url)


def removeprefix(base: str, prefix: str) -> str:
    if base.startswith(prefix):
        return base[len(prefix) :]
    return base


def removesuffix(base: str, suffix: str) -> str:
    if base.endswith(suffix):
        return base[: len(base) - len(suffix)]
    return base


def format_url(host_url: str, row_range: Optional[RowRange]) -> str:
    if "github.com" not in host_url:
        raise UnsupportedHostException(host_url)
    return ""


# TODO: load replacements from settings
def convert_remote_url(u: str, replacements: Optional[Dict[str, str]] = None) -> str:
    if u.startswith("https://github.com"):
        return u
    if u.startswith("git@github.com"):
        u = removeprefix(u, "git@")
        u = removesuffix(u, ".git")
        u = u.replace(":", "/", 1)
        return "https://" + u
    # TODO: make this configurable
    if u.startswith("https://go.googlesource.com/"):
        return u.replace(
            "https://go.googlesource.com/", "https://github.com/golang/", 1
        )
    if replacements:
        for k, v in replacements.items():
            if u.startswith(k):
                return u.replace(k, v, 1)
    raise UnsupportedURIException(u)


def view_row(view: sublime.View, point: int) -> int:
    return 0


def view_selection_rows(view: sublime.View) -> Optional[RowRange]:
    # Use the first selection, if any.
    try:
        sel = view.sel()
        if sel and len(sel) >= 1:
            begin = view.rowcol(sel[0].begin())
            end = view.rowcol(sel[0].end())
            if begin == end:
                return None  # no selection
            return RowRange(begin=begin[0] + 1, end=end[0] + 1)
    except IndexError as e:
        # 'This happens when the file is closed before this can run
        log.exception("index error: %s", e)
    except Exception as e:
        log.exception("calculating offset: %s", e)
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
    return _git(path, "rev-parse", "HEAD")


def git_branch(path: str) -> str:
    try:
        branch = _git(path, "name-rev", "--name-only", "HEAD")
        if branch != "HEAD" and not branch.startswith("tags/"):
            return branch
    except subprocess.CalledProcessError:
        pass
    return git_commit_sha(path)


# WARN: this should probably only be used if we're in a detached state.
# git_commit_branch returns the branch that a git commit belongs.
def git_commit_branch(path: str, commit: str) -> Optional[str]:
    # TODO: see if we can use the "/remotes/*" result
    # Example output:
    #     * cev/fixes
    #     remotes/charlievieth/cev/fixes
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
        if b.startswith("* ") and "HEAD detached at" not in b:
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


def git_remotes(path: str) -> List[str]:
    return _git(path, "remote", "show").splitlines()


# WARN: use or remove
def git_branch_remote_url(path: str, branch: str) -> Optional[str]:
    remotes = git_remotes(path)
    if len(remotes) == 1:
        return _git(path, "config", "get", f"remote.{remotes[0]}.url")
    # TODO: not all branches have a remote configured
    # so we need a better way to figure this out.
    try:
        remote = _git(path, "config", "get", f"branch.{branch}.remote")
        if remote:
            return _git(path, "config", "get", f"remote.{remote}.url")
        else:
            return ""
    except subprocess.CalledProcessError:
        return None


def repo_relpath(path: str) -> str:
    return relpath(path, git_top_level(path))
