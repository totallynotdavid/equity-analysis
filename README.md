# [internal]: Equity analysis

<!-- prettier-ignore -->
> [!WARNING]
> This is an internal tool. While the repository is public, it depends on
> proprietary data and a specific Excel format, so it is unlikely to work out of the
> box.

This monorepo provides tools for analyzing stock market data from Excel files
using machine learning. It includes a command-line interface for automation and
a web API with a frontend.

## Architecture

The project follows a monorepo structure managed with
[uv workspaces](https://docs.astral.sh/uv/concepts/projects/workspaces/#getting-started),
organizing code into independent Python packages. This structure supports
cross-package development, clear separation of concerns, and isolated dependency
management.

The `equity-analyzer-core` implements all analytical logic as pure functions
that process data and return structured results, independent of any interface.
The API and CLI packages provide interfaces only.

The monorepo consists of four main components:

**Core package** ([`packages/core`](packages/core)) houses
`equity-analyzer-core`, the application's engine. It includes data processing,
machine learning models, and reusable analytical functions, with no
external-facing interfaces.

**CLI package** ([`packages/cli`](packages/cli)) provides `equity-analyzer-cli`
for automation and batch processing. It uses the core library to run analyses
while handling argument parsing, file I/O, and console output.

**API package** ([`packages/api`](packages/api)) contains `equity-analyzer-api`,
a FastAPI server exposing the core via REST endpoints. It handles file uploads,
executes analyses asynchronously, and returns JSON results for the frontend.

**Frontend application** ([`web`](web)) houses a simple Astro-based UI that
communicates with the API to upload files and display results in a graphical
interface.

## Technology stack

**Backend and CLI:** Built with Python 3.10+, using `uv` for package management.
Pandas handles data manipulation, Scikit-learn covers machine learning, and
Openpyxl supports Excel integration. The web API is powered by FastAPI.

**Frontend:** Developed with Astro, styled using Tailwind CSS + Starwind CSS,
and running on the Bun JavaScript runtime.

For the development stack, I use `mise` to manage task automation and to enforce
version consistency across environments. Python packages are linted and
type-checked with Ruff and Mypy (configured in the root
[`pyproject.toml`](pyproject.toml?plain=1#L18)), while the frontend is currently
formatted with Prettier.

## Development environment

First, install `mise` by following the official guide at
[mise.jdx.dev](https://mise.jdx.dev/getting-started.html) for your operating
system. You'll also need Git for version control. Once installed, `mise` will
automatically provision Python, `uv`, and Bun as defined in the configuration.

Clone the repository and enter the project directory:

```bash
git clone https://github.com/totallynotdavid/excel-analysis
cd excel-analysis
```

Set up the environment with:

```bash
mise install
mise run install
```

The first command installs all required tools from `.mise.toml`. The second
installs Python workspace dependencies from `uv.lock` and frontend dependencies
with `bun install` inside `web/`. See [mise.toml](mise.toml?plain=1#L10) for
more details.

By default, `uv` also creates and manages a virtual environment, enabled through
the `uv_venv_auto = true` setting.

## Running the system

Running the application typically requires two or three terminal sessions,
depending on which components you need.

**API server:** From the repository root, run:

```
mise run api
```

This starts Uvicorn with hot reloading. The API will be available at
`http://127.0.0.1:8000`, with interactive docs at `http://127.0.0.1:8000/docs`.

**CLI tool:** The CLI can be used independently for terminal-based analysis:

```
mise run cli
```

By default, it reads Excel files from `./data/` and writes results to
`./outputs/`. Additional arguments can be passed after `--`, for example:

```bash
mise run cli -- --debug --data-dir ./custom/path/
```

**Frontend:** To launch the development server, run:

```
mise run web
```

The interface will be available at `http://localhost:4321` on your machine, and
can also be accessed from other devices on your local network using your
computer’s IP address.

## Project structure

The monorepo structure:

```
├── .mise.toml                      # Tool versions and task definitions
├── pyproject.toml                  # Workspace configuration, ruff and mypy settings
├── packages/
│   ├── core/
│   │   ├── equity_analyzer_core/   # Analytical engine
│   │   └── pyproject.toml          # Core package definition
│   ├── cli/
│   │   ├── equity_analyzer_cli/    # Command-line interface
│   │   └── pyproject.toml          # CLI package definition
│   └── api/
│       ├── equity_analyzer_api/    # Web API
│       └── pyproject.toml          # API package definition
├── web/                            # Astro frontend application
│   ├── src/
│   ├── astro.config.mjs
│   └── package.json
└── data/                           # Input Excel files (git-ignored)
```

The root `pyproject.toml` acts as the central configuration. Its
`[tool.uv.workspace]` section defines the monorepo members and centralizes tool
settings for consistency. Each package has its own `pyproject.toml`, declaring
dependencies and referencing local packages (e.g. `equity-analyzer-core`)
[1](packages\api\pyproject.toml?plain=1#L13)
[2](packages\cli\pyproject.toml?plain=1#L14).
