# Contributing to Guard

Thank you for considering contributing to Guard! We welcome contributions from everyone, whether it’s fixing bugs, 
adding new features, sharpening the ergonomics, or improving documentation.

The following is a set of guidelines for contributing to this repository.

## The Contributor License Agreement (CLA)

Before we can merge your first Pull Request, you will need to sign our Contributor License Agreement.

Don't worry, the process is fully automated! When you open your first Pull Request, our CLA bot will automatically 
comment on it with instructions. You will simply need to reply to that comment to sign the agreement. You only have to 
do this once.

This repository is licensed under the **Apache-2.0**, and your contributions are published under those terms. The 
optional on-device engine it can talk to, `guard-local-detector`, uses the AGPL-3.0 license and lives in its own 
repository. This difference is exactly why the local engine is an opt-in extra rather than a standard dependency.

## Getting Started

Before you start writing code, make sure your development environment is set up properly.

0. Fork the repository to your own GitHub account.

1. Install prerequisites: [uv](https://docs.astral.sh/uv/) and Python 3.10 or newer. You do not need to install Python 
yourself — uv will fetch a suitable interpreter.

2. Clone your fork: `git clone https://github.com/YOUR_USERNAME/guard-python.git`

3. Sync the environment: `uv sync`

That last command creates a `.venv`, reads `uv.lock`, and installs the runtime and development dependencies exactly as 
they were locked. Nothing else is needed — the test suite is fully mocked, so there is no API key to obtain and no 
network access at any point.

Note that two things are deliberately left out of that default environment. The local extra is not installed by default 
because it relies on that separate optional engine. If you need it, see [Working on the local engine](#working-on-the-local-engine).
The `docs` group is also excluded since it is only used for building documentation.

## Branching Strategy

To keep the repository organized, please use descriptive branch names based on the type of work you are doing:

* **Features:** `feature/activity-pagination` or `feat/share-expiry-filters`
* **Bug Fixes:** `fix/presigned-upload-retry`
* **Documentation:** `docs/update-readme`

Always branch off of the `main` branch, and make sure your fork is up to date before starting new work.

## Development, Linting & Testing

Please ensure your changes pass all code quality checks and tests before opening a Pull Request.

### Code Quality & Types

Run the formatter, linter, and type-checker to ensure your code complies with our standards:

```bash
# format the code
uv run ruff format

# check for lint issues
uv run ruff check

# automatically fix lint issues where possible
uv run ruff check --fix

# check types (strict mode)
uv run mypy
```

Note that `ruff format` rewraps code but never touches comments or docstrings. If you still see an `E501` error, the 
long line is inside a comment or docstring and must be split manually.

### Running Tests

We use pytest. Please add or update tests whenever you introduce new features or fix bugs.

```bash
# run the whole suite
uv run pytest

# run one test with output
uv run pytest tests/test_local.py::test_analyze_returns_unified_result -v
```

The suite needs no fixture files: images and video clips are generated in `tests/conftest.py` at run time. If you add a 
new media format, add its builder there rather than committing a binary. Similarly, every API response comes from a 
builder function in that file, such as `create_response`, `detail_response`, `space_response`, and friends. These are 
served through respx against [https://api.test.invalid](https://api.test.invalid). If you add a new endpoint, please 
add its builder there instead of committing a fixture. Async tests need no marker since `asyncio_mode` is set to `auto`.

### Documentation

Every release publishes a JSON description of the public API built straight from the docstrings. The build runs on every 
pull request, so a missing docstring fails the PR instead of the release:

```bash
uv sync --group docs
uv run python scripts/build_docs.py --strict
```

The `scripts/build_docs.py` script is shared with the sibling `guard-local-python` repository. It takes no 
package-specific arguments by design and reads everything it needs out of `pyproject.toml`. Please keep any changes to 
this script synchronized across both repositories.

### Working on the Local Engine

The `local` engine routes through `src/guard_client/local.py` to `guard-local-detector`, the optional on-device engine. 
It is not installed by default, so you will need to install the extra when you need it.

The test suite drives a fake implementing the `LocalEngine` protocol, allowing everything to pass without the engine 
installed. However, changes to anything the engine interacts with (like `local.py`, the `LocalEngine` protocol, 
`_adapt`, and the exception mapping) should be checked against the real thing:

```bash
uv sync --extra local
uv run pytest tests/test_contract.py tests/test_local.py
```

The `tests/test_contract.py` file is the conformance suite the two packages share. A plain `uv run pytest` skips it 
silently when the extra is not installed. Because of this, a green full-suite run does not guarantee you exercised the 
local engine. If you explicitly name the file, the test runner will loudly warn you if `guard_local` is missing. When 
you are finished, running `uv sync` returns you to the default environment.

### Smoke Testing against a Live API

The test suite is fully mocked and never touches the network. To exercise the real lifecycle end-to-end, use the smoke 
script. **It runs against production by default and spends real tokens**. Each run creates two activities. You can point 
it elsewhere using the `GUARD_BASE_URL` environment variable.

```bash
export GUARD_API_KEY=...      # a token_raw from POST /api/v1/tokens/
export GUARD_SPACE_ID=...

uv run python scripts/smoke.py path/to/photo.jpg

# against a local dev server instead
GUARD_BASE_URL=http://localhost:8000 uv run python scripts/smoke.py path/to/photo.jpg
```

Credentials resolve the same way everywhere in this client: explicit arguments, followed by `GUARD_* `environment 
variables, and finally a `.env` file. You can simply run `cp .env.example .env` and fill in your details instead of 
exporting variables. The `.env` file is git-ignored and must stay that way. Please never put a real key in 
`.env.example`.

If you do not have a `space_id` yet, two more scripts can help:

```bash
# list the spaces your key can see, with their ids
uv run python scripts/list_spaces.py

# walk predictors -> tasks -> create a space (--list-only creates nothing)
uv run python scripts/create_space.py --list-only
```

## What to Watch Out For

This package is mostly a thin, well-typed shell around an HTTP API, but a few things in it are load-bearing in ways that 
are not obvious from the code.

**`import guard_client` must never import `guard_local`.** Because the local engine is optional, `local.py` imports it 
lazily inside the call. The `tests/test_local.py` file checks this in a subprocess. An in-process assertion would only 
report whatever the rest of the test session had already imported.

**Never make `guard-local-detector` a hard dependency.** It must remain an extra. Aside from the licensing differences 
mentioned earlier, making it a hard dependency would create a circular relationship since the engine has no need for 
this package.

**`tests/test_contract.py` is shared byte-identically with `guard-local-python`.** A diff between the two copies means 
the contract has changed. It is excluded from `ruff format` and carries its own per-file-ignores entry to preserve this. 
Please do not reformat it, and if you modify it, be sure to update both copies.

**Everything raised must subclass `GuardError`.** hat is the promise our `except` clauses rely on. The engine's own 
exceptions do not subclass it, so `local.py` translates each one before it escapes using `_map_local_error`. Anything 
that does not come from `guard_local` propagates untouched. This is intentional, as a bug in the engine should surface 
exactly as the bug it is.

**The task labels are a public API.** `_adapt` derives each local result's id from `uuid5(namespace, label)`. Renaming a 
label silently changes the ID that callers rely on. These labels also mirror the tasks the cloud API seeds, allowing 
users to test locally and then route to the cloud without changing their code.

**Engine scores must be floats between `0.0` and `1.0`.** The `_score_to_int` function rescales by value rather than by 
a declared scale, meaning an integer `1` would be read as full confidence rather than as near-zero.

**Docstrings have a house style, and it is enforced twice.** The summary goes on the line below the opening quotes and 
keeps that shape even when the body is one sentence (with `D200` and `D212` off, and `D213` on). Since Ruff cannot 
enforce this expanded form everywhere, `tests/test_docstyle.py` walks the package to verify it. Any public object with 
no docstring at all will also fail the strict documentation build.

## Submitting a Pull Request

When you are ready to submit your code, open a Pull Request (PR) against the main branch of the original repository.

Please include the following in your PR description:

* **The Problem:** What issue does this PR solve? (Link to an existing Issue if applicable).
* **The Solution:** A brief explanation of how you solved it.
* **Testing:** How did you test your changes? Mention which tests were added, and whether you ran the contract suite 
with the `[local]` extra.
* **Public API impact:** If anything in a module's `__all__` changed, say so. The generated documentation is published 
with every release, and every new public object needs a docstring that survives `--strict`.

Once submitted, a maintainer will review your code. We may request some changes before merging, but we will always be 
respectful and constructive!

## Reporting Bugs & Requesting Features

If you aren't writing code but found a bug or have a feature idea, please open an Issue!

* Provide as much detail as possible, including your Python version, operating system, and `guard_client.__version__`. 
The `guard-local-detector` version matters too, but only for `engine="local"` reports.
* For a failed request, include the full traceback, the status code, and the request id. Every `GuardAPIError` carries 
one as `.request_id`, taken from the server's `x-request-id` header — it is the single most useful thing in a report, 
because it lets us find your exact request in our logs.
* If media was flagged incorrectly, please provide feedback by verifying the result against one of our verification 
models (offered through API endpoints) and send a reaction for this particular result. 
