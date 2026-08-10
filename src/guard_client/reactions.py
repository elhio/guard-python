"""
Low-level bindings for the `/api/v1/reactions/` endpoint.

A reaction is feedback on one task result for one activity. It answers whether the
detection was right, and if not, what the expected result was.

Two API rules shape this module: First, you may only react to your own activity.
Anything else returns a 404, which is indistinguishable from an unknown id. Second, only
one reaction is allowed per activity and task. A second attempt returns a 409 conflict.

Reading or removing a reaction is an admin-only operation, so this resource is
create-only.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from .exceptions import GuardError
from .filters import IdLike, validate_length
from .models import (
    ActivityResultItem,
    Reaction,
    ResultSource,
    activity_id_of,
    ensure_bool,
    result_items_of,
)
from .transport import AsyncTransport, SyncTransport

__all__ = ["AsyncReactions", "Reactions"]

#: The base URL path for the reactions endpoints.
_BASE = "/api/v1/reactions/"

#: The server-side constraint on the maximum length of a reaction description.
DESCRIPTION_MAX_LENGTH = 255


class _ReactionsBase:
    """
    Query and payload construction with no network I/O.

    Everything that does not touch the network lives here. This ensures the synchronous
    and asynchronous resources cannot drift in how they build or validate a request.
    """

    @staticmethod
    def _create_payload(
        *,
        activity_id: IdLike,
        task_id: IdLike,
        is_positive: bool,
        key_value: Optional[int],
        description: Optional[str],
    ) -> Dict[str, Any]:
        """
        Build the JSON body for creating a reaction.

        The arguments mirror `Reactions.create`, but none are optional here.

        Args:
            activity_id: The ID of the activity the result belongs to.
            task_id: The ID of the task result being reacted to.
            is_positive: Indicates if the detection was correct.
            key_value: The expected result as an integer key.
            description: A free text description.

        Returns:
            The request body with every unset optional omitted.

        Raises:
            GuardError: If `is_positive` is not a boolean, `key_value` is not an
                integer, or `description` exceeds 255 characters.
        """
        payload: Dict[str, Any] = {
            "activity_id": str(activity_id),
            "task_id": str(task_id),
            "is_positive": ensure_bool(is_positive, field="is_positive"),
        }

        if key_value is not None:
            # bool subclasses int, so `isinstance(True, int)` is True. We check
            # it out explicitly since True would silently become expected-result option
            # 1
            if isinstance(key_value, bool) or not isinstance(key_value, int):
                raise GuardError(
                    f"Invalid key_value={key_value!r}. Expected an integer key from "
                    f"the task's `reactions` map, e.g. "
                    f"task.reactions -> {{1: 'Real photo'}}"
                )
            payload["key_value"] = key_value

        if description is not None:
            clean = str(description).strip()
            if clean:
                payload["description"] = validate_length(
                    clean, field="description", max_len=DESCRIPTION_MAX_LENGTH
                )

        return payload

    @staticmethod
    def _resolve_source(source: ResultSource, item: ActivityResultItem) -> IdLike:
        """
        Get the activity id while checking the item really belongs to this result.

        Args:
            source: A result from `analyze()` or `activities.get()`.
            item: The result item being commented on.

        Returns:
            The activity id behind the result.

        Raises:
            GuardError: If the source came from the local engine, meaning no activity
                exists on the server, or if the item belongs to a different activity.
        """
        activity_id = activity_id_of(source)
        if activity_id is None:
            raise GuardError(
                "This result came from the local engine, so no activity exists on the "
                "server to react to. Reactions apply to cloud results only."
            )

        known = result_items_of(source)
        if not any(existing.task_id == item.task_id for existing in known):
            available = ", ".join(str(existing.task_id) for existing in known) or "none"
            raise GuardError(
                f"task_id {item.task_id} is not part of this activity's results "
                f"(available: {available})."
            )
        return activity_id


class Reactions(_ReactionsBase):
    """
    Synchronous reaction endpoints.

    This class is accessed through the client rather than being constructed directly,
    and it shares the client connection pool.
    """

    def __init__(self, transport: SyncTransport) -> None:
        """
        Bind this resource to a transport.

        Args:
            transport: The client transport whose connection pool is shared.
        """
        self._transport = transport

    def create(
        self,
        *,
        activity_id: IdLike,
        task_id: IdLike,
        is_positive: bool,
        key_value: Optional[int] = None,
        description: Optional[str] = None,
    ) -> Reaction:
        """
        Submit feedback on one task result.

        Args:
            activity_id: The activity the result belongs to. It must be your own.
            task_id: Which task result you are reacting to.
            is_positive: `True` if the detection was correct. Accepts `True` or `False`
                only.
            key_value: The expected result as an integer key of the task `reactions`
                map. See `Task.reactions` for the choices.
            description: Free text up to 255 characters. Blank counts as unset.

        Returns:
            The created `Reaction`.

        Raises:
            GuardError: If a value is invalid. This is raised before any request is
                sent.
            GuardNotFoundError: If the activity is unknown or it is not yours. The API
                does not distinguish between the two.
            GuardConflictError: If you have already reacted to this activity and task.

        Examples:
            ```python
            client.reactions.create(
                activity_id=result.activity_id,
                task_id=item.task_id,
                is_positive=False,
                key_value=2,
                description="This is a real photo of me",
            )
            ```
        """
        payload = self._create_payload(
            activity_id=activity_id,
            task_id=task_id,
            is_positive=is_positive,
            key_value=key_value,
            description=description,
        )
        # not retried: a replay would trip the one-reaction-per-task guard and report a
        # 409 for a reaction that actually succeeded
        data = self._transport.request("POST", _BASE, json=payload)
        return Reaction.model_validate(data)

    def create_for(
        self,
        source: ResultSource,
        item: ActivityResultItem,
        *,
        is_positive: bool,
        key_value: Optional[int] = None,
        description: Optional[str] = None,
    ) -> Reaction:
        """
        React using the objects you already hold.

        Args:
            source: Either a `DetectionResult` from `GuardClient.analyze` or an
                `ActivityDetail` from `activities.get()`.
            item: The `ActivityResultItem` being commented on.
            is_positive: `True` if the detection was correct.
            key_value: The expected result as an integer key of the task `reactions`
                map.
            description: Free text up to 255 characters.

        Returns:
            The created `Reaction`.

        Raises:
            GuardError: If the source is a local result or the item is not one of its
                results. Both checks happen before any request is sent.
        """
        activity_id = self._resolve_source(source, item)
        return self.create(
            activity_id=activity_id,
            task_id=item.task_id,
            is_positive=is_positive,
            key_value=key_value,
            description=description,
        )


class AsyncReactions(_ReactionsBase):
    """
    Asynchronous reaction endpoints.

    This class mirrors `Reactions` method for method. See the synchronous methods for
    full argument details.
    """

    def __init__(self, transport: AsyncTransport) -> None:
        """
        Bind this resource to a transport.

        Args:
            transport: The client transport whose connection pool is shared.
        """
        self._transport = transport

    async def create(
        self,
        *,
        activity_id: IdLike,
        task_id: IdLike,
        is_positive: bool,
        key_value: Optional[int] = None,
        description: Optional[str] = None,
    ) -> Reaction:
        """
        Submit feedback on one task result.

        Args:
            activity_id: The activity the result belongs to. It must be your own.
            task_id: Which task result you are reacting to.
            is_positive: `True` if the detection was correct. Accepts `True` or `False`
                only.
            key_value: The expected result as an integer key of the task `reactions`
                map.
            description: Free text up to 255 characters. Blank counts as unset.

        Returns:
            The created `Reaction`. Review `Reactions.create` for more details.

        Raises:
            GuardError: If a value is invalid. This is raised before any request.
            GuardNotFoundError: If the activity is unknown or not yours.
            GuardConflictError: If you already reacted to this activity and task.
        """
        payload = self._create_payload(
            activity_id=activity_id,
            task_id=task_id,
            is_positive=is_positive,
            key_value=key_value,
            description=description,
        )
        # not retried: see the note on Reactions.create
        data = await self._transport.request("POST", _BASE, json=payload)
        return Reaction.model_validate(data)

    async def create_for(
        self,
        source: ResultSource,
        item: ActivityResultItem,
        *,
        is_positive: bool,
        key_value: Optional[int] = None,
        description: Optional[str] = None,
    ) -> Reaction:
        """
        React using the objects you already hold.

        Args:
            source: Either a `DetectionResult` from `GuardClient.analyze` or an
                `ActivityDetail` from `activities.get()`.
            item: The `ActivityResultItem` being commented on.
            is_positive: `True` if the detection was correct.
            key_value: The expected result as an integer key of the task `reactions`
                map.
            description: Free text up to 255 characters.

        Returns:
            The created `Reaction`. Review `Reactions.create_for` for more details.

        Raises:
            GuardError: If the source is a local result or the item is not one of its
                results.
        """
        activity_id = self._resolve_source(source, item)
        return await self.create(
            activity_id=activity_id,
            task_id=item.task_id,
            is_positive=is_positive,
            key_value=key_value,
            description=description,
        )
