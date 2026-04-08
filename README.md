# Nexus

Autonomous LLM-powered knowledge base guardian for GitHub repos.

Nexus is a GitHub App that watches your repositories and automatically maintains a structured knowledge base (wiki) as your codebase evolves. When a PR is opened, Nexus analyzes the diff, updates the relevant wiki pages, and opens a Shadow PR for human review.

## Quick Start

### Prerequisites

- Docker and Docker Compose
- A GitHub App registered for local development (see below)

### Local Development

1. Copy the example env file and fill in your credentials:

```bash
cp .env.example .env
```

2. Start all services:

```bash
docker compose up --build
```

This starts:
- **app** (FastAPI) on http://localhost:8000
- **worker** (ARQ background processor)
- **postgres** on port 5432
- **redis** on port 6379

3. Verify it's running:

```bash
curl http://localhost:8000/health
```

### Registering a GitHub App (for local dev)

1. Go to **GitHub Settings > Developer settings > GitHub Apps > New GitHub App**
2. Set the following:
   - **App name**: `Nexus Dev` (or any unique name)
   - **Homepage URL**: `http://localhost:8000`
   - **Webhook URL**: Create a channel at [smee.io](https://smee.io) and use that URL
   - **Webhook secret**: Generate a random string, save it to `.env` as `GITHUB_WEBHOOK_SECRET`
3. **Permissions**:
   - Contents: Read & write
   - Pull requests: Read & write
   - Metadata: Read-only
4. **Subscribe to events**: Installation, Pull request, Push
5. **Generate a private key**, download it, save as `private-key.pem` in the project root
6. Note the **App ID**, save to `.env` as `GITHUB_APP_ID`
7. Forward webhooks locally:

```bash
npx smee -u https://smee.io/YOUR_CHANNEL -t http://localhost:8000/webhooks/github
```

## Architecture

```
nexus/
├── nexus/              # Python package
│   ├── main.py         # FastAPI app entry point
│   ├── config.py       # Environment-based settings
│   ├── api/            # HTTP endpoints (webhooks, health)
│   ├── core/           # Business logic (diff parsing, node resolution, AI scribe)
│   ├── models/         # Pydantic + SQLAlchemy models
│   ├── services/       # External integrations (GitHub API, Anthropic, git)
│   ├── wiki/           # Wiki engine (index, linter, templates)
│   ├── pipelines/      # End-to-end orchestration (init, PR update)
│   ├── worker/         # ARQ background task definitions
│   └── prompts/        # LLM prompt templates
├── tests/
├── alembic/            # Database migrations
├── docker-compose.yml
├── Dockerfile
└── pyproject.toml
```
