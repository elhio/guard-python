"""
Shared query-parameter helpers for the list endpoints.

Filters are validated here on the client side. This ensures that a typo fails
immediately with the valid options listed rather than returning a 422 or 400 error from
the server.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from enum import Enum
from typing import Any, Collection, Dict, List, Optional, Sequence, Type, TypeVar, Union
from uuid import UUID

from .exceptions import GuardError
from .models import SortOrder, coerce_enum, ensure_bool

__all__ = [
    "MAX_HISTORY",
    "MAX_LIMIT",
    "DateLike",
    "IdLike",
    "add_bool",
    "add_datetime",
    "add_ids",
    "add_sort",
    "add_statuses",
    "id_list",
    "reject_conflicting_owners",
    "reject_too_old",
    "require_exactly_one_owner",
    "validate_length",
    "validate_pagination",
]

#: The server caps a single page at this many items.
MAX_LIMIT = 100

#: How far back activity history reaches. This matches `one_year_ago` in the
#: `read_activities` endpoint of the API.
MAX_HISTORY = timedelta(days=365)

#: Tolerance applied when checking `MAX_HISTORY` locally. The server computes its cutoff
#: from *its* clock, which is always a little later than ours by the time the request
#: lands.
_CLOCK_GRACE = timedelta(minutes=5)

#: A type alias for UUID-like inputs.
IdLike = Union[UUID, str]

#: A type alias for date-like inputs.
DateLike = Union[datetime, date, str]

#: A type variable for enum validation.
E = TypeVar("E", bound=Enum)


def validate_pagination(skip: int, limit: int) -> None:
    """
    Check `skip` and `limit` against the range accepted by the server.

    Args:
        skip: The number of items to skip.
        limit: The maximum number of items to return.

    Raises:
        GuardError: If `skip` is negative or `limit` is outside 1-100.
    """
    if skip < 0:
        raise GuardError(f"Invalid skip={skip}. Expected 0 or greater")
    if not 1 <= limit <= MAX_LIMIT:
        raise GuardError(f"Invalid limit={limit}. Expected between 1 and {MAX_LIMIT}")


def reject_conflicting_owners(
    user_id: Optional[IdLike], organization_id: Optional[IdLike]
) -> None:
    """
    Mirror the server rejection of both owner filters at once.

    The API answers 400 when given both. Failing here saves the round-trip.

    Args:
        user_id: The user ID to filter by.
        organization_id: The organization ID to filter by.

    Raises:
        GuardError: If both `user_id` and `organization_id` are provided.
    """
    if user_id is not None and organization_id is not None:
        raise GuardError(
            "Cannot filter by both user_id and organization_id at the same time. "
            "Pass whichever one you mean."
        )


def require_exactly_one_owner(
    user_id: Optional[IdLike], organization_id: Optional[IdLike]
) -> None:
    """
    Verify that a space belongs to exactly one owner.

    A space belongs to exactly one owner. It must be either a user or an organization,
    but never both. The server answers 400 for either mistake. Checking here saves the
    round-trip.

    Args:
        user_id: The user ID of the owner.
        organization_id: The organization ID of the owner.

    Raises:
        GuardError: If neither or both were given.
    """
    if user_id is not None and organization_id is not None:
        raise GuardError(
            "A space cannot belong to both a user and an organization. "
            "Pass exactly one of user_id or organization_id."
        )
    if user_id is None and organization_id is None:
        raise GuardError(
            "A space needs an owner. Pass exactly one of user_id or organization_id "
            "(an existing space from spaces.list() shows which ids are available)."
        )


def validate_length(
    value: str, *, field: str, min_len: int = 0, max_len: Optional[int] = None
) -> str:
    """
    Check the length of a string that has already been stripped by the caller.

    Stripping the string first is important because the server also strips it. To the
    server, `"  ab  "` is two characters, not six. Validating the raw string would allow
    a name that is too short to pass through.

    Args:
        value: The string to validate.
        field: The name of the field for use in error messages.
        min_len: The minimum allowed length. Defaults to 0.
        max_len: The maximum allowed length, or `None` for no maximum limit.

    Returns:
        The original string if it passes validation.

    Raises:
        GuardError: If the value is outside the allowed length.
    """
    if len(value) < min_len:
        raise GuardError(
            f"Invalid {field}={value!r}. Expected at least {min_len} characters, "
            f"got {len(value)}"
        )
    if max_len is not None and len(value) > max_len:
        raise GuardError(
            f"Invalid {field}. Expected at most {max_len} characters, got {len(value)}"
        )
    return value


def id_list(values: Optional[Sequence[IdLike]], *, field: str) -> Optional[List[str]]:
    """
    Stringify a sequence of ids, dropping duplicates but keeping order.

    This mirrors the server's `dict.fromkeys` deduplication so the client and server
    agree on what was sent. It returns `None` when nothing was supplied, allowing the
    caller to omit the key rather than send an empty list.

    Args:
        values: A sequence of IDs to process.
        field: The name of the field for use in error messages.

    Returns:
        A deduplicated list of IDs as strings, or `None` if the input was `None`.

    Raises:
        GuardError: An entry is empty or not usable as an id.
    """
    if values is None:
        return None
    seen: Dict[str, None] = {}
    for value in values:
        text = str(value).strip()
        if not text:
            raise GuardError(f"Invalid {field}: entries must be non-empty ids")
        seen.setdefault(text, None)
    return list(seen)


def add_ids(params: Dict[str, Any], **ids: Optional[IdLike]) -> None:
    """
    Add the supplied id filters as strings.

    Omitting an unset filter is necessary. Sending `None` would instruct the server to
    match a null id rather than leave the field unfiltered.

    Args:
        params: The query dict to add to. This is mutated in place.
        **ids: Filter name mapped to its value. Entries that are `None` are skipped.
    """
    for name, value in ids.items():
        if value is not None:
            params[name] = str(value)


def add_bool(params: Dict[str, Any], name: str, value: Optional[bool]) -> None:
    """
    Add a boolean filter.

    Args:
        params: The query dict to add to. This is mutated in place.
        name: The query parameter name.
        value: Pass `True` or `False` to filter, or `None` to leave the field
            unfiltered.

    Raises:
        GuardError: If `value` is neither a boolean nor `None`.

    Note:
        The `httpx` library serializes real booleans to `"true"` or `"false"`. Stand-ins
        like `1` or `"false"` are refused rather than guessed at. Because `"false"` is
        a truthy string in Python, silently inverting a filter is worse than raising an
        error.
    """
    if value is not None:
        # httpx serialises real bools to "true"/"false"
        params[name] = ensure_bool(value, field=name)


def add_statuses(
    params: Dict[str, Any],
    statuses: Optional[Sequence[Union[E, str]]],
    enum_cls: Type[E],
    *,
    allowed: Optional[Collection[E]] = None,
) -> None:
    """
    Add a repeated `statuses` filter while validating every entry.

    Args:
        params: The query dict to add to. This is mutated in place.
        statuses: Enum members or their string values. Passing `None` or an empty
            sequence leaves the field unfiltered.
        enum_cls: The status enum this resource parses responses with.
        allowed: Restricts which members may be used as a filter. Some resources model
            responses on a wider enum than they accept for filtering. For instance, a
            runner can return `terminated`, but you cannot search for that status. This
            means the parsing enum and the filterable set are not always the same.

    Raises:
        GuardError: If an entry is not a member of `enum_cls` or is excluded by
            `allowed`. The error message lists the values that would be accepted.
    """
    if not statuses:
        return
    values: List[str] = []
    for status in statuses:
        member = coerce_enum(status, enum_cls, field="statuses")
        if allowed is not None and member not in allowed:
            valid = ", ".join(repr(m.value) for m in enum_cls if m in allowed)
            raise GuardError(
                f"Invalid statuses={member.value!r}. Expected one of: {valid}"
            )
        values.append(str(member.value))
    params["statuses"] = values


def add_sort(
    params: Dict[str, Any],
    sort_by: Optional[Union[E, str]],
    sort_order: Optional[Union[SortOrder, str]],
    order_cls: Type[E],
) -> None:
    """
    Add `sort_by` and `sort_order` when given.

    Leaving them out lets the server apply its own default. The default differs per
    resource. Spaces sort ascending, while activities and shares sort descending.

    Args:
        params: The query dict to add to. This is mutated in place.
        sort_by: An ordering enum member or its string value.
        sort_order: `"asc"`, `"desc"`, or a `SortOrder` member.
        order_cls: The ordering enum valid for this resource.

    Raises:
        GuardError: If either value is not a member of its enum. The error message lists
            the valid options.
    """
    if sort_by is not None:
        params["sort_by"] = coerce_enum(sort_by, order_cls, field="sort_by").value
    if sort_order is not None:
        params["sort_order"] = coerce_enum(
            sort_order, SortOrder, field="sort_order"
        ).value


def add_datetime(params: Dict[str, Any], name: str, value: Optional[DateLike]) -> None:
    """
    Add a date filter as ISO-8601.

    Args:
        params: The query dict to add to. This is mutated in place.
        name: The query parameter name.
        value: A `datetime`, a `date`, or a string already in ISO-8601 format. Passing
            `None` leaves the field unfiltered.

    Note:
        Strings pass through untouched, meaning an offset the caller supplied is
        preserved. A naive `datetime` is sent without an offset, which the server reads
        as UTC.
    """
    if value is None:
        return
    if isinstance(value, (datetime, date)):
        params[name] = value.isoformat()
    else:
        params[name] = str(value)


def _as_utc_datetime(value: DateLike) -> Optional[datetime]:
    """
    Perform a best-effort conversion to an aware UTC datetime.

    Args:
        value: The date or time to parse.

    Returns:
        An aware UTC datetime, or `None` if the string could not be parsed. This leaves
        format validation to the server rather than guessing at the caller's intent.
    """
    # datetime is a subclass of date, so it has to be tested first
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, date):
        parsed = datetime(value.year, value.month, value.day)
    else:
        text = str(value).strip()
        # Python 3.9-3.10's fromisoformat does not accept a trailing "Z"
        if text.endswith(("Z", "z")):
            text = text[:-1] + "+00:00"
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError:
            return None

    # server treats a naive datetime as UTC; match it rather than assume local time
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def reject_too_old(
    value: Optional[DateLike], *, field: str, max_age: timedelta = MAX_HISTORY
) -> None:
    """
    Reject a date beyond the API retention window.

    The API keeps a limited history and answers 400 for anything older. Checking here
    turns that into an immediate, self-explanatory error. Unparseable strings are left
    alone because the server validates the format.

    Args:
        value: The date to check.
        field: The name of the field for use in error messages.
        max_age: The maximum allowed age. Defaults to `MAX_HISTORY`.

    Raises:
        GuardError: If `value` predates the retention window.
    """
    if value is None:
        return
    moment = _as_utc_datetime(value)
    if moment is None:
        return

    oldest = datetime.now(timezone.utc) - max_age - _CLOCK_GRACE
    if moment < oldest:
        raise GuardError(
            f"Invalid {field}={value!r}. The API keeps only {max_age.days} days of "
            f"history; the oldest queryable date is about "
            f"{oldest.date().isoformat()}"
        )
