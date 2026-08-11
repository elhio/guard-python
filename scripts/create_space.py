#!/usr/bin/env python3
"""
Create a space after showing the available predictors and tasks.

A space needs a predictor and optionally a set of tasks. This script walks through the
entire flow. It lists the predictors, lists the tasks for the chosen predictor, creates
the space, and prints the new space ID.

Only GUARD_API_KEY is required. Everything else resolves through the standard client
precedence using arguments, the environment, or a `.env` file.

Example:
    ```bash
    # look around first without creating anything
    uv run python scripts/create_space.py --list-only

    # create a personal space
    uv run python scripts/create_space.py --name "Mine" --user-id <uuid> --public

    # create a space for an organization
    uv run python scripts/create_space.py --name "My Space" --organization-id <uuid>
    ```
"""

from __future__ import annotations

import argparse
import sys

from guard_client import GuardClient, GuardError


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--name", help="space name, 3-50 characters")
    parser.add_argument("--description", help="optional, up to 2000 characters")
    parser.add_argument(
        "--predictor-id", help="defaults to the first available predictor"
    )

    owner = parser.add_mutually_exclusive_group()
    owner.add_argument("--user-id", help="create a personal space")
    owner.add_argument("--organization-id", help="create an organization space")

    parser.add_argument("--public", action="store_true", help="make the space public")
    parser.add_argument(
        "--all-tasks",
        action="store_true",
        help="enable every task the predictor supports (default: none)",
    )
    parser.add_argument(
        "--list-only",
        action="store_true",
        help="print predictors and tasks, then exit without creating anything",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    try:
        with GuardClient() as client:
            print(f"Connected to {client.base_url}\n")

            predictors = client.predictors.list()
            if not predictors:
                print("No predictors available to this key.", file=sys.stderr)
                return 1

            print("Predictors:")
            for predictor in predictors:
                media = "/".join(m.value for m in predictor.supported_media) or "-"
                print(f"  {predictor.id}  {predictor.name:28} media={media}")

            predictor_id = args.predictor_id or str(predictors[0].id)
            tasks = client.tasks.list(predictor_id=predictor_id)

            print(f"\nTasks for predictor {predictor_id}:")
            for task in tasks:
                print(f"  {task.id}  {task.name}")
            if not tasks:
                print("  (none)")

            if args.list_only:
                print("\n--list-only: nothing was created.")
                return 0

            if not args.name:
                print("\nPass --name to create a space.", file=sys.stderr)
                return 1

            task_ids = [t.id for t in tasks] if args.all_tasks else None
            print(f"\nCreating space {args.name!r}...")

            space = client.spaces.create(
                name=args.name,
                predictor_id=predictor_id,
                description=args.description,
                is_public=args.public,
                user_id=args.user_id,
                organization_id=args.organization_id,
                enabled_task_ids=task_ids,
            )

            print(f"\nCreated: {space.id}")
            print(f"  name    {space.name}")
            print(f"  public  {space.is_public}")
            print(f"  tasks   {', '.join(space.enabled_task_names) or '-'}")
            print(f"\nPut this in .env as GUARD_SPACE_ID={space.id}")
    except GuardError as exc:
        print(f"\nFAILED: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
