"""
Telegram Notification Service

Sends build notifications and approval requests to Telegram.
Receives approval/rejection responses via webhook.
"""
import httpx
import logging
from typing import Optional, Dict, Any
from app.config import get_settings

logger = logging.getLogger(__name__)

settings = get_settings()


class TelegramService:
    """Send notifications and approval requests via Telegram Bot."""

    def __init__(self):
        self.bot_token = settings.TELEGRAM_BOT_TOKEN
        self.chat_id = settings.TELEGRAM_CHAT_ID
        self.base_url = f"https://api.telegram.org/bot{self.bot_token}"

    async def send_approval_request(
        self,
        build_id: str,
        project_id: str,
        reason: str,
        changed_files: list[str],
        test_passed: bool,
        lint_passed: bool
    ) -> bool:
        """
        Send approval request with inline buttons.

        Args:
            build_id: Build identifier
            project_id: Project name
            reason: Why approval is needed
            changed_files: List of modified files
            test_passed: Test status
            lint_passed: Lint status

        Returns:
            True if sent successfully
        """
        if not self.bot_token or not self.chat_id:
            logger.warning("Telegram not configured - skipping notification")
            return False

        # Format message
        status_emoji = "✅" if (test_passed and lint_passed) else "⚠️"
        files_preview = "\n".join([f"  • {f}" for f in changed_files[:5]])
        if len(changed_files) > 5:
            files_preview += f"\n  ... and {len(changed_files) - 5} more"

        message = f"""
🤖 *Ralph Loop - Approval Needed*

*Project:* `{project_id}`
*Build:* `{build_id}`

{status_emoji} *Status:*
  Tests: {"✅ Passed" if test_passed else "❌ Failed"}
  Lint: {"✅ Passed" if lint_passed else "❌ Failed"}

⚠️ *Reason:* {reason}

📝 *Changed Files:*
{files_preview}

👆 *Action Required:*
Approve or reject this build to continue.
        """.strip()

        # Inline keyboard with approve/reject buttons
        keyboard = {
            "inline_keyboard": [
                [
                    {"text": "✅ Approve", "callback_data": f"approve:{build_id}"},
                    {"text": "❌ Reject", "callback_data": f"reject:{build_id}"}
                ],
                [
                    {"text": "📋 View Details", "url": f"{settings.RALPH_WEB_URL}/builds/{build_id}"}
                ]
            ]
        }

        try:
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    f"{self.base_url}/sendMessage",
                    json={
                        "chat_id": self.chat_id,
                        "text": message,
                        "parse_mode": "Markdown",
                        "reply_markup": keyboard
                    },
                    timeout=10.0
                )

                if response.status_code == 200:
                    logger.info(f"Sent approval request for build {build_id} to Telegram")
                    return True
                else:
                    logger.error(f"Telegram API error: {response.status_code} - {response.text}")
                    return False

        except Exception as e:
            logger.error(f"Failed to send Telegram notification: {e}")
            return False

    async def send_status_update(
        self,
        build_id: str,
        project_id: str,
        status: str,
        message: str
    ) -> bool:
        """
        Send build status update.

        Args:
            build_id: Build identifier
            project_id: Project name
            status: Status (submitted, inspecting, passed, failed, deployed)
            message: Status message

        Returns:
            True if sent successfully
        """
        if not self.bot_token or not self.chat_id:
            return False

        # Emoji mapping
        emoji_map = {
            "submitted": "📤",
            "inspecting": "🔍",
            "passed": "✅",
            "failed": "❌",
            "deployed": "🚀",
            "revision": "🔧"
        }

        emoji = emoji_map.get(status, "ℹ️")

        text = f"""
{emoji} *Ralph Loop Update*

*Project:* `{project_id}`
*Build:* `{build_id}`

{message}
        """.strip()

        try:
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    f"{self.base_url}/sendMessage",
                    json={
                        "chat_id": self.chat_id,
                        "text": text,
                        "parse_mode": "Markdown"
                    },
                    timeout=10.0
                )

                return response.status_code == 200

        except Exception as e:
            logger.error(f"Failed to send status update: {e}")
            return False

    async def send_revision_notification(
        self,
        build_id: str,
        project_id: str,
        feedback_summary: str,
        priority_fixes: list[str]
    ) -> bool:
        """
        Notify that ChatGPT requested revisions.

        Args:
            build_id: Build identifier
            project_id: Project name
            feedback_summary: Summary of issues
            priority_fixes: List of required fixes

        Returns:
            True if sent successfully
        """
        if not self.bot_token or not self.chat_id:
            return False

        fixes_preview = "\n".join([f"  {i+1}. {fix}" for i, fix in enumerate(priority_fixes[:3])])
        if len(priority_fixes) > 3:
            fixes_preview += f"\n  ... and {len(priority_fixes) - 3} more"

        text = f"""
🔧 *Ralph Loop - Revision Requested*

*Project:* `{project_id}`
*Build:* `{build_id}`

❌ *ChatGPT Inspector says:*
{feedback_summary}

📋 *Priority Fixes:*
{fixes_preview}

🤖 *Next:* Claude is working on fixes...
        """.strip()

        try:
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    f"{self.base_url}/sendMessage",
                    json={
                        "chat_id": self.chat_id,
                        "text": text,
                        "parse_mode": "Markdown"
                    },
                    timeout=10.0
                )

                return response.status_code == 200

        except Exception as e:
            logger.error(f"Failed to send revision notification: {e}")
            return False


# Singleton instance
_telegram_service: Optional[TelegramService] = None


def get_telegram_service() -> TelegramService:
    """Get or create Telegram service singleton."""
    global _telegram_service

    if _telegram_service is None:
        _telegram_service = TelegramService()

    return _telegram_service
