"""
Typed models for the Elhio Guard API.

These models mirror the server's OpenAPI schemas (`/api/v1/openapi.json`). Every model
ignores unknown fields so that additive backend changes never break the client.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Dict, Generic, Iterator, List, Optional, Type, TypeVar, Union
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from .exceptions import GuardError

__all__ = [
    "FILTERABLE_RUNNER_STATUSES",
    "Activity",
    "ActivityCreateResponse",
    "ActivityDetail",
    "ActivityOrder",
    "ActivityPage",
    "ActivityResult",
    "ActivityResultItem",
    "ActivityStatus",
    "ActivityStatusResponse",
    "DetectionMatch",
    "DetectionResult",
    "Engine",
    "MediaCategory",
    "MediaType",
    "Page",
    "Predictor",
    "PredictorOrder",
    "PredictorPage",
    "PredictorStatus",
    "PresignedUploadData",
    "Reaction",
    "ResultSource",
    "Runner",
    "RunnerOrder",
    "RunnerPage",
    "RunnerStatus",
    "Share",
    "ShareOrder",
    "SharePage",
    "ShareStatus",
    "SortOrder",
    "Space",
    "SpaceDetail",
    "SpaceOrder",
    "SpacePage",
    "SpaceStatus",
    "SpaceThresholds",
    "Task",
    "TaskOrder",
    "TaskPage",
    "TaskStatus",
    "activity_id_of",
    "coerce_enum",
    "ensure_bool",
    "result_items_of",
]


class _Base(BaseModel):
    """
    Shared configuration for every response model.

    Unknown fields are ignored rather than rejected. This ensures a backend that starts
    sending a new key does not break a client released before that key existed.
    """

    model_config = ConfigDict(extra="ignore")


class ActivityStatus(str, Enum):
    """
    Lifecycle status of an activity.

    An activity advances from `PENDING_UPLOAD` through `PROCESSING` to one of three
    terminal states. Only `COMPLETED` carries results.

    Attributes:
        PENDING_UPLOAD: Created and awaiting the media bytes.
        PROCESSING: Media received and detection is running.
        COMPLETED: Finished successfully. The `result_payload` is populated.
        FAILED: Processing failed.
        CANCELED: Stopped before completion.
    """

    PENDING_UPLOAD = "pending_upload"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELED = "canceled"

    @property
    def is_terminal(self) -> bool:
        """
        Whether the activity has stopped moving.

        Returns:
            `True` for `COMPLETED`, `FAILED`, and `CANCELED`. Polling stops on these
            states.
        """
        return self in _TERMINAL_STATUSES


#: A set containing the terminal activity statuses.
_TERMINAL_STATUSES = frozenset(
    {ActivityStatus.COMPLETED, ActivityStatus.FAILED, ActivityStatus.CANCELED}
)


class MediaType(str, Enum):
    """
    Media MIME types the backend accepts.

    Anything outside this set is rejected locally before an upload is attempted.

    Attributes:
        JPEG: `image/jpeg`.
        PNG: `image/png`.
        WEBP: `image/webp`.
        GIF: `image/gif`.
        HEIC: `image/heic`. Accepted by the API but not renderable by most browsers.
        MP4: `video/mp4`.
        WEBM: `video/webm`.
        QUICKTIME: `video/quicktime`. Accepted but poorly supported by browsers.
    """

    JPEG = "image/jpeg"
    PNG = "image/png"
    WEBP = "image/webp"
    GIF = "image/gif"
    HEIC = "image/heic"
    MP4 = "video/mp4"
    WEBM = "video/webm"
    QUICKTIME = "video/quicktime"


class Engine(str, Enum):
    """
    Which detection engine produced a result.

    Attributes:
        CLOUD: The Elhio API. Creates an activity so the result has an `activity_id`.
        LOCAL: The optional on-device engine. Creates nothing server-side so
            the result has no `activity_id` and cannot be shared or reacted to.
    """

    CLOUD = "cloud"
    LOCAL = "local"


class SortOrder(str, Enum):
    """
    Direction for a `sort_by` field.

    Shared by every list endpoint, though their defaults differ. Spaces sort ascending,
    while activities and shares sort descending.

    Attributes:
        ASC: Ascending.
        DESC: Descending.
    """

    ASC = "asc"
    DESC = "desc"


class SpaceOrder(str, Enum):
    """
    Fields activities can be sorted by.

    Attributes:
        NAME: Alphabetical by space name.
        CREATED_AT: By creation time. This is the server default.
    """

    NAME = "name"
    CREATED_AT = "created_at"


class ActivityOrder(str, Enum):
    """
    Fields activities can be sorted by.

    Attributes:
        CREATED_AT: By creation time. This is the only option and the server default.
    """

    CREATED_AT = "created_at"


class PredictorOrder(str, Enum):
    """
    Fields predictors can be sorted by.

    Attributes:
        NAME: Alphabetical by predictor name. This is the server default.
        CREATED_AT: By creation time.
    """

    NAME = "name"
    CREATED_AT = "created_at"


class TaskOrder(str, Enum):
    """
    Fields tasks can be sorted by.

    Attributes:
        NAME: Alphabetical by task name. This is the server default.
        CREATED_AT: By creation time.
    """

    NAME = "name"
    CREATED_AT = "created_at"


class SpaceStatus(str, Enum):
    """
    Lifecycle status of a space as exposed publicly.

    Attributes:
        ACTIVE: Available for use.
    """

    ACTIVE = "active"


class PredictorStatus(str, Enum):
    """
    Lifecycle status of a predictor as exposed publicly.

    Attributes:
        ACTIVE: Available for use.
    """

    ACTIVE = "active"


class TaskStatus(str, Enum):
    """
    Lifecycle status of a task as exposed publicly.

    Attributes:
        ACTIVE: Available for use.
    """

    ACTIVE = "active"


class RunnerStatus(str, Enum):
    """
    Lifecycle status of a runner.

    All five values can appear on a response but only four may be used as a filter.
    Review `FILTERABLE_RUNNER_STATUSES` for details.

    Attributes:
        PENDING: Created and the deployment is starting.
        RUNNING: Serving requests.
        DRAINING: Shutting down and finishing what it has.
        FAILED: Creation or teardown failed.
        TERMINATED: Gone. Returned on a response but not accepted as a filter.
    """

    PENDING = "pending"
    RUNNING = "running"
    DRAINING = "draining"
    FAILED = "failed"
    TERMINATED = "terminated"


#: The subset of `RunnerStatus` the `statuses` filter accepts. The API models responses
#: on the full enum but filters on a narrower one, so a runner may come back with a
#: status you cannot search for.
FILTERABLE_RUNNER_STATUSES = frozenset(
    {
        RunnerStatus.PENDING,
        RunnerStatus.RUNNING,
        RunnerStatus.DRAINING,
        RunnerStatus.FAILED,
    }
)


class RunnerOrder(str, Enum):
    """
    Fields runners can be sorted by.

    Attributes:
        NAME: Alphabetical by runner name.
        CREATED_AT: By creation time. This is the server default.
    """

    NAME = "name"
    CREATED_AT = "created_at"


class ShareStatus(str, Enum):
    """
    Lifecycle status of an activity share.

    Filter-only. The API accepts these on `statuses` but returns no status field, so a
    fetched `Share` reports expiry through `Share.is_expired` instead.

    Attributes:
        ACTIVE: The link still works.
        EXPIRED: Past its `expired_at` deadline.
    """

    ACTIVE = "active"
    EXPIRED = "expired"


class ShareOrder(str, Enum):
    """
    Fields activity shares can be sorted by.

    Attributes:
        CREATED_AT: By creation time. This is the only option and the server default.
    """

    CREATED_AT = "created_at"


class MediaCategory(str, Enum):
    """
    High-level media category a space accepts.

    Coarser than `MediaType`. A space enables `image` or `video` rather than individual
    MIME types.

    Attributes:
        IMAGE: Still images.
        VIDEO: Moving media.
    """

    IMAGE = "image"
    VIDEO = "video"


#: A type variable used for enum coercion.
E = TypeVar("E", bound=Enum)


def coerce_enum(value: Union[E, str], enum_cls: Type[E], *, field: str) -> E:
    """
    Turn a loose string into an enum member or explain what was expected.

    Callers pass plain strings like `sort_by="name"`. This validates them at the
    boundary so a typo fails locally with the valid options listed rather than returning
    a 422 from the server.

    Args:
        value: The string or enum member to coerce.
        enum_cls: The enum class to coerce the value into.
        field: The field name for the error message.

    Returns:
        The matched enum member.

    Raises:
        GuardError: If `value` is not one of the `enum_cls` members.
    """
    if isinstance(value, enum_cls):
        return value
    try:
        return enum_cls(value)
    except ValueError as exc:
        valid = ", ".join(repr(member.value) for member in enum_cls)
        raise GuardError(
            f"Invalid {field}={value!r}. Expected one of: {valid}"
        ) from exc


def ensure_bool(value: object, *, field: str) -> bool:
    """
    Require a real `bool` and reject stand-ins like `1` or `"true"`.

    Boolean filters take only `True` or `False`. Strings and integers are refused rather
    than guessed at. This ensures `is_public="false"` fails loudly instead of being read
    as a truthy string.

    Note that `bool` subclasses `int`, so this check has to come before any integer
    handling.

    Args:
        value: The object to check.
        field: The field name for the error message.

    Returns:
        The boolean value if valid.

    Raises:
        GuardError: If `value` is not `True` or `False`.
    """
    if isinstance(value, bool):
        return value
    raise GuardError(f"Invalid {field}={value!r}. Expected a boolean: True or False")


class PresignedUploadData(_Base):
    """
    S3 presigned POST descriptor returned when an activity is created.

    Attributes:
        url: Where to POST the media. This is not the API host and is not authenticated
            with your API key.
        fields: Policy fields that must appear in the multipart body before the file
            part, otherwise storage rejects the upload.
    """

    url: str
    fields: Dict[str, str]


class DetectionMatch(_Base):
    """
    One piece of evidence behind a score.

    The local engine weighs three independent sources against each other. These are
    signed C2PA provenance, embedded metadata, and a vision model. It reports what each
    of them found. A match is one such finding. It is not "this scored 73" but "the
    C2PA manifest names a generative tool", accompanied by the assertion that says so.

    Attributes:
        id: Stable identifier for the underlying rule, unique across all three sources.
            This is safe to branch on.
        category: Which category the rule argues about in the vocabulary of the engine
            (`aiGenerated`, `violent`, `explicit`).
        label: Short human-readable name of the rule.
        description: What the rule means and why it points the way it does.
        confidence: 0-100 indicating how strongly this evidence alone implies the
            category.
        kind: Direction of the evidence. `"authentic"` and `"safe"` argue against the
            category, while anything else argues for it.
        evidence: The specific tag, assertion, or score found in the media.
        source: Which layer reported it. `c2pa`, `metadata`, or `model`.
    """

    id: str
    category: str
    label: str
    description: Optional[str] = None
    confidence: int = Field(ge=0, le=100, description="Confidence between 0-100")
    kind: Optional[str] = None
    evidence: Optional[str] = None
    source: Optional[str] = None


class ActivityResultItem(_Base):
    """
    One task verdict on a piece of media.

    An activity produces one of these per enabled task. The `label` and `description`
    arrive already resolved to the client locale.

    Attributes:
        task_id: Which detection produced this. Match it against `Task` ids.
        score: 0-100 where higher means more strongly detected. This is not a percentage
            probability.
        label: Human-readable name of the detection in the request locale.
        description: Longer explanation in the request locale when one exists.
        media_key: Storage key for the solution image. This is internal. `media_url` is
            the fetchable form.
        media_url: Public URL of the solution image, or `None` when this task produced
            no image. It is unauthenticated so `show` and `save` need no credentials.
        detected: The engine's threshold verdict, which knows a per-category cutoff no
            caller could infer from `score` alone. This is `None` from the cloud API
            because it does not report one.
        matches: The evidence behind the `score`. `None` from the cloud API because it
            returns a score without its reasons. An empty list means the local engine
            looked and found nothing.

    Note:
        `detected` and `matches` are the one place the two engines differ. They are not
        local-only by design. The models ignore unknown fields, so both fill in
        automatically if the API starts returning them.
    """

    task_id: UUID
    score: int = Field(ge=0, le=100, description="Score between 0-100")
    label: str
    description: Optional[str] = None
    media_key: Optional[str] = None
    media_url: Optional[str] = None
    detected: Optional[bool] = None
    matches: Optional[List[DetectionMatch]] = None


class ActivityResult(_Base):
    """
    The result payload of a completed activity.

    Attributes:
        results: One entry per task the space had enabled. This is empty until
            processing finishes.
    """

    results: List[ActivityResultItem] = Field(default_factory=list)


class _ActivityOwner(_Base):
    """
    The mutually-exclusive owner identifiers carried by most activity models.

    Exactly one is set to identify who the activity belongs to. This determines who may
    react to or share it.

    Attributes:
        user_id: Owning user when a user token created it.
        account_id: Owning service account when a service account token created it.
        guest_id: Owning guest for anonymous flows in a public space. This is
            read-only here. This client always authenticates and the API assigns
            a guest owner only to activities created without an identity, such
            as by the browser extension. It is reported so those activities still
            round-trip, but is never set by us.
    """

    user_id: Optional[UUID] = None
    account_id: Optional[UUID] = None
    guest_id: Optional[UUID] = None


class Activity(_ActivityOwner):
    """
    An analysis run as it appears in listings.

    This is the summary shape. `ActivityDetail` adds the results and processing
    metadata.

    Attributes:
        id: Server-assigned identifier.
        status: Where the run has got to.
        created_at: When it was created.
        space_id: The space it belongs to.
        space_name: That space display name.
        user_name: Owning user display name when a user owns it.
        account_name: Owning service account name when one owns it.
        media_type: MIME type of the submitted media.
    """

    id: UUID
    status: ActivityStatus
    created_at: datetime
    space_id: UUID
    space_name: Optional[str] = None
    user_name: Optional[str] = None
    account_name: Optional[str] = None
    media_type: MediaType


class ActivityCreateResponse(Activity):
    """
    An activity plus the one-time target for uploading its media.

    This is returned only from creation. Fetching the activity again will not give you
    `upload_data` a second time, so upload before discarding it.

    Attributes:
        upload_data: Where and how to POST the bytes.
    """

    upload_data: PresignedUploadData


class ActivityStatusResponse(_ActivityOwner):
    """
    Just enough of an activity to poll it.

    This is deliberately small. Polling fetches this rather than the full detail so
    waiting does not repeatedly transfer its results.

    Attributes:
        id: The activity being polled.
        status: Where the run has got to.
    """

    id: UUID
    status: ActivityStatus


class ActivityDetail(_ActivityOwner):
    """
    An activity with everything the API knows about it.

    This is what `activities.get()` returns and the shape carrying the results.

    Attributes:
        id: Server-assigned identifier.
        status: Where the run has got to.
        created_at: When it was created.
        updated_at: When it last changed status.
        space_id: The space it belongs to.
        predictor_id: The model that processed it.
        runner_id: The runner that served it when a dedicated one did.
        media_type: MIME type of the submitted media.
        media_size: Size of the submitted media in bytes.
        payed_tokens: What it actually cost. This is the authoritative figure against
            which `TokenEstimate` is only an estimate. It remains `None` until
            processing ends.
        result_payload: The scored results, or `None` while still processing.
        space_name: The space display name.
        predictor_name: The predictor display name.
        runner_name: The runner name when a dedicated one served it.
        user_name: Owning user display name.
        account_name: Owning service account name.
    """

    id: UUID
    status: ActivityStatus
    created_at: datetime
    updated_at: datetime
    space_id: Optional[UUID] = None
    predictor_id: Optional[UUID] = None
    runner_id: Optional[UUID] = None
    media_type: MediaType
    media_size: int
    payed_tokens: Optional[int] = None
    result_payload: Optional[ActivityResult] = None
    space_name: Optional[str] = None
    predictor_name: Optional[str] = None
    runner_name: Optional[str] = None
    user_name: Optional[str] = None
    account_name: Optional[str] = None


class Space(_Base):
    """
    A container that activities are created in as it appears in listings.

    A space fixes which predictor runs, which tasks are enabled, and who owns the
    results. `SpaceDetail` is the fuller shape from `spaces.get()`.

    Attributes:
        id: Server-assigned identifier. This is what `GUARD_SPACE_ID` holds.
        status: Lifecycle status. Only `ACTIVE` is ever returned.
        created_at: When the space was created.
        name: Display name consisting of 3-50 characters.
        description: Longer description when one was set.
        slug: URL-safe form of the name.
        url_id: Short public identifier used in web links.
        is_default: Whether it is shown as the default space of the owner.
        is_public: Whether everyone can see it.
        user_id: Owning user when a person owns it.
        user_name: That user display name.
        organization_id: Owning organization when one owns it.
        organization_name: That organization display name.
        predictor_id: The model this space runs.
        predictor_name: That predictor display name.
        enabled_media: Which media categories may be submitted.
        enabled_task_names: Names of the enabled detections. The detail endpoint
            returns full task objects under `enabled_tasks` instead.
    """

    id: UUID
    status: SpaceStatus
    created_at: datetime
    name: str
    description: Optional[str] = None
    slug: str
    url_id: str
    is_default: bool
    is_public: bool
    user_id: Optional[UUID] = None
    user_name: Optional[str] = None
    organization_id: Optional[UUID] = None
    organization_name: Optional[str] = None
    predictor_id: UUID
    predictor_name: Optional[str] = None
    enabled_media: List[MediaCategory] = Field(default_factory=list)
    enabled_task_names: List[str] = Field(default_factory=list)

    @property
    def owner_name(self) -> Optional[str]:
        """
        Display name of whoever owns this space.

        Returns:
            The organization name when an organization owns it, otherwise the user name.
            Returns `None` if the server sent neither.
        """
        return self.organization_name or self.user_name


class Predictor(_Base):
    """
    A detection model that powers a space.

    Its id is required to create a space, and its `token_multiplier` is what
    `SpaceDetail.predictor_multiplier` mirrors.

    Attributes:
        id: Server-assigned identifier used for `spaces.create(predictor_id=...)`.
        name: Display name.
        status: Lifecycle status. Only `ACTIVE` is ever returned.
        description: What the model does.
        token_multiplier: Cost multiplier applied to every activity in a space using it.
        slug: URL-safe form of the name.
        url_id: Short public identifier used in web links.
        supported_media: Which media categories it can process.
        supported_task_ids: Which detections it can run.
    """

    id: UUID
    name: str
    status: PredictorStatus
    description: str
    token_multiplier: int
    slug: str
    url_id: str
    supported_media: List[MediaCategory] = Field(default_factory=list)
    supported_task_ids: List[UUID] = Field(default_factory=list)


class Task(_Base):
    """
    One detection a space can enable, such as deepfake or violence.

    Attributes:
        id: Server-assigned identifier used for `spaces.create(enabled_task_ids=...)`.
        status: Lifecycle status. Only `ACTIVE` is ever returned.
        name: Display name in the request locale.
        description: What the detection looks for in the request locale.
        reactions: Expected-result options keyed by the integer a reaction sends as
            `key_value`. For example `{1: "Real photo", 2: "AI generated"}`.
    """

    id: UUID
    status: TaskStatus
    name: str
    description: Optional[str] = None
    reactions: Dict[int, str] = Field(
        default_factory=dict,
        description=(
            "Expected-result options, e.g. {1: 'Real photo', 2: 'AI generated'}. "
            "The keys are the valid key_value choices for Reactions.create."
        ),
    )


class Reaction(_Base):
    """
    Feedback on whether one task result was correct.

    The API accepts one reaction per activity and task, and offers no way for a
    non-admin to read or remove one afterwards. This is only ever seen as the return
    value of creating it.

    Attributes:
        id: Server-assigned identifier.
        created_at: When the feedback was recorded.
        activity_id: The activity being commented on.
        task_id: Which task result the feedback concerns.
        is_positive: `True` if the detection was right.
        key_value: The expected result as a key of that task `reactions` map.
        description: Free-text comment up to 255 characters.
    """

    id: UUID
    created_at: datetime
    activity_id: UUID
    task_id: UUID
    is_positive: bool
    key_value: Optional[int] = Field(
        default=None,
        description="The expected result, keyed into the task's `reactions` map",
    )
    description: Optional[str] = None


class SpaceThresholds(_Base):
    """
    Per-task score cut-offs configured on a space.

    Attributes:
        task_id: Which detection these thresholds apply to.
        blur_threshold: Score at or above which media is blurred, 0-100.
        hide_threshold: Score at or above which media is hidden entirely, 0-100.
    """

    task_id: UUID
    blur_threshold: Optional[int] = Field(default=None, ge=0, le=100)
    hide_threshold: Optional[int] = Field(default=None, ge=0, le=100)


class SpaceDetail(_Base):
    """
    A space with its full configuration.

    This is deliberately not a subclass of `Space`. The detail endpoint returns
    `enabled_tasks` as full task objects, whereas the list endpoint returns
    `enabled_task_names` as plain strings. The two shapes genuinely diverge.

    Attributes:
        id: Server-assigned identifier.
        status: Lifecycle status. Only `ACTIVE` is ever returned.
        created_at: When the space was created.
        name: Display name consisting of 3-50 characters.
        description: Longer description when one was set.
        slug: URL-safe form of the name.
        url_id: Short public identifier used in web links.
        is_default: Whether it is shown as the default space of the owner.
        is_public: Whether everyone can see it.
        user_id: Owning user when a person owns it.
        user_name: That user display name.
        organization_id: Owning organization when one owns it.
        organization_name: That organization display name.
        predictor_id: The model this space runs.
        predictor_name: That predictor display name.
        predictor_multiplier: Token cost multiplier. Mirrors the predictor
            `token_multiplier` and is what `GuardClient.estimate_tokens` reads.
        max_media_size: Largest accepted upload in bytes when the space caps it.
        enabled_media: Which media categories may be submitted.
        enabled_tasks: The enabled detections as full objects rather than names.
        task_thresholds: Per-task blur and hide cut-offs.
    """

    id: UUID
    status: SpaceStatus
    created_at: datetime
    name: str
    description: Optional[str] = None
    slug: str
    url_id: str
    is_default: bool
    is_public: bool
    user_id: Optional[UUID] = None
    user_name: Optional[str] = None
    organization_id: Optional[UUID] = None
    organization_name: Optional[str] = None
    predictor_id: UUID
    predictor_name: Optional[str] = None
    predictor_multiplier: Optional[int] = Field(
        default=None,
        description="Token cost multiplier; mirrors the predictor's token_multiplier",
    )
    max_media_size: Optional[int] = Field(default=None, description="Bytes")
    enabled_media: List[MediaCategory] = Field(default_factory=list)
    enabled_tasks: List[Task] = Field(default_factory=list)
    task_thresholds: List[SpaceThresholds] = Field(default_factory=list)

    @property
    def owner_name(self) -> Optional[str]:
        """
        Display name of whoever owns this space.

        Returns:
            The organization name when an organization owns it, otherwise the user name.
            Returns `None` if the server sent neither.
        """
        return self.organization_name or self.user_name


class Share(_Base):
    """
    A public link to one task result for an activity.

    The API allows one share per activity and offers no way to revoke it. A link lives
    until `expired_at` passes.

    Attributes:
        id: Server-assigned identifier.
        created_at: When the link was created.
        expired_at: When it stops working. See `is_expired`.
        activity_id: The activity being shared.
        task_id: Which task result the link shows.
        task_name: That task display name in the request locale.
        space_name: The owning space display name.
        expires_in: Requested lifetime in days, 1-7.
        share_url: The public link to hand out. This is the point of the object.
        media_url: Direct URL of the shared media when there is one.
        result: The scored result the link displays.
    """

    id: UUID
    created_at: datetime
    expired_at: datetime
    activity_id: UUID
    task_id: UUID
    task_name: Optional[str] = None
    space_name: Optional[str] = None
    expires_in: int = Field(default=7, description="Lifetime in days, 1-7")
    share_url: str
    media_url: Optional[str] = None
    result: Optional[ActivityResultItem] = None

    @property
    def is_expired(self) -> bool:
        """
        Whether the link has lapsed.

        Derived from `expired_at` because the API returns no status field even though it
        accepts `active` or `expired` as a filter.

        Returns:
            `True` once the expiry has passed. A naive `expired_at` is read as UTC,
            matching the server.
        """
        deadline = self.expired_at
        if deadline.tzinfo is None:
            # match the server, which works in UTC
            deadline = deadline.replace(tzinfo=timezone.utc)
        return deadline <= datetime.now(timezone.utc)


class Runner(_Base):
    """
    A dedicated compute instance serving one predictor for an organization.

    Attributes:
        id: Server-assigned identifier.
        status: Where the deployment has got to. May be `TERMINATED`, which cannot be
            used as a filter.
        created_at: When the runner was created.
        terminated_at: When it was torn down, or `None` while it lives.
        name: Display name.
        slug: URL-safe form of the name.
        url_id: Short public identifier used in web links.
        predictor_id: The model this runner serves.
        predictor_name: That predictor display name.
        organization_id: The owning organization. Runners are always org-scoped.
        organization_name: That organization display name.
    """

    id: UUID
    status: RunnerStatus
    created_at: datetime
    terminated_at: Optional[datetime] = None
    name: str
    slug: str
    url_id: str
    predictor_id: UUID
    predictor_name: Optional[str] = None
    organization_id: UUID
    organization_name: Optional[str] = None


#: A type variable for elements in a page.
ItemT = TypeVar("ItemT")


class Page(BaseModel, Generic[ItemT]):
    """
    One page of results plus the total number matching the filter.

    This behaves like a list. You can iterate it, index it, and check its `len()`, while
    keeping `count` available so callers can tell whether more pages exist.

    `len(page)` is the size of this particular page. `page.count` is the total across
    all pages. Note that iterating yields items, so `dict(page)` does not produce field
    pairs. Use `model_dump()` to serialize it.
    """

    model_config = ConfigDict(extra="ignore")

    data: List[ItemT] = Field(default_factory=list)
    count: int = 0

    def __iter__(self) -> Iterator[ItemT]:  # type: ignore[override]
        """
        Iterate the items on this page.

        Yields:
            Each item in `data` in the order the server returned them.

        Note:
            This overrides the default `__iter__` behavior of pydantic so `dict(page)`
            yields items rather than field pairs. Use `model_dump()` to serialize.
        """
        return iter(self.data)

    def __len__(self) -> int:
        """
        Report how many items this page holds.

        Returns:
            The size of this page, not the total. `count` is the total.
        """
        return len(self.data)

    def __getitem__(self, index: int) -> ItemT:
        """
        Index into the page items.

        Args:
            index: Position within this page.

        Returns:
            The item at that position.
        """
        return self.data[index]

    def __bool__(self) -> bool:
        """
        Report whether this page holds anything.

        Returns:
            `False` for an empty page so `if page:` reads naturally.
        """
        return bool(self.data)

    @property
    def has_more(self) -> bool:
        """
        True when `count` exceeds what this page holds.

        This is only meaningful for a first page where `skip=0`. With a non-zero skip,
        you should compare `skip + len(page)` against `count` yourself.
        """
        return len(self.data) < self.count


#: One page of spaces as returned by `Spaces.list`.
SpacePage = Page[Space]
#: One page of activities as returned by `Activities.list`.
ActivityPage = Page[Activity]
#: One page of predictors as returned by `Predictors.list`.
PredictorPage = Page[Predictor]
#: One page of tasks as returned by `Tasks.list`.
TaskPage = Page[Task]
#: One page of runners as returned by `Runners.list`.
RunnerPage = Page[Runner]
#: One page of shares as returned by `Shares.list`.
SharePage = Page[Share]


class DetectionResult(_Base):
    """
    The unified return type of `GuardClient.analyze`.

    This is identical in shape whether the cloud API or the local engine produced it.
    This ensures callers can switch engines without touching the code that reads
    results. The local engine additionally fills in each item's `detected` and `matches`
    fields which the cloud API leaves unset. Reading them is opting into extra detail,
    not into a different shape.
    """

    engine: Engine
    results: List[ActivityResultItem] = Field(default_factory=list)
    activity_id: Optional[UUID] = Field(
        default=None, description="None for local runs, which create no activity"
    )

    @property
    def max_score(self) -> int:
        """
        The strongest detection across every task.

        This is useful as a single number to threshold on when the specific task matters
        less than whether anything fired.

        Returns:
            The highest score in `results` or 0 when there are none.
        """
        return max((item.score for item in self.results), default=0)

    def score_for(self, task_id: UUID) -> Optional[int]:
        """
        Look up one task score.

        Args:
            task_id: Which detection to read.

        Returns:
            That task score, or `None` when the task is absent from the results. This is
            different from a score of 0.
        """
        return next(
            (item.score for item in self.results if item.task_id == task_id), None
        )


#: A type alias for anything carrying the scored results of an activity, whichever call
#: produced it. `GuardClient.analyze` returns a `DetectionResult` and `activities.get()`
#: returns an `ActivityDetail`. They spell the same two things differently, which the
#: helper functions below reconcile.
ResultSource = Union[DetectionResult, ActivityDetail]


def activity_id_of(source: ResultSource) -> Optional[UUID]:
    """
    Extract the activity id behind a result.

    Args:
        source: The result source to examine.

    Returns:
        The activity id behind the result, or `None` when there is no server-side
        activity. `None` means it is a local-engine `DetectionResult`. Nothing was
        created on the server, so there is nothing to react to or share.
    """
    if isinstance(source, DetectionResult):
        return source.activity_id
    return source.id


def result_items_of(source: ResultSource) -> List[ActivityResultItem]:
    """
    Pull the scored items out of either result shape.

    Args:
        source: A `DetectionResult` from `analyze()` or an `ActivityDetail` from
            `activities.get()`.

    Returns:
        The result items. This is empty when the activity is still processing because
        `result_payload` is only populated once it completes.
    """
    if isinstance(source, DetectionResult):
        return list(source.results)
    return list(source.result_payload.results) if source.result_payload else []
