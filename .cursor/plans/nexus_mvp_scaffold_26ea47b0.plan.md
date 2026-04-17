---
name: Nexus MVP Scaffold
overview: Nexus is a SaaS GitHub App that automatically maintains a knowledge base wiki for any repo that installs it. Wiki files live in-repo (.nexus/ directory) for version control, and Nexus also hosts a web dashboard for browsing, search, and AI-powered Q&A.
todos:
  - id: s1-1-scaffold
    content: "S1.1: Project scaffold -- pyproject.toml, .env.example, Dockerfile, docker-compose.yml (app + postgres + redis), config.py, main.py with health endpoint"
    status: completed
  - id: s1-2-database
    content: "S1.2: Database -- database.py (async engine + session), SQLAlchemy models (Installation, Repo, Job), Alembic init + first migration"
    status: completed
  - id: s1-3-github-app-auth
    content: "S1.3: GitHub App auth -- github_client.py with JWT generation (PyJWT + private key) and installation token exchange"
    status: pending
  - id: s1-4-webhook-handler
    content: "S1.4: Webhook handler -- POST /webhooks/github with HMAC signature verification, parse installation + pull_request events, save to DB"
    status: pending
  - id: s1-5-worker
    content: "S1.5: Background worker -- ARQ worker with Redis, placeholder init_repo and process_pr tasks, job status updates in Postgres"
    status: pending
  - id: s1-6-wiring
    content: "S1.6: Wire it up -- webhook handler enqueues ARQ jobs, worker picks them up, update Job status to completed, test end-to-end with smee.io"
    status: pending
  - id: s2-raw-ingest
    content: "S2 Raw ingester -- fetch .nexus/raw/**, filter to .md/.txt/.rst, extract text, upsert RawSource rows"
    status: pending
  - id: s2-db-schema
    content: "S2 DB schema -- new Alembic migration for raw_sources, wiki_pages, knowledge_nodes, knowledge_edges"
    status: pending
  - id: s2-init-compile-pipeline
    content: "S2 Init compile pipeline -- Claude compiles raw corpus to {pages, nodes, edges}; map/reduce if over budget; transactional DB upsert"
    status: pending
  - id: s2-context-guard
    content: "S2 Context Guard -- schema valid, interlinks resolve, all edge endpoints exist, no duplicate slugs"
    status: pending
  - id: s3-node-resolver
    content: "S3 Node resolver -- query wiki_pages + knowledge_nodes in Postgres (replaces INDEX.md parsing)"
    status: pending
  - id: s3-pr-pipeline
    content: "S3 PR pipeline -- DB-only; dual triggers: .nexus/raw/ re-ingest vs code-diff scribe; no Shadow PR"
    status: pending
isProject: false
---

# Nexus: The Autonomous LLM Wiki for Engineering

---

## Part 1: What Is Nexus?

Nexus is a **SaaS product** distributed as a **GitHub App**. Teams install it on their GitHub repos the same way they install Vercel, Codecov, or Dependabot. Once installed, Nexus autonomously maintains a structured knowledge base (wiki) of the codebase -- no human has to write or maintain documentation.

Nexus is three things running together:

- **A backend server** (Python/FastAPI) that receives GitHub webhooks, runs the AI pipeline, and manages state across all installed repos
- **A web dashboard** (Next.js) where developers browse their wiki, view the knowledge graph, and ask natural-language questions about their codebase
- **An MCP server** that plugs into IDEs (Cursor, VS Code) so developers get wiki context inline while coding

```mermaid
flowchart LR
    subgraph product [Nexus Platform]
        API[FastAPI Backend]
        DB[(PostgreSQL)]
        Queue[Redis + Worker]
        Web[Next.js Dashboard]
        MCPServer[MCP Server]
    end

    GH[GitHub] -->|webhooks| API
    API -->|enqueue jobs| Queue
    Queue -->|process| API
    API -->|read/write| DB
    API -->|call| Claude[Anthropic Claude API]
    API -->|read .nexus/raw/**| GH

    Dev1[Developer Browser] --> Web
    Web --> API
    Dev2[Developer IDE] --> MCPServer
    MCPServer --> API
```

### Philosophy

Nexus follows the framing Andrej Karpathy sketched in these tweets:

- https://x.com/karpathy/status/2039805659525644595
- https://x.com/karpathy/status/2040470801506541998

The short version: **humans curate the raw context; the LLM compiles, maintains, and renders it.** Engineers drop durable, opinionated notes -- architecture in prose, design decisions, gotchas, onboarding briefs -- into a `.nexus/raw/` directory at the repo root. Nexus compiles that corpus into a structured wiki plus a knowledge graph and stores the result in its own database, where a web dashboard renders it. When code changes via PRs, Nexus refreshes the compiled artifacts; when the raw notes change, it recompiles the affected slices. The raw notes are the only human-authored source of truth, and no compiled artifacts are ever committed back to the repo.

---

## Part 2: Tech Stack

### Backend (the brain)
- **Python 3.12+** with **FastAPI** -- async HTTP server, handles webhooks and dashboard API
- **PostgreSQL** -- multi-tenant data: installations, repos, users, wiki page metadata, processing jobs
- **Redis** + **ARQ** (async task queue) -- webhook processing takes 30-120 seconds (LLM calls); the webhook handler returns 200 immediately and enqueues the real work
- **Anthropic SDK** -- Claude API calls for the AI Scribe
- **httpx** -- async GitHub API client (fetching diffs, creating PRs, reading repo contents)
- **GitPython** -- local git operations for cloning, branching, committing wiki changes

### Frontend (the face)
- **Next.js 15** (App Router) -- the web dashboard where devs browse their wiki
- **Tailwind CSS** -- styling
- **D3.js or react-force-graph** -- interactive knowledge graph visualization
- **Vercel AI SDK** -- streaming AI chat interface for Q&A

### Infrastructure
- **Docker Compose** for local dev (FastAPI + Postgres + Redis + Next.js)
- **Deploy to Railway or Fly.io** for production (or AWS ECS later)
- **GitHub App** registration -- this is how repos "install" Nexus and grant it permissions

### Why these choices
- FastAPI over Django because the workload is webhook-driven and async-heavy, not CRUD-heavy
- ARQ over Celery because it is async-native and lightweight (Celery is overkill for this)
- PostgreSQL over a NoSQL store because we need relational queries across tenants, repos, and wiki nodes
- Next.js for the dashboard because it pairs well with the FastAPI JSON API and gives us SSR for the wiki pages (good for SEO if wikis are public)

---

## Part 3: The PR Workflow (Step by Step)

The core loop -- what happens when a developer opens or updates a PR on a repo with Nexus installed. The pipeline has **two triggers rolled into one**: diffs under `.nexus/raw/` cause re-ingestion of human-authored notes, and diffs to source code cause the Scribe to refresh the compiled wiki and graph. Both paths end with a transactional upsert to Postgres -- no Shadow PRs, no repo commits.

```mermaid
sequenceDiagram
    participant Dev as Developer
    participant GH as GitHub
    participant NX as Nexus Backend
    participant Q as Task Queue
    participant AI as Claude API
    participant DB as Postgres

    Dev->>GH: Opens/updates PR #42
    GH->>NX: Webhook (pull_request event)
    NX->>NX: Verify signature, extract metadata
    NX->>Q: Enqueue processing job
    NX-->>GH: 200 OK (immediate)

    Q->>NX: Worker picks up job
    NX->>GH: GET /pulls/{n}/files
    NX->>NX: Split into raw_changes vs code_changes

    alt raw_changes touched
        NX->>GH: Fetch changed .nexus/raw/** files
        NX->>DB: Upsert RawSource rows
        NX->>DB: Load pages/nodes linked to affected raw_sources
    end

    alt code_changes present
        NX->>DB: Query wiki_pages + knowledge_nodes by file_paths
    end

    NX->>AI: Diff + current KB slice + update prompt
    AI-->>NX: Updated pages, nodes, edges
    NX->>NX: Context Guard lint
    NX->>DB: Transactional upsert
    NX->>NX: Mark Job completed
    Note over DB: Frontend dashboard reads from DB live
```

### Step-by-step breakdown:

1. **Trigger** -- Developer opens PR #42 on `acme/backend`. GitHub sends a `pull_request` webhook to `https://api.nexus.dev/webhooks/github`.

2. **Validate and enqueue** -- Nexus verifies the `X-Hub-Signature-256` using the app's webhook secret, extracts the repo + PR number + action, returns `200 OK` immediately, and enqueues a background job.

3. **Fetch file list** -- The worker calls `GET /repos/{owner}/{repo}/pulls/{n}/files` for the structured file list (path, status, additions, deletions, patch per file).

4. **Classify changes** -- Split the file list into `raw_changes` (paths under `.nexus/raw/`) and `code_changes` (everything else).

5. **Re-ingest raw changes (if any)** -- For each added or modified raw file with an allowed extension (`.md`, `.txt`, `.rst`), fetch content via the Contents API, decode UTF-8, compute SHA-256, and upsert the `raw_sources` row. Deletions remove the row.

6. **Resolve affected slice** -- From the DB, load the `wiki_pages` / `knowledge_nodes` touched by the change: for raw edits, the pages/nodes linked to the affected `raw_sources`; for code edits, the pages whose `frontmatter_json.file_paths` match the changed source paths. Ambiguous cases get a cheap Claude classifier call.

7. **AI Scribe (incremental)** -- Send Claude a prompt containing the PR title + description, the relevant diff excerpts, any newly re-ingested raw text, and the current DB slice being updated. Claude returns a structured `KnowledgeBaseDelta` (upserts / deletes for pages, nodes, edges).

8. **Context Guard lint** -- Validate the delta in the post-apply state: frontmatter schema, every `[[slug]]` interlink resolves, every edge endpoint exists, no duplicate slugs or node names, no obvious hallucinations (updated pages must reference at least one changed symbol).

9. **Transactional DB upsert** -- Apply the delta inside a single transaction. Mark the `Job` as `completed` on success, `failed` (with error details) otherwise.

10. **Dashboard refresh** -- The Next.js dashboard polls or subscribes to the API; users see the updated wiki and graph without any merge step.

---

## Part 4: Where the Knowledge Base Lives

There are two artifacts with two homes:

### Home 1: `.nexus/raw/` in the repo (human source of truth)

Before installing Nexus, the team drops durable, opinionated notes into a `.nexus/raw/` directory at the repo root. No required sub-structure -- developers organize the folder however they want. v1 ingests `.md`, `.txt`, and `.rst` files; other file types are skipped with a warning on the `Job` row.

```
acme-backend/
├── src/
│   └── ...
├── .nexus/
│   └── raw/
│       ├── overview.md
│       ├── architecture/
│       │   ├── services.md
│       │   └── data-model.md
│       ├── decisions/
│       │   └── 2026-04-chose-postgres-over-dynamodb.md
│       └── onboarding.md
├── package.json
└── ...
```

**Why in-repo:**
- Version-controlled alongside the code they describe.
- Reviewable in normal PRs -- no special tooling or Shadow PR concept needed.
- Developers keep full authorship and ownership of the prose Nexus feeds on.

### Home 2: Nexus Postgres (compiled source of truth)

Nexus compiles `.nexus/raw/` into a structured wiki and a knowledge graph stored in its own Postgres:

- `raw_sources` -- one row per ingested file (path, content, SHA-256).
- `wiki_pages` -- compiled markdown pages with slug, title, content, and frontmatter (including `file_paths` for code-level linkage).
- `knowledge_nodes` -- typed nodes (concept / module / decision / entity) with descriptions.
- `knowledge_edges` -- typed relations between nodes (depends_on, uses, related_to, ...).
- `wiki_page_nodes` -- optional join linking pages to nodes they cover.

The compiled artifacts power the Next.js dashboard at `https://app.nexus.dev/{org}/{repo}`:

- **Wiki browser** -- rendered pages with navigation, search, breadcrumbs.
- **Knowledge graph** -- interactive D3 / react-force-graph visualization driven by `knowledge_nodes` and `knowledge_edges`.
- **AI Q&A** -- chat grounded in `wiki_pages` + retrieval over the graph, answers cite page slugs.
- **Activity feed** -- timeline of compile jobs and the PRs that triggered them.

### How they stay in sync

```mermaid
flowchart TD
    subgraph repo [GitHub Repo]
        Raw[".nexus/raw/ (human-authored)"]
        Code["src/ (code)"]
    end

    subgraph nexus [Nexus Platform]
        Worker[ARQ Worker]
        PG[("Postgres: raw_sources, wiki_pages, knowledge_nodes, knowledge_edges")]
        Dashboard[Next.js Dashboard]
    end

    Raw -->|install webhook / raw-path PR diff| Worker
    Code -->|code-path PR diff| Worker
    Worker -->|compile via Claude| PG
    PG -->|read API| Dashboard
    Developer[Developer Browser] --> Dashboard
```

- **Install:** Nexus ingests `.nexus/raw/` once and compiles the full knowledge base into Postgres.
- **PR with raw edits:** Nexus re-ingests the affected files and recompiles the slice they feed.
- **PR with code edits:** Nexus updates the wiki/graph slice whose `file_paths` intersect the diff.
- Nothing ever writes back to the repo. To change what the compiled wiki says, developers edit `.nexus/raw/`.

### Obsidian round-trip invariant (design constraint)

The compiled wiki in Postgres must always be a **lossless projection of an Obsidian-compatible vault**. Concretely, at any point an exporter should be able to walk `wiki_pages` and emit one `.md` file per row such that:

- Frontmatter is YAML-parseable (Obsidian reads it directly).
- Every cross-page reference in the body uses `[[slug]]` (Obsidian interlink syntax), never raw URLs or titles.
- Every row in `knowledge_edges` is mirrored in the source page's body as a Dataview-style inline field: `relation:: [[target-slug]]`. That way the typed graph survives even when rendered by tooling that only knows untyped links plus Dataview.

This keeps the door open to rendering the wiki + graph through Obsidian, Quartz, Foam, or any Obsidian-compatible surface later -- without re-shaping the backend. The Context Guard enforces this invariant on every compile (Sprint 2) and every delta (Sprint 3).

---

## Part 5: How Developers Interact with the Wiki

Three access patterns, from lightweight to rich:

### 1. In the repo (the raw notes they already wrote)
Developers read their own `.nexus/raw/**` files directly on GitHub or in their IDE. These are just the human-authored sources; no compiled wiki lives in the repo in v1.

### 2. Web dashboard (browse + ask)
At `https://app.nexus.dev/{org}/{repo}`, developers get:
- A rendered, searchable wiki with navigation
- An interactive knowledge graph
- An AI chat panel: ask questions, get answers with wiki citations
- Activity timeline of all wiki changes

### 3. MCP in IDE (inline context)
An MCP server that IDEs like Cursor can connect to. When a developer is working in `src/auth/jwt.py`, the MCP can surface the relevant `JWT_Tokens` wiki page as context. Developers can also query the wiki from the IDE's AI chat.

---

## Part 6: Backend Development Plan (Detailed)

This is what we build first. The backend is the entire product brain -- once this works end-to-end, we have a functioning tool before any frontend or MCP work begins.

### Directory structure (backend only)

```
nexus/
├── nexus/                          # Python package
│   ├── __init__.py
│   ├── main.py                     # FastAPI app, lifespan, router mounting
│   ├── config.py                   # Pydantic Settings (env-based)
│   ├── database.py                 # Async SQLAlchemy engine + session factory
│   ├── api/
│   │   ├── __init__.py
│   │   ├── webhooks.py             # POST /webhooks/github (signature verify + dispatch)
│   │   ├── health.py               # GET /health
│   │   └── dependencies.py         # Shared FastAPI deps (db session, github client)
│   ├── models/
│   │   ├── __init__.py
│   │   ├── db.py                   # SQLAlchemy: Installation, Repo, Job, RawSource, WikiPage, KnowledgeNode, KnowledgeEdge, WikiPageNode
│   │   ├── events.py               # Pydantic: GitHub webhook payloads
│   │   ├── nodes.py                # Pydantic: CompiledKnowledgeBase, CompiledPage, CompiledNode, CompiledEdge, NodeFrontmatter, KnowledgeBaseDelta
│   │   └── diff.py                 # Pydantic: ChangeSet, FileDiff, Hunk
│   ├── services/
│   │   ├── __init__.py
│   │   ├── github_client.py        # GitHub REST API (App JWT auth, Trees, Contents)
│   │   └── llm_client.py           # Anthropic SDK wrapper + Jinja prompt loader
│   ├── core/
│   │   ├── __init__.py
│   │   ├── raw_ingester.py         # Walk .nexus/raw/**, filter allowed extensions, upsert raw_sources
│   │   ├── diff_parser.py          # Parse GitHub PR file list into ChangeSet (raw_changes / code_changes)
│   │   ├── node_resolver.py        # Resolve AffectedSlice from Postgres (replaces INDEX.md parsing)
│   │   └── scribe.py               # LLM orchestration: init compile + incremental delta
│   ├── wiki/
│   │   ├── __init__.py
│   │   ├── schema.py               # NodeFrontmatter + CompiledKnowledgeBase validation
│   │   └── linter.py               # Context Guard: schema, interlinks, edge validity, no dupes
│   ├── pipelines/
│   │   ├── __init__.py
│   │   ├── initialization.py       # Install -> ingest -> compile -> DB upsert
│   │   └── pr_update.py            # PR webhook -> dual-trigger update -> DB upsert
│   ├── worker/
│   │   ├── __init__.py
│   │   ├── tasks.py                # ARQ task definitions (init_repo, process_pr)
│   │   └── settings.py             # ARQ worker config
│   └── prompts/
│       ├── system.md               # Shared system prompt; includes Obsidian round-trip invariant (YAML frontmatter, [[slug]] links, Dataview `relation:: [[target]]` lines mirroring every knowledge_edge)
│       ├── init_compile.md         # Prompt: raw corpus -> {pages, nodes, edges} respecting the invariant
│       ├── init_compile_chunk.md   # Prompt: per-file partial KB (map step)
│       ├── init_merge.md           # Prompt: merge partial KBs (reduce step)
│       └── pr_update.md            # Prompt: diff + current slice -> KnowledgeBaseDelta (must preserve the invariant on every upserted page)
├── tests/
│   ├── __init__.py
│   ├── conftest.py                 # Fixtures: mock payloads, sample diffs, fake raw corpora
│   ├── test_diff_parser.py
│   ├── test_node_resolver.py
│   ├── test_raw_ingester.py
│   └── test_linter.py
├── alembic/
│   ├── alembic.ini
│   └── versions/
├── pyproject.toml
├── Dockerfile
├── docker-compose.yml              # FastAPI + Postgres + Redis (backend-only for now)
├── .env.example
└── README.md
```

---

### Sprint 1: Foundation -- Detailed Build Plan

**Goal:** Install the Nexus GitHub App on a real repo, see the `installation` webhook arrive, see the installation + repos saved in Postgres, and see an ARQ worker pick up a placeholder `init_repo` job and mark it completed. The entire thing runs locally via Docker Compose + smee.io for webhook forwarding.

---

#### S1.1 -- Project scaffold

Create the project skeleton that boots a running FastAPI server in Docker.

**Files to create:**

`pyproject.toml`
- Project metadata (name = "nexus", version = "0.1.0", python = ">=3.12")
- Dependencies:
  - fastapi, uvicorn[standard]
  - pydantic, pydantic-settings
  - sqlalchemy[asyncio], asyncpg, alembic
  - arq, redis
  - httpx
  - anthropic (not used yet, but installed for Sprint 2)
  - pyjwt[crypto] (for GitHub App JWT signing)
  - python-frontmatter (not used yet)
  - jinja2 (not used yet)
- Dev dependencies: pytest, pytest-asyncio, ruff

`docker-compose.yml`
- **app**: builds from Dockerfile, runs `uvicorn nexus.main:app --host 0.0.0.0 --port 8000 --reload`, mounts `./nexus` as volume for live reload, depends on postgres + redis, loads `.env`
- **worker**: same image, runs `arq nexus.worker.settings.WorkerSettings`, depends on postgres + redis, loads `.env`
- **postgres**: postgres:16-alpine, port 5432, creates `nexus` database
- **redis**: redis:7-alpine, port 6379

`Dockerfile`
- Python 3.12-slim base, install deps via pip from pyproject.toml, copy source

`.env.example`
```
DATABASE_URL=postgresql+asyncpg://nexus:nexus@postgres:5432/nexus
REDIS_URL=redis://redis:6379
GITHUB_APP_ID=
GITHUB_PRIVATE_KEY_PATH=./private-key.pem
GITHUB_WEBHOOK_SECRET=
ANTHROPIC_API_KEY=
```

`nexus/config.py`
- Pydantic `Settings` class with all env vars above
- `model_config = SettingsConfigDict(env_file=".env")`
- A module-level `settings = Settings()` singleton (or lazy loader)

`nexus/main.py`
- FastAPI app with `lifespan` async context manager
- On startup: create async SQLAlchemy engine, create Redis connection pool, store both on `app.state`
- On shutdown: dispose engine, close Redis pool
- Mount the health router

`nexus/api/health.py`
- `GET /health` -> `{"status": "ok", "version": "0.1.0"}`

**Checkpoint:** `docker compose up` boots all 4 containers. `curl localhost:8000/health` returns 200.

---

#### S1.2 -- Database layer

Set up the database models and migrations so we can persist installations, repos, and jobs.

**Files to create:**

`nexus/database.py`
- `create_async_engine` using `settings.DATABASE_URL`
- `async_sessionmaker` configured with `expire_on_commit=False`
- A `get_session()` async generator for FastAPI dependency injection
- A `Base` declarative base (or use `DeclarativeBase` from SQLAlchemy 2.0)

`nexus/models/__init__.py` -- re-export all models

`nexus/models/db.py` -- three tables:

```python
class Installation(Base):
    __tablename__ = "installations"
    id: Mapped[uuid.UUID]                # PK, server-default uuid
    github_installation_id: Mapped[int]  # unique, from GitHub
    account_login: Mapped[str]           # "acme" or "kaidenmerchant"
    account_type: Mapped[str]            # "Organization" or "User"
    status: Mapped[str]                  # "active" / "suspended" / "deleted"
    created_at: Mapped[datetime]
    repos: Mapped[list["Repo"]] = relationship(back_populates="installation")

class Repo(Base):
    __tablename__ = "repos"
    id: Mapped[uuid.UUID]
    installation_id: Mapped[uuid.UUID]   # FK -> installations.id
    github_repo_id: Mapped[int]
    full_name: Mapped[str]               # "acme/backend"
    default_branch: Mapped[str]          # "main"
    status: Mapped[str]                  # "pending_init" / "initializing" / "active" / "error"
    created_at: Mapped[datetime]
    installation: Mapped["Installation"] = relationship(back_populates="repos")
    jobs: Mapped[list["Job"]] = relationship(back_populates="repo")

class Job(Base):
    __tablename__ = "jobs"
    id: Mapped[uuid.UUID]
    repo_id: Mapped[uuid.UUID]           # FK -> repos.id
    job_type: Mapped[str]                # "init" / "pr_update"
    trigger_ref: Mapped[str]             # "installation" or "PR #42"
    status: Mapped[str]                  # "queued" / "running" / "completed" / "failed"
    error_message: Mapped[str | None]
    created_at: Mapped[datetime]
    started_at: Mapped[datetime | None]
    completed_at: Mapped[datetime | None]
    repo: Mapped["Repo"] = relationship(back_populates="jobs")
```

`alembic/` setup:
- `alembic init alembic` (use async template)
- Configure `alembic.ini` to read `DATABASE_URL` from env
- Edit `alembic/env.py` to use async engine and import `Base.metadata`
- Generate initial migration: `alembic revision --autogenerate -m "initial schema"`

**Checkpoint:** `docker compose up`, then `docker compose exec app alembic upgrade head` creates the three tables. Can verify with `psql`.

---

#### S1.3 -- GitHub App auth

Build the GitHub API client with just enough to authenticate as the App. No repo-level API calls yet -- those are Sprint 2.

**Manual prerequisite (documented in README):**
1. Go to GitHub Settings > Developer settings > GitHub Apps > New GitHub App
2. App name: "Nexus Dev" (or similar)
3. Homepage URL: `http://localhost:8000`
4. Webhook URL: a smee.io channel URL (e.g. `https://smee.io/your-channel`)
5. Webhook secret: generate one, save to `.env`
6. Permissions needed: Contents (read & write), Pull Requests (read & write), Metadata (read)
7. Subscribe to events: Installation, Pull Request, Push
8. Generate a private key, download it, save as `private-key.pem`
9. Note the App ID, save to `.env`
10. Install smee-client locally: `npx smee -u https://smee.io/your-channel -t http://localhost:8000/webhooks/github`

**Files to create:**

`nexus/services/__init__.py`

`nexus/services/github_client.py`
- `GitHubClient` class initialized with `app_id`, `private_key_path`, `base_url="https://api.github.com"`
- `_generate_jwt()` -> creates a JWT signed with RS256 using the private key, expires in 10 minutes, iss = app_id
- `get_installation_token(installation_id: int) -> str` -> `POST /app/installations/{id}/access_tokens` with the JWT, returns the token string
- Token caching: store token + expiry, regenerate when expired (tokens last 1 hour)
- Internal `_request(method, url, token, ...)` helper using httpx.AsyncClient

**Checkpoint:** Write a quick test script or pytest that generates a JWT and calls `GET /app` to verify the App credentials are valid.

---

#### S1.4 -- Webhook handler

The endpoint that GitHub hits when events happen. Verifies the signature, parses the event, and saves data to Postgres.

**Files to create:**

`nexus/models/events.py` -- Pydantic models for the webhook payloads we care about:

```python
class WebhookHeaders(BaseModel):
    event: str          # X-GitHub-Event header value
    delivery: str       # X-GitHub-Delivery header value
    signature: str      # X-Hub-Signature-256 header value

class InstallationEvent(BaseModel):
    action: str         # "created", "deleted", "suspend", "unsuspend"
    installation: InstallationPayload
    repositories: list[RepoPayload] | None = None

class InstallationPayload(BaseModel):
    id: int
    account: AccountPayload
    # ... other fields we need

class AccountPayload(BaseModel):
    login: str
    type: str           # "Organization" or "User"

class RepoPayload(BaseModel):
    id: int
    name: str
    full_name: str
    default_branch: str = "main"

class PullRequestEvent(BaseModel):
    action: str         # "opened", "synchronize", "closed"
    number: int
    pull_request: PullRequestPayload
    repository: RepoPayload
    installation: InstallationRef

class InstallationRef(BaseModel):
    id: int

class PullRequestPayload(BaseModel):
    title: str
    body: str | None = None
    base: BranchRef
    head: BranchRef
    merged: bool = False

class BranchRef(BaseModel):
    ref: str
    sha: str
```

`nexus/api/webhooks.py`:
- `POST /webhooks/github`
- Step 1: Read the raw request body as bytes
- Step 2: Extract `X-Hub-Signature-256`, `X-GitHub-Event`, `X-GitHub-Delivery` headers
- Step 3: Verify HMAC -- `hmac.new(secret, body, hashlib.sha256).hexdigest()` matches the signature
- Step 4: Parse the JSON body based on the event type:
  - `installation` event with `action == "created"`:
    - Create an `Installation` record in Postgres
    - Create a `Repo` record for each repo in the payload (status = `"pending_init"`)
    - Create a `Job` record for each repo (type = `"init"`, status = `"queued"`)
    - (Do NOT enqueue to ARQ yet -- that's S1.6)
  - `pull_request` event with `action in ("opened", "synchronize")`:
    - Look up the `Repo` by `github_repo_id`
    - Create a `Job` record (type = `"pr_update"`, trigger_ref = `"PR #{number}"`, status = `"queued"`)
  - All other events: log and ignore
- Step 5: Return `{"status": "accepted"}` with 200

`nexus/api/dependencies.py`:
- `get_db()` -- yields an async session from the session factory
- `get_github_client()` -- returns a `GitHubClient` instance (can be cached on app.state)

Mount the webhook router in `main.py`.

**Checkpoint:** Run smee.io proxy, install the GitHub App on a test repo. See the `installation` webhook arrive at `/webhooks/github`. Check Postgres -- the `installations`, `repos`, and `jobs` tables have records.

---

#### S1.5 -- Background worker

Set up ARQ so jobs actually get processed (even though the processing is a placeholder).

**Files to create:**

`nexus/worker/__init__.py`

`nexus/worker/settings.py`:
```python
from nexus.worker.tasks import init_repo_task, process_pr_task

class WorkerSettings:
    functions = [init_repo_task, process_pr_task]
    redis_settings = RedisSettings.from_dsn(settings.REDIS_URL)
```

`nexus/worker/tasks.py`:
```python
async def init_repo_task(ctx: dict, job_id: str, repo_id: str):
    """Placeholder -- will be replaced with the real initialization pipeline in Sprint 2."""
    async with get_session_context() as session:
        job = await session.get(Job, job_id)
        job.status = "running"
        job.started_at = datetime.utcnow()
        await session.commit()

        logger.info(f"[init_repo] Starting initialization for repo {repo_id}")
        # --- placeholder: this is where the real pipeline will go ---
        await asyncio.sleep(2)  # simulate work
        logger.info(f"[init_repo] Initialization complete for repo {repo_id}")

        job.status = "completed"
        job.completed_at = datetime.utcnow()
        await session.commit()

async def process_pr_task(ctx: dict, job_id: str, repo_id: str, pr_number: int):
    """Placeholder -- will be replaced with the real PR update pipeline in Sprint 3."""
    async with get_session_context() as session:
        job = await session.get(Job, job_id)
        job.status = "running"
        job.started_at = datetime.utcnow()
        await session.commit()

        logger.info(f"[process_pr] Processing PR #{pr_number} for repo {repo_id}")
        await asyncio.sleep(2)
        logger.info(f"[process_pr] Done processing PR #{pr_number}")

        job.status = "completed"
        job.completed_at = datetime.utcnow()
        await session.commit()
```

Note: `get_session_context()` is a standalone async context manager (not a FastAPI dependency) so the worker can use it outside of request context. Add this to `database.py`.

**Checkpoint:** `docker compose up` starts the worker container. It connects to Redis and logs "Worker started." No jobs are processing yet because nothing is enqueuing them.

---

#### S1.6 -- Wire it up (end-to-end)

Connect the webhook handler to the ARQ worker so that receiving a webhook enqueues a real job.

**Changes to existing files:**

`nexus/main.py` -- on startup, create an ARQ Redis pool and store it on `app.state.arq_pool`:
```python
from arq import create_pool
app.state.arq_pool = await create_pool(RedisSettings.from_dsn(settings.REDIS_URL))
```

`nexus/api/dependencies.py` -- add `get_arq_pool(request)` dependency that returns `request.app.state.arq_pool`

`nexus/api/webhooks.py` -- after creating `Job` records in the DB:
- For `installation` created: `await arq_pool.enqueue_job("init_repo_task", job_id=str(job.id), repo_id=str(repo.id))`
- For `pull_request`: `await arq_pool.enqueue_job("process_pr_task", job_id=str(job.id), repo_id=str(repo.id), pr_number=number)`

**End-to-end verification flow:**

```
1. docker compose up                    # boots app, worker, postgres, redis
2. docker compose exec app alembic upgrade head  # runs migrations
3. npx smee -u https://smee.io/xxx -t http://localhost:8000/webhooks/github
4. Go to GitHub > Install the Nexus Dev app on a test repo
5. Watch the logs:

   app     | POST /webhooks/github 200 (installation event)
   app     | Saved installation 12345 with 1 repo(s)
   app     | Enqueued init_repo job abc-123 for repo acme/test-repo
   worker  | [init_repo] Starting initialization for repo acme/test-repo
   worker  | [init_repo] Initialization complete for repo acme/test-repo

6. Check Postgres:
   - installations: 1 row (github_installation_id=12345, account_login=acme)
   - repos: 1 row (full_name=acme/test-repo, status=pending_init)
   - jobs: 1 row (job_type=init, status=completed)
```

---

#### Sprint 1 -- Summary of all files created

```
nexus/
├── nexus/
│   ├── __init__.py
│   ├── main.py               # FastAPI app + lifespan + router mounting
│   ├── config.py              # Pydantic Settings
│   ├── database.py            # Async engine, session factory, get_session_context()
│   ├── api/
│   │   ├── __init__.py
│   │   ├── health.py          # GET /health
│   │   ├── webhooks.py        # POST /webhooks/github
│   │   └── dependencies.py    # get_db, get_github_client, get_arq_pool
│   ├── models/
│   │   ├── __init__.py
│   │   ├── db.py              # Installation, Repo, Job (SQLAlchemy)
│   │   └── events.py          # GitHub webhook payloads (Pydantic)
│   ├── services/
│   │   ├── __init__.py
│   │   └── github_client.py   # GitHubClient (JWT auth + token exchange only)
│   └── worker/
│       ├── __init__.py
│       ├── tasks.py            # init_repo_task, process_pr_task (placeholders)
│       └── settings.py         # ARQ WorkerSettings
├── alembic/
│   ├── alembic.ini
│   ├── env.py
│   └── versions/
│       └── 001_initial_schema.py
├── tests/
│   ├── __init__.py
│   └── conftest.py
├── pyproject.toml
├── Dockerfile
├── docker-compose.yml
├── .env.example
└── README.md
```

18 files total. Everything needed to go from `docker compose up` to a working webhook-to-worker pipeline with persistent state.

---

### Sprint 2: Initialization Pipeline (todos S2-*)

The goal: when Nexus is installed on a repo, it ingests `.nexus/raw/**` and compiles an initial wiki + knowledge graph into Postgres. Nothing is committed back to the repo.

**2A -- GitHub read client**

Extend `github_client.py` with just what the init needs -- no branch / PR methods (not required in v1):

- `list_tree_prefix(owner, repo, ref, prefix=".nexus/raw/") -> list[TreeEntry]` -- calls the Trees API recursively and filters to paths under `prefix`. Returns `(path, sha, size)` per file.
- `get_file_content(owner, repo, path, ref) -> bytes` -- base64-decoded content via the Contents API.
- `get_file_contents_batch(owner, repo, paths, ref) -> dict[str, bytes]` -- parallel fetches via `asyncio.gather`, capped by a semaphore (e.g. 8 concurrent).
- All calls use the installation token from S1.3; retry with exponential backoff on 403 (token expiry) and 429 (rate limit).

**2B -- LLM client**

- `LLMClient` wrapping the Anthropic SDK.
- `async def complete(system, messages, model, max_tokens) -> str` -- basic completion.
- `async def complete_structured(system, messages, response_schema: type[BaseModel]) -> BaseModel` -- uses Claude's `tool_use` to emit JSON matching a Pydantic model.
- Prompt template loader: reads `.md` files from `prompts/` with Jinja2 variable substitution.
- Retry with exponential backoff on 429 / 529.

**2C -- Raw source ingester**

`core/raw_ingester.py`:

1. Call `list_tree_prefix(..., prefix=".nexus/raw/")`.
2. Filter to allowed extensions -- `.md`, `.txt`, `.rst`. Anything else goes into a `skipped_files: list[str]` warning attached to the `Job`.
3. For each allowed file: fetch content, decode UTF-8 (replacement on errors), compute `sha256`, and upsert into `raw_sources` keyed by `(repo_id, path)`. Rows for deleted paths are removed.
4. Emit a deterministic `RawCorpus` for the compile step:

```python
class RawFile(BaseModel):
    path: str                        # e.g. "decisions/2026-04-chose-postgres.md"
    content: str
    content_hash: str

class RawCorpus(BaseModel):
    repo_full_name: str
    files: list[RawFile]
    skipped: list[str]
```

**2D -- Data model + migration**

New tables (single Alembic migration):

```python
class RawSource(Base):
    __tablename__ = "raw_sources"
    id: Mapped[uuid.UUID]
    repo_id: Mapped[uuid.UUID]                 # FK -> repos.id
    path: Mapped[str]                          # relative to repo root
    content: Mapped[str]
    content_hash: Mapped[str]                  # sha256 hex
    updated_at: Mapped[datetime]
    __table_args__ = (UniqueConstraint("repo_id", "path"),)

class WikiPage(Base):
    __tablename__ = "wiki_pages"
    id: Mapped[uuid.UUID]
    repo_id: Mapped[uuid.UUID]
    slug: Mapped[str]                          # "authentication"
    title: Mapped[str]
    content_md: Mapped[str]                    # full markdown body
    frontmatter_json: Mapped[dict]             # node_type, related, sources, confidence, file_paths
    updated_at: Mapped[datetime]
    __table_args__ = (UniqueConstraint("repo_id", "slug"),)

class KnowledgeNode(Base):
    __tablename__ = "knowledge_nodes"
    id: Mapped[uuid.UUID]
    repo_id: Mapped[uuid.UUID]
    name: Mapped[str]                          # canonical concept name
    node_type: Mapped[str]                     # "concept" | "module" | "decision" | "entity"
    description: Mapped[str]
    updated_at: Mapped[datetime]
    __table_args__ = (UniqueConstraint("repo_id", "name"),)

class KnowledgeEdge(Base):
    __tablename__ = "knowledge_edges"
    id: Mapped[uuid.UUID]
    repo_id: Mapped[uuid.UUID]
    source_node_id: Mapped[uuid.UUID]          # FK -> knowledge_nodes.id
    target_node_id: Mapped[uuid.UUID]          # FK -> knowledge_nodes.id
    relation: Mapped[str]                      # "depends_on" | "uses" | "related_to" | ...
    evidence: Mapped[str | None]               # short quote from raw corpus
    __table_args__ = (UniqueConstraint("repo_id", "source_node_id", "target_node_id", "relation"),)

class WikiPageNode(Base):                      # optional join
    __tablename__ = "wiki_page_nodes"
    page_id: Mapped[uuid.UUID]                 # FK -> wiki_pages.id
    node_id: Mapped[uuid.UUID]                 # FK -> knowledge_nodes.id
```

Generate: `alembic revision --autogenerate -m "raw_sources + wiki + graph"`.

**2E -- Initialization pipeline**

`pipelines/initialization.py` orchestrates:

```mermaid
flowchart TD
    Start[init_repo_task fires] --> Fetch[raw_ingester: fetch .nexus/raw/**]
    Fetch --> Store[Upsert raw_sources]
    Store --> Plan{Corpus fits one prompt?}
    Plan -->|yes| OneShot["Claude: init_compile.md -> {pages, nodes, edges}"]
    Plan -->|no| Map["Claude map: init_compile_chunk.md per file -> partial KB"]
    Map --> Reduce["Claude reduce: init_merge.md -> unified {pages, nodes, edges}"]
    OneShot --> Guard
    Reduce --> Guard
    Guard[Context Guard lint] --> Upsert[Transactional upsert to Postgres]
    Upsert --> Mark[repos.status = active; job.status = completed]
```

Step-by-step:

1. Load the raw corpus via `raw_ingester.ingest(repo_id)`.
2. Estimate input tokens. If under budget (~150K input tokens), make a single Claude call with `prompts/init_compile.md`.
3. If over budget, map per file with `prompts/init_compile_chunk.md` (parallelized, ~5 concurrent), then reduce the partial outputs with `prompts/init_merge.md`.
4. Claude always returns a `CompiledKnowledgeBase` via `tool_use`:

```python
class CompiledPage(BaseModel):
    slug: str
    title: str
    content_md: str
    frontmatter: NodeFrontmatter

class CompiledNode(BaseModel):
    name: str
    node_type: Literal["concept", "module", "decision", "entity"]
    description: str
    page_slugs: list[str]            # pages that cover this node

class CompiledEdge(BaseModel):
    source: str                      # node name
    target: str                      # node name
    relation: str
    evidence: str | None

class CompiledKnowledgeBase(BaseModel):
    pages: list[CompiledPage]
    nodes: list[CompiledNode]
    edges: list[CompiledEdge]
```

5. Context Guard (`wiki/linter.py`) validates:
   - Frontmatter schema passes on every page.
   - Every `[[slug]]` interlink in page content resolves to another page in the payload.
   - Every `CompiledEdge.source` / `target` resolves to a `CompiledNode.name`.
   - No duplicate `slug` or node `name` values.
   - **Obsidian round-trip invariant:** `wiki_pages` must be a lossless projection of an Obsidian vault. Specifically: (a) frontmatter is YAML-parseable; (b) all cross-page references use `[[slug]]` (never URLs or raw titles); (c) every typed edge landing in `knowledge_edges` also appears in the page body as a Dataview-style inline field -- `relation:: [[target-slug]]` -- so a future exporter can regenerate a graph-equivalent `.md` folder without consulting the DB. The linter fails the page if a `knowledge_edge` has no corresponding Dataview line on its source page.
6. Transactional upsert: `wiki_pages`, `knowledge_nodes`, `knowledge_edges`, and the `wiki_page_nodes` join all commit in one DB transaction. Set `repos.status = "active"` and `jobs.status = "completed"`.

**Checkpoint:** Install Nexus on a test repo that has `.nexus/raw/` pre-populated. Inspect Postgres: `raw_sources` matches the folder, `wiki_pages` has compiled content, `knowledge_nodes` + `knowledge_edges` describe the graph, `jobs` row is `completed`.

**2F -- Frontend (deferred to Sprint 4)**

The read-only FastAPI surface (`GET /repos/{id}/wiki`, `GET /repos/{id}/wiki/{slug}`, `GET /repos/{id}/graph`) plus the Next.js dashboard are **out of scope for Sprint 2**; they become Sprint 4. Sprint 2 ships with curl-verifiable DB state and nothing else on the frontend.

**Follow-ups (not in v1):**
- PDF / image ingestion (`pypdf`, Claude vision).
- Incremental re-ingestion that skips files whose `content_hash` is unchanged (optimization; the transactional upsert is already idempotent).
- Per-repo config at `.nexus/config.yml` for custom allow-lists or alternate raw-folder paths.

---

### Sprint 3: PR Update Pipeline (todos S3-*)

The goal: when a PR is opened on an installed repo, Nexus detects whether the diff touches code, `.nexus/raw/`, or both, and updates the compiled KB in Postgres. No Shadow PRs in v1.

**3A -- Diff parser**

`core/diff_parser.py`:

- Input: the structured file list from `GET /repos/{owner}/{repo}/pulls/{n}/files` (GitHub returns path, status, additions, deletions, and patch per file -- simpler than parsing raw unified diffs).
- Output: `ChangeSet` with two partitions: `raw_changes` (paths under `.nexus/raw/`) and `code_changes` (everything else).

```python
class Hunk(BaseModel):
    header: str
    added_lines: list[str]
    removed_lines: list[str]

class FileDiff(BaseModel):
    path: str
    previous_path: str | None
    status: Literal["added", "modified", "removed", "renamed"]
    hunks: list[Hunk]
    additions: int
    deletions: int

class ChangeSet(BaseModel):
    pr_number: int
    pr_title: str
    pr_body: str
    base_ref: str
    head_ref: str
    raw_changes: list[FileDiff]
    code_changes: list[FileDiff]
```

**3B -- Node resolver (DB-backed)**

`core/node_resolver.py` queries Postgres rather than parsing `INDEX.md`:

- For `raw_changes`: look up `raw_sources` rows by `(repo_id, path)`, then find all `wiki_pages` whose `frontmatter_json.sources` references those raw paths, plus `knowledge_nodes` linked via `wiki_page_nodes`.
- For `code_changes`: find `wiki_pages` whose `frontmatter_json.file_paths` prefix-matches a changed path. Linked nodes come via the join.
- For ambiguous orphans: call Claude with a short classifier prompt ("given these changes and these known pages, which slugs should be updated?").

```python
class AffectedSlice(BaseModel):
    pages: list[WikiPage]
    nodes: list[KnowledgeNode]
    edges: list[KnowledgeEdge]
    new_page_candidates: list[str]       # proposed slugs Claude can create
    reason_by_slug: dict[str, str]       # "direct_path_match" | "raw_source_link" | "llm_classified"
```

**3C -- Incremental Scribe**

`core/scribe.py` gains an incremental mode driven by `prompts/pr_update.md`. Inputs:

- PR metadata (title, body).
- Diff excerpts from both `raw_changes` and `code_changes`.
- Freshly re-ingested raw text (when `raw_changes` is non-empty).
- The current `AffectedSlice` (pages, nodes, edges).

Output: a `KnowledgeBaseDelta` -- insert / update / delete operations for pages, nodes, and edges. For large PRs the Scribe batches the slice by page group to stay within context.

```python
class WikiPageOp(BaseModel):
    op: Literal["upsert", "delete"]
    slug: str
    page: CompiledPage | None            # required for upsert
    change_summary: str

class KnowledgeNodeOp(BaseModel):
    op: Literal["upsert", "delete"]
    name: str
    node: CompiledNode | None

class KnowledgeEdgeOp(BaseModel):
    op: Literal["upsert", "delete"]
    source: str
    target: str
    relation: str
    edge: CompiledEdge | None

class KnowledgeBaseDelta(BaseModel):
    pages: list[WikiPageOp]
    nodes: list[KnowledgeNodeOp]
    edges: list[KnowledgeEdgeOp]
```

**3D -- Context Guard linter**

`wiki/linter.py` validates the delta against the **post-apply** state:

- Frontmatter schema passes on every upserted page.
- Every `[[slug]]` interlink in upserted pages resolves (delta or existing DB).
- Every `KnowledgeEdgeOp` endpoint exists (delta or DB) after applying the delta.
- No duplicate slugs or node names.
- Each updated page references at least one symbol from the diff (catches hallucinated, unrelated edits).
- **Obsidian round-trip invariant** (same rule as Sprint 2): YAML-parseable frontmatter; cross-page refs use `[[slug]]`; every typed edge in `knowledge_edges` has a matching `relation:: [[target-slug]]` Dataview line on its source page. Any `KnowledgeEdgeOp` without a corresponding body line on the source page fails the lint.

Errors block the upsert; the Job is marked `failed` with details. Warnings are attached to the Job for dashboard display.

**3E -- PR update pipeline (end-to-end)**

`pipelines/pr_update.py`:

```mermaid
sequenceDiagram
    participant W as Worker
    participant GH as GitHub API
    participant DB as Postgres
    participant AI as Claude (Scribe)
    participant CG as Context Guard

    W->>GH: GET /pulls/{n}/files
    W->>W: Split into raw_changes vs code_changes
    opt raw_changes non-empty
        W->>GH: Fetch changed .nexus/raw/** files
        W->>DB: Upsert RawSource rows
    end
    W->>DB: Resolve AffectedSlice (pages, nodes, edges)
    W->>AI: Diff + slice + pr_update.md prompt
    AI-->>W: KnowledgeBaseDelta
    W->>CG: Lint delta vs post-apply state
    alt lint passed
        W->>DB: Apply delta transactionally
        W->>DB: job.status = completed
    else lint failed
        W->>DB: job.status = failed; store errors
    end
```

1. Fetch PR file list from GitHub.
2. Partition into `raw_changes` vs `code_changes`.
3. For raw changes, re-ingest and upsert `raw_sources`.
4. Resolve the `AffectedSlice` from the DB.
5. Call the Scribe -> `KnowledgeBaseDelta`.
6. Run Context Guard on the delta (post-apply view).
7. On pass: apply the delta in one transaction; update `jobs.status = completed`.
8. On fail: store error details; `jobs.status = failed`.

**Checkpoint:** On a test repo with an initialized KB, open two PRs: one touching only code, one touching only `.nexus/raw/`. Watch Postgres -- affected rows reflect each diff's intent; unrelated rows are unchanged.

---

### What "done" looks like for the backend

When all three sprints are complete, the following works end-to-end without a frontend. The frontend dashboard (Sprint 4) is a thin reader over the same DB state.

1. Register the GitHub App (one-time, with a smee.io proxy for local dev).
2. Pre-populate `.nexus/raw/` on a test repo with a handful of `.md` notes (overview, decisions, architecture prose).
3. Install the app on that repo.
4. Nexus receives the `installation` webhook, ingests `.nexus/raw/`, and compiles the KB into Postgres. `raw_sources`, `wiki_pages`, `knowledge_nodes`, `knowledge_edges` are populated; the `jobs` row is `completed`.
5. Open a PR that modifies code. Nexus updates the affected wiki pages and graph rows in the DB. No Shadow PR, no repo commits.
6. Open a PR that edits a file in `.nexus/raw/`. Nexus re-ingests that file and recompiles the slice it feeds.
7. Verify DB state with `psql` or a curl to a future read API.

The output is the compiled knowledge base in Postgres -- everything after this (read API, dashboard, MCP) exposes it.
