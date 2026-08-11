"""
Estimates what an activity will cost before creating it.

The calculation comes down to the following formula:
    `tokens = frames x resolution_cost x model_multiplier`

Frames are calculated as one per second of video, rounded up, with a minimum of 1. A
still image is 1 frame and a 10.4 second clip is 11 frames. Resolution cost is a tier
taken from the long side of the media, which keeps the price independent of orientation.
The model multiplier is the `predictor_multiplier` of the space.

| Tier | `max(w, h)` | Cost |
| ---- | ----------- | ---- |
| 1    | <= 1920     | 1x   |
| 2    | <= 2560     | 2x   |
| 3    | <= 3840     | 4x   |
| None | > 3840      | raises an error |

`GuardClient.estimate_tokens` reads the dimensions from the file and looks the
multiplier up for you. The functions here are the pure calculations underneath.

Examples:
    ```python
    frames_for(10.4)  # returns 11

    # portrait dimensions result in the same price as landscape
    tier_for(1080, 1920)  # returns (1, 1)

    est = estimate_tokens(frames=11, width=2560, height=1440, multiplier=4)
    print(est.tokens)  # returns 88
    ```

Warning:
    This is an estimate. The API currently reserves only the minimum possible cost when
    an activity is created, and the authoritative figure is `payed_tokens` on the
    finished activity. Expect the two to differ.

Note:
    There are two deliberate consequences of pricing by the long side. An ultrawide
    2560x1080 video costs 2x despite having fewer pixels than 1080p. Additionally, DCI
    4K (4096x2160) raises an error because its long side exceeds 3840, whereas standard
    UHD 3840x2160 is accepted.
"""

from __future__ import annotations

import math
from typing import Tuple

from pydantic import BaseModel, ConfigDict, Field

from .exceptions import GuardError

__all__ = [
    "MAX_LONG_SIDE",
    "RESOLUTION_TIERS",
    "TokenEstimate",
    "estimate_tokens",
    "frames_for",
    "tier_for",
]

#: Maps `(max long side, cost)` per tier, starting with the cheapest first. The long
#: side is calculated as `max(width, height)`. This keeps the rule
#: orientation-independent so a portrait 1080x1920 and a landscape 1920x1080 cost the
#: same. It also makes the calculation insensitive to MP4 rotation metadata, which only
#: swaps the two dimensions.
RESOLUTION_TIERS: Tuple[Tuple[int, int], ...] = (
    (1920, 1),  # tier 1, up to 1080p
    (2560, 2),  # tier 2, up to 1440p
    (3840, 4),  # tier 3, up to 4K UHD
)

#: The largest long side that can be priced. Beyond this limit, there is no defined
#: tier.
MAX_LONG_SIDE = RESOLUTION_TIERS[-1][0]


class TokenEstimate(BaseModel):
    """
    The projected cost of one activity, along with the inputs that produced it.

    Carrying the breakdown alongside the total lets a caller explain the number rather
    than just report it.

    Attributes:
        tokens: The estimate, calculated as `frames * tier_cost * multiplier`.
        frames: Billable frames, one per second of video rounded up, minimum 1.
        resolution_tier: Which tier the media fell into, from 1 to 3.
        tier_cost: The multiplier that tier contributes (1, 2, or 4).
        multiplier: The `predictor_multiplier` of the space.
        width: Media width in pixels.
        height: Media height in pixels.
        duration_seconds: Source duration. Zero for a still image.

    Examples:
        ```python
        est = estimate_tokens(frames=11, width=2560, height=1440, multiplier=4)
        print(est.tokens)  # 88
        print(est)  # 88 tokens (11 frames x 2 x 4)
        ```
    """

    model_config = ConfigDict(extra="ignore")

    tokens: int = Field(description="frames x tier_cost x multiplier")
    frames: int
    resolution_tier: int = Field(ge=1, le=3)
    tier_cost: int
    multiplier: int
    width: int
    height: int
    duration_seconds: float = 0.0

    def __str__(self) -> str:
        """
        Render the total with the factors that produced it.

        Returns:
            A formatted string like `88 tokens (11 frames x 2 x 4)`. Printing an
            estimate shows exactly why it is what it is.
        """
        return (
            f"{self.tokens} tokens "
            f"({self.frames} frames x {self.tier_cost} x {self.multiplier})"
        )


def frames_for(duration_seconds: float) -> int:
    """
    Calculate the billable frames for a given duration.

    The calculation is one frame per second of video, rounded up, with a minimum of 1.
    A still image has no duration and bills a single frame. A 10.4 second clip bills 11
    frames because a partial second is still processed.

    Args:
        duration_seconds: The length of the media in seconds.

    Returns:
        The calculated number of billable frames.

    Raises:
        GuardError: If `duration_seconds` is negative.
    """
    if duration_seconds < 0:
        raise GuardError(
            f"Invalid duration_seconds={duration_seconds}. Expected 0 or greater"
        )
    return max(1, math.ceil(duration_seconds))


def tier_for(width: int, height: int) -> Tuple[int, int]:
    """
    Resolve pixel dimensions to a tier and cost pair.

    The tier is chosen by the long side of the media. This ensures a rotated video lands
    in the same tier either way.

    Args:
        width: The width of the media in pixels.
        height: The height of the media in pixels.

    Returns:
        A tuple of `(tier, cost)` based on the defined resolution tiers.

    Raises:
        GuardError: If a dimension is not positive, or if the long side exceeds
            `MAX_LONG_SIDE`. Beyond the top tier there is no defined price, and guessing
            one would misstate the cost.
    """
    if width <= 0 or height <= 0:
        raise GuardError(
            f"Invalid dimensions {width}x{height}. Both sides must be positive"
        )

    long_side = max(width, height)
    for tier, (limit, cost) in enumerate(RESOLUTION_TIERS, start=1):
        if long_side <= limit:
            return tier, cost

    raise GuardError(
        f"Cannot estimate {width}x{height}: its long side ({long_side}) exceeds the "
        f"largest tier ({MAX_LONG_SIDE}). Note this rejects DCI 4K (4096x2160) even "
        f"though UHD (3840x2160) is fine."
    )


def estimate_tokens(
    *,
    frames: int,
    width: int,
    height: int,
    multiplier: int,
    duration_seconds: float = 0.0,
) -> TokenEstimate:
    """
    Apply the cost formula to already-known values.

    This is the pure calculation without any file access or network requests. Use
    `GuardClient.estimate_tokens` to have the media probed and the multiplier looked up
    for you automatically.

    Args:
        frames: Billable frames, typically derived from `frames_for`.
        width: Media width in pixels.
        height: Media height in pixels.
        multiplier: The `predictor_multiplier` of the space.
        duration_seconds: Carried through onto the result for display purposes only.
            Defaults to 0.0.

    Returns:
        A `TokenEstimate` object containing the total cost and the breakdown.

    Raises:
        GuardError: If any input is out of range, or if the resolution exceeds the
            maximum tier.
    """
    if frames < 1:
        raise GuardError(f"Invalid frames={frames}. Expected 1 or greater")
    if multiplier < 1:
        raise GuardError(f"Invalid multiplier={multiplier}. Expected 1 or greater")

    tier, cost = tier_for(width, height)
    return TokenEstimate(
        tokens=frames * cost * multiplier,
        frames=frames,
        resolution_tier=tier,
        tier_cost=cost,
        multiplier=multiplier,
        width=width,
        height=height,
        duration_seconds=duration_seconds,
    )
