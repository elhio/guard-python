<div align="center">
  <h1>
    <img src="./docs/assets/guard.svg" width="100" alt="Guard Logo"><br>
    Guard
  </h1>
  <p><em>A seamless Python client for integrating visual safety filters into your applications</em></p>
  <p>
    <a href="https://opensource.org/licenses/Apache-2.0"><img src="https://img.shields.io/badge/License-Apache%202.0-blue.svg" alt="License: Apache 2.0"></a>
    <a href="https://github.com/elhio/guard-python/fork"><img src="https://img.shields.io/badge/PRs-welcome-brightgreen.svg" alt="PRs Welcome"></a>  
  </p>
</div>

## Features

**☁️ Robust Cloud API:** Seamlessly connects to Elhio Guard's powerful cloud infrastructure for high-accuracy, 
multi-layered visual safety filtering.

**🔌 Optional Local Engine:** Add the `[local]` extension to instantly route local file checks to an on-device, 
zero-latency ONNX engine—without changing your code.

**⚡ Sync & Async Support:** Natively supports both synchronous operations and `async/await` out of the box, making it 
perfect for high-performance frameworks like FastAPI.

**🛠️ Unified Data Structures:** Whether a request is processed in the cloud API or locally on your hardware, the client 
returns the exact same strictly typed models.


## Installation

You can install the client in two ways, depending on whether you want to rely purely on the cloud API or include the 
offline fallback engine.

### Cloud-Only (Standard)

Installs the lightweight client. All media detection is routed to the Elhio Cloud API. This version is entirely Apache 
2.0 licensed.

```bash
pip install guard-client
```

### Cloud + Local (Hybrid)

Installs the client along with the `guard-local-detector` engine. This allows you to process local file paths directly on 
your hardware with zero network latency.
*Note: The local engine dependency is licensed under the AGPL-3.0.*

```bash
pip install "guard-client[local]"
```

## Quick Start

### Cloud Detection

If you installed via `guard-client[local]`, you do not need to import this package directly. The main client will 
automatically detect its presence and route local file checks to this engine.

```python
from guard_client import GuardClient

client = GuardClient(api_key="your_api_key_here")

# automatically routed to the Elhio Cloud API
result = client.check_media(url="https://example.com/video.mp4")

print(f"Detection Results: {result}")
```

### Local Detection

If you completed the `guard-client[local]` installation, you can also check images with the local on-device engine. It 
returns the exact same data structure as the cloud API, making local testing and air-gapped deployments seamless.

```python
from guard_client import GuardClient

client = GuardClient()

# automatically routed to local detection engine
result = client.check_media(file_path="/local/paths/to/video.mp4")

print(f"Detection Results: {result}")
```

## Development

This project uses [uv](https://docs.astral.sh/uv/) for lightning-fast Python package and environment management.

### Prerequisites

* [uv](https://docs.astral.sh/uv/) (already installed on your system)

### Setup

1. Clone the repository:
    ```bash
    git clone https://github.com/elhio/guard-python.git
    cd guard-local-python
    ```

2. Sync the environment:
    ```bash
    uv sync
    ```
    *This command automatically creates a `.venv` virtual environment, reads the `uv.lock` file, and installs all core* 
    *and development dependencies exactly as they were locked.*

3. Run tests:
    ```bash
    uv run pytest
    ```

4. Formatting and linting:
    ```bash
    uv run ruff format
    uv run ruff check
    ```

5. Build for production:
    ```bash
    uv build
    ```

## Contributing

We welcome contributions! Please note that all contributors must sign our automated CLA. Read more in our 
[Contributing Guide](CONTRIBUTING.md).

## License

This repository and its corresponding PyPI package are licensed under the Apache v2.0 (Apache-2.0) - see the 
[LICENSE](LICENSE) file for details.

