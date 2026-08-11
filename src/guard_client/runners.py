"""
Low-level bindings for the `/api/v1/runners/` endpoints.

A runner is a dedicated compute instance serving one predictor for an organization.
Listing them supplies the `dedicated_runner_ids` accepted by `Spaces.create`.

Note:
    These routes currently authenticate a *user* rather than a service account. A
    service-account key will get a 404 here. The API is expected to widen this scope,
    and nothing in the client will need to change when it does.
"""

from __future__ import annotations

from typing import Any, AsyncIterator, Dict, Iterator, Optional, Sequence, Union

from .exceptions import GuardError
from .filters import (
    MAX_LIMIT,
    IdLike,
    add_ids,
    add_sort,
    add_statuses,
    id_list,
    validate_pagination,
)
from .models import (
    FILTERABLE_RUNNER_STATUSES,
    Runner,
    RunnerOrder,
    RunnerPage,
    RunnerStatus,
    SortOrder,
    ensure_bool,
)
from .transport import AsyncTransport, SyncTransport

__all__ = ["AsyncRunners", "Runners"]

#: The base URL path for runner endpoints.
_BASE = "/api/v1/runners/"

#: A type alias for runner statuses.
StatusLike = Union[RunnerStatus, str]


class _RunnersBase:
    """
    Query and payload construction with no network I/O.

    Everything that does not touch the network lives here. This ensures the synchronous
    and asynchronous resources cannot drift in how they build or validate a request.
    """

    def __init__(self, default_organization_id: Optional[IdLike] = None) -> None:
        """
        Remember the organization to fall back on.

        Args:
            default_organization_id: Used when a call omits it. Runners are always
                organization-scoped, so this is required one way or another.
        """
        self._default_organization_id = default_organization_id

    def _resolve_organization_id(self, organization_id: Optional[IdLike]) -> str:
        """
        Pick the organization for this call.

        Args:
            organization_id: The per-call value, or `None` to use the client default.

        Returns:
            The organization id as a string.

        Raises:
            GuardError: If neither source supplied an ID. Unlike other list endpoints,
                the API requires this, so there is no unfiltered fallback.
        """
        effective = (
            organization_id
            if organization_id is not None
            else self._default_organization_id
        )
        if effective is None:
            raise GuardError(
                "organization_id is required for runners. Pass it to this call, set it "
                "on the client with GuardClient(organization_id=...), or put "
                "GUARD_ORGANIZATION_ID in your environment or .env file."
            )
        return str(effective)

    def _list_params(
        self,
        *,
        organization_id: Optional[IdLike],
        predictor_id: Optional[IdLike],
        statuses: Optional[Sequence[StatusLike]],
        sort_by: Optional[Union[RunnerOrder, str]],
        sort_order: Optional[Union[SortOrder, str]],
        skip: int,
        limit: int,
    ) -> Dict[str, Any]:
        """
        Build the query parameters for a list request.

        Validating here means a bad filter never reaches the network. The arguments
        mirror `list`, but none are optional here.

        Args:
            organization_id: Only runners belonging to this organization.
            predictor_id: Only runners serving this predictor.
            statuses: Keep only runners with these statuses.
            sort_by: The field to sort the results by.
            sort_order: The direction to sort the results.
            skip: Offset of the results. Must be 0 or greater.
            limit: Page size, 1-100.

        Returns:
            The query dict with every unset filter omitted.

        Raises:
            GuardError: If a filter value is invalid.
        """
        validate_pagination(skip, limit)

        params: Dict[str, Any] = {
            "skip": skip,
            "limit": limit,
            "organization_id": self._resolve_organization_id(organization_id),
        }
        add_ids(params, predictor_id=predictor_id)
        add_statuses(params, statuses, RunnerStatus, allowed=FILTERABLE_RUNNER_STATUSES)
        add_sort(params, sort_by, sort_order, RunnerOrder)
        return params

    def _create_payload(
        self,
        *,
        predictor_id: IdLike,
        organization_id: Optional[IdLike],
        is_default: bool,
        dedicated_space_ids: Optional[Sequence[IdLike]],
    ) -> Dict[str, Any]:
        """
        Build the JSON body for creating a runner.

        The arguments mirror `Runners.create`, but none are optional here.

        Args:
            predictor_id: The ID of the predictor this runner serves.
            organization_id: The ID of the organization to own the runner.
            is_default: Whether spaces get this as their default runner.
            dedicated_space_ids: Sequence of space IDs to restrict the runner to.

        Returns:
            The request body. Unlike spaces, `is_default` is always sent since the API
            accepts it here.

        Raises:
            GuardError: If no organization id is available or `is_default` is not a
                boolean.
        """
        payload: Dict[str, Any] = {
            "predictor_id": str(predictor_id),
            "organization_id": self._resolve_organization_id(organization_id),
            # unlike spaces, the API does accept is_default at creation here
            "is_default": ensure_bool(is_default, field="is_default"),
        }
        space_ids = id_list(dedicated_space_ids, field="dedicated_space_ids")
        if space_ids is not None:
            payload["dedicated_space_ids"] = space_ids
        return payload


class Runners(_RunnersBase):
    """
    Synchronous runner endpoints.

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

    def list(
        self,
        *,
        organization_id: Optional[IdLike] = None,
        predictor_id: Optional[IdLike] = None,
        statuses: Optional[Sequence[StatusLike]] = None,
        sort_by: Optional[Union[RunnerOrder, str]] = None,
        sort_order: Optional[Union[SortOrder, str]] = None,
        skip: int = 0,
        limit: int = MAX_LIMIT,
    ) -> RunnerPage:
        """
        List an organization's runners.

        Args:
            organization_id: Required by the API. Falls back to the client default.
            predictor_id: Only runners serving this predictor.
            statuses: Keep only runners with these statuses. `"terminated"` is not
                filterable. Review `FILTERABLE_RUNNER_STATUSES` for details.
            sort_by: `"name"` or `"created_at"`. Server default: `"created_at"`.
            sort_order: `"asc"` or `"desc"`. Server default: `"asc"`.
            skip: Offset. 0 or greater.
            limit: Page size, 1-100.

        Returns:
            A `RunnerPage`. You can iterate it like a list or read `.count`.

        Raises:
            GuardError: If no organization id is available or a filter value is invalid.
            GuardNotFoundError: If you are not a member of the organization.
        """
        params = self._list_params(
            organization_id=organization_id,
            predictor_id=predictor_id,
            statuses=statuses,
            sort_by=sort_by,
            sort_order=sort_order,
            skip=skip,
            limit=limit,
        )
        data = self._transport.request("GET", _BASE, params=params)
        return RunnerPage.model_validate(data)

    def iter_all(
        self,
        *,
        organization_id: Optional[IdLike] = None,
        predictor_id: Optional[IdLike] = None,
        statuses: Optional[Sequence[StatusLike]] = None,
        sort_by: Optional[Union[RunnerOrder, str]] = None,
        sort_order: Optional[Union[SortOrder, str]] = None,
        page_size: int = MAX_LIMIT,
    ) -> Iterator[Runner]:
        """
        Yield every matching runner by fetching pages as needed.

        Args:
            organization_id: Only runners belonging to this organization.
            predictor_id: Only runners serving this predictor.
            statuses: Keep only runners with these statuses.
            sort_by: The field to sort the results by.
            sort_order: The direction to sort the results.
            page_size: The number of items to fetch per page. Defaults to `MAX_LIMIT`.

        Yields:
            Each matching runner with the oldest page first.

        Note:
            Pages are fetched lazily. Breaking out of the loop early stops the requests
            rather than paying for the whole set.
        """
        skip = 0
        while True:
            page = self.list(
                organization_id=organization_id,
                predictor_id=predictor_id,
                statuses=statuses,
                sort_by=sort_by,
                sort_order=sort_order,
                skip=skip,
                limit=page_size,
            )
            yield from page.data

            skip += len(page.data)
            if not page.data or len(page.data) < page_size or skip >= page.count:
                return

    def get(self, runner_id: IdLike) -> Runner:
        """
        Read one runner.

        Args:
            runner_id: The ID of the runner to fetch.

        Returns:
            The requested `Runner`.

        Raises:
            GuardNotFoundError: If the runner is unknown or you are not a
                member of its organization. The API does not distinguish the two.
        """
        data = self._transport.request("GET", f"{_BASE}{runner_id}")
        return Runner.model_validate(data)

    def create(
        self,
        *,
        predictor_id: IdLike,
        organization_id: Optional[IdLike] = None,
        is_default: bool = False,
        dedicated_space_ids: Optional[Sequence[IdLike]] = None,
    ) -> Runner:
        """
        Create a runner and start its deployment.

        This requires ownership of the organization, not merely membership.

        Args:
            predictor_id: The predictor this runner serves. Must be enabled in the
                organization's active plan.
            organization_id: The organization to own the runner. Falls back to the
                client default.
            is_default: Whether spaces get this as their default runner.
            dedicated_space_ids: Restrict the runner to these spaces. They must belong
                to the same organization. Duplicates are dropped and order is preserved.

        Returns:
            The created `Runner` with an initial status of `pending`.

        Raises:
            GuardError: If a value is invalid. Raised before any request is sent.
            GuardPaymentRequiredError: If there is no active subscription or the runner
                limit is reached.
            GuardConflictError: If a runner with this name already exists here.
            GuardNotFoundError: If the organization, predictor, or space is unknown, or
                if you do not own the organization.

        Examples:
            ```python
            runner = client.runners.create(predictor_id=P)
            print(runner.status)  # <RunnerStatus.PENDING: 'pending'>
            ```
        """
        payload = self._create_payload(
            predictor_id=predictor_id,
            organization_id=organization_id,
            is_default=is_default,
            dedicated_space_ids=dedicated_space_ids,
        )
        data = self._transport.request("POST", _BASE, json=payload)
        return Runner.model_validate(data)

    def delete(self, runner_id: IdLike) -> None:
        """
        Delete a runner.

        The API drains the runner, tears down its deployment, and then removes the
        record. This is not reversible.

        Args:
            runner_id: The ID of the runner to delete.

        Raises:
            GuardNotFoundError: If the runner is unknown or you do not own its
                organization.
            GuardServerError: If draining or teardown fails. The runner is left with a
                `failed` status.
        """
        # never retried: a replay after a dropped connection would report a confusing
        # 404 for a delete that actually succeeded, or re-trigger a live teardown
        self._transport.request("DELETE", f"{_BASE}{runner_id}", retry=False)


class AsyncRunners(_RunnersBase):
    """
    Asynchronous runner endpoints.

    This class mirrors `Runners` method for method. See the synchronous methods for
    full argument details.
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

    async def list(
        self,
        *,
        organization_id: Optional[IdLike] = None,
        predictor_id: Optional[IdLike] = None,
        statuses: Optional[Sequence[StatusLike]] = None,
        sort_by: Optional[Union[RunnerOrder, str]] = None,
        sort_order: Optional[Union[SortOrder, str]] = None,
        skip: int = 0,
        limit: int = MAX_LIMIT,
    ) -> RunnerPage:
        """
        List the runners matching the given filters.

        Args:
            organization_id: Only runners belonging to this organization.
            predictor_id: Only runners serving this predictor.
            statuses: Keep only runners with these statuses.
            sort_by: The field to sort the results by.
            sort_order: The direction to sort the results.
            skip: Offset. 0 or greater.
            limit: Page size, 1-100.

        Returns:
            A `RunnerPage`. Review `Runners.list` for full filter details.
        """
        params = self._list_params(
            organization_id=organization_id,
            predictor_id=predictor_id,
            statuses=statuses,
            sort_by=sort_by,
            sort_order=sort_order,
            skip=skip,
            limit=limit,
        )
        data = await self._transport.request("GET", _BASE, params=params)
        return RunnerPage.model_validate(data)

    async def iter_all(
        self,
        *,
        organization_id: Optional[IdLike] = None,
        predictor_id: Optional[IdLike] = None,
        statuses: Optional[Sequence[StatusLike]] = None,
        sort_by: Optional[Union[RunnerOrder, str]] = None,
        sort_order: Optional[Union[SortOrder, str]] = None,
        page_size: int = MAX_LIMIT,
    ) -> AsyncIterator[Runner]:
        """
        Yield every matching runner by fetching pages as needed.

        Args:
            organization_id: Only runners belonging to this organization.
            predictor_id: Only runners serving this predictor.
            statuses: Keep only runners with these statuses.
            sort_by: The field to sort the results by.
            sort_order: The direction to sort the results.
            page_size: The number of items to fetch per page. Defaults to `MAX_LIMIT`.

        Yields:
            Each matching runner. Review `Runners.iter_all` for more context.
        """
        skip = 0
        while True:
            page = await self.list(
                organization_id=organization_id,
                predictor_id=predictor_id,
                statuses=statuses,
                sort_by=sort_by,
                sort_order=sort_order,
                skip=skip,
                limit=page_size,
            )
            for runner in page.data:
                yield runner

            skip += len(page.data)
            if not page.data or len(page.data) < page_size or skip >= page.count:
                return

    async def get(self, runner_id: IdLike) -> Runner:
        """
        Read one runner.

        Args:
            runner_id: The ID of the runner to fetch.

        Returns:
            The requested `Runner`.

        Raises:
            GuardNotFoundError: If the runner is unknown or you are not a member of its
                organization.
        """
        data = await self._transport.request("GET", f"{_BASE}{runner_id}")
        return Runner.model_validate(data)

    async def create(
        self,
        *,
        predictor_id: IdLike,
        organization_id: Optional[IdLike] = None,
        is_default: bool = False,
        dedicated_space_ids: Optional[Sequence[IdLike]] = None,
    ) -> Runner:
        """
        Create a runner and start its deployment.

        Args:
            predictor_id: The predictor this runner serves. Must be enabled in the
                organization's active plan.
            organization_id: The organization to own the runner. Falls back to the
                client default.
            is_default: Whether spaces get this as their default runner.
            dedicated_space_ids: Sequence of space IDs to restrict the runner to.

        Returns:
            The created `Runner` with an initial status of `pending`.

        Raises:
            GuardError: If a value is invalid. Raised before any request is sent.
            GuardPaymentRequiredError: If there is no active subscription or the runner
                limit is reached.
            GuardConflictError: If a runner with this name already exists here.
            GuardNotFoundError: If the organization, predictor, or space is unknown.
        """
        payload = self._create_payload(
            predictor_id=predictor_id,
            organization_id=organization_id,
            is_default=is_default,
            dedicated_space_ids=dedicated_space_ids,
        )
        data = await self._transport.request("POST", _BASE, json=payload)
        return Runner.model_validate(data)

    async def delete(self, runner_id: IdLike) -> None:
        """
        Delete a runner. This is not reversible.

        Args:
            runner_id: The ID of the runner to delete.

        Raises:
            GuardNotFoundError: If the runner is unknown or you do not own its
                organization.
            GuardServerError: If draining or teardown fails.

        Warning:
            This tears down the runner's deployment. Review `Runners.delete` for more
                details.
        """
        # never retried: see the note on Runners.delete
        await self._transport.request("DELETE", f"{_BASE}{runner_id}", retry=False)
