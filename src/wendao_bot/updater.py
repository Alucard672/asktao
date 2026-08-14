"""Self-update support: manifest check, verified download, staged replace.

Updates are never applied silently: the caller must confirm before download,
the archive hash must match the manifest, and the running program is only
replaced after it has exited.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable
from urllib.request import urlopen

VERSION_CODE = 3
DEFAULT_MANIFEST_URL = "https://ota.alucard.top/wendao/version.json"
_TIMEOUT_SECONDS = 10.0
_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")


class UpdateError(RuntimeError):
    """The update manifest or archive is unavailable or unsafe."""


@dataclass(frozen=True)
class UpdateInfo:
    version_code: int
    version_name: str
    force_update: bool
    zip_url: str
    sha256: str
    changelog: str


def manifest_url() -> str:
    return os.environ.get("WENDAO_UPDATE_URL", DEFAULT_MANIFEST_URL)


def _require_https(url: str, name: str) -> str:
    if not isinstance(url, str) or not url.startswith("https://"):
        raise UpdateError(f"{name} must be an https URL")
    return url


def parse_manifest(raw: object) -> UpdateInfo:
    if not isinstance(raw, dict):
        raise UpdateError("manifest must be a JSON object")
    code = raw.get("versionCode")
    if isinstance(code, bool) or not isinstance(code, int) or code < 1:
        raise UpdateError("versionCode must be a positive integer")
    name = raw.get("versionName")
    if not isinstance(name, str) or not name.strip():
        raise UpdateError("versionName must be a non-empty string")
    zip_url = raw.get("zipUrl") or raw.get("downloadUrl")
    _require_https(zip_url, "zipUrl")
    digest = raw.get("sha256")
    if not isinstance(digest, str) or not _SHA256_PATTERN.fullmatch(digest.lower()):
        raise UpdateError("sha256 must be a 64-character hex digest")
    changelog = raw.get("changelog")
    if not isinstance(changelog, str):
        changelog = ""
    return UpdateInfo(
        version_code=code,
        version_name=name,
        force_update=bool(raw.get("forceUpdate") or raw.get("force")),
        zip_url=zip_url,
        sha256=digest.lower(),
        changelog=changelog,
    )


def _fetch_bytes(url: str, reporthook: Callable[[int, int], None] | None = None) -> bytes:
    _require_https(url, "url")
    with urlopen(url, timeout=_TIMEOUT_SECONDS) as response:
        total = int(response.headers.get("Content-Length") or 0)
        chunks = []
        received = 0
        while True:
            chunk = response.read(65536)
            if not chunk:
                break
            chunks.append(chunk)
            received += len(chunk)
            if reporthook is not None:
                reporthook(received, total)
        return b"".join(chunks)


def check_update(
    fetcher: Callable[[str], bytes] | None = None,
    current_code: int = VERSION_CODE,
) -> UpdateInfo | None:
    fetch = fetcher if fetcher is not None else _fetch_bytes
    try:
        payload = fetch(manifest_url())
        info = parse_manifest(json.loads(payload.decode("utf-8")))
    except UpdateError:
        raise
    except Exception as exc:
        raise UpdateError(f"could not read update manifest: {exc}") from exc
    if info.version_code <= current_code:
        return None
    return info


def download_and_verify(
    info: UpdateInfo,
    destination_dir: Path,
    fetcher: Callable[..., bytes] | None = None,
    reporthook: Callable[[int, int], None] | None = None,
) -> Path:
    fetch = fetcher if fetcher is not None else _fetch_bytes
    try:
        payload = fetch(info.zip_url, reporthook)
    except UpdateError:
        raise
    except Exception as exc:
        raise UpdateError(f"download failed: {exc}") from exc
    digest = hashlib.sha256(payload).hexdigest()
    if digest != info.sha256:
        raise UpdateError("downloaded archive hash does not match the manifest")
    destination_dir = Path(destination_dir)
    destination_dir.mkdir(parents=True, exist_ok=True)
    destination = destination_dir / f"wendao-update-{info.version_name}.zip"
    destination.write_bytes(payload)
    return destination


def install_directory() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    raise UpdateError("self-update is only available in the packaged app")


def build_update_script(
    zip_path: Path, install_dir: Path, exe_name: str, pid: int
) -> str:
    stage = install_dir.parent / "_wendao_update_stage"
    return (
        f"$ErrorActionPreference = 'Stop'\n"
        f"Wait-Process -Id {int(pid)} -ErrorAction SilentlyContinue\n"
        f"Start-Sleep -Seconds 1\n"
        f"Remove-Item -Recurse -Force -ErrorAction SilentlyContinue '{stage}'\n"
        f"Expand-Archive -LiteralPath '{zip_path}' -DestinationPath '{stage}' -Force\n"
        f"$source = Get-ChildItem -LiteralPath '{stage}' -Directory | Select-Object -First 1\n"
        f"if (-not $source) {{ throw 'update archive is empty' }}\n"
        f"Copy-Item -Path (Join-Path $source.FullName '*') "
        f"-Destination '{install_dir}' -Recurse -Force\n"
        f"Remove-Item -Recurse -Force -ErrorAction SilentlyContinue '{stage}'\n"
        f"Start-Process -FilePath '{install_dir / exe_name}'\n"
    )


def stage_windows_update(zip_path: Path, install_dir: Path, exe_name: str) -> None:
    if sys.platform != "win32":
        raise UpdateError("self-update is only implemented for Windows")
    script = build_update_script(zip_path, install_dir, exe_name, os.getpid())
    descriptor, script_path = tempfile.mkstemp(prefix="wendao-update-", suffix=".ps1")
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(b"\xef\xbb\xbf" + script.encode("utf-8"))
    creation = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(
        subprocess, "CREATE_NEW_PROCESS_GROUP", 0
    )
    subprocess.Popen(
        [
            "powershell",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            script_path,
        ],
        creationflags=creation,
        close_fds=True,
    )
