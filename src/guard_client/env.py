"""
Resolves settings across explicit arguments, the environment, and a `.env` file.

Every `GuardClient` setting can come from any of three places. Precedence resolves in
this order, highest first: 1. Explicit argument. 2. Real environment variable. 3. `.env`
file. 4. Built-in default.

Real environment variables deliberately override the `.env` file. A stale local `.env`
file must never shadow a secret injected by a CI pipeline or a container runtime.

| Variable                 | Argument           | Default                             |
| ------------------------ | ------------------ | ------------------------------------|
| `GUARD_API_KEY`          | `api_key`          | required for every cloud request    |
| `GUARD_SPACE_ID`         | `space_id`         | required for cloud                  |
| `GUARD_ORGANIZATION_ID`  | `organization_id`  | default owner for spaces or runners |
| `GUARD_BASE_URL`         | `base_url`         | `https://api.elhio.com`             |
| `GUARD_ENGINE`           | `engine`           | `cloud`                             |
| `GUARD_LOCALE`           | `locale`           | `en`                                |
| `GUARD_TIMEOUT`          | `timeout`          | `30.0`                              |
| `GUARD_MAX_RETRIES`      | `max_retries`      | `3`                                 |
| `GUARD_LOCAL_MODEL_PATH` | `local_model_path` | unset                               |
| `GUARD_ENV_FILE`         | `env_file`         | `.env`                              |

Copy `.env.example` to `.env` and fill it in. The `.env` file is git-ignored so secrets
stay on your machine.

Examples:
    ```python
    from guard_client import GuardClient

    with GuardClient() as client:
        # The .env file supplies the key and space.
        result = client.analyze("photo.jpg")

    GuardClient(env_file=None)  # doctest: +SKIP
    GuardClient(env_file=".env.staging")  # doctest: +SKIP
    ```

Note:
    Reading a `.env` file never writes to `os.environ`. Values are held in a plain dict
    on the `EnvSource`. Because of this, constructing a client cannot surprise anything
    else running in the same process.

Tip:
    Reading a `.env` file never writes to `os.environ`. Values are held in a plain dict
    on the `EnvSource`. Because of this, constructing a client cannot surprise anything
    else running in the same process.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Callable, Dict, Optional, Tuple, TypeVar, Union

from dotenv import dotenv_values, find_dotenv

from .exceptions import GuardError

__all__ = ["DEFAULT_ENV_FILE", "ENV_FILE_VAR", "EnvSource", "read_env_file"]

#: The file looked for when no path is explicitly given.
DEFAULT_ENV_FILE = ".env"

#: Selects which env file to read. This is resolved from the real environment only. It
#: names the file so it cannot come from inside the file itself.
ENV_FILE_VAR = "GUARD_ENV_FILE"

#: A generic type variable used for casting environment values.
T = TypeVar("T")

#: A generic type variable used for casting environment values.
EnvFile = Optional[Union[str, "os.PathLike[str]"]]


def read_env_file(env_file: EnvFile = DEFAULT_ENV_FILE) -> Dict[str, str]:
    """
    Parse an env file into a dict without touching `os.environ`.

    Args:
        env_file: Path to the file. When left at the default, the file is discovered by
            walking up from the current directory. Pass `None` to skip reading entirely
            and get an empty dict.

    Returns:
        The key/value pairs found in the file. Entries with no value are dropped. This
        ensures a bare `GUARD_API_KEY=` falls through to the next layer instead of
        yielding an empty string.

    Raises:
        GuardError: If an explicitly named file does not exist. A discovered default
            `.env` that is simply absent is not an error.
    """
    if env_file is None:
        return {}

    if str(env_file) != DEFAULT_ENV_FILE:
        # named explicitly: a missing file is a typo, not a valid state
        path = Path(os.fspath(env_file))
        if not path.is_file():
            raise GuardError(f"env file not found: {path}")
        resolved = str(path)
    else:
        # walk up from the cwd so the file is still found from a subdirectory
        resolved = find_dotenv(DEFAULT_ENV_FILE, usecwd=True)
        if not resolved:
            # no .env at all is perfectly normal in production
            return {}

    return {key: value for key, value in dotenv_values(resolved).items() if value}


class EnvSource:
    """
    Resolves individual settings against one loaded `.env` file.

    The file is read once on construction. The resulting dict is reused for every
    subsequent lookup.
    """

    def __init__(self, env_file: EnvFile = DEFAULT_ENV_FILE) -> None:
        """
        Read the env file once, making it ready for repeated lookups.

        Args:
            env_file: Which file to read. Passing `None` disables file reading entirely.
                The default is discovered by walking up from the current directory.

        Raises:
            GuardError: An explicitly named file does not exist.
        """
        # GUARD_ENV_FILE can redirect to another file, but only from the real
        # environment. A file cannot nominate itself.
        if env_file is not None and str(env_file) == DEFAULT_ENV_FILE:
            env_file = os.environ.get(ENV_FILE_VAR) or DEFAULT_ENV_FILE
        self._env_file = env_file
        self._values = read_env_file(env_file)

    @property
    def values(self) -> Dict[str, str]:
        """
        The parsed file contents.

        Returns:
            A copy of the key/value pairs. This ensures mutating it cannot corrupt the
            source. This returns an empty dictionary when no file was read.
        """
        return dict(self._values)

    def _lookup(self, name: str) -> Tuple[Optional[str], str]:
        """
        Find a variable in the environment, and then check the file.

        Args:
            name: The variable to look for.

        Returns:
            The raw value and a human-readable source for error messages. Returns
            `(None, "")` when neither layer has it.

        Note:
            An empty string counts as unset. A bare `GUARD_API_KEY=` falls through
            rather than producing an empty bearer token.
        """
        from_env = os.environ.get(name)
        if from_env:
            return from_env, "the environment"
        from_file = self._values.get(name)
        if from_file:
            return from_file, str(self._env_file or DEFAULT_ENV_FILE)
        return None, ""

    def get(
        self,
        name: str,
        explicit: Optional[T] = None,
        *,
        default: Optional[T] = None,
        cast: Optional[Callable[[str], T]] = None,
    ) -> Optional[T]:
        """
        Resolve one setting.

        Args:
            name: The `GUARD_*` variable to look for.
            explicit: The value passed to the constructor. `None` means it was not
                given, which is why every env-backed parameter defaults to `None`.
            default: Used when neither the environment nor the file supplies a value.
            cast: Converts the raw string. Omit this for plain strings.

        Returns:
            The resolved setting. This will be the explicit value if provided, the
            value from the environment or `.env` file (cast to the target type if
            requested), or the default value if nothing was found.

        Raises:
            GuardError: If `cast` rejects the value, naming the variable and its source.
        """
        if explicit is not None:
            return explicit

        raw, source = self._lookup(name)
        if raw is None:
            return default
        if cast is None:
            return raw  # type: ignore[return-value]

        try:
            return cast(raw)
        except (TypeError, ValueError) as exc:
            expected = getattr(cast, "__name__", "value")
            raise GuardError(
                f"{name}={raw!r} (from {source}) is not a valid {expected}: {exc}"
            ) from exc

    def get_optional(self, name: str, explicit: Optional[str] = None) -> Optional[str]:
        """
        Resolve a string setting that has no default.

        This is deliberately non-generic. `get` cannot infer a useful type parameter for
        a union-typed argument such as `space_id` and widens it to `object`.

        Args:
            name: The `GUARD_*` variable to look for.
            explicit: The value passed to the constructor, or `None` if omitted.

        Returns:
            The resolved string value, or `None` if it was not found in the environment
            or file and no explicit value was provided.
        """
        if explicit is not None:
            return explicit
        raw, _ = self._lookup(name)
        return raw

    def get_str(self, name: str, explicit: Optional[str], default: str) -> str:
        """
        Resolve a string setting that has a default.

        Args:
            name: The `GUARD_*` variable to look for.
            explicit: The value passed to the constructor, or `None` if omitted.
            default: Used when no layer supplies a value.

        Returns:
            The resolved string, which is never `None`.
        """
        value = self.get(name, explicit, default=default)
        return default if value is None else value

    def get_float(self, name: str, explicit: Optional[float], default: float) -> float:
        """
        Resolve a float setting that has a default.

        Args:
            name: The `GUARD_*` variable to look for.
            explicit: The value passed to the constructor, or `None` if omitted.
            default: Used when no layer supplies a value.

        Returns:
            The resolved number, which is never `None`.

        Raises:
            GuardError: If the value could not be parsed as a number.
        """
        value = self.get(name, explicit, default=default, cast=float)
        return default if value is None else value

    def get_int(self, name: str, explicit: Optional[int], default: int) -> int:
        """
        Resolve an integer setting that has a default.

        Args:
            name: The `GUARD_*` variable to look for.
            explicit: The value passed to the constructor, or `None` if omitted.
            default: Used when no layer supplies a value.

        Returns:
            The resolved integer, which is never `None`.

        Raises:
            GuardError: If the value could not be parsed as an integer.
        """
        value = self.get(name, explicit, default=default, cast=int)
        return default if value is None else value
