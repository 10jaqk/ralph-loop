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

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse
from typing import Dict, Any, Optional, List
from pydantic import BaseModel, Field
import json
import asyncio
import asyncpg

router = APIRouter(prefix="/mcp", tags=["mcp"])


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
async def execute_tool_direct(tool_name: str, request: Request):
    """
    Direct tool execution endpoint (alternative to MCP protocol).

    Allows calling tools directly without MCP wrapping.

    Example:
        POST /mcp/tools/get_latest_ready_build
        {"arguments": {"project_id": "kaiscout"}}
    """
    body = await request.json()
    arguments = body.get("arguments", {})

    # TODO: Implement actual tool handlers with DB access
    result = {"status": "not_implemented", "tool": tool_name, "arguments": arguments}

    return {"result": result}


# --- Tool Handlers (Placeholder - TODO: Implement) ---

async def handle_get_latest_ready_build(db: asyncpg.Pool, project_id: str) -> Optional[Dict]:
    """
    Get newest build with READY_FOR_REVIEW signal and PENDING inspection.

    Args:
        db: Database connection pool
        project_id: Project identifier

    Returns:
        Build dict or None if no builds ready
    """
    # TODO: Implement
    return None


async def handle_get_build(db: asyncpg.Pool, build_id: str) -> Optional[Dict]:
    """
    Get full build artifact by ID.

    Args:
        db: Database connection pool
        build_id: Build identifier

    Returns:
        Full build artifact dict
    """
    # TODO: Implement
    return None


async def handle_submit_inspection(
    db: asyncpg.Pool,
    build_id: str,
    passed: bool,
    issues: List[Dict],
    suggestions: Optional[str],
    confidence: Optional[float]
) -> Dict:
    """
    Submit inspection verdict (idempotent).

    Args:
        db: Database connection pool
        build_id: Build identifier
        passed: Whether build passed inspection
        issues: List of issues found
        suggestions: Suggestions for improvement
        confidence: Confidence score (0-1)

    Returns:
        Inspection result with status
    """
    # TODO: Implement idempotent inspection submission
    # Check for existing inspection, return if exists
    # Otherwise create new inspection and update build status
    return {"status": "not_implemented"}


async def handle_request_revision(
    db: asyncpg.Pool,
    build_id: str,
    feedback_summary: str,
    priority_fixes: List[str],
    patch_guidance: Optional[str],
    do_not_change: Optional[List[str]]
) -> Dict:
    """
    Request builder revision with structured feedback.

    Args:
        db: Database connection pool
        build_id: Build identifier
        feedback_summary: Summary of issues
        priority_fixes: List of priority fixes needed
        patch_guidance: Specific guidance for fixes
        do_not_change: List of things not to change

    Returns:
        Revision request confirmation
    """
    # TODO: Implement revision request creation
    return {"status": "not_implemented"}


async def handle_approve_build(
    db: asyncpg.Pool,
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
        db: Database connection pool
        build_id: Build identifier
        notes: Approval notes
        human_approved_by: Human approver (if required)

    Returns:
        Approval confirmation
    """
    # TODO: Implement approval logic with guardrail checks
    return {"status": "not_implemented"}


async def handle_get_pending_revisions(
    db: asyncpg.Pool,
    project_id: str,
    build_id: Optional[str]
) -> List[Dict]:
    """
    Get pending revision requests for builder.

    CRITICAL: This enables the FAIL → revise → resubmit loop.

    Args:
        db: Database connection pool
        project_id: Project identifier
        build_id: Optional specific build ID

    Returns:
        List of pending revision requests
    """
    # TODO: Implement pending revisions query
    return []
