# Ralph Loop - Autonomous AI Development Pipeline

> **Universal AI development infrastructure with two-gate review system**

Ralph Loop is a standalone service that enables autonomous, validated development across all your projects. It provides a two-gate review system (PLAN → CODE) with GPT-powered inspection, automated dispatcher, and structured feedback loops.

## Features

- **Two-Gate Review System**: PLAN gate (architecture review) + CODE gate (implementation review)
- **Universal Project Registry**: Works with KaiScout, Oneeko, and any future projects
- **Automated Review Dispatcher**: Respects 4/hour rate limit, handles queue deduplication
- **MCP Integration**: HTTP/SSE transport for ChatGPT remote access
- **Function-Based DB Context**: Safe, audited project database access (no arbitrary SQL)
- **Railway-First**: Designed for Railway deployment with simple secret management

---

## Architecture

### Components

1. **Project Registry API** (`/projects`) - Admin-only CRUD for project configurations
2. **Build Ingestion API** (`/builds`) - Accepts build artifacts from builders (Claude Code)
3. **Review Queue & Dispatcher** - Automatically dispatches pending reviews to GPT
4. **MCP Server** (`/mcp`) - Exposes tools for GPT to inspect builds and submit verdicts
5. **DB Context Service** - Safe database access for project context (schema, row counts, sample data)
6. **Secret Resolver** - Railway environment variable resolution (pluggable for AWS/GCP/Vault)

### Data Flow

```
Claude Code → POST /builds/ingest → ralph_builds table
                                   ↓
                            ralph_review_queue (auto-enqueued)
                                   ↓
Review Dispatcher (every 5min) → DISPATCHED (GPT polls via MCP)
                                   ↓
GPT → MCP tools → submit_inspection → ralph_inspections table
                                   ↓
                            PASS → approve_build → Deploy
                            FAIL → request_revision → Claude fixes → loop
```

---

## Deployment Guide

### 1. Railway Setup

#### Create New Service

1. Go to Railway dashboard
2. Click "New Project" → "Empty Project"
3. Add service: "Database" → PostgreSQL
4. Add service: "Database" → Redis
5. Add service: "GitHub Repo" → Connect `ralph-service` directory

#### Environment Variables

Set these in Railway service settings:

```bash
ENV=production
DATABASE_URL=${POSTGRESQL_URL}  # Auto-populated by Railway
REDIS_URL=${REDIS_URL}          # Auto-populated by Railway
ADMIN_API_KEY=<generate-secure-key>
REVIEW_RATE_LIMIT=4
REVIEW_RATE_WINDOW=3600
```

For project database connections (e.g., KaiScout):
```bash
KAISCOUT_DB_URL=postgresql://ralph_ro:<password>@<host>:5432/kaiscout_db
```

#### Deploy Settings

Railway should auto-detect:
- **Build Command**: `pip install -r requirements.txt`
- **Start Command**: `python -m uvicorn app.main:app --host 0.0.0.0 --port ${PORT}`
- **Health Check**: `/health`

If not, use `railway.json` config (already in repo).

---

### 2. Database Setup

#### Run Migrations

SSH into Railway service or use Railway CLI:

```bash
# Install Railway CLI
npm install -g @railway/cli

# Login
railway login

# Link to project
railway link

# Run migration
railway run alembic upgrade head
```

#### Create Read-Only DB User

For each project database (e.g., KaiScout), create a read-only user:

```sql
-- Connect to project database (e.g., kaiscout_db)
\c kaiscout_db;

-- Create ralph_ro user
CREATE USER ralph_ro WITH PASSWORD 'secure-password-here';

-- Grant SELECT on all tables in public schema
GRANT CONNECT ON DATABASE kaiscout_db TO ralph_ro;
GRANT USAGE ON SCHEMA public TO ralph_ro;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO ralph_ro;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO ralph_ro;

-- Verify
\du ralph_ro
```

Then set `KAISCOUT_DB_URL` in Ralph service environment variables.

---

### 3. Register Projects

Use the Project Registry API to register your projects:

```bash
curl -X POST https://ralph-loop.up.railway.app/projects \
  -H "Authorization: Bearer ${ADMIN_API_KEY}" \
  -H "Content-Type: application/json" \
  -d '{
    "project_id": "kaiscout",
    "name": "KaiScout",
    "repo_url": "https://github.com/user/kaiscout",
    "secrets_provider": "railway",
    "db_connection_ref": "railway:KAISCOUT_DB_URL",
    "db_context_mode": "metadata_only",
    "allowed_schemas": ["public"],
    "pii_fields": ["email", "phone", "address"]
  }'
```

---

## Usage

### 1. Submit a Build (Claude Code)

After completing a build cycle:

```bash
curl -X POST https://ralph-loop.up.railway.app/builds/ingest \
  -H "Content-Type: application/json" \
  -d '{
    "project_id": "kaiscout",
    "build_type": "CODE",
    "task_id": "feat-user-api",
    "task_description": "Build user management API",
    "commit_sha": "abc123def456",
    "branch": "feat-user-api",
    "changed_files": ["backend/app/api/users.py"],
    "diff_unified": "...",
    "test_exit_code": 0,
    "lint_exit_code": 0,
    "builder_signal": "READY_FOR_REVIEW"
  }'
```

**Response:**
```json
{
  "status": "ingested",
  "build_id": "2026-01-11T10:00:00-abc123",
  "inspection_status": "PENDING",
  "review_queued": true,
  "requires_human_approval": false
}
```

### 2. Review Dispatcher (Automatic)

The dispatcher runs every 5 minutes and:
1. Fetches pending reviews (priority desc, created_at asc)
2. Checks rate limit (4/hour via Redis token bucket)
3. Marks reviews as DISPATCHED
4. Logs dispatch events to `ralph_review_dispatches`

**No manual intervention needed** - GPT polls via MCP.

### 3. GPT Inspection (via MCP)

#### ChatGPT Setup

1. Enable Developer Mode in ChatGPT settings
2. Add MCP app:
   - Name: "Ralph Loop"
   - URL: `https://ralph-loop.up.railway.app/mcp/sse`
   - Transport: HTTP/SSE

#### GPT Workflow

GPT uses these MCP tools:

```typescript
// 1. Get latest build awaiting review
get_latest_ready_build({ project_id: "kaiscout" })

// 2. Review build artifact (diff, tests, review_bundle)

// 3. Submit verdict
submit_inspection({
  build_id: "2026-01-11T10:00:00-abc123",
  passed: false,
  issues: [
    {
      severity: "BLOCKER",
      file: "backend/app/api/users.py",
      line: 42,
      description: "SQL injection vulnerability in search endpoint",
      evidence: "User input directly interpolated into SQL query",
      fix_hint: "Use parameterized queries"
    }
  ],
  confidence: 0.95
})

// 4. Request revision
request_revision({
  build_id: "2026-01-11T10:00:00-abc123",
  feedback_summary: "Security vulnerability requires immediate fix",
  priority_fixes: [
    "Fix SQL injection in users.py:42",
    "Add input validation tests"
  ],
  patch_guidance: "Use SQLAlchemy query builder, not raw SQL"
})
```

### 4. Builder Fetches Feedback

Claude Code polls for pending revisions:

```bash
curl https://ralph-loop.up.railway.app/mcp/tools/get_pending_revisions \
  -X POST \
  -H "Content-Type: application/json" \
  -d '{"arguments": {"project_id": "kaiscout"}}'
```

**Response:**
```json
{
  "result": [
    {
      "revision_id": "rev-abc123",
      "build_id": "2026-01-11T10:00:00-abc123",
      "feedback_summary": "Security vulnerability requires immediate fix",
      "priority_fixes": [
        "Fix SQL injection in users.py:42",
        "Add input validation tests"
      ],
      "patch_guidance": "Use SQLAlchemy query builder, not raw SQL",
      "status": "PENDING"
    }
  ]
}
```

Claude fixes → submits new build → loop continues until PASS.

---

## API Reference

### Admin Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/projects` | Create project |
| GET | `/projects` | List projects |
| GET | `/projects/{id}` | Get project |
| PUT | `/projects/{id}` | Update project |
| DELETE | `/projects/{id}` | Delete project |
| GET | `/projects/{id}/schema` | Get DB schema |
| GET | `/projects/{id}/row-counts` | Get table row counts |
| POST | `/projects/{id}/sample-data` | Get sample data (PII redacted) |

**All require:** `Authorization: Bearer <ADMIN_API_KEY>`

### Build Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/builds/ingest` | Submit build artifact |
| POST | `/builds/test-ingest` | Test ingestion (dev only) |
| GET | `/builds/{id}` | Get build details |
| GET | `/builds/{id}/inspection` | Get inspection results |
| GET | `/builds/{id}/revisions` | Get revision feedback |

### MCP Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/mcp/sse` | MCP SSE stream |
| POST | `/mcp/tools/list` | List available tools |
| POST | `/mcp/tools/call` | Execute tool (MCP protocol) |
| POST | `/mcp/tools/{name}` | Execute tool (direct) |

---

## MCP Tools

### get_latest_ready_build

Get newest build with `builder_signal=READY_FOR_REVIEW` and `inspection_status=PENDING`.

**Input:**
```json
{
  "project_id": "kaiscout"
}
```

**Output:**
```json
{
  "build_id": "...",
  "build_type": "CODE",
  "diff_unified": "...",
  "test_exit_code": 0,
  "review_bundle": {...}
}
```

### submit_inspection

Submit inspection verdict (idempotent).

**Input:**
```json
{
  "build_id": "...",
  "passed": false,
  "issues": [{...}],
  "suggestions": "...",
  "confidence": 0.95
}
```

### request_revision

Request builder to fix issues.

**Input:**
```json
{
  "build_id": "...",
  "feedback_summary": "...",
  "priority_fixes": ["..."],
  "patch_guidance": "..."
}
```

### approve_build

Approve for deployment (verifies inspection passed).

**Input:**
```json
{
  "build_id": "...",
  "notes": "...",
  "human_approved_by": "..." // if requires_human_approval=true
}
```

### get_pending_revisions

Get pending revision feedback for builder (CRITICAL for loop).

**Input:**
```json
{
  "project_id": "kaiscout",
  "build_id": "..." // optional
}
```

---

## Testing

### Local Development

```bash
# Install dependencies
cd ralph-service
pip install -r requirements.txt

# Set environment variables
export DATABASE_URL=postgresql://user:pass@localhost:5432/ralph_db
export REDIS_URL=redis://localhost:6379
export ADMIN_API_KEY=test-key
export ENV=development

# Run migrations
alembic upgrade head

# Start server
python -m uvicorn app.main:app --reload --port 8080
```

### Test Endpoints

```bash
# Health check
curl http://localhost:8080/health

# Root
curl http://localhost:8080/

# Test build ingestion (dev only)
curl -X POST http://localhost:8080/builds/test-ingest \
  -H "Content-Type: application/json" \
  -d '{
    "project_id": "test",
    "build_type": "CODE",
    "commit_sha": "test123",
    "branch": "main",
    "builder_signal": "READY_FOR_REVIEW"
  }'
```

---

## Troubleshooting

### Issue: Scheduler not running

**Symptoms:** Reviews stay in PENDING status, dispatcher logs missing

**Fix:**
1. Check Railway logs: `railway logs`
2. Verify scheduler started: Look for "Ralph scheduler started" in logs
3. Check APScheduler version: `pip show apscheduler`

### Issue: Rate limit errors

**Symptoms:** "Rate limit exceeded" in logs, reviews not dispatching

**Fix:**
1. Check Redis connection: `redis-cli ping`
2. Verify `REDIS_URL` environment variable
3. Review falls back to "fail open" if Redis unavailable (check logs)

### Issue: Database connection failed

**Symptoms:** "Failed to connect to project database" errors

**Fix:**
1. Verify project's `db_connection_ref` is correct (e.g., `railway:KAISCOUT_DB_URL`)
2. Check environment variable exists: `echo $KAISCOUT_DB_URL`
3. Verify `ralph_ro` user exists in project database
4. Test connection: `psql $KAISCOUT_DB_URL -c "SELECT 1"`

### Issue: MCP tools not appearing in ChatGPT

**Symptoms:** ChatGPT can't see Ralph tools

**Fix:**
1. Verify `/mcp/sse` endpoint is accessible: `curl https://ralph-loop.up.railway.app/mcp/sse`
2. Check CORS settings in Railway environment (allow ChatGPT domains)
3. Re-add MCP app in ChatGPT Developer Mode settings
4. Check Railway logs for MCP connection attempts

---

## Security Notes

### Admin API Key

- **Generate strong key**: `openssl rand -hex 32`
- **Store securely**: Use Railway's environment variable encryption
- **Rotate regularly**: Update in Railway settings every 90 days

### Project Database Access

- **Read-only user**: Always use `ralph_ro` user, never admin credentials
- **PII redaction**: Configure `pii_fields` in project registry
- **Audit logging**: All DB access logged to `ralph_db_access_log`
- **Allowed schemas/tables**: Restrict access via `allowed_schemas` and `allowed_tables`

### Guardrails

**Automatic approval blocks:**
- Changes to protected paths (`backend/app/core/security`, etc.)
- Dependency file changes (`requirements.txt`, `package.json`, etc.)
- Max iteration limit (3 review cycles per build)

**Override:** Set `human_approved_by` in `approve_build` call.

---

## Future Enhancements

### Phase 1 (Current)
- ✅ Railway-first secret resolution
- ✅ MCP poll dispatch method
- ✅ Function-based DB context

### Phase 2 (Next)
- [ ] ChatGPT API integration (direct dispatch)
- [ ] Webhook notifications (alternative to polling)
- [ ] AWS Secrets Manager support
- [ ] GCP Secret Manager support

### Phase 3 (Future)
- [ ] Multi-instance dispatcher coordination
- [ ] Review analytics dashboard
- [ ] GitHub App integration (auto-submit on push)
- [ ] Slack notifications for approvals

---

## Support

- **Issues**: GitHub Issues (when repo is public)
- **Documentation**: This README + code comments
- **Architecture**: See `/docs/TWO_GATE_REVIEW_SYSTEM.md` (if available)

---

## License

Proprietary - All rights reserved.
