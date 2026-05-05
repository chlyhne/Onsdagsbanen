# Cloudflare Email Trigger Worker

This Worker listens for inbound email and dispatches a GitHub Actions workflow.

## Trigger logic

The Worker dispatches only when both checks pass:

1. The sender exists in `ALLOWED_SENDERS`.
2. The email subject contains the shared secret `TRIGGER_TOKEN`.

## Required Worker settings

Configure in `wrangler.toml`:

- `GH_OWNER`: GitHub owner/org
- `GH_REPO`: Repository name
- `GH_WORKFLOW`: Workflow file name in `.github/workflows/`
- `GH_REF`: Branch/ref to dispatch (usually `main`)
- `ALLOWED_SENDERS`: Comma-separated allowed sender addresses

Configure as Worker secrets:

- `GH_TOKEN`: GitHub fine-grained token with Actions write permission on this repo
- `TRIGGER_TOKEN`: Shared secret text that must appear in subject line

## Setup

```bash
cd cloudflare-email-worker
npm install
npx wrangler login
npx wrangler secret put GH_TOKEN
npx wrangler secret put TRIGGER_TOKEN
npm run deploy
```

## Email routing

In Cloudflare Email Routing, set your destination to this Worker.

Example subject to trigger:

```text
M2S run please [my-long-trigger-token]
```
