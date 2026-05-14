"""One-time Todoist setup script.

Todoist uses a long-lived personal API token (no OAuth, no refresh).
The token comes from your Todoist account UI:

  https://app.todoist.com/app/settings/integrations/developer

This script just looks up the project ID for the Voice Inbox
project and (optionally) writes the token + project ID to Doppler,
so you don't have to copy them around by hand.

Run with the API token in env:

    TODOIST_API_TOKEN=<token> \
      uv run python -m notes_ingest_cog.voicenotes.scripts.setup_todoist

Or, if you've already saved the token in Doppler:

    doppler run -- uv run python -m notes_ingest_cog.voicenotes.scripts.setup_todoist

The script reads ``TODOIST_API_TOKEN`` directly from ``os.environ``
rather than going through ``notes_ingest_cog.voicenotes.config.settings``: at
bootstrap time ``TODOIST_INBOX_PROJECT_ID`` doesn't exist yet, so
constructing the full ``Settings`` model would fail on that
required field.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from typing import Any

import httpx

# Default project name to look up. Override with --project-name.
DEFAULT_PROJECT_NAME = "Voice Inbox"

# Todoist's unified v1 API (the previous /rest/v2/ endpoints
# return HTTP 410 as of 2025).
_PROJECTS_URL = "https://api.todoist.com/api/v1/projects"

_HTTP_TIMEOUT_SEC = 15.0
_BANNER = "=" * 72
_RULE = "-" * 72


def _find_project_id(
    *, api_token: str, project_name: str
) -> tuple[str | None, list[dict[str, Any]]]:
    """Find the project ID for ``project_name`` via GET /api/v1/projects.

    Returns ``(project_id, all_projects)``. ``project_id`` is None if
    no project matches; the full project list is returned either way
    so the caller can show the operator what names exist.
    """
    response = httpx.get(
        _PROJECTS_URL,
        headers={"Authorization": f"Bearer {api_token}"},
        timeout=_HTTP_TIMEOUT_SEC,
    )
    if response.status_code in (401, 403):
        raise RuntimeError(
            f"Todoist rejected the API token ({response.status_code}). "
            "Confirm the token is correct (Settings → Integrations → "
            "Developer in the Todoist app) and re-run."
        )
    if response.status_code != 200:
        raise RuntimeError(
            f"GET /projects failed: HTTP {response.status_code}\n"
            f"Response body: {response.text}"
        )
    body = response.json()
    # Todoist v1 wraps list results as {"results": [...], "next_cursor": ...}.
    # Older deployments may return a bare list — accept either.
    if isinstance(body, dict) and isinstance(body.get("results"), list):
        raw = body["results"]
    elif isinstance(body, list):
        raw = body
    else:
        raise RuntimeError(f"Unexpected /projects response shape: {body!r}")
    projects: list[dict[str, Any]] = [p for p in raw if isinstance(p, dict)]
    for p in projects:
        if p.get("name") == project_name:
            pid = p.get("id")
            if isinstance(pid, str):
                return pid, projects
    return None, projects


def _save_to_doppler(values: dict[str, str]) -> bool:
    """Write each ``NAME=VALUE`` pair via ``doppler secrets set``.

    Returns True iff every set succeeded. No-ops with a warning if
    the doppler CLI isn't on PATH.
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
            "One-time Todoist setup. Looks up the inbox project ID "
            "by name and (optionally) writes the values to Doppler."
        )
    )
    parser.add_argument(
        "--project-name",
        default=DEFAULT_PROJECT_NAME,
        help=(
            "Name of the Todoist project to use as the voice inbox. "
            f"Default: {DEFAULT_PROJECT_NAME!r}"
        ),
    )
    parser.add_argument(
        "--save-to-doppler",
        action="store_true",
        help=(
            "Also run `doppler secrets set` for the API token + "
            "project ID. Skipped with a warning if doppler isn't "
            "installed."
        ),
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """CLI entry point. Returns a process exit code (0 = success)."""
    args = _parse_args(argv)

    api_token = os.environ.get("TODOIST_API_TOKEN")
    if not api_token:
        print(
            "ERROR: TODOIST_API_TOKEN must be set in the environment.\n"
            "Get a token from Settings → Integrations → Developer in "
            "the Todoist app, then run:\n"
            "  TODOIST_API_TOKEN=<token> uv run python -m "
            "notes_ingest_cog.voicenotes.scripts.setup_todoist",
            file=sys.stderr,
        )
        return 2

    print()
    print(_BANNER)
    print("Todoist setup")
    print(_BANNER)
    print(f"Project name: {args.project_name!r}")
    print()
    print(f"Looking up the project ID for {args.project_name!r}...")
    project_id, all_projects = _find_project_id(
        api_token=api_token, project_name=args.project_name
    )
    if project_id is None:
        names = sorted(str(p.get("name")) for p in all_projects if p.get("name"))
        existing = "\n".join(f"  - {n}" for n in names) if names else "  (none)"
        print(
            f"\nWARNING: no project named {args.project_name!r} found.\n"
            f"Existing projects:\n{existing}\n\n"
            "Create the project in Todoist first, or pass "
            "--project-name <existing>.",
            file=sys.stderr,
        )
        return 1

    secrets_to_save: dict[str, str] = {
        "TODOIST_API_TOKEN": api_token,
        "TODOIST_INBOX_PROJECT_ID": project_id,
    }

    print()
    _emit_value("TODOIST_API_TOKEN:", api_token)
    print()
    _emit_value("TODOIST_INBOX_PROJECT_ID:", project_id)

    if args.save_to_doppler:
        print("\nSaving to Doppler...")
        ok = _save_to_doppler(secrets_to_save)
        if not ok:
            print(
                "\nOne or more Doppler writes failed. Re-run "
                "`doppler secrets set` manually with the values above.",
                file=sys.stderr,
            )
            return 1
    else:
        print()
        print("To save to Doppler, run:")
        for name, value in secrets_to_save.items():
            print(f"  doppler secrets set {name}={value}")

    print()
    print("Done.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
