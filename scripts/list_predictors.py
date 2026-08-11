#!/usr/bin/env python3
"""
Print the predictors available to your API key along with their IDs.

A predictor is the model that powers a space. The `spaces.create` function requires a
predictor ID, making this script useful for finding one. Only the `GUARD_API_KEY` is
required. Everything else resolves through the standard client precedence using
arguments, the environment, or a `.env` file.

The `--task-id` argument may be repeated. It narrows the results to predictors
supporting all of the given tasks. Run `scripts/list_tasks.py` to find those IDs.

Example:
    ```bash
    uv run python scripts/list_predictors.py
    uv run python scripts/list_predictors.py --sort-by created_at --sort-order desc
    uv run python scripts/list_predictors.py --task-id <uuid> --task-id <uuid>
    ```
"""

from __future__ import annotations

import argparse
import sys

from guard_client import GuardClient, GuardError


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    owner = parser.add_mutually_exclusive_group()
    owner.add_argument("--user-id", help="only predictors available to this user")
    owner.add_argument(
        "--organization-id", help="only predictors available to this organization"
    )

    parser.add_argument(
        "--task-id",
        action="append",
        metavar="UUID",
        help="only predictors supporting this task; repeatable",
    )
    parser.add_argument(
        "--sort-by", choices=["name", "created_at"], help="server default: name"
    )
    parser.add_argument(
        "--sort-order", choices=["asc", "desc"], help="server default: asc"
    )
    parser.add_argument(
        "--limit", type=int, help="stop after this many predictors (default: all)"
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    filters = {
        "user_id": args.user_id,
        "organization_id": args.organization_id,
        "supported_task_ids": args.task_id,
        "sort_by": args.sort_by,
        "sort_order": args.sort_order,
    }

    try:
        with GuardClient() as client:
            print(f"Predictors on {client.base_url}\n")

            rows = []
            for predictor in client.predictors.iter_all(**filters):
                rows.append(predictor)
                if args.limit is not None and len(rows) >= args.limit:
                    break

            if not rows:
                print("No predictors matched.")
                return 0

            width = max(len(p.name) for p in rows)
            for predictor in rows:
                media = "/".join(m.value for m in predictor.supported_media) or "-"
                print(
                    f"{predictor.id}  {predictor.name:{width}}  "
                    f"x{predictor.token_multiplier} tokens  media={media}"
                )

            print(f"\n{len(rows)} predictor(s).")
            print("Pass one of the ids above as predictor_id when creating a space.")
    except GuardError as exc:
        print(f"FAILED: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
