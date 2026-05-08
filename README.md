# m2s-combiner

Python CLI project to:

1. Fetch Manage2Sail class results via API
2. Combine class-group race rankings into one result
3. Produce a combined PDF report

## Architecture

- API-first only: no browser automation
- Reads event bootstrap JSON from the event page to discover class IDs
- Fetches all requested classes in parallel from:
  - https://www.manage2sail.com/api/event/{eventId}/regattaresult/{regattaId}
- Computes race and overall combined results from corrected time (Beregnet)
- Applies dynamic discard thresholds from API payload (`Discards`)

## Setup

```bash
python -m venv .venv
```

Activate the virtual environment:

Linux/macOS:

```bash
source .venv/bin/activate
```

Windows (cmd.exe):

```bat
.venv\Scripts\activate.bat
```

Install dependencies:

```bash
pip install -e .
```

## Run

By default the CLI uses:

- Event URL: https://www.manage2sail.com/da-DK/event/Onsdagsbanen2026#!/
- Class groups:
  - Stor bane 1 + Stor bane 2
  - Lille bane 1 + Lille bane 2
- Races: all available aligned race labels per group

```bash
python -m m2s_combiner.cli
```

Use custom event/class values:

```bash
python -m m2s_combiner.cli --event-url "https://www.manage2sail.com/...#!/" --class-names "Stor bane 1, Stor bane 2"
```

To run multiple groups in one report, repeat --class-names (one comma-separated group per flag):

```bash
python -m m2s_combiner.cli --class-names "Stor bane 1, Stor bane 2" --class-names "Lille bane 1, Lille bane 2"
```

Groups can include 3 or more classes:

```bash
python -m m2s_combiner.cli --class-names "Stor bane 1, Stor bane 2, Stor bane 3"
```

Compatibility warnings:

- The CLI prints a warning when classes in the same group differ in race count.
- The CLI prints a warning when classes in the same group have different race lengths.
- The CLI prints a warning when classes in the same group have different start times.

Max-race behavior:

- `--max-race` is group-aware.
- Provide it once to apply the same cap to all groups.
- Or provide it once per `--class-names` group, in the same order.
- A group cap can be lower than the group's available max race number.
- A group cap cannot be higher than the group's available max race number; the CLI raises an error in that case.

Examples:

```bash
# Same cap for all groups
python -m m2s_combiner.cli --class-names "Stor bane 1, Stor bane 2" --class-names "Lille bane 1, Lille bane 2" --max-race 12

# Per-group caps (order matches --class-names flags)
python -m m2s_combiner.cli --class-names "Stor bane 1, Stor bane 2" --class-names "Lille bane 1, Lille bane 2" --max-race 12 --max-race 10
```

Common options:

```bash
python -m m2s_combiner.cli --output-pdf Results.pdf --output-dir . --max-race 12
```

Scoring rule options:

- `--scoring-rule low-point` (default): classic low-point scoring where lowest total wins.
- `--scoring-rule high-point`: each boat gets 1 point for participating in a race row, plus 1 point per boat left behind. Highest total wins.
- `--scoring-rule fractional-point`: high-point normalized per race by participant count, then multiplied by 100 and rounded (`round((high-point points / participants) * 100)`). Highest total wins.
- `--scoring-rule bayesian-point`: order-invariant, time-based hierarchical Bayesian score using corrected times with race-specific bias correction and handicap baseline adjustment. Displayed as percent with one decimal. No discards.
- For `bayesian-point`, the final table includes `Vinderchance næste`: posterior-predictive Monte Carlo estimate of winning the next race conditional on participation, using speed-space sampling converted to pace (shown as percent).
- Discards apply for `low-point`, `high-point`, and `fractional-point`, but not for `bayesian-point`.

Examples:

```bash
# Use high-point scoring directly from CLI
python -m m2s_combiner.cli --scoring-rule high-point

# Use normalized high-point scoring
python -m m2s_combiner.cli --scoring-rule fractional-point

# Use order-invariant Bayesian scoring
python -m m2s_combiner.cli --scoring-rule bayesian-point

# run_2026.py forwards extra args to the CLI
python run_2026.py --scoring-rule high-point

# run_2024.py and run_2025.py also forward extra args
python run_2025.py --scoring-rule fractional-point

# Bayesian scoring works via run scripts too
python run_2025.py --scoring-rule bayesian-point
```

## Email Results (Gmail)

Use the script `send_results_gmail.py` to email result PDFs to multiple recipients.

Recipient privacy:

- All addresses from the recipients text file are sent as BCC.
- Recipients do not see each other's email addresses.

Safety confirmation:

- Before sending, the script shows a popup asking for a final "really, really sure" confirmation.
- The email is only sent if you confirm in the popup.
- For automation/non-interactive runs, pass `--yes` to skip the popup.

Important:

- Gmail SMTP requires an App Password (not your normal account password).
- If you use 2FA, create an App Password in your Google account security settings.
- By default, the script reads sender email + app password from `gmail_app_password.txt` on this PC.
- If the password file is missing/empty, it falls back to an interactive paste-friendly prompt.
- You can still override sender with `--from-email`.

Credentials file format (`gmail_app_password.txt`):

```txt
hummesse@gmail.com
abcd efgh ijkl mnop
```

Recipient list format (`recipients.txt`):

```txt
person1@example.com
person2@example.com
# comments are allowed
```

A starter template is included as `recipients.txt.example`.
Send default result PDFs (auto-detects existing `Results2025.pdf`, `Results2026.pdf`, `Results.pdf`) using `recipients.txt`:

```bash
python send_results_gmail.py
```

Use a custom credentials file path if needed:

```bash
python send_results_gmail.py --app-password-file my_gmail_credentials.txt
```

Attachment naming:

- The sent PDF attachment is renamed in the email to:
  - `Onsdagsbanen Kombinerede Resultater DD-MM-YYYY.pdf`
- Example: `Onsdagsbanen Kombinerede Resultater 04-05-2026.pdf`
- This is the attachment display name in the email; your local file is not renamed.

Default attachment behavior:

- If `--attach` is not provided, the script sends `Results2026.pdf` from the current folder.

Or provide a custom recipients file:

```bash
python send_results_gmail.py --to-file recipients_crew.txt --subject "Onsdagsbanen results"
```

You can also send one explicitly:

```bash
python send_results_gmail.py --to-file recipients.txt --attach Results2026.pdf
```

Dry-run (validate inputs without sending):

```bash
python send_results_gmail.py --to-file recipients.txt --dry-run
```

Skip popup confirmation (password prompt still appears):

```bash
python send_results_gmail.py --to-file recipients.txt --yes
```

Automation credentials fallback:

- `send_results_gmail.py` now also reads credentials from environment variables.
- Sender address: `M2S_GMAIL_FROM`
- App password: `M2S_GMAIL_APP_PASSWORD`

## Cloudflare Email Trigger Automation

This repo includes an email-triggered automation path:

1. Cloudflare Email Worker receives incoming email.
2. Worker verifies sender + trigger token in subject.
3. Worker dispatches GitHub Actions workflow.
4. Workflow runs `run_2026.py` and then `send_results_gmail.py --yes`.

### Files

- Worker code: `cloudflare-email-worker/src/index.js`
- Worker config: `cloudflare-email-worker/wrangler.toml`
- Workflow: `.github/workflows/run-2026-email-pipeline.yml`

### GitHub secrets required

- `M2S_GMAIL_FROM`
- `M2S_GMAIL_APP_PASSWORD`
- `M2S_RECIPIENTS_KEY` (Fernet key used to decrypt/encrypt recipient registry)

Recipient source of truth:

- Main registry file: `recipients_repo.enc` (encrypted and committed to the repo).
- Workflow runtime file: `recipients.txt` (generated during Actions runs).
- Registry data is decrypted only inside the GitHub Actions job using `M2S_RECIPIENTS_KEY`, then re-encrypted before commit.
- If sender is `hummesse@gmail.com` and body emails are provided, those addresses are used for that run, then merged into the encrypted registry at the end of the workflow (deduped).
- For other senders, behavior stays sender-only for that run, and that sender address is merged into the encrypted registry at the end of the workflow (deduped).
- If `recipients_repo.enc` is empty/missing and no body override is provided, the run fails fast.

Email trigger subjects:

- `resultater`: normal run (build + send results) using sender-specific recipient behavior.
  - From `hummesse@gmail.com` with one or more emails in the message body:
    - Results are sent to those body email addresses for that run.
    - The same addresses are appended to the encrypted recipient registry (deduped).
  - From `hummesse@gmail.com` without body emails:
    - Runs normally using the persisted registry recipients.
  - From any other sender:
    - Runs sender-only for that request, and the sender is appended to the encrypted recipient registry.
  - Use this mode when you want both immediate delivery and persistence of new recipients.
- `append`: append-only mode.
  - Only accepted from `hummesse@gmail.com`.
  - Extracts emails from message body and appends them to the encrypted recipient registry (deduped).
  - Does not build PDFs and does not send result emails.
  - Use this mode when you want persistence only (no email send in that run).
- `delete`: delete-only mode.
  - Only accepted from `hummesse@gmail.com`.
  - Extracts emails from message body and removes those addresses from the encrypted recipient registry.
  - Does not build PDFs and does not send result emails.
- `afmeld resultater`: unsubscribe mode.
  - Accepted for non-`hummesse@gmail.com` senders.
  - Removes the sender email from the encrypted recipient registry.
  - Does not build PDFs and does not send result emails.
  - Sends a confirmation email that the sender is no longer on the list.

### Cloudflare Worker setup

From `cloudflare-email-worker`:

```bash
npm install
npx wrangler login
npx wrangler secret put GH_TOKEN
npx wrangler secret put TRIGGER_TOKEN
npm run deploy
```

Set worker variables (in `wrangler.toml` or dashboard):

- `GH_OWNER`
- `GH_REPO`
- `GH_WORKFLOW` (default in repo: `run-2026-email-pipeline.yml`)
- `GH_REF` (typically `main`)

Then configure Cloudflare Email Routing so a dedicated address forwards to this Worker.

No custom domain option:

- If you do not have a domain, skip Email Routing.
- Use the Worker HTTP endpoint on workers.dev instead:
  - `POST /trigger` on your deployed Worker URL
  - Include header `x-trigger-token: <TRIGGER_TOKEN>` (or `Authorization: Bearer <TRIGGER_TOKEN>`)
  - Include JSON body with valid sender and one exact subject:
   - `{"from":"hummesse@gmail.com","subject":"resultater"}`
   - `{"from":"hummesse@gmail.com","subject":"append","body_text":"new@example.com"}`

Phone email trigger without domain (recommended):

1. Use Google Apps Script as a bridge from Gmail to the Worker HTTP endpoint.
2. Script file in this repo: `cloudflare-email-worker/gmail_phone_bridge.gs`
3. Configure in the script:
   - `triggerToken`
  - Subject tokens: `resultater`, `append`, `delete`, `afmeld resultater`
  - Sender rules (`appendSender`, `deleteSender`, `unsubscribeDisallowedSender`)
4. Install the time trigger by running `installMinuteTrigger()` once.
5. From phone, send an email to your Gmail inbox with one exact subject:
  - `resultater`
  - `append` (hummesse only, body must include recipient emails)
  - `delete` (hummesse only, body must include recipient emails)
  - `afmeld resultater` (non-hummesse only)
6. The script polls inbox, calls Worker `/trigger`, labels processed threads, and the GitHub workflow runs.
