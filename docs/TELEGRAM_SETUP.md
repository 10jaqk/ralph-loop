# Telegram Setup Guide

Get Ralph Loop notifications on your phone via Telegram - approve builds from anywhere!

---

## Step 1: Create Your Telegram Bot

1. **Open Telegram** and search for `@BotFather`
2. **Start a chat** with BotFather
3. **Send the command**: `/newbot`
4. **Choose a name**: `Ralph Loop` (or whatever you like)
5. **Choose a username**: `ralph_loop_bot` (must end in `_bot`)

BotFather will reply with your **Bot Token** - it looks like:
```
1234567890:ABCdefGHIjklMNOpqrSTUvwxYZ
```

**SAVE THIS TOKEN** - you'll need it in Step 3.

---

## Step 2: Get Your Chat ID

You need to tell the bot YOUR Telegram chat ID so it knows where to send messages.

### Method A: Use a Helper Bot (Easy)

1. Search for `@userinfobot` in Telegram
2. Start a chat and send any message
3. It will reply with your User ID - it looks like: `123456789`

**SAVE THIS ID** - you'll need it in Step 3.

### Method B: Manual Method

1. Start a chat with your new bot (search for the username you created)
2. Send it any message (like "hello")
3. Open this URL in your browser (replace `YOUR_BOT_TOKEN`):
   ```
   https://api.telegram.org/botYOUR_BOT_TOKEN/getUpdates
   ```
4. You'll see JSON response - find `"chat":{"id":123456789}`
5. That number is your Chat ID

---

## Step 3: Configure Railway

Go to your Railway project → `ralph-mcp` service → Variables:

Add these 3 environment variables:

```bash
TELEGRAM_BOT_TOKEN=1234567890:ABCdefGHIjklMNOpqrSTUvwxYZ
TELEGRAM_CHAT_ID=123456789
RALPH_WEB_URL=https://ralph-mcp-production.up.railway.app
```

**Replace:**
- `TELEGRAM_BOT_TOKEN` with YOUR bot token from Step 1
- `TELEGRAM_CHAT_ID` with YOUR chat ID from Step 2
- `RALPH_WEB_URL` with your actual Railway app URL

**Save and Redeploy** - Railway will restart with Telegram enabled.

---

## Step 4: Set Up Webhook

Tell Telegram where to send button click events:

Open this URL in your browser (replace `YOUR_BOT_TOKEN` and `YOUR_RAILWAY_URL`):

```
https://api.telegram.org/botYOUR_BOT_TOKEN/setWebhook?url=YOUR_RAILWAY_URL/telegram/webhook
```

**Example:**
```
https://api.telegram.org/bot1234567890:ABCdefGHIjklMNOpqrSTUvwxYZ/setWebhook?url=https://ralph-mcp-production.up.railway.app/telegram/webhook
```

You should see:
```json
{"ok":true,"result":true,"description":"Webhook was set"}
```

---

## Step 5: Test It!

### Test Notification (Simple)

Submit a test build that requires approval:

```bash
curl -X POST https://ralph-mcp-production.up.railway.app/builds/test-ingest \
  -H "Content-Type: application/json" \
  -d '{
    "project_id": "test",
    "commit_sha": "abc123",
    "branch": "main",
    "changed_files": ["backend/requirements.txt"],
    "test_exit_code": 0,
    "lint_exit_code": 0,
    "builder_signal": "READY_FOR_REVIEW"
  }'
```

**You should receive a Telegram message** with:
- 🤖 Build details
- ⚠️ "Dependency change: requirements.txt"
- ✅ Approve / ❌ Reject buttons

### Test Approval Flow

1. **Submit a build** (see above)
2. **Check Telegram** - you'll get a message
3. **Press "✅ Approve"** button
4. **Check the database** - build should be approved:

```bash
# Via Railway CLI
railway run psql $DATABASE_URL -c "SELECT build_id, human_approved_by FROM ralph_builds ORDER BY created_at DESC LIMIT 1;"
```

You should see: `human_approved_by` = `telegram:YourName(123456789)`

---

## What You'll Get

### 1. Build Submitted
```
📤 Ralph Loop Update

Project: kaiscout
Build: 2026-01-12T10:30:00-abc123

📤 New build submitted

Tests: ✅
Lint: ✅

🔍 ChatGPT will inspect soon...
```

### 2. Needs Your Approval
```
🤖 Ralph Loop - Approval Needed

Project: kaiscout
Build: 2026-01-12T10:30:00-abc123

⚠️ Status:
  Tests: ✅ Passed
  Lint: ✅ Passed

⚠️ Reason: Dependency change: requirements.txt

📝 Changed Files:
  • backend/requirements.txt
  • backend/app/main.py

👆 Action Required:
Approve or reject this build to continue.

[✅ Approve] [❌ Reject]
[📋 View Details]
```

### 3. Inspection Results
```
✅ Ralph Loop Update

Project: kaiscout
Build: 2026-01-12T10:30:00-abc123

✅ Inspection PASSED

ChatGPT approved the build!

Confidence: 92.5%
```

OR

```
❌ Ralph Loop Update

Project: kaiscout
Build: 2026-01-12T10:30:00-abc123

❌ Inspection FAILED

2 issue(s) found.

🔧 Claude will address the feedback...
```

### 4. Revision Requested
```
🔧 Ralph Loop - Revision Requested

Project: kaiscout
Build: 2026-01-12T10:30:00-abc123

❌ ChatGPT Inspector says:
Missing input validation on user registration endpoint

📋 Priority Fixes:
  1. Add email format validation
  2. Add password strength check
  3. Add rate limiting

🤖 Next: Claude is working on fixes...
```

---

## Troubleshooting

### Not Getting Messages?

1. **Check bot token**: Make sure it's correct in Railway variables
2. **Check chat ID**: Send a message to your bot, then check `/getUpdates` again
3. **Check webhook**: Run the setWebhook URL again
4. **Check Railway logs**: `railway logs -s api | grep telegram`

### Buttons Not Working?

1. **Verify webhook is set**:
   ```
   https://api.telegram.org/botYOUR_TOKEN/getWebhookInfo
   ```
   Should show your Railway URL

2. **Check Railway logs** when you press a button:
   ```
   railway logs -s api | grep "Telegram webhook"
   ```

### Getting Duplicate Messages?

- Only ONE webhook should be set
- Delete old webhook if you changed Railway URL:
  ```
  https://api.telegram.org/botYOUR_TOKEN/deleteWebhook
  ```
- Then set new one (Step 4)

---

## Security Notes

- **Keep your bot token secret** - anyone with it can send messages as your bot
- **Keep your chat ID private** - it identifies your Telegram account
- **Only YOU can approve** - the webhook checks that requests come from Telegram API
- **Buttons expire** - old approval buttons won't work after build status changes

---

## What's Next?

Now when Claude builds something:

1. 📤 **Build submitted** → You get notified
2. 🔍 **ChatGPT inspects** → You get the verdict
3. ⚠️ **Needs approval?** → Press the button on your phone
4. ✅ **Approved** → Deployment continues
5. 🚀 **Done!**

**You never have to be at your computer to approve builds!**

Approve from:
- The gym 💪
- Your couch 🛋️
- Vacation 🏖️
- Coffee shop ☕
- Anywhere with phone signal 📱

---

That's it! You're now running an autonomous AI development pipeline that can ask for your approval via Telegram! 🤖✨
