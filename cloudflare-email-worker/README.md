# Cloudflare Email Trigger Worker

This Worker dispatches the GitHub Actions pipeline from either:

1. Cloudflare Email Routing events (custom domain mode)
2. HTTP POST to `/trigger` (workers.dev or bridge mode)

## Email interface (current behavior)

Subjects are matched exactly (trimmed + case-insensitive):

- `resultater`
- `append`
- `delete`
- `afmeld resultater`

Sender and mode rules:

- `resultater`
	- Any sender can use this.
	- If sender is not `hummesse@gmail.com`, sender address is auto-added to recipients list.
	- If sender is `hummesse@gmail.com`, addresses in email body are extracted and may be persisted.
- `append`
	- Only `hummesse@gmail.com` can use this.
	- Requires at least one email address in body.
	- Runs in append-only mode (recipient update, no results email send).
- `delete`
	- Only `hummesse@gmail.com` can use this.
	- Requires at least one email address in body.
	- Runs in delete mode (recipient removal, no results email send).
- `afmeld resultater`
	- Allowed for any sender except `hummesse@gmail.com`.
	- Removes sender address from recipients list.
	- Triggers unsubscribe confirmation email.

## Required Worker configuration

Set in `wrangler.toml`:

- `GH_OWNER`: GitHub owner/org
- `GH_REPO`: Repository name
- `GH_WORKFLOW`: Workflow filename in `.github/workflows/`
- `GH_REF`: Branch/ref to dispatch (typically `main`)

Set as Worker secrets:

- `GH_TOKEN`: GitHub token with permission to dispatch Actions workflows
- `TRIGGER_TOKEN`: Shared token for HTTP trigger auth

## Setup

```bash
cd cloudflare-email-worker
npm install
npx wrangler login
npx wrangler secret put GH_TOKEN
npx wrangler secret put TRIGGER_TOKEN
npm run deploy
```

## HTTP trigger mode

Endpoint:

`POST https://m2s-email-trigger.hummesse.workers.dev/trigger`

Auth headers (one of these):

- `x-trigger-token: <TRIGGER_TOKEN>`
- `Authorization: Bearer <TRIGGER_TOKEN>`

JSON fields:

- `from` (required)
- `subject` (required, must be one of the exact subjects above)
- `dry_run` (optional; forced to true for append/delete/unsubscribe)
- `body_text` or `body` (optional; used to extract emails)
- `recipients_override` (optional string or array)

Example:

```json
{
	"from": "hummesse@gmail.com",
	"subject": "append",
	"body_text": "new1@example.com, new2@example.com"
}
```

## Gmail bridge mode (no custom domain)

Use Apps Script file `gmail_phone_bridge.gs` to poll Gmail and forward matching messages to `/trigger`.

One-time setup:

1. Create a Google Apps Script project.
2. Paste content from `gmail_phone_bridge.gs`.
3. Set `M2S_CONFIG.triggerToken`.
4. Run `installMinuteTrigger()` and grant permissions.

Bridge behavior:

- Polls unread messages for subjects: `resultater`, `append`, `delete`, `afmeld resultater`.
- Applies sender restrictions consistent with Worker rules.
- Labels processed threads with `m2s-processed` and can archive them.

## Email routing mode (custom domain)

If using Cloudflare Email Routing, route destination emails directly to this Worker.
Use one of the supported exact subject commands above.
