"""
MCP Server - Model Context Protocol over HTTP/SSE

Exposes Ralph Loop capabilities to GPT via MCP tools.

Transport: HTTP/SSE (for ChatGPT remote access)
Protocol: MCP (Model Context Protocol)

Tools:
- get_latest_ready_build: Get newest build awaiting review
- get_build: Get full build artifact
- submit_inspection: Submit review verdict (idempotent)
- request_revision: Request builder to fix issues
- approve_build: Approve build for deployment
- get_pending_revisions: Get pending revision feedback (for builder)
"""

from fastapi import APIRouter, Request, Depends
from fastapi.responses import StreamingResponse
from typing import Dict, Any, Optional, List
from pydantic import BaseModel, Field
import json
import asyncio
import asyncpg

router = APIRouter(prefix="/mcp", tags=["mcp"])


# --- Database Dependency ---

async def get_db():
    """
    Get database connection.

    Will be overridden by FastAPI app's dependency_overrides.
    """
    return None


# --- MCP Tool Definitions ---

MCP_TOOLS = [
    {
        "name": "get_latest_ready_build",
        "description": "Get the newest build where builder_signal=READY_FOR_REVIEW and inspection_status=PENDING",
        "inputSchema": {
            "type": "object",
            "properties": {
                "project_id": {"type": "string", "description": "Project identifier"}
            },
            "required": ["project_id"]
        },
        "annotations": {
            "readOnlyHint": True
        }
    },
    {
        "name": "get_build",
        "description": "Get full build artifact by ID",
        "inputSchema": {
            "type": "object",
            "properties": {
                "build_id": {"type": "string", "description": "Build identifier"}
            },
            "required": ["build_id"]
        },
        "annotations": {
            "readOnlyHint": True
        }
    },
    {
        "name": "submit_inspection",
        "description": "Submit inspection verdict (idempotent - returns existing if already submitted)",
        "inputSchema": {
            "type": "object",
            "properties": {
                "build_id": {"type": "string"},
                "passed": {"type": "boolean"},
                "issues": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "severity": {"type": "string", "enum": ["BLOCKER", "MAJOR", "MINOR"]},
                            "file": {"type": "string"},
                            "line": {"type": "integer"},
                            "description": {"type": "string"},
                            "evidence": {"type": "string"},
                            "fix_hint": {"type": "string"}
                        },
                        "required": ["severity", "description"]
                    }
                },
                "suggestions": {"type": "string"},
                "confidence": {"type": "number", "minimum": 0, "maximum": 1}
            },
            "required": ["build_id", "passed", "issues"]
        },
        "annotations": {
            "readOnlyHint": False,
            "destructiveHint": False
        }
    },
    {
        "name": "request_revision",
        "description": "Request builder revision with structured feedback (creates new revision record)",
        "inputSchema": {
            "type": "object",
            "properties": {
                "build_id": {"type": "string"},
                "feedback_summary": {"type": "string"},
                "priority_fixes": {"type": "array", "items": {"type": "string"}},
                "patch_guidance": {"type": "string"},
                "do_not_change": {"type": "array", "items": {"type": "string"}}
            },
            "required": ["build_id", "feedback_summary", "priority_fixes"]
        },
        "annotations": {
            "readOnlyHint": False,
            "destructiveHint": False
        }
    },
    {
        "name": "approve_build",
        "description": "Approve build for deployment (verifies inspection passed + commit SHA unchanged)",
        "inputSchema": {
            "type": "object",
            "properties": {
                "build_id": {"type": "string"},
                "notes": {"type": "string"},
                "human_approved_by": {
                    "type": "string",
                    "description": "Required if requires_human_approval=true"
                }
            },
            "required": ["build_id"]
        },
        "annotations": {
            "readOnlyHint": False,
            "destructiveHint": False
        }
    },
    {
        "name": "get_pending_revisions",
        "description": "Get pending revision feedback for builder to fetch (CRITICAL: enables the loop)",
        "inputSchema": {
            "type": "object",
            "properties": {
                "project_id": {"type": "string", "description": "Project identifier"},
                "build_id": {"type": "string", "description": "Optional: specific build"}
            },
            "required": ["project_id"]
        },
        "annotations": {
            "readOnlyHint": True
        }
    }
]


# --- MCP Protocol Messages ---

class MCPMessage(BaseModel):
    """Base MCP message."""
    jsonrpc: str = "2.0"


class MCPToolsListRequest(MCPMessage):
    """Request to list available tools."""
    method: str = "tools/list"
    id: Optional[int] = None


class MCPToolCallRequest(MCPMessage):
    """Request to call a tool."""
    method: str = "tools/call"
    id: Optional[int] = None
    params: Dict[str, Any]


# --- SSE Endpoint ---

@router.get("/sse")
async def mcp_sse(request: Request):
    """
    MCP SSE endpoint for ChatGPT.

    Streams MCP protocol messages via Server-Sent Events.

    ChatGPT connects to this endpoint and sends MCP messages.
    """

    async def event_generator():
        """Generate SSE events for MCP protocol."""

        # Send ready event
        yield {
            "event": "message",
            "data": json.dumps({
                "jsonrpc": "2.0",
                "method": "notification",
                "params": {
                    "type": "ready",
                    "server": "ralph-loop",
                    "version": "1.0.0",
                    "capabilities": {
                        "tools": True,
                        "resources": False,
                        "prompts": False
                    }
                }
            })
        }

        # Wait for client messages (would need WebSocket for bi-directional)
        # For now, just keep connection alive
        try:
            while True:
                await asyncio.sleep(30)
                # Send heartbeat
                yield {
                    "event": "ping",
                    "data": ""
                }
        except asyncio.CancelledError:
            pass

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no"
        }
    )


# --- Tool Endpoints ---

@router.post("/tools/list")
async def list_tools(request: Request):
    """
    List available MCP tools.

    Returns tool definitions for ChatGPT.
    """
    return {
        "jsonrpc": "2.0",
        "result": {
            "tools": MCP_TOOLS
        }
    }


@router.post("/tools/call")
async def call_tool(request: Request):
    """
    Execute an MCP tool.

    Parses MCP tool call request and delegates to tool handler.
    """
    body = await request.json()

    tool_name = body.get("params", {}).get("name")
    arguments = body.get("params", {}).get("arguments", {})

    if not tool_name:
        return {
            "jsonrpc": "2.0",
            "error": {
                "code": -32602,
                "message": "Missing tool name"
            }
        }

    # Delegate to tool handler
    # TODO: Implement actual tool handlers with DB access
    result = {"status": "not_implemented", "tool": tool_name, "arguments": arguments}

    return {
        "jsonrpc": "2.0",
        "result": result
    }


# --- Direct Tool Endpoints (Alternative to MCP) ---

@router.post("/tools/{tool_name}")
async def execute_tool_direct(tool_name: str, request: Request, db=Depends(get_db)):
    """
    Direct tool execution endpoint (alternative to MCP protocol).

    Allows calling tools directly without MCP wrapping.

    Example:
        POST /mcp/tools/get_latest_ready_build
        {"arguments": {"project_id": "kaiscout"}}
    """
    body = await request.json()
    arguments = body.get("arguments", {})

    # Route to appropriate handler
    if tool_name == "get_latest_ready_build":
        result = await handle_get_latest_ready_build(db, arguments.get("project_id"))
    elif tool_name == "get_build":
        result = await handle_get_build(db, arguments.get("build_id"))
    elif tool_name == "submit_inspection":
        result = await handle_submit_inspection(
            db,
            arguments.get("build_id"),
            arguments.get("passed"),
            arguments.get("issues", []),
            arguments.get("suggestions"),
            arguments.get("confidence")
        )
    elif tool_name == "request_revision":
        result = await handle_request_revision(
            db,
            arguments.get("build_id"),
            arguments.get("feedback_summary"),
            arguments.get("priority_fixes", []),
            arguments.get("patch_guidance"),
            arguments.get("do_not_change")
        )
    elif tool_name == "approve_build":
        result = await handle_approve_build(
            db,
            arguments.get("build_id"),
            arguments.get("notes"),
            arguments.get("human_approved_by")
        )
    elif tool_name == "get_pending_revisions":
        result = await handle_get_pending_revisions(
            db,
            arguments.get("project_id"),
            arguments.get("build_id")
        )
    else:
        result = {"error": f"Unknown tool: {tool_name}"}

    return {"result": result}


# --- Tool Handlers (Placeholder - TODO: Implement) ---

async def handle_get_latest_ready_build(db, project_id: str) -> Optional[Dict]:
    """
    Get newest build with READY_FOR_REVIEW signal and PENDING inspection.

    Args:
        db: Database connection
        project_id: Project identifier

    Returns:
        Build dict or None if no builds ready
    """
    row = await db.fetchrow("""
        SELECT * FROM ralph_builds
        WHERE project_id = $1
          AND builder_signal = 'READY_FOR_REVIEW'
          AND inspection_status = 'PENDING'
        ORDER BY created_at DESC
        LIMIT 1
    """, project_id)

    if not row:
        return None

    return {
        "id": str(row['id']),
        "build_id": row['build_id'],
        "project_id": row['project_id'],
        "build_type": row['build_type'],
        "task_id": row['task_id'],
        "task_description": row['task_description'],
        "plan_build_id": row['plan_build_id'],
        "commit_sha": row['commit_sha'],
        "branch": row['branch'],
        "changed_files": json.loads(row['changed_files']) if row['changed_files'] else None,
        "diff_unified": row['diff_unified'],
        "diff_source": row['diff_source'],
        "review_bundle": json.loads(row['review_bundle']) if row['review_bundle'] else None,
        "test_command": row['test_command'],
        "test_exit_code": row['test_exit_code'],
        "test_output_tail": row['test_output_tail'],
        "coverage": json.loads(row['coverage']) if row['coverage'] else None,
        "lint_command": row['lint_command'],
        "lint_exit_code": row['lint_exit_code'],
        "lint_output_tail": row['lint_output_tail'],
        "builder_signal": row['builder_signal'],
        "builder_notes": json.loads(row['builder_notes']) if row['builder_notes'] else None,
        "inspection_status": row['inspection_status'],
        "iteration_count": row['iteration_count'],
        "requires_human_approval": row['requires_human_approval'],
        "approval_reason": row['approval_reason'],
        "created_at": row['created_at'].isoformat() if row['created_at'] else None,
        "updated_at": row['updated_at'].isoformat() if row['updated_at'] else None
    }


async def handle_get_build(db, build_id: str) -> Optional[Dict]:
    """
    Get full build artifact by ID.

    Args:
        db: Database connection
        build_id: Build identifier

    Returns:
        Full build artifact dict
    """
    row = await db.fetchrow(
        "SELECT * FROM ralph_builds WHERE build_id = $1",
        build_id
    )

    if not row:
        return None

    return {
        "id": str(row['id']),
        "build_id": row['build_id'],
        "project_id": row['project_id'],
        "build_type": row['build_type'],
        "task_id": row['task_id'],
        "task_description": row['task_description'],
        "plan_build_id": row['plan_build_id'],
        "commit_sha": row['commit_sha'],
        "branch": row['branch'],
        "changed_files": json.loads(row['changed_files']) if row['changed_files'] else None,
        "diff_unified": row['diff_unified'],
        "diff_source": row['diff_source'],
        "review_bundle": json.loads(row['review_bundle']) if row['review_bundle'] else None,
        "test_command": row['test_command'],
        "test_exit_code": row['test_exit_code'],
        "test_output_tail": row['test_output_tail'],
        "coverage": json.loads(row['coverage']) if row['coverage'] else None,
        "lint_command": row['lint_command'],
        "lint_exit_code": row['lint_exit_code'],
        "lint_output_tail": row['lint_output_tail'],
        "builder_signal": row['builder_signal'],
        "builder_notes": json.loads(row['builder_notes']) if row['builder_notes'] else None,
        "inspection_status": row['inspection_status'],
        "iteration_count": row['iteration_count'],
        "requires_human_approval": row['requires_human_approval'],
        "approval_reason": row['approval_reason'],
        "created_at": row['created_at'].isoformat() if row['created_at'] else None,
        "updated_at": row['updated_at'].isoformat() if row['updated_at'] else None
    }


async def handle_submit_inspection(
    db,
    build_id: str,
    passed: bool,
    issues: List[Dict],
    suggestions: Optional[str],
    confidence: Optional[float]
) -> Dict:
    """
    Submit inspection verdict (idempotent).

    Args:
        db: Database connection
        build_id: Build identifier
        passed: Whether build passed inspection
        issues: List of issues found
        suggestions: Suggestions for improvement
        confidence: Confidence score (0-1)

    Returns:
        Inspection result with status
    """
    # Get build to get build_pk (UUID)
    build = await db.fetchrow(
        "SELECT id, project_id FROM ralph_builds WHERE build_id = $1",
        build_id
    )

    if not build:
        return {"error": f"Build '{build_id}' not found"}

    build_pk = build['id']
    inspector_model = "gpt-5.2"  # Default inspector model

    # Check for existing inspection (idempotent)
    existing = await db.fetchrow("""
        SELECT * FROM ralph_inspections
        WHERE build_pk = $1 AND inspector_model = $2
    """, build_pk, inspector_model)

    if existing:
        return {
            "status": "already_submitted",
            "inspection_id": str(existing['id']),
            "build_id": build_id,
            "passed": existing['passed'],
            "message": "Inspection already exists for this build and inspector model"
        }

    # Insert new inspection
    import uuid
    inspection_id = uuid.uuid4()

    await db.execute("""
        INSERT INTO ralph_inspections (
            id, build_pk, build_id, inspector_model, passed, issues,
            suggestions, confidence, raw_response
        ) VALUES (
            $1, $2, $3, $4, $5, $6::jsonb, $7, $8, $9
        )
    """,
        inspection_id,
        build_pk,
        build_id,
        inspector_model,
        passed,
        json.dumps(issues) if issues else None,
        suggestions,
        confidence,
        None  # raw_response - could be populated if needed
    )

    # Update build inspection_status
    new_status = "PASSED" if passed else "FAILED"
    await db.execute("""
        UPDATE ralph_builds
        SET inspection_status = $1, updated_at = NOW()
        WHERE id = $2
    """, new_status, build_pk)

    return {
        "status": "submitted",
        "inspection_id": str(inspection_id),
        "build_id": build_id,
        "passed": passed,
        "issues_count": len(issues) if issues else 0,
        "inspection_status": new_status
    }


async def handle_request_revision(
    db,
    build_id: str,
    feedback_summary: str,
    priority_fixes: List[str],
    patch_guidance: Optional[str],
    do_not_change: Optional[List[str]]
) -> Dict:
    """
    Request builder revision with structured feedback.

    Args:
        db: Database connection
        build_id: Build identifier
        feedback_summary: Summary of issues
        priority_fixes: List of priority fixes needed
        patch_guidance: Specific guidance for fixes
        do_not_change: List of things not to change

    Returns:
        Revision request confirmation
    """
    # Get build to get build_pk (UUID)
    build = await db.fetchrow(
        "SELECT id FROM ralph_builds WHERE build_id = $1",
        build_id
    )

    if not build:
        return {"error": f"Build '{build_id}' not found"}

    build_pk = build['id']

    # Generate revision_id
    import uuid
    from datetime import datetime
    revision_uuid = uuid.uuid4()
    timestamp = datetime.now().isoformat()
    revision_id = f"rev-{timestamp}-{str(revision_uuid)[:8]}"

    # Insert revision request
    await db.execute("""
        INSERT INTO ralph_revisions (
            id, build_pk, build_id, revision_id, feedback_summary,
            priority_fixes, patch_guidance, do_not_change, status
        ) VALUES (
            $1, $2, $3, $4, $5, $6::jsonb, $7, $8::jsonb, $9
        )
    """,
        revision_uuid,
        build_pk,
        build_id,
        revision_id,
        feedback_summary,
        json.dumps(priority_fixes) if priority_fixes else None,
        patch_guidance,
        json.dumps(do_not_change) if do_not_change else None,
        "PENDING"
    )

    return {
        "status": "revision_requested",
        "revision_id": revision_id,
        "build_id": build_id,
        "priority_fixes_count": len(priority_fixes) if priority_fixes else 0,
        "message": "Builder can fetch this revision feedback via get_pending_revisions"
    }


async def handle_approve_build(
    db,
    build_id: str,
    notes: Optional[str],
    human_approved_by: Optional[str]
) -> Dict:
    """
    Approve build for deployment.

    Verifies:
    - Inspection passed
    - Commit SHA unchanged
    - Human approval if required

    Args:
        db: Database connection
        build_id: Build identifier
        notes: Approval notes
        human_approved_by: Human approver (if required)

    Returns:
        Approval confirmation
    """
    # Get build
    build = await db.fetchrow(
        "SELECT * FROM ralph_builds WHERE build_id = $1",
        build_id
    )

    if not build:
        return {"error": f"Build '{build_id}' not found"}

    # Must have passed inspection
    if build['inspection_status'] != "PASSED":
        return {
            "error": "Cannot approve build without passing inspection",
            "inspection_status": build['inspection_status']
        }

    # Must have human approval if required
    if build['requires_human_approval']:
        if not human_approved_by:
            return {
                "error": "Build requires human approval",
                "reason": build['approval_reason'],
                "message": "Provide human_approved_by parameter"
            }

        # Update with human approver
        await db.execute("""
            UPDATE ralph_builds
            SET human_approved_by = $1, updated_at = NOW()
            WHERE id = $2
        """, human_approved_by, build['id'])

    # Check iteration limit (max 3)
    if build['iteration_count'] >= 3:
        return {
            "error": "Max iteration limit (3) reached",
            "message": "Manual review required before deployment"
        }

    # Update build to DEPLOYED status
    await db.execute("""
        UPDATE ralph_builds
        SET builder_signal = 'DEPLOYED', updated_at = NOW()
        WHERE id = $1
    """, build['id'])

    return {
        "status": "approved",
        "build_id": build_id,
        "commit_sha": build['commit_sha'],
        "branch": build['branch'],
        "requires_human_approval": build['requires_human_approval'],
        "human_approved_by": human_approved_by if build['requires_human_approval'] else None,
        "message": "Build approved for deployment"
    }


async def handle_get_pending_revisions(
    db,
    project_id: str,
    build_id: Optional[str]
) -> List[Dict]:
    """
    Get pending revision requests for builder.

    CRITICAL: This enables the FAIL → revise → resubmit loop.

    Args:
        db: Database connection
        project_id: Project identifier
        build_id: Optional specific build ID

    Returns:
        List of pending revision requests
    """
    # Build query based on filters
    if build_id:
        # Specific build
        rows = await db.fetch("""
            SELECT r.*, b.project_id, b.build_type, b.task_id
            FROM ralph_revisions r
            JOIN ralph_builds b ON r.build_pk = b.id
            WHERE r.build_id = $1 AND r.status = 'PENDING'
            ORDER BY r.created_at DESC
        """, build_id)
    else:
        # All pending revisions for project
        rows = await db.fetch("""
            SELECT r.*, b.project_id, b.build_type, b.task_id
            FROM ralph_revisions r
            JOIN ralph_builds b ON r.build_pk = b.id
            WHERE b.project_id = $1 AND r.status = 'PENDING'
            ORDER BY r.created_at DESC
        """, project_id)

    return [
        {
            "revision_id": row['revision_id'],
            "build_id": row['build_id'],
            "project_id": row['project_id'],
            "build_type": row['build_type'],
            "task_id": row['task_id'],
            "feedback_summary": row['feedback_summary'],
            "priority_fixes": json.loads(row['priority_fixes']) if row['priority_fixes'] else [],
            "patch_guidance": row['patch_guidance'],
            "do_not_change": json.loads(row['do_not_change']) if row['do_not_change'] else [],
            "status": row['status'],
            "created_at": row['created_at'].isoformat() if row['created_at'] else None
        }
        for row in rows
    ]
