# M2S Web App (Isolated from Python)

This folder contains the JavaScript/TypeScript web implementation and does not modify the existing Python pipeline.

## Run locally

1. Install dependencies:
   npm install
2. Start dev server:
   npm run dev
3. Open the URL printed by Vite.

## Current scope

- Fetches live regatta data from Manage2Sail in-browser and computes standings locally
- Fixed setup matching the 2026 PDF defaults:
   - Event: Onsdagsbanen 2026
   - Stor Bane = Stor bane 1 + Stor bane 2
   - Lille Bane = Lille bane 1 + Lille bane 2
- One-button UI: `Refresh results`
- Uses per-group dynamic color themes

If live fetch fails due to CORS/network constraints, the app fails hard and does not load any fallback/snapshot data.

## Security note

GitHub Pages is static hosting.

## CORS note

Direct browser fetches to Manage2Sail may be blocked by CORS in some environments.
The app automatically attempts direct fetch first, then proxy fallbacks (`https://api.codetabs.com/v1/proxy?quest=` and `https://api.allorigins.win/raw?url=`).
