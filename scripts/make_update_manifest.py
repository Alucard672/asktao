"""Generate a version.json update manifest for a packaged Windows build.

Usage:
    python scripts/make_update_manifest.py \
        --zip "dist/问道前台助手-win.zip" \
        --version-name 0.3.0 --version-code 3 \
        --url "https://ota.alucard.top/wendao/问道前台助手-0.3.0.zip" \
        --changelog "一键检测模拟器；软件内更新" \
        --output dist/version.json

Upload both the zip (at the exact --url location) and the generated
version.json to the OTA server. The sha256 in the manifest must match the
uploaded zip byte-for-byte, so regenerate the manifest whenever the zip
changes.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--zip", type=Path, required=True)
    parser.add_argument("--version-name", required=True)
    parser.add_argument("--version-code", type=int, required=True)
    parser.add_argument("--url", required=True)
    parser.add_argument("--changelog", default="")
    parser.add_argument("--force-update", action="store_true")
    parser.add_argument("--output", type=Path, default=Path("version.json"))
    args = parser.parse_args(argv)

    if not args.url.startswith("https://"):
        parser.error("--url must be an https URL")
    if args.version_code < 1:
        parser.error("--version-code must be positive")

    digest = hashlib.sha256(args.zip.read_bytes()).hexdigest()
    manifest = {
        "versionCode": args.version_code,
        "versionName": args.version_name,
        "forceUpdate": args.force_update,
        "zipUrl": args.url,
        "sha256": digest,
        "changelog": args.changelog,
    }
    args.output.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"wrote {args.output} (sha256={digest})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
