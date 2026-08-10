"""
Low-level bindings for the `/api/v1/spaces/` endpoints.

Spaces are the containers activities are created in. Listing them is how a caller
discovers the `space_id` that `GuardClient.analyze` needs.

`Spaces` and `AsyncSpaces` mirror each other. All query construction lives on
`_SpacesBase` so the two classes cannot drift.
"""

from __future__ import annotations

from typing import Any, AsyncIterator, Dict, Iterator, Optional, Sequence, Union

from .exceptions import GuardError
from .filters import (
    MAX_LIMIT,
    IdLike,
    add_bool,
    add_ids,
    add_sort,
    add_statuses,
    id_list,
    reject_conflicting_owners,
    require_exactly_one_owner,
    validate_length,
    validate_pagination,
)
from .models import (
    SortOrder,
    Space,
    SpaceDetail,
    SpaceOrder,
    SpacePage,
    SpaceStatus,
    ensure_bool,
)
from .transport import AsyncTransport, SyncTransport

__all__ = ["AsyncSpaces", "Spaces"]

#: The base URL path for the spaces endpoints.
_BASE = "/api/v1/spaces/"

#: Minimum length for a space name.
NAME_MIN_LENGTH = 3

#: Maximum length for a space name.
NAME_MAX_LENGTH = 50

#: Maximum length for a space description.
DESCRIPTION_MAX_LENGTH = 2000

#: A type alias for space statuses.
StatusLike = Union[SpaceStatus, str]


class _SpacesBase:
    """
    Query and payload construction with no network I/O.

    Everything that does not touch the network lives here. This ensures the synchronous
    and asynchronous resources cannot drift in how they build or validate a request.
    """

    def __init__(self, default_organization_id: Optional[IdLike] = None) -> None:
        """
        Remember the organization to fall back on.

        Args:
            default_organization_id: This owns a created space when neither `user_id`
                nor `organization_id` is given. An explicit `user_id` still overrides
                this.
        """
        self._default_organization_id = default_organization_id

    @staticmethod
    def _list_params(
        *,
        user_id: Optional[IdLike],
        organization_id: Optional[IdLike],
        predictor_id: Optional[IdLike],
        is_public: Optional[bool],
        is_default: Optional[bool],
        statuses: Optional[Sequence[StatusLike]],
        sort_by: Optional[Union[SpaceOrder, str]],
        sort_order: Optional[Union[SortOrder, str]],
        skip: int,
        limit: int,
    ) -> Dict[str, Any]:
        """
        Build the query parameters for a list request.

        Validating here means a bad filter never reaches the network. The arguments
        mirror the `list` method, but none are optional here.

        Args:
            user_id: Only spaces owned by this user.
            organization_id: Only spaces owned by this organization.
            predictor_id: Only spaces using this predictor.
            is_public: Filter by public visibility.
            is_default: Filter to default spaces.
            statuses: Keep only these statuses.
            sort_by: The field to sort the results by.
            sort_order: The direction to sort the results.
            skip: Offset of the results. Must be 0 or greater.
            limit: Page size, 1-100.

        Returns:
            The query dict with every unset filter omitted.

        Raises:
            GuardError: If a filter value is invalid.
        """
        # validate before building so a bad filter never reaches the network
        reject_conflicting_owners(user_id, organization_id)
        validate_pagination(skip, limit)

        params: Dict[str, Any] = {"skip": skip, "limit": limit}
        add_ids(
            params,
            user_id=user_id,
            organization_id=organization_id,
            predictor_id=predictor_id,
        )
        add_bool(params, "is_public", is_public)
        add_bool(params, "is_default", is_default)
        add_statuses(params, statuses, SpaceStatus)
        add_sort(params, sort_by, sort_order, SpaceOrder)
        return params

    def _create_payload(
        self,
        *,
        name: str,
        predictor_id: IdLike,
        description: Optional[str],
        is_public: bool,
        user_id: Optional[IdLike],
        organization_id: Optional[IdLike],
        enabled_task_ids: Optional[Sequence[IdLike]],
        dedicated_runner_ids: Optional[Sequence[IdLike]],
    ) -> Dict[str, Any]:
        """
        Build the JSON body for creating a space.

        Validating here means an invalid space never reaches the network. The arguments
        mirror `Spaces.create`, but none are optional here.

        Args:
            name: The display name of the space.
            predictor_id: The model powering the space.
            description: A longer description of the space.
            is_public: Whether everyone can see the space.
            user_id: The owning user. Mutually exclusive with `organization_id`.
            organization_id: The owning organization.
            enabled_task_ids: The tasks to enable in the space.
            dedicated_runner_ids: Allowed for organization spaces only.

        Returns:
            The request body. It omits every unset optional field so the server applies
            its own defaults.

        Raises:
            GuardError: If a value is out of range, the owner rule was broken, or
            runners were given for a user space.
        """
        # fall back to the client's organization only when no owner was named at all,
        # so an explicit user_id still produces a personal space
        if user_id is None and organization_id is None:
            organization_id = self._default_organization_id
        require_exactly_one_owner(user_id, organization_id)

        # strip before measuring: the server strips too, so "  ab  " is two characters
        # to it, and validating the raw string would let a too-short name through
        clean_name = validate_length(
            str(name).strip(),
            field="name",
            min_len=NAME_MIN_LENGTH,
            max_len=NAME_MAX_LENGTH,
        )

        payload: Dict[str, Any] = {
            "name": clean_name,
            "predictor_id": str(predictor_id),
            "is_public": ensure_bool(is_public, field="is_public"),
        }

        if description is not None:
            clean_description = str(description).strip()
            if clean_description:
                payload["description"] = validate_length(
                    clean_description,
                    field="description",
                    max_len=DESCRIPTION_MAX_LENGTH,
                )

        if user_id is not None:
            payload["user_id"] = str(user_id)
        else:
            payload["organization_id"] = str(organization_id)

        task_ids = id_list(enabled_task_ids, field="enabled_task_ids")
        if task_ids is not None:
            payload["enabled_task_ids"] = task_ids

        runner_ids = id_list(dedicated_runner_ids, field="dedicated_runner_ids")
        if runner_ids:
            if user_id is not None:
                raise GuardError(
                    "dedicated_runner_ids is only available for organization spaces. "
                    "Pass organization_id instead of user_id, or drop the runners."
                )
            payload["dedicated_runner_ids"] = runner_ids

        return payload


class Spaces(_SpacesBase):
    """
    Synchronous space endpoints.

    This class is accessed through the client rather than being constructed directly,
    and it shares the client connection pool.
    """

    def __init__(
        self,
        transport: SyncTransport,
        *,
        default_organization_id: Optional[IdLike] = None,
    ) -> None:
        """
        Bind this resource to a transport with an optional default id.

        Args:
            transport: The client transport whose connection pool is shared.
            default_organization_id: Used when a call omits it. The id can be set once
                on the client instead of on every call.
        """
        super().__init__(default_organization_id)
        self._transport = transport

    def create(
        self,
        *,
        name: str,
        predictor_id: IdLike,
        description: Optional[str] = None,
        is_public: bool = False,
        user_id: Optional[IdLike] = None,
        organization_id: Optional[IdLike] = None,
        enabled_task_ids: Optional[Sequence[IdLike]] = None,
        dedicated_runner_ids: Optional[Sequence[IdLike]] = None,
    ) -> Space:
        """
        Create a space.

        A space belongs to exactly one owner. You must pass either `user_id` or
        `organization_id`, but never both and never neither.

        Args:
            name: Must be between 3 and 50 characters after surrounding whitespace is
                stripped.
            predictor_id: The model powering the space from `Predictors.list`. It must
                be enabled in your active plan.
            description: Up to 2000 characters. Blank is treated as unset.
            is_public: Whether everyone can see the space. Accepts `True` or `False`
                only. A public space cannot be created inside a private organization.
            user_id: The owning user. Mutually exclusive with `organization_id`.
            organization_id: The owning organization.
            enabled_task_ids: Tasks to enable from `Tasks.list`. Duplicates are dropped
                and the order is preserved.
            dedicated_runner_ids: Allowed for organization spaces only. Rejected for
                user spaces.

        Returns:
            The created `Space`.

        Raises:
            GuardError: If a value is invalid or the owner rule was broken. Raised
                before any request is sent.
            GuardConflictError: If a space with this name already exists in this
                context.
            GuardPaymentRequiredError: If there is no active subscription or the space
                limit is reached.
            GuardAuthError: If the predictor is not enabled in your active plan.
            GuardNotFoundError: If a task, runner, user, or organization id is unknown.

        Examples:
            ```python
            predictor = client.predictors.list()[0]
            tasks = client.tasks.list(predictor_id=predictor.id)
            space = client.spaces.create(
                name="My Space",
                predictor_id=predictor.id,
                organization_id=ORG_ID,
                enabled_task_ids=[t.id for t in tasks],
            )
            ```

        Note:
            `is_default` is deliberately absent. The API rejects it for both user and
            organization spaces, so it can never be set at creation time.
        """
        payload = self._create_payload(
            name=name,
            predictor_id=predictor_id,
            description=description,
            is_public=is_public,
            user_id=user_id,
            organization_id=organization_id,
            enabled_task_ids=enabled_task_ids,
            dedicated_runner_ids=dedicated_runner_ids,
        )
        # never retried: creating a space is not idempotent, so replaying a timed-out
        # request risks a second space or a spurious 409
        data = self._transport.request("POST", _BASE, json=payload)
        return Space.model_validate(data)

    def list(
        self,
        *,
        user_id: Optional[IdLike] = None,
        organization_id: Optional[IdLike] = None,
        predictor_id: Optional[IdLike] = None,
        is_public: Optional[bool] = None,
        is_default: Optional[bool] = None,
        statuses: Optional[Sequence[StatusLike]] = None,
        sort_by: Optional[Union[SpaceOrder, str]] = None,
        sort_order: Optional[Union[SortOrder, str]] = None,
        skip: int = 0,
        limit: int = MAX_LIMIT,
    ) -> SpacePage:
        """
        List the spaces available to this API key.

        Enum filters accept either the member or its string value.
        `sort_by=SpaceOrder.NAME` and `sort_by="name"` are equivalent and are validated
        locally against the real enums. Boolean filters take only `True` or `False`.

        Args:
            user_id: Only spaces owned by this user. Mutually exclusive with
                `organization_id`.
            organization_id: Only spaces owned by this organization.
            predictor_id: Only spaces using this predictor.
            is_public: Filter by public visibility. Accepts `True` or `False`.
            is_default: Filter to default spaces. Accepts `True` or `False`.
            statuses: Keep only these statuses. Publicly only `"active"` exists.
            sort_by: Valid options include `"name"` or `"created_at"`. Server default:
                `"created_at"`.
            sort_order: `"asc"` or `"desc"`. Server default for spaces: `"asc"`.
            skip: Offset. 0 or greater.
            limit: Page size, 1-100.

        Returns:
            A `SpacePage`. You can iterate it like a list or read `.count` for the total
            number matching the filter across all pages.

        Raises:
            GuardError: If a filter value is invalid or both owner filters were given.
        """
        params = self._list_params(
            user_id=user_id,
            organization_id=organization_id,
            predictor_id=predictor_id,
            is_public=is_public,
            is_default=is_default,
            statuses=statuses,
            sort_by=sort_by,
            sort_order=sort_order,
            skip=skip,
            limit=limit,
        )
        data = self._transport.request("GET", _BASE, params=params)
        return SpacePage.model_validate(data)

    def get(self, space_id: IdLike) -> SpaceDetail:
        """
        Read one space with its full configuration.

        Args:
            space_id: The ID of the space to fetch.

        Returns:
            A `SpaceDetail` object. This carries `predictor_multiplier`,
            `max_media_size`, and the full `enabled_tasks`, none of which appear on the
            summary `Space` objects returned by `list`.

        Raises:
            GuardNotFoundError: If the space is unknown or you cannot see it.
        """
        data = self._transport.request("GET", f"{_BASE}{space_id}")
        return SpaceDetail.model_validate(data)

    def iter_all(
        self,
        *,
        user_id: Optional[IdLike] = None,
        organization_id: Optional[IdLike] = None,
        predictor_id: Optional[IdLike] = None,
        is_public: Optional[bool] = None,
        is_default: Optional[bool] = None,
        statuses: Optional[Sequence[StatusLike]] = None,
        sort_by: Optional[Union[SpaceOrder, str]] = None,
        sort_order: Optional[Union[SortOrder, str]] = None,
        page_size: int = MAX_LIMIT,
    ) -> Iterator[Space]:
        """
        Yield every matching space by fetching pages as needed.

        Args:
            user_id: Only spaces owned by this user.
            organization_id: Only spaces owned by this organization.
            predictor_id: Only spaces using this predictor.
            is_public: Filter by public visibility.
            is_default: Filter to default spaces.
            statuses: Keep only these statuses.
            sort_by: The field to sort the results by.
            sort_order: The direction to sort the results.
            page_size: The number of items to fetch per page. Defaults to `MAX_LIMIT`.

        Yields:
            Each matching space with the oldest page first.

        Note:
            Pages are fetched lazily. Breaking out of the loop early stops
            the requests rather than paying for the whole set.
        """
        skip = 0
        while True:
            page = self.list(
                user_id=user_id,
                organization_id=organization_id,
                predictor_id=predictor_id,
                is_public=is_public,
                is_default=is_default,
                statuses=statuses,
                sort_by=sort_by,
                sort_order=sort_order,
                skip=skip,
                limit=page_size,
            )
            yield from page.data

            skip += len(page.data)
            # a short page means the end; the length check also guarantees termination
            # if `count` is stale or wrong
            if not page.data or len(page.data) < page_size or skip >= page.count:
                return


class AsyncSpaces(_SpacesBase):
    """
    Asynchronous space endpoints.

    This class mirrors `Spaces` method for method. See the synchronous methods for full
    argument details.
    """

    def __init__(
        self,
        transport: AsyncTransport,
        *,
        default_organization_id: Optional[IdLike] = None,
    ) -> None:
        """
        Bind this resource to a transport with an optional default id.

        Args:
            transport: The client transport whose connection pool is shared.
            default_organization_id: Used when a call omits it. The id can be set once
                on the client instead of on every call.
        """
        super().__init__(default_organization_id)
        self._transport = transport

    async def create(
        self,
        *,
        name: str,
        predictor_id: IdLike,
        description: Optional[str] = None,
        is_public: bool = False,
        user_id: Optional[IdLike] = None,
        organization_id: Optional[IdLike] = None,
        enabled_task_ids: Optional[Sequence[IdLike]] = None,
        dedicated_runner_ids: Optional[Sequence[IdLike]] = None,
    ) -> Space:
        """
        Create a space.

        Args:
            name: The display name of the space.
            predictor_id: The model powering the space.
            description: A longer description of the space.
            is_public: Whether everyone can see the space.
            user_id: The owning user. Mutually exclusive with `organization_id`.
            organization_id: The owning organization.
            enabled_task_ids: The tasks to enable in the space.
            dedicated_runner_ids: Allowed for organization spaces only.

        Returns:
            The created space. Review `Spaces.create` for every argument and rule.

        Raises:
            GuardError: If a value is invalid or the owner rule was broken.
            GuardConflictError: If a space with this name already exists here.
            GuardPaymentRequiredError: If there is no active subscription or the space
                limit is reached.
        """
        payload = self._create_payload(
            name=name,
            predictor_id=predictor_id,
            description=description,
            is_public=is_public,
            user_id=user_id,
            organization_id=organization_id,
            enabled_task_ids=enabled_task_ids,
            dedicated_runner_ids=dedicated_runner_ids,
        )
        # never retried: see the note on Spaces.create
        data = await self._transport.request("POST", _BASE, json=payload)
        return Space.model_validate(data)

    async def list(
        self,
        *,
        user_id: Optional[IdLike] = None,
        organization_id: Optional[IdLike] = None,
        predictor_id: Optional[IdLike] = None,
        is_public: Optional[bool] = None,
        is_default: Optional[bool] = None,
        statuses: Optional[Sequence[StatusLike]] = None,
        sort_by: Optional[Union[SpaceOrder, str]] = None,
        sort_order: Optional[Union[SortOrder, str]] = None,
        skip: int = 0,
        limit: int = MAX_LIMIT,
    ) -> SpacePage:
        """
        List the spaces matching the given filters.

        Args:
            user_id: Only spaces owned by this user.
            organization_id: Only spaces owned by this organization.
            predictor_id: Only spaces using this predictor.
            is_public: Filter by public visibility.
            is_default: Filter to default spaces.
            statuses: Keep only these statuses.
            sort_by: The field to sort the results by.
            sort_order: The direction to sort the results.
            skip: Offset of the results. Must be 0 or greater.
            limit: Page size, 1-100.

        Returns:
            A `SpacePage`. Review `Spaces.list` for full filter details.
        """
        params = self._list_params(
            user_id=user_id,
            organization_id=organization_id,
            predictor_id=predictor_id,
            is_public=is_public,
            is_default=is_default,
            statuses=statuses,
            sort_by=sort_by,
            sort_order=sort_order,
            skip=skip,
            limit=limit,
        )
        data = await self._transport.request("GET", _BASE, params=params)
        return SpacePage.model_validate(data)

    async def get(self, space_id: IdLike) -> SpaceDetail:
        """
        Read one space with its full configuration.

        Args:
            space_id: The ID of the space to fetch.

        Returns:
            A `SpaceDetail` object carrying `predictor_multiplier` and the full
            `enabled_tasks`. Review `Spaces.get` for more details.

        Raises:
            GuardNotFoundError: If the space is unknown or you cannot see it.
        """
        data = await self._transport.request("GET", f"{_BASE}{space_id}")
        return SpaceDetail.model_validate(data)

    async def iter_all(
        self,
        *,
        user_id: Optional[IdLike] = None,
        organization_id: Optional[IdLike] = None,
        predictor_id: Optional[IdLike] = None,
        is_public: Optional[bool] = None,
        is_default: Optional[bool] = None,
        statuses: Optional[Sequence[StatusLike]] = None,
        sort_by: Optional[Union[SpaceOrder, str]] = None,
        sort_order: Optional[Union[SortOrder, str]] = None,
        page_size: int = MAX_LIMIT,
    ) -> AsyncIterator[Space]:
        """
        Yield every matching space by fetching pages as needed.

        Args:
            user_id: Only spaces owned by this user.
            organization_id: Only spaces owned by this organization.
            predictor_id: Only spaces using this predictor.
            is_public: Filter by public visibility.
            is_default: Filter to default spaces.
            statuses: Keep only these statuses.
            sort_by: The field to sort the results by.
            sort_order: The direction to sort the results.
            page_size: The number of items to fetch per page. Defaults to `MAX_LIMIT`.

        Yields:
            Each matching space. Review `Spaces.iter_all` for more context.
        """
        skip = 0
        while True:
            page = await self.list(
                user_id=user_id,
                organization_id=organization_id,
                predictor_id=predictor_id,
                is_public=is_public,
                is_default=is_default,
                statuses=statuses,
                sort_by=sort_by,
                sort_order=sort_order,
                skip=skip,
                limit=page_size,
            )
            for space in page.data:
                yield space

            skip += len(page.data)
            if not page.data or len(page.data) < page_size or skip >= page.count:
                return
