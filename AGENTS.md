# AGENTS.md

Cross-tool entry point for AI coding agents working on Nexus. Cursor, Claude Code, Codex, etc. should read this first.

## What Nexus is

A GitHub App that compiles a human-authored `.nexus/raw/` folder into a structured wiki + knowledge graph stored in Postgres, served via a web dashboard. Humans curate the raw context; the LLM compiles, maintains, and renders it. See `README.md` for user-facing docs.

## Canonical spec

`.cursor/plans/nexus_mvp_scaffold_26ea47b0.plan.md` is the authoritative design doc. **Read it before making any non-trivial change.** It covers architecture, data model, the three sprints, and the Obsidian round-trip invariant.

## Cursor-scoped rules

Detailed conventions live in `.cursor/rules/` as `.mdc` files. Other tools that don't auto-load Cursor rules should read them directly — they are plain markdown with YAML frontmatter:

- `project-context.mdc` — always-apply product overview, tech stack, sprint status, core invariants.
- `python-backend.mdc` — SQLAlchemy 2.0 async, Pydantic v2, Alembic workflow, async rules. Scoped to `nexus/**/*.py`.
- `wiki-invariants.mdc` — Obsidian round-trip invariant (YAML frontmatter, `[[slug]]` links, Dataview `relation::` lines mirroring `knowledge_edges`). Scoped to wiki / scribe / prompts / pipelines.
- `testing.mdc` — pytest-asyncio, mocked external APIs, rollback DB fixtures. Scoped to `tests/**`.

## Sprint status

- **Sprint 1 (Foundation)** — complete. Docker stack, DB models (`Installation`, `Repo`, `Job`), GitHub App JWT auth, webhook handler with HMAC verification, ARQ worker with placeholder tasks.
- **Sprint 2 (Init pipeline)** — in progress. Raw ingester over `.nexus/raw/**`, DB schema for `raw_sources` / `wiki_pages` / `knowledge_nodes` / `knowledge_edges` / `wiki_page_nodes`, compile pipeline (one-shot with map/reduce fallback), Context Guard.
- **Sprint 3 (PR update pipeline)** — queued. Dual-trigger pipeline (code diff → update affected wiki slice; `.nexus/raw/` diff → re-ingest and recompile), DB-only writes.
- **Sprint 4 (Frontend)** — queued. Read-only FastAPI surface (`GET /repos/{id}/wiki`, `/wiki/{slug}`, `/graph`) and Next.js dashboard.

## Key invariants (do not break)

1. **DB-only output.** Nexus never writes back to the user's repo. No Shadow PRs in v1.
2. **Obsidian round-trip.** See `wiki-invariants.mdc`.
3. **Raw sources are the only human input.** Users edit `.nexus/raw/**`; everything else is compiled.

## Local dev

`docker compose up --build` boots `app`, `worker`, `postgres`, `redis`. `docker compose exec app alembic upgrade head` applies migrations. Forward GitHub webhooks via `npx smee -u <channel> -t http://localhost:8000/webhooks/github`. See `README.md` for full GitHub App setup.
