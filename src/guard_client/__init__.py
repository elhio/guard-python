"""
A Python client for Elhio's Guard visual safety detection API.

Quick start:

```python
from guard_client import GuardClient

with GuardClient(api_key="...", space_id="...") as client:
    result = client.analyze("photo.jpg")
    for item in result.results:
        print(item.label, item.score)
```
"""

from importlib.metadata import PackageNotFoundError, version

from .activities import Activities, AsyncActivities
from .client import AsyncGuardClient, GuardClient
from .display import BROWSER_RENDERABLE, load_media, save, show
from .env import DEFAULT_ENV_FILE, ENV_FILE_VAR, EnvSource, read_env_file
from .exceptions import (
    ActivityFailedError,
    GuardAPIError,
    GuardAuthError,
    GuardConflictError,
    GuardConnectionError,
    GuardError,
    GuardLocalEngineError,
    GuardLocalModelError,
    GuardMediaDecodeError,
    GuardNotFoundError,
    GuardPaymentRequiredError,
    GuardRateLimitError,
    GuardServerError,
    GuardTimeoutError,
    GuardUploadError,
    GuardValidationError,
    LocalEngineNotInstalledError,
    UnsupportedMediaTypeError,
)
from .filters import MAX_HISTORY, MAX_LIMIT
from .media import SUPPORTED_MEDIA_TYPES, MediaSource
from .models import (
    FILTERABLE_RUNNER_STATUSES,
    Activity,
    ActivityCreateResponse,
    ActivityDetail,
    ActivityOrder,
    ActivityPage,
    ActivityResult,
    ActivityResultItem,
    ActivityStatus,
    ActivityStatusResponse,
    DetectionMatch,
    DetectionResult,
    Engine,
    MediaCategory,
    MediaType,
    Page,
    Predictor,
    PredictorOrder,
    PredictorPage,
    PredictorStatus,
    PresignedUploadData,
    Reaction,
    Runner,
    RunnerOrder,
    RunnerPage,
    RunnerStatus,
    Share,
    ShareOrder,
    SharePage,
    ShareStatus,
    SortOrder,
    Space,
    SpaceDetail,
    SpaceOrder,
    SpacePage,
    SpaceStatus,
    SpaceThresholds,
    Task,
    TaskOrder,
    TaskPage,
    TaskStatus,
)
from .predictors import AsyncPredictors, Predictors
from .probe import MediaInfo, probe_media
from .reactions import AsyncReactions, Reactions
from .runners import AsyncRunners, Runners
from .shares import AsyncShares, Shares
from .spaces import AsyncSpaces, Spaces
from .tasks import AsyncTasks, Tasks
from .tokens import (
    MAX_LONG_SIDE,
    RESOLUTION_TIERS,
    TokenEstimate,
    estimate_tokens,
    frames_for,
    tier_for,
)
from .transport import DEFAULT_BASE_URL

try:
    __version__ = version("guard-client")
except PackageNotFoundError:  # pragma: running from an uninstalled checkout
    __version__ = "0.0.0+unknown"

__all__ = [
    "DEFAULT_BASE_URL",
    "DEFAULT_ENV_FILE",
    "ENV_FILE_VAR",
    "FILTERABLE_RUNNER_STATUSES",
    "BROWSER_RENDERABLE",
    "MAX_HISTORY",
    "MAX_LIMIT",
    "MAX_LONG_SIDE",
    "RESOLUTION_TIERS",
    "SUPPORTED_MEDIA_TYPES",
    "GuardClient",
    "AsyncGuardClient",
    "Activities",
    "AsyncActivities",
    "Spaces",
    "AsyncSpaces",
    "Predictors",
    "AsyncPredictors",
    "Tasks",
    "AsyncTasks",
    "Runners",
    "AsyncRunners",
    "Reactions",
    "AsyncReactions",
    "Shares",
    "AsyncShares",
    "EnvSource",
    "read_env_file",
    "load_media",
    "save",
    "show",
    "estimate_tokens",
    "frames_for",
    "probe_media",
    "tier_for",
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
    "MediaSource",
    "MediaType",
    "Page",
    "Predictor",
    "PredictorOrder",
    "PredictorPage",
    "PredictorStatus",
    "PresignedUploadData",
    "Reaction",
    "Runner",
    "RunnerOrder",
    "RunnerPage",
    "RunnerStatus",
    "Share",
    "ShareOrder",
    "SharePage",
    "ShareStatus",
    "SortOrder",
    "MediaInfo",
    "Space",
    "SpaceDetail",
    "SpaceOrder",
    "SpacePage",
    "SpaceStatus",
    "SpaceThresholds",
    "Task",
    "TokenEstimate",
    "TaskOrder",
    "TaskPage",
    "TaskStatus",
    "ActivityFailedError",
    "GuardAPIError",
    "GuardAuthError",
    "GuardConflictError",
    "GuardConnectionError",
    "GuardError",
    "GuardLocalEngineError",
    "GuardLocalModelError",
    "GuardMediaDecodeError",
    "GuardNotFoundError",
    "GuardPaymentRequiredError",
    "GuardRateLimitError",
    "GuardServerError",
    "GuardTimeoutError",
    "GuardUploadError",
    "GuardValidationError",
    "LocalEngineNotInstalledError",
    "UnsupportedMediaTypeError",
    "__version__",
]
