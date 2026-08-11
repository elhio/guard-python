<div align="center">
  <h1>
    <img src="https://raw.githubusercontent.com/elhio/guard-python/main/docs/guard.svg" width="100" alt="Guard Logo"><br>
    Guard
  </h1>
  <p><em>A Python client for seamlessly integrating visual safety filters into your applications</em></p>
  <p>
    <a href="https://pypi.org/project/guard-client/"><img src="https://img.shields.io/pypi/v/guard-client.svg?label=Release" alt="Release"></a>
    <a href="https://opensource.org/licenses/Apache-2.0"><img src="https://img.shields.io/badge/License-Apache%202.0-blue.svg" alt="License: Apache 2.0"></a>
    <a href="https://github.com/elhio/guard-python/fork"><img src="https://img.shields.io/badge/PRs-welcome-brightgreen.svg" alt="PRs Welcome"></a>  
  </p>
</div>

## Features

**🛡️ Multi-Layered Content Moderation:** Automatically detects AI-generated, violent, and explicit content in images and 
videos.

**🔄️ Two Engines, One Result Shape:** Use the cloud API for maximum accuracy, or the optional on-device engine to build 
and test locally without an API key. Both return the exact same strictly typed models.

**⚡ Sync & Async Support:** Natively supports both synchronous operations and `async/await` out of the box, making it 
a perfect fit for high-performance frameworks like FastAPI.

**🛠️ Comprehensive API Bindings:** Typed bindings for the entire API including spaces, activities, tasks, predictors, 
runners, shares, and reactions. We also include token estimation, so you can price a job before you spend on it.

## Installation

You can install the client in two ways, depending on whether you want to rely purely on the cloud API or include the 
local fallback engine.

### Cloud-Only (Standard)

Installs the lightweight client. All media detection is routed to the Elhio Cloud API. This version is entirely Apache 
2.0 licensed.

```bash
pip install guard-client
```

### Cloud + Local (Hybrid)

Installs the client along with the `guard-local-detector` engine. This allows you to process local file paths directly 
on your hardware with zero network latency.
*Note: The local engine dependency is licensed under the AGPL-3.0.*

```bash
pip install "guard-client[local]"
```

## Quick Start

The `analyze()` method runs the entire detection lifecycle for you. It creates an activity, uploads the media, confirms 
the upload, polls until processing finishes, and returns the result.

You will need an **API key** and a space to create the activity in. You can create a new access token in the **Elhio 
dashboard** (under Settings > Account Settings > Access Tokens). If you do not know your space ID yet, you can 
ask the API for it. The `spaces.list()` method returns every space the key can see.


```python
from guard_client import GuardClient

with GuardClient(api_key="your_api_key_here") as client:
    # 1. Get an available space
    space = client.spaces.list()[0]

    # 2. Analyze the media (creates activity, uploads, and polls for results)
    result = client.analyze("photo.jpg", space_id=space.id)

    # 3. Print the detection results
    for item in result.results:
        print(f"{item.label}: {item.score}/100")

# AI-Generated: 87/100
# Violence: 2/100
# Explicit: 1/100
```

Both values also resolve from the environment, so the common case needs no arguments at all:

```python
# With GUARD_API_KEY and GUARD_SPACE_ID set (environment or .env)
with GuardClient() as client:
    result = client.analyze("photo.jpg")
```

The `analyze()` method accepts a file path, raw bytes, or an open binary file object. The media type is detected 
automatically from the filename or the file's magic bytes. You can pass `media_type=` to skip this automatic detection.

### Local Detection

With the `[local]` extra installed, you can pass `engine="local"` to run entirely on-device. This requires no network 
calls, no API key, and no space ID. Results use the exact same `DetectionResult` type as the cloud API, so the code 
reading them does not need to change.

Local detection is **opt-in** and never automatic. The engine you get is the engine you explicitly ask for, regardless 
of which extras happen to be installed.

```python
from guard_client import GuardClient

with GuardClient(engine="local") as client:
    # 1. Analyze the media
    result = client.analyze("/local/path/to/video.mp4")
    
    # 2. Print the detection results
    for item in result.results:
        print(f"{item.label}: {item.score}/100")

# AI-Generated: 90/100
# Violence: 2/100
# Explicit: 1/100
```

Two fields easily tell the engines apart. The `result.activity_id` is `None` because a local run creates nothing 
server-side, meaning there is nothing to share or react to. The `result.detected` field carries the engine's own 
per-category threshold verdict, which the cloud API does not report. Reading it means you are opting into extra detail, 
not into a different data shape.

## Development

This project uses [uv](https://docs.astral.sh/uv/) for package and environment management. A single `uv sync` creates 
the `.venv`, reads `uv.lock`, and installs everything exactly as it was locked. The test suite is fully mocked, so 
there is no API key to obtain and no network access at any point.

```bash
# set up the environment
uv sync

# run tests
uv run pytest

# build for production
uv build
```

The [Contributing Guide](https://github.com/elhio/guard-python/blob/main/CONTRIBUTING.md) covers the rest: linting and 
type checking, the documentation build, working against the optional local engine and its shared contract suite, and 
testing end-to-end against a live API.

## Contributing

We welcome contributions! Please note that all contributors must sign our automated CLA. Read more in our 
[Contributing Guide](https://github.com/elhio/guard-python/blob/main/CONTRIBUTING.md).

## License

This repository and its corresponding PyPI package are licensed under the Apache v2.0 (Apache-2.0) - see the 
[LICENSE](https://github.com/elhio/guard-python/blob/main/LICENSE) file for details.
