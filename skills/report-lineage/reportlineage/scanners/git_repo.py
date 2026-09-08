"""
Shallow-clone a git repository into a scratch directory for estate scanning.

Uses the system ``git`` binary via ``subprocess`` so this package stays free
of a compiled git dependency. The clone is always shallow (``--depth``) since
the scanner only reads current file contents, never history.

A caller-supplied token (read from an environment variable, never a literal
argument) is spliced into an https URL for PAT-style authentication. The token
value itself is never logged, printed, or written to a file; any subprocess
failure message is sanitized before it is raised.
"""
from __future__ import annotations

import os
import re
import subprocess
import tempfile
from typing import Optional
from urllib.parse import urlparse, urlunparse

from reportlineage.scanners.filesystem import ScanResult, scan_filesystem

_REDACT_USERINFO = re.compile(r"://[^/@\s]+@")


def _splice_token(url: str, token: str) -> str:
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise RuntimeError("A token can only be injected into an http(s) clone URL.")
    host = parsed.hostname or ""
    if parsed.port:
        host = f"{host}:{parsed.port}"
    netloc = f"{token}@{host}"
    return urlunparse(parsed._replace(netloc=netloc))


def _sanitize(text: str, token: Optional[str]) -> str:
    """Strip a known token value, plus any embedded ``user:pass@``, from ``text``."""
    cleaned = text or "git clone failed"
    if token:
        cleaned = cleaned.replace(token, "***")
    return _REDACT_USERINFO.sub("://***@", cleaned)


def clone_repo(
    url: str,
    dest: Optional[str] = None,
    *,
    branch: Optional[str] = None,
    depth: int = 1,
    token_env_var: Optional[str] = None,
) -> str:
    """Shallow-clone ``url`` and return the local checkout directory.

    ``dest`` defaults to a fresh ``tempfile.mkdtemp(prefix="reportlineage-")``.
    When ``token_env_var`` is given, its value is read from the environment
    and injected into an https URL; the token is never logged, printed, or
    persisted, and any failure message has it redacted.

    Raises :class:`RuntimeError` (never a raw ``CalledProcessError``) with a
    sanitized message on failure.
    """
    if dest is None:
        dest = tempfile.mkdtemp(prefix="reportlineage-")

    token: Optional[str] = None
    clone_url = url
    if token_env_var:
        token = os.environ.get(token_env_var)
        if not token:
            raise RuntimeError(
                f"Environment variable '{token_env_var}' is not set; cannot authenticate the clone."
            )
        clone_url = _splice_token(url, token)

    args = ["git", "clone", "--depth", str(depth)]
    if branch:
        args += ["--branch", branch]
    args += [clone_url, dest]

    try:
        subprocess.run(args, check=True, capture_output=True, text=True, timeout=300)
    except FileNotFoundError as exc:
        raise RuntimeError("git is not installed or not on PATH.") from exc
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError("git clone timed out.") from exc
    except subprocess.CalledProcessError as exc:
        message = _sanitize(exc.stderr or exc.stdout or "", token)
        raise RuntimeError(f"git clone failed: {message}") from None

    return dest


def scan_git_repo(url: str, **clone_kwargs: object) -> ScanResult:
    """Clone ``url`` (see :func:`clone_repo` for kwargs), then scan the checkout."""
    local_path = clone_repo(url, **clone_kwargs)  # type: ignore[arg-type]
    return scan_filesystem(local_path)
