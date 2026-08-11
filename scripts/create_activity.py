#!/usr/bin/env python3
"""
Create one activity after showing the space and its estimated cost.

This script runs the complete detection lifecycle through `analyze()`. It creates the
activity, uploads the media, confirms it, polls until completion, and prints the final
scores. Be aware that running this against a live API will spend real tokens. You can
pass `--list-only` to see the cost estimate without creating anything, or use
`--engine local` to run the detection on your device for free.

Only `GUARD_API_KEY` and a space ID are required. Everything else resolves through the
standard client precedence using arguments, the environment, or a `.env` file. The
media file is taken from the first argument or the `GUARD_MEDIA` environment variable.

Example:
    ```bash
    # preview the cost without creating anything
    uv run python scripts/create_activity.py photo.jpg --list-only

    # run the activity against the cloud API
    uv run python scripts/create_activity.py photo.jpg
    uv run python scripts/create_activity.py photo.jpg --space-id <uuid>

    # run on-device instead, which requires no space and consumes no tokens
    uv run python scripts/create_activity.py photo.jpg --engine local
    ```
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Optional

from guard_client import GuardClient, GuardError, probe_media, read_env_file


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("media", nargs="?", help="media file; defaults to GUARD_MEDIA")
    parser.add_argument("--space-id", help="the space to run in; or GUARD_SPACE_ID")
    parser.add_argument("--user-id", help="create a user-owned activity")
    parser.add_argument("--account-id", help="create a service-account-owned activity")
    parser.add_argument(
        "--engine", choices=["cloud", "local"], help="client default: cloud"
    )
    parser.add_argument(
        "--timeout", type=float, help="seconds to wait for processing to finish"
    )
    parser.add_argument(
        "--list-only",
        action="store_true",
        help="print the space and the estimate, then exit without creating anything",
    )
    return parser.parse_args()


def from_env(name: str) -> Optional[str]:
    """Read one setting the way the client does: real environment first, then .env."""
    return os.environ.get(name) or read_env_file().get(name)


def media_path(argument: Optional[str]) -> Path:
    """The one setting the client does not resolve for us."""
    media = argument or from_env("GUARD_MEDIA")
    if not media:
        sys.exit("Pass a media file path as the first argument or set GUARD_MEDIA")

    path = Path(media)
    if not path.is_file():
        sys.exit(f"No such file: {path}")
    return path


def print_media(path: Path) -> None:
    """Show what the probe read out of the file headers."""
    info = probe_media(path)
    print(f"Media: {path.name}")
    print(f"  type    {info.media_type.value}")
    print(f"  size    {path.stat().st_size} bytes")
    print(f"  pixels  {info.width}x{info.height}")
    print(f"  frames  {info.frames} ({info.duration_seconds:.1f}s)")


def print_space(client: GuardClient, space_id: str, media: Path) -> None:
    """Show the space this will run in, and what it is expected to cost."""
    space = client.spaces.get(space_id)
    tasks = ", ".join(task.name for task in space.enabled_tasks) or "-"

    print(f"\nSpace: {space.name}")
    print(f"  id         {space.id}")
    print(f"  predictor  {space.predictor_name} (x{space.predictor_multiplier})")
    print(f"  max media  {space.max_media_size or '-'} bytes")
    print(f"  tasks      {tasks}")

    print(f"\nEstimated cost: {client.estimate_tokens(media, space_id=space_id)}")
    print("  An estimate: the API reserves the minimum, payed_tokens is final.")


def main() -> int:
    args = parse_args()
    media = media_path(args.media)
    local = args.engine == "local"

    space_id = args.space_id or from_env("GUARD_SPACE_ID")
    if not local and not space_id:
        sys.exit(
            "Pass --space-id or set GUARD_SPACE_ID (scripts/list_spaces.py finds one)"
        )

    try:
        with GuardClient(space_id=space_id, engine=args.engine) as client:
            print(f"Connected to {client.base_url}\n")

            print_media(media)
            if local:
                print("\nEngine: local. No space, no network, no tokens.")
            else:
                print_space(client, str(space_id), media)

            if args.list_only:
                print("\n--list-only: nothing was created.")
                return 0

            print(f"\nAnalyzing {media.name}...")
            result = client.analyze(
                media,
                user_id=args.user_id,
                account_id=args.account_id,
                **({} if args.timeout is None else {"timeout": args.timeout}),
            )

            print(f"\nDone: engine={result.engine.value}")
            print(f"  activity  {result.activity_id or '-'}")
            for item in result.results:
                print(f"  - {item.label}: {item.score}/100")
            if not result.results:
                print("  (no results returned)")
            print(f"  max_score {result.max_score}")
    except GuardError as exc:
        # Covers a missing API key or space id too, which the client reports itself.
        print(f"\nFAILED: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
