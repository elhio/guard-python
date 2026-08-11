"""
Tests for settings resolution across arguments, os.environ, and a .env file.

The autouse `isolate_env` fixture in conftest.py clears every real `GUARD_*` variable
and chdirs into an empty temporary directory. Therefore, tests here write their own
`.env` file into the current working directory and never touch the repository.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from guard_client import (
    DEFAULT_BASE_URL,
    Engine,
    GuardClient,
    GuardError,
    read_env_file,
)
from guard_client.env import EnvSource

KEY = "key-from-somewhere"
SPACE = "11111111-1111-1111-1111-111111111111"


def write_env(**values: str) -> Path:
    """Write a .env file into the temporary current working directory."""
    path = Path(".env")
    path.write_text("\n".join(f"{k}={v}" for k, v in values.items()) + "\n")
    return path


def test_dotenv_supplies_settings():
    """Verify that settings are correctly loaded from the .env file."""
    write_env(
        GUARD_API_KEY=KEY, GUARD_SPACE_ID=SPACE, GUARD_BASE_URL="https://from-file"
    )

    client = GuardClient()

    assert client.base_url == "https://from-file"
    assert client._config.api_key == KEY
    assert str(client._space_id) == SPACE


def test_real_env_beats_dotenv(monkeypatch):
    """
    Ensure that real environment variables override values in the .env file.

    A stale .env file must never shadow a secret injected by a CI pipeline.
    """
    write_env(GUARD_API_KEY="from-file", GUARD_BASE_URL="https://from-file")
    monkeypatch.setenv("GUARD_API_KEY", "from-env")
    monkeypatch.setenv("GUARD_BASE_URL", "https://from-env")

    client = GuardClient(space_id=SPACE)

    assert client._config.api_key == "from-env"
    assert client.base_url == "https://from-env"


def test_explicit_argument_beats_everything(monkeypatch):
    """Ensure explicit constructor arguments override both the environment and .env."""
    write_env(GUARD_API_KEY="from-file", GUARD_BASE_URL="https://from-file")
    monkeypatch.setenv("GUARD_API_KEY", "from-env")
    monkeypatch.setenv("GUARD_BASE_URL", "https://from-env")

    client = GuardClient(
        api_key="explicit", space_id=SPACE, base_url="https://explicit"
    )

    assert client._config.api_key == "explicit"
    assert client.base_url == "https://explicit"


def test_defaults_apply_when_nothing_is_set():
    """Verify that the client applies correct defaults when no settings are provided."""
    write_env(GUARD_API_KEY=KEY, GUARD_SPACE_ID=SPACE)

    client = GuardClient()

    assert client.base_url == DEFAULT_BASE_URL
    assert client.engine is Engine.CLOUD
    assert client._config.locale == "en"
    assert client._config.timeout == 30.0
    assert client._config.max_retries == 3


def test_explicit_base_url_equal_to_default_is_not_overridden(monkeypatch):
    """
    Check that passing the default base_url explicitly prevents overrides from the
    environment.
    """
    monkeypatch.setenv("GUARD_BASE_URL", "http://localhost:8000")

    client = GuardClient(api_key=KEY, space_id=SPACE, base_url=DEFAULT_BASE_URL)

    assert client.base_url == DEFAULT_BASE_URL


def test_reading_dotenv_does_not_mutate_os_environ():
    """
    Ensure that reading the .env file does not pollute os.environ.

    This is the headline promise: constructing a client cannot surprise other libraries.
    """
    write_env(GUARD_API_KEY=KEY, GUARD_SPACE_ID=SPACE, GUARD_LOCALE="de")
    before = dict(os.environ)

    client = GuardClient()

    assert client._config.locale == "de"  # the file was genuinely read
    assert os.environ == before
    assert "GUARD_API_KEY" not in os.environ


def test_read_env_file_does_not_mutate_os_environ():
    """Verify that read_env_file keeps os.environ clean."""
    write_env(GUARD_API_KEY=KEY)

    values = read_env_file()

    assert values["GUARD_API_KEY"] == KEY
    assert "GUARD_API_KEY" not in os.environ


def test_env_file_none_disables_file_reading():
    """
    Check that passing env_file=None completely disables reading from the .env file.
    """
    write_env(GUARD_API_KEY=KEY, GUARD_SPACE_ID=SPACE)

    with pytest.raises(GuardError, match="API key is required"):
        GuardClient(env_file=None)


def test_env_file_none_still_honours_real_env(monkeypatch):
    """
    Verify that even with env_file=None, real environment variables are still honored.
    """
    write_env(GUARD_BASE_URL="https://from-file")
    monkeypatch.setenv("GUARD_API_KEY", KEY)

    client = GuardClient(space_id=SPACE, env_file=None)

    assert client._config.api_key == KEY
    assert client.base_url == DEFAULT_BASE_URL  # the file was ignored


def test_named_env_file_is_read():
    """Ensure the client reads from a specifically named .env file when requested."""
    Path("staging.env").write_text(
        f"GUARD_API_KEY={KEY}\nGUARD_BASE_URL=https://staging\n"
    )

    client = GuardClient(space_id=SPACE, env_file="staging.env")

    assert client.base_url == "https://staging"


def test_missing_named_env_file_raises():
    """
    Verify that providing an explicit env file path that does not exist raises an error.

    An explicitly named file that is absent is a typo, not a valid state.
    """
    with pytest.raises(GuardError, match="env file not found"):
        GuardClient(api_key=KEY, space_id=SPACE, env_file="nope.env")


def test_missing_default_env_file_is_silent(monkeypatch):
    """
    Ensure that the absence of a default .env file does not cause an error.

    Running without a .env file is normal in production.
    """
    monkeypatch.setenv("GUARD_API_KEY", KEY)

    client = GuardClient(space_id=SPACE)

    assert client.base_url == DEFAULT_BASE_URL


def test_guard_env_file_selects_the_file(monkeypatch):
    """
    Verify that the GUARD_ENV_FILE environment variable successfully redirects the .env
    path.
    """
    Path("other.env").write_text(f"GUARD_API_KEY={KEY}\nGUARD_BASE_URL=https://other\n")
    monkeypatch.setenv("GUARD_ENV_FILE", "other.env")

    client = GuardClient(space_id=SPACE)

    assert client.base_url == "https://other"


def test_discovery_walks_up_from_subdirectory(monkeypatch):
    """
    Ensure the default .env file is found even when the client is run from a nested
    directory.
    """
    write_env(GUARD_API_KEY=KEY, GUARD_SPACE_ID=SPACE, GUARD_BASE_URL="https://parent")
    nested = Path("a/b/c")
    nested.mkdir(parents=True)
    monkeypatch.chdir(nested)

    client = GuardClient()

    assert client.base_url == "https://parent"


def test_numeric_values_are_cast():
    """
    Check that string values from the environment are correctly cast to integers and
    floats.
    """
    write_env(
        GUARD_API_KEY=KEY,
        GUARD_SPACE_ID=SPACE,
        GUARD_TIMEOUT="12.5",
        GUARD_MAX_RETRIES="7",
    )

    client = GuardClient()

    assert client._config.timeout == 12.5
    assert client._config.max_retries == 7


@pytest.mark.parametrize(
    ("variable", "value"),
    [("GUARD_TIMEOUT", "abc"), ("GUARD_MAX_RETRIES", "3.5")],
)
def test_bad_numeric_value_raises_naming_the_variable(variable, value):
    """
    Ensure that invalid numeric formats raise an error naming the problematic variable.
    """
    write_env(GUARD_API_KEY=KEY, GUARD_SPACE_ID=SPACE, **{variable: value})

    with pytest.raises(GuardError, match=variable):
        GuardClient()


def test_cast_error_names_the_source():
    """
    Verify that cast errors include the source of the variable for easier debugging.
    """
    write_env(GUARD_API_KEY=KEY, GUARD_SPACE_ID=SPACE, GUARD_TIMEOUT="abc")

    with pytest.raises(GuardError, match=r"\.env"):
        GuardClient()


def test_blank_value_falls_through_rather_than_becoming_empty(monkeypatch):
    """
    Ensure a bare variable assignment in .env falls through instead of producing an
    empty string.

    A bare `GUARD_API_KEY=` must not produce an empty bearer token.
    """
    write_env(GUARD_API_KEY="", GUARD_BASE_URL="https://from-file")
    monkeypatch.setenv("GUARD_API_KEY", "from-env")

    client = GuardClient(space_id=SPACE)

    assert client._config.api_key == "from-env"


def test_blank_value_with_no_fallback_is_treated_as_missing():
    """
    Verify that a blank variable with no fallback correctly triggers a missing required
    parameter error.
    """
    write_env(GUARD_API_KEY="", GUARD_SPACE_ID=SPACE)

    with pytest.raises(GuardError, match="API key is required"):
        GuardClient()


def test_engine_from_dotenv_needs_no_api_key():
    """
    Check that configuring the local engine via .env successfully bypasses the API key
    requirement.
    """
    write_env(GUARD_ENGINE="local")

    client = GuardClient()

    assert client.engine is Engine.LOCAL


def test_invalid_engine_from_env_is_rejected():
    """
    Ensure an invalid engine configuration in the environment raises a clear error.
    """
    write_env(GUARD_API_KEY=KEY, GUARD_SPACE_ID=SPACE, GUARD_ENGINE="quantum")

    with pytest.raises(GuardError, match=r"Invalid engine='quantum'"):
        GuardClient()


def test_missing_key_error_mentions_dotenv():
    """
    Verify that the error for a missing API key mentions the .env file as a solution.
    """
    with pytest.raises(GuardError, match=r"\.env"):
        GuardClient(space_id=SPACE)


def test_env_source_values_are_a_copy():
    """
    Ensure EnvSource returns a copy of its values to prevent accidental external
    mutation.
    """
    write_env(GUARD_API_KEY=KEY)
    source = EnvSource()

    source.values["GUARD_API_KEY"] = "mutated"

    assert source.values["GUARD_API_KEY"] == KEY


def test_env_source_get_returns_default_when_unset():
    """
    Check that EnvSource.get correctly returns the provided default when a value is
    absent.
    """
    assert (
        EnvSource(env_file=None).get("GUARD_NOTHING", default="fallback") == "fallback"
    )


def test_read_env_file_drops_blank_entries():
    """Verify that read_env_file drops empty entries to allow fallbacks to trigger."""
    write_env(GUARD_API_KEY="", GUARD_LOCALE="de")

    values = read_env_file()

    assert "GUARD_API_KEY" not in values
    assert values["GUARD_LOCALE"] == "de"
