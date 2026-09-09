"""One-time Asana setup script.

Asana uses a long-lived personal access token (no OAuth, no refresh).
Mint one from your Asana account UI:

  My Settings → Apps → Manage Developer Apps → Personal Access Tokens

This script resolves the three gids the cog needs — workspace, project
and section — from their human names, and (optionally) writes them to
Doppler so you don't copy gids around by hand. Asana names gids as
opaque numeric strings; nothing about them is guessable, which is the
whole reason this script exists.

Run with the token in env:

    ASANA_ACCESS_TOKEN=<token> \
      uv run python -m transcription_cog.voicenotes.scripts.setup_asana

Or, if you've already saved the token in Doppler:

    doppler run -- uv run python -m transcription_cog.voicenotes.scripts.setup_asana

The script reads ``ASANA_ACCESS_TOKEN`` directly from ``os.environ``
rather than going through ``transcription_cog.voicenotes.config.settings``:
at bootstrap time the project and workspace gids don't exist yet, and
constructing the full ``Settings`` model to read one field it does have
would be the long way round.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from typing import Any

import httpx

# Defaults match the board this cog posts to. Override with the flags.
DEFAULT_WORKSPACE_NAME = "My workspace"
DEFAULT_PROJECT_NAME = "MiniAppPolis"
DEFAULT_SECTION_NAME = "Ideas"

_API_BASE = "https://app.asana.com/api/1.0"
_HTTP_TIMEOUT_SEC = 15.0
_BANNER = "=" * 72
_RULE = "-" * 72


def _get(path: str, *, api_token: str, params: dict[str, Any] | None = None) -> Any:
    """GET ``path`` and return the unwrapped ``data`` member."""
    response = httpx.get(
        f"{_API_BASE}{path}",
        headers={"Authorization": f"Bearer {api_token}"},
        params=params,
        timeout=_HTTP_TIMEOUT_SEC,
    )
    if response.status_code in (401, 403):
        raise RuntimeError(
            f"Asana rejected the access token ({response.status_code}). "
            "Confirm the token is correct (My Settings → Apps → Manage "
            "Developer Apps → Personal Access Tokens) and re-run."
        )
    if response.status_code != 200:
        raise RuntimeError(
            f"GET {path} failed: HTTP {response.status_code}\n"
            f"Response body: {response.text}"
        )
    body = response.json()
    if not isinstance(body, dict) or "data" not in body:
        raise RuntimeError(f"Unexpected {path} response shape: {body!r}")
    return body["data"]


def _find_named(records: Any, name: str, *, what: str) -> tuple[str | None, list[str]]:
    """Find the gid of the record called ``name``.

    Returns ``(gid, all_names)``. ``gid`` is None when nothing matches;
    the full name list comes back either way so the caller can show the
    operator what does exist, which is nearly always the fix.
    """
    names: list[str] = []
    match: str | None = None
    for record in records if isinstance(records, list) else []:
        if not isinstance(record, dict):
            continue
        record_name = record.get("name")
        record_gid = record.get("gid")
        if not isinstance(record_name, str) or not isinstance(record_gid, str):
            continue
        names.append(record_name)
        if match is None and record_name.strip().lower() == name.strip().lower():
            match = record_gid
    if match is None:
        print(
            f"\nWARNING: no {what} named {name!r} found. Existing:\n"
            + ("\n".join(f"  - {n}" for n in sorted(names)) or "  (none)"),
            file=sys.stderr,
        )
    return match, names


def _save_to_doppler(values: dict[str, str]) -> bool:
    """Write each ``NAME=VALUE`` pair via ``doppler secrets set``.

    Returns True iff every set succeeded. No-ops with a warning if the
    doppler CLI isn't on PATH.
    """
    if shutil.which("doppler") is None:
        print(
            "doppler CLI not found on PATH; skipping auto-save.\n"
            "Install with: brew install dopplerhq/cli/doppler",
            file=sys.stderr,
        )
        return False

    all_ok = True
    for name, value in values.items():
        result = subprocess.run(
            ["doppler", "secrets", "set", f"{name}={value}"],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            print(
                f"doppler secrets set {name} failed "
                f"(exit {result.returncode}):\n{result.stderr.strip()}",
                file=sys.stderr,
            )
            all_ok = False
        else:
            print(f"  saved {name} to Doppler")
    return all_ok


def _emit_value(label: str, value: str) -> None:
    """Print a labeled value with rules above and below."""
    print(_RULE)
    print(label)
    print(value)
    print(_RULE)


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "One-time Asana setup. Resolves the workspace, project and "
            "section gids by name and (optionally) writes them to Doppler."
        )
    )
    parser.add_argument("--workspace-name", default=DEFAULT_WORKSPACE_NAME)
    parser.add_argument("--project-name", default=DEFAULT_PROJECT_NAME)
    parser.add_argument(
        "--section-name",
        default=DEFAULT_SECTION_NAME,
        help=(
            "Board column new voice notes land in — the intake column, "
            f"where notes wait to be groomed. Default: {DEFAULT_SECTION_NAME!r}"
        ),
    )
    parser.add_argument(
        "--save-to-doppler",
        action="store_true",
        help=(
            "Also run `doppler secrets set` for the token and the resolved "
            "gids. Skipped with a warning if doppler isn't installed."
        ),
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """CLI entry point. Returns a process exit code (0 = success)."""
    args = _parse_args(argv)

    api_token = os.environ.get("ASANA_ACCESS_TOKEN")
    if not api_token:
        print(
            "ERROR: ASANA_ACCESS_TOKEN must be set in the environment.\n"
            "Mint a token at My Settings → Apps → Manage Developer Apps → "
            "Personal Access Tokens, then run:\n"
            "  ASANA_ACCESS_TOKEN=<token> uv run python -m "
            "transcription_cog.voicenotes.scripts.setup_asana",
            file=sys.stderr,
        )
        return 2

    print()
    print(_BANNER)
    print("Asana setup")
    print(_BANNER)
    print(f"Workspace: {args.workspace_name!r}")
    print(f"Project:   {args.project_name!r}")
    print(f"Section:   {args.section_name!r}")
    print()

    workspace_gid, _ = _find_named(
        _get("/workspaces", api_token=api_token, params={"opt_fields": "name"}),
        args.workspace_name,
        what="workspace",
    )
    if workspace_gid is None:
        return 1

    project_gid, _ = _find_named(
        _get(
            "/projects",
            api_token=api_token,
            params={"workspace": workspace_gid, "opt_fields": "name", "limit": 100},
        ),
        args.project_name,
        what="project",
    )
    if project_gid is None:
        return 1

    section_gid, _ = _find_named(
        _get(
            f"/projects/{project_gid}/sections",
            api_token=api_token,
            params={"opt_fields": "name", "limit": 100},
        ),
        args.section_name,
        what="section",
    )
    if section_gid is None:
        return 1

    secrets_to_save: dict[str, str] = {
        "ASANA_ACCESS_TOKEN": api_token,
        "ASANA_WORKSPACE_ID": workspace_gid,
        "ASANA_INBOX_PROJECT_ID": project_gid,
        "ASANA_INBOX_SECTION_ID": section_gid,
    }

    print()
    for name, value in secrets_to_save.items():
        _emit_value(f"{name}:", value)
        print()

    if args.save_to_doppler:
        print("Saving to Doppler...")
        if not _save_to_doppler(secrets_to_save):
            print(
                "\nOne or more Doppler writes failed. Re-run "
                "`doppler secrets set` manually with the values above.",
                file=sys.stderr,
            )
            return 1
    else:
        print("To save to Doppler, run:")
        for name, value in secrets_to_save.items():
            print(f"  doppler secrets set {name}={value}")

    print()
    print("Done.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
