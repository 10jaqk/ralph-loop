"""
Telegram Webhook Handler

Receives approval/rejection responses from Telegram bot.
"""
from fastapi import APIRouter, Request, HTTPException, Depends
from typing import Dict, Any
import logging
from datetime import datetime

from app.services.telegram_service import get_telegram_service

router = APIRouter(prefix="/telegram", tags=["telegram"])

logger = logging.getLogger(__name__)


async def get_db():
    """Database dependency (will be overridden by main app)."""
    return None


@router.post("/webhook")
async def telegram_webhook(request: Request, db=Depends(get_db)):
    """
    Receive updates from Telegram Bot API.

    Handles callback queries from inline buttons (approve/reject).

    Example payload:
    {
        "update_id": 123456789,
        "callback_query": {
            "id": "...",
            "from": {"id": 123456, "first_name": "Mihai"},
            "message": {...},
            "data": "approve:2026-01-12T10:30:00-abc123"
        }
    }
    """
    try:
        payload = await request.json()
        logger.info(f"Telegram webhook received: {payload}")

        # Handle callback query (button press)
        if "callback_query" in payload:
            callback = payload["callback_query"]
            data = callback.get("data", "")

            # Parse callback data: "approve:build_id" or "reject:build_id"
            if ":" not in data:
                logger.warning(f"Invalid callback data: {data}")
                return {"ok": True}

            action, build_id = data.split(":", 1)

            if action == "approve":
                await handle_approval(db, build_id, callback["from"])
            elif action == "reject":
                await handle_rejection(db, build_id, callback["from"])
            else:
                logger.warning(f"Unknown action: {action}")

            # Answer callback query (removes loading state from button)
            telegram = get_telegram_service()
            await answer_callback_query(telegram, callback["id"], action)

        return {"ok": True}

    except Exception as e:
        logger.error(f"Error handling Telegram webhook: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


async def handle_approval(db, build_id: str, user: Dict[str, Any]):
    """
    Handle approval button press.

    Args:
        db: Database connection
        build_id: Build identifier
        user: Telegram user info
    """
    user_name = user.get("first_name", "Unknown")
    user_id = user.get("id", "unknown")
    approved_by = f"telegram:{user_name}({user_id})"

    logger.info(f"Build {build_id} approved by {approved_by}")

    try:
        # Update build approval status
        await db.execute("""
            UPDATE ralph_builds
            SET human_approved_by = $1, updated_at = NOW()
            WHERE build_id = $2
        """, approved_by, build_id)

        # Send confirmation
        telegram = get_telegram_service()
        await telegram.send_status_update(
            build_id=build_id,
            project_id="",  # Will be fetched if needed
            status="deployed",
            message=f"✅ Build approved by {user_name}!\n\nClaude will proceed with deployment."
        )

    except Exception as e:
        logger.error(f"Failed to process approval: {e}")
        telegram = get_telegram_service()
        await telegram.send_status_update(
            build_id=build_id,
            project_id="",
            status="failed",
            message=f"❌ Failed to process approval: {str(e)}"
        )


async def handle_rejection(db, build_id: str, user: Dict[str, Any]):
    """
    Handle rejection button press.

    Args:
        db: Database connection
        build_id: Build identifier
        user: Telegram user info
    """
    user_name = user.get("first_name", "Unknown")
    user_id = user.get("id", "unknown")
    rejected_by = f"telegram:{user_name}({user_id})"

    logger.info(f"Build {build_id} rejected by {rejected_by}")

    try:
        # Update build status to blocked
        await db.execute("""
            UPDATE ralph_builds
            SET builder_signal = 'NEEDS_WORK',
                inspection_status = 'FAILED',
                updated_at = NOW()
            WHERE build_id = $1
        """, build_id)

        # Create revision request
        await db.execute("""
            INSERT INTO ralph_revisions (
                id, build_pk, build_id, revision_id, feedback_summary,
                priority_fixes, patch_guidance, do_not_change, status
            )
            SELECT
                gen_random_uuid(),
                id,
                build_id,
                'rev-' || build_id || '-rejected',
                'Build rejected by human reviewer',
                $1::jsonb,
                'Please address the concerns and resubmit',
                '[]'::jsonb,
                'PENDING'
            FROM ralph_builds
            WHERE build_id = $2
        """, '["Build rejected by human - review and revise"]', build_id)

        # Send confirmation
        telegram = get_telegram_service()
        await telegram.send_status_update(
            build_id=build_id,
            project_id="",
            status="revision",
            message=f"❌ Build rejected by {user_name}.\n\n🔧 Claude will revise and resubmit."
        )

    except Exception as e:
        logger.error(f"Failed to process rejection: {e}")


async def answer_callback_query(telegram, callback_query_id: str, action: str):
    """
    Answer callback query to remove button loading state.

    Args:
        telegram: Telegram service instance
        callback_query_id: Callback query ID
        action: Action performed (approve/reject)
    """
    import httpx

    text_map = {
        "approve": "✅ Build approved!",
        "reject": "❌ Build rejected"
    }

    try:
        async with httpx.AsyncClient() as client:
            await client.post(
                f"{telegram.base_url}/answerCallbackQuery",
                json={
                    "callback_query_id": callback_query_id,
                    "text": text_map.get(action, "Done"),
                    "show_alert": False
                },
                timeout=5.0
            )
    except Exception as e:
        logger.error(f"Failed to answer callback query: {e}")
