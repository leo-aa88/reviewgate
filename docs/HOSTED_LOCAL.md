# Run the hosted stack locally

This guide is the **canonical** path for running the GitHub App–style
HTTP API and the Dramatiq analysis worker against **Postgres** and
**Redis** on your machine. It complements the [Docker image](../README.md#docker-image)
section in the README (container defaults and one-off Alembic).

**Scope:** API + worker + broker + DB. It does **not** configure a real
GitHub App webhook tunnel (e.g. ngrok); use mock or manual requests for
smoke checks unless you wire GitHub yourself.

## Prerequisites

* Python **3.12+** (same as CI).
* **Docker** (or any Postgres 16+ and Redis 7+ you already run locally).
* A clone of this repository.

## 1. Dependencies and tooling

From the repository root:

```bash
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
make install-dev
```

`make install-dev` performs an editable install with **`[dev,app]`**
extras (same shape as CI): tests, Ruff, FastAPI, Dramatiq, SQLAlchemy,
Alembic, Redis, and Postgres drivers.

## 2. Postgres and Redis

Pick **one** of the following.

### Option A — Docker one-liners (ephemeral data)

These use default ports **5432** and **6379** on `localhost`. Data is
discarded when containers are removed.

```bash
docker run --name reviewgate-pg -e POSTGRES_PASSWORD=postgres \
  -e POSTGRES_USER=postgres -e POSTGRES_DB=reviewgate \
  -p 5432:5432 -d postgres:16

docker run --name reviewgate-redis -p 6379:6379 -d redis:7
```

### Option B — Your own instances

Use any reachable Postgres and Redis URLs; set them in the next step.

## 3. Environment variables

The app reads **`REVIEWGATE_`-prefixed** variables (see
[`src/reviewgate/app/settings.py`](../src/reviewgate/app/settings.py)).
For local smoke runs, set at least:

| Variable | Example (Option A) |
|----------|--------------------|
| `REVIEWGATE_DATABASE_URL` | `postgresql+psycopg://postgres:postgres@127.0.0.1:5432/reviewgate` |
| `REVIEWGATE_REDIS_URL` | `redis://127.0.0.1:6379/0` |

Use a **synchronous** SQLAlchemy URL (`postgresql+psycopg://…`) for both
Alembic and the runtime app (`create_engine` in
[`src/reviewgate/app/storage/db.py`](../src/reviewgate/app/storage/db.py)).

Optional:

| Variable | Default | Purpose |
|----------|---------|---------|
| `REVIEWGATE_HTTP_PORT` | `8000` | API listen port (`reviewgate-api`). |
| `REVIEWGATE_GITHUB_APP_ID`, webhook secret, private key, etc. | unset | Real GitHub App delivery; omit for DB/worker/API smoke only. |

Export in your shell or use a `.env` file if your tooling loads it.

## 4. Database migrations

Apply schema with Alembic from the repo root (with the venv active):

```bash
alembic upgrade head
```

Makefile shortcut (same command):

```bash
make alembic-upgrade
```

## 5. Run API and worker

Use **two** terminals, both with the venv activated and the same
`REVIEWGATE_*` environment.

**Terminal 1 — HTTP API**

```bash
reviewgate-api
```

This runs Uvicorn on `0.0.0.0` at `REVIEWGATE_HTTP_PORT` (default **8000**).

**Terminal 2 — Dramatiq worker**

```bash
reviewgate-worker
```

This runs `python -m dramatiq reviewgate.app.analysis.worker_app` with
the same settings (Redis broker).

## 6. Smoke check

With only the API up (Postgres reachable, migrations applied):

```bash
curl -sS "http://127.0.0.1:${REVIEWGATE_HTTP_PORT:-8000}/health"
```

You should get **`{"ok":true}`** (see [`GET /health`](../src/reviewgate/app/main.py)).

## How the Dockerfile and Makefile fit

* **`Dockerfile`:** multi-stage image; runtime installs `pip install -e ".[app]"`.
  Default **`CMD` is `reviewgate-api`**. Run the worker by overriding the
  container command, for example:

  ```bash
  docker run --rm -e REVIEWGATE_DATABASE_URL=... -e REVIEWGATE_REDIS_URL=... \
    IMAGE reviewgate-worker
  ```

* **`Makefile`:** `make docker-build` builds the image; `make docker-run-api`
  and `make docker-run-worker` pass through `REVIEWGATE_*` from your
  environment. `make alembic-upgrade` runs **`python -m alembic upgrade head`**
  on the host (requires `REVIEWGATE_DATABASE_URL`); for a one-off migration
  inside the image, use the `docker run … python -m alembic upgrade head` example
  in the [README Docker section](../README.md#docker-image).

For a full container-only path, set the same env vars on `docker run`
and run `alembic upgrade head` once before starting API and worker
containers.

## Troubleshooting

* **Connection refused (Postgres/Redis):** confirm containers are up
  (`docker ps`) and ports match `REVIEWGATE_DATABASE_URL` /
  `REVIEWGATE_REDIS_URL`.
* **`ModuleNotFoundError` for FastAPI/Dramatiq:** run `make install-dev` from
  the repo root with your venv activated.
* **Alembic / engine URL errors:** use `postgresql+psycopg://…` as in
  [`alembic/env.py`](../alembic/env.py) examples.
