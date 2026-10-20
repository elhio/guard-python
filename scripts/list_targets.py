#!/usr/bin/env python3
"""
Print the detection targets available to your API key along with their IDs.

A target is one detection a space can run. The `spaces.create` function takes these IDs
as `enabled_target_ids`, making this script useful for finding them. Only the
`GUARD_API_KEY` is required. Everything else resolves through the standard client
precedence using arguments, the environment, or a `.env` file.

Names, descriptions, and reaction labels are rendered by the server in the request
locale, so using `--locale de` changes what you see. The reaction keys are the valid
`key_value` choices for `reactions.create`.

Example:
    ```bash
    uv run python scripts/list_targets.py
    uv run python scripts/list_targets.py --predictor-id <uuid>
    uv run python scripts/list_targets.py --locale de --sort-by created_at
    ```
"""

from __future__ import annotations

import argparse
import sys

from guard_client import GuardClient, GuardError


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    owner = parser.add_mutually_exclusive_group()
    owner.add_argument("--user-id", help="only targets available to this user")
    owner.add_argument(
        "--organization-id", help="only targets available to this organization"
    )

    parser.add_argument("--predictor-id", help="only targets this predictor supports")
    parser.add_argument(
        "--sort-by", choices=["name", "created_at"], help="server default: name"
    )
    parser.add_argument(
        "--sort-order", choices=["asc", "desc"], help="server default: asc"
    )
    parser.add_argument("--locale", help="language of the labels, e.g. en or de")
    parser.add_argument(
        "--limit", type=int, help="stop after this many targets (default: all)"
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    filters = {
        "user_id": args.user_id,
        "organization_id": args.organization_id,
        "predictor_id": args.predictor_id,
        "sort_by": args.sort_by,
        "sort_order": args.sort_order,
    }

    try:
        # locale=None keeps the usual precedence: GUARD_LOCALE, then `.env`, then "en"
        with GuardClient(locale=args.locale) as client:
            print(f"Targets on {client.base_url}\n")

            rows = []
            for target in client.targets.iter_all(**filters):
                rows.append(target)
                if args.limit is not None and len(rows) >= args.limit:
                    break

            if not rows:
                print("No targets matched.")
                return 0

            width = max(len(t.name) for t in rows)
            for target in rows:
                reactions = (
                    ", ".join(f"{k}: {v}" for k, v in sorted(target.reactions.items()))
                    or "-"
                )
                print(f"{target.id}  {target.name:{width}}  reactions={{{reactions}}}")

            print(f"\n{len(rows)} target(s).")
            print("Pass the ids above as enabled_target_ids when creating a space.")
    except GuardError as exc:
        print(f"FAILED: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
