# Merv UI

Browser frontend for the Merv brain. It reads and mutates research
state through the brain's HTTP API; it never reads a research checkout
directly. Checkout-local registration, validation, and output transfer remain
the MCP proxy's responsibility.

## Run Locally

Start the local brain first, usually on `127.0.0.1:8787`.
Then run the UI:

```bash
npm install
npm run dev
```

Vite serves the app on `http://127.0.0.1:5173` by default and proxies `/api`
and `/health` to the local brain.

## Backend Target

For local development, point the Vite proxy at a non-default brain URL with:

```bash
RSUI_API=http://127.0.0.1:8788 npm run dev
```

For hosted or static builds, use:

```bash
VITE_API_BASE=https://your-control-plane.example.com npm run build
```

The client can attach an optional bearer token from `VITE_API_TOKEN` or the
`rsui:apiToken` local-storage key. The current Merv brain does not
authenticate end users, so a hosted deployment must remain behind a trusted
network boundary; CORS is not authentication.

The UI receives updates over server-sent events and falls back to conditional
polling when the stream is unavailable.

## Commands

```bash
npm run dev      # development server
npm run build    # production build into dist/
npm run preview  # preview the production build
```
