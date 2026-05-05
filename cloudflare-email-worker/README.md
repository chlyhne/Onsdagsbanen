# Cloudflare Email Trigger Worker

This Worker can trigger a GitHub Actions workflow in two ways:

1. Email event mode (requires a custom domain with Cloudflare Email Routing)
2. HTTP trigger mode (works on workers.dev, no custom domain required)

## Trigger logic

The Worker dispatches only when all checks pass:

1. The sender exists in `ALLOWED_SENDERS`.
2. The provided trigger token matches `TRIGGER_TOKEN`.
3. The subject is present.

## Required Worker settings

Configure in `wrangler.toml`:

- `GH_OWNER`: GitHub owner/org
- `GH_REPO`: Repository name
- `GH_WORKFLOW`: Workflow file name in `.github/workflows/`
- `GH_REF`: Branch/ref to dispatch (usually `main`)
- `ALLOWED_SENDERS`: Comma-separated allowed sender addresses

Configure as Worker secrets:

- `GH_TOKEN`: GitHub fine-grained token with Actions write permission on this repo
- `TRIGGER_TOKEN`: Shared secret token for email or HTTP trigger auth

## Setup

```bash
cd cloudflare-email-worker
npm install
npx wrangler login
npx wrangler secret put GH_TOKEN
npx wrangler secret put TRIGGER_TOKEN
npm run deploy
```

## No-domain mode (workers.dev)

If you do not own a custom domain, use HTTP trigger mode:

POST to:

`https://m2s-email-trigger.hummesse.workers.dev/trigger`

Headers:

- `x-trigger-token: <TRIGGER_TOKEN>`

JSON body:

```json
{
	"from": "hummesse@gmail.com",
	"subject": "M2S run request",
	"dry_run": true
}
```

Example with curl:

```bash
curl -X POST "https://m2s-email-trigger.hummesse.workers.dev/trigger" \
	-H "Content-Type: application/json" \
	-H "x-trigger-token: YOUR_TRIGGER_TOKEN" \
	-d '{"from":"hummesse@gmail.com","subject":"M2S run request","dry_run":true}'
```

## Trigger from phone email (no domain)

Use Gmail Apps Script to bridge inbox messages to the HTTP trigger endpoint.

Script file in this repo:

- `gmail_phone_bridge.gs`

High-level flow:

1. You send an email from phone with subject containing `M2S RUN`.
2. Apps Script polls inbox every minute.
3. Matching message triggers `POST /trigger` with `x-trigger-token`.
4. Worker dispatches the GitHub Actions workflow.

One-time setup in Google Apps Script:

1. Create a new script project at script.google.com.
2. Paste content from `gmail_phone_bridge.gs`.
3. Update `triggerToken` and any sender/subject settings.
4. Run `installMinuteTrigger()` once and grant permissions.

## Email routing (domain required)

In Cloudflare Email Routing, set your destination to this Worker.

Example subject to trigger:

```text
M2S run please [my-long-trigger-token]
```
