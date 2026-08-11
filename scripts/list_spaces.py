#!/usr/bin/env python3
"""
Print the spaces available to your API key along with their IDs.

Creating an activity requires a `space_id`. This script helps you find one to place in
your `.env` file as `GUARD_SPACE_ID`. Only the `GUARD_API_KEY` is required. Everything
else resolves through the standard client precedence using arguments, the environment,
or a `.env` file.

Using the `--default` flag steps outside your own spaces. It matches on the `is_default`
column, meaning it can return a public default space owned by someone else.

Example:
    ```bash
    uv run python scripts/list_spaces.py
    uv run python scripts/list_spaces.py --default
    uv run python scripts/list_spaces.py --public --sort-by name
    uv run python scripts/list_spaces.py --organization-id <uuid>
    ```
"""

from __future__ import annotations

import argparse
import sys

from guard_client import GuardClient, GuardError


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    visibility = parser.add_mutually_exclusive_group()
    visibility.add_argument("--public", action="store_true", help="only public spaces")
    visibility.add_argument(
        "--private", action="store_true", help="only non-public spaces"
    )

    default = parser.add_mutually_exclusive_group()
    default.add_argument("--default", action="store_true", help="only default spaces")
    default.add_argument(
        "--not-default", action="store_true", help="only non-default spaces"
    )

    owner = parser.add_mutually_exclusive_group()
    owner.add_argument("--user-id", help="only spaces owned by this user")
    owner.add_argument(
        "--organization-id", help="only spaces owned by this organization"
    )

    parser.add_argument("--predictor-id", help="only spaces using this predictor")
    parser.add_argument(
        "--sort-by", choices=["name", "created_at"], help="server default: created_at"
    )
    parser.add_argument(
        "--sort-order", choices=["asc", "desc"], help="server default: asc"
    )
    parser.add_argument(
        "--limit", type=int, help="stop after this many spaces (default: all)"
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    is_public = True if args.public else (False if args.private else None)
    is_default = True if args.default else (False if args.not_default else None)
    filters = {
        "user_id": args.user_id,
        "organization_id": args.organization_id,
        "predictor_id": args.predictor_id,
        "is_public": is_public,
        "is_default": is_default,
        "sort_by": args.sort_by,
        "sort_order": args.sort_order,
    }

    try:
        with GuardClient() as client:
            print(f"Spaces on {client.base_url}\n")

            rows = []
            for space in client.spaces.iter_all(**filters):
                rows.append(space)
                if args.limit is not None and len(rows) >= args.limit:
                    break

            if not rows:
                print("No spaces matched.")
                return 0

            width = max(len(s.name) for s in rows)
            for space in rows:
                flags = []
                if space.is_default:
                    flags.append("default")
                flags.append("public" if space.is_public else "private")
                media = "/".join(m.value for m in space.enabled_media) or "-"
                print(
                    f"{space.id}  {space.name:{width}}  "
                    f"[{', '.join(flags)}]  media={media}  "
                    f"owner={space.owner_name or '-'}"
                )

            print(f"\n{len(rows)} space(s).")
            print("Put one of the ids above in .env as GUARD_SPACE_ID.")
    except GuardError as exc:
        print(f"FAILED: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
