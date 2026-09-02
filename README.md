# Ozon AI Analytics

Private analytics service for an Ozon Seller shop. It collects Seller API and
Performance API data, calculates operational KPIs and serves a visual dashboard
and a safe read-only API for a custom GPT Action.

## Included in the first version

- Seller API client: products, stocks, FBO/FBS postings and finance reports.
- Performance API OAuth client, ready for advertising reports.
- Scheduled collection (hourly operations, daily finance/advertising).
- KPI layer: revenue, units, average order value, returns, stock cover, CTR,
  CPC, advertising spend and ROAS.
- Dashboard with clear green/yellow/red status indicators.
- Read-only endpoints designed for a ChatGPT GPT Action.

## Run locally

1. Copy `.env.example` to `.env` and enter **newly generated** Ozon secrets.
2. Run `docker compose up --build`.
3. Open `http://localhost:8000`.

Never commit `.env`, API keys, OAuth client secrets or exported reports.

## GitHub setup

Create a new **private** repository called `ozon-ai-analytics`, then push this
folder to its `main` branch. Add the same variables from `.env.example` in the
repository's Actions/Deployment secrets; do not place them in source code.

## Custom GPT Action

After the service is deployed at a protected HTTPS domain, use
`GET /openapi.json` as the GPT Action schema and configure authentication with
the separate `GPT_ACTION_TOKEN`. Keep only the `GET /api/v1/*` endpoints
available to GPT; Ozon write operations are deliberately not exposed.

## Data needed for accurate profit

Ozon provides revenue, fees, logistics, returns and ad spend. Add purchase cost
per SKU (CSV or a later admin screen) to calculate net profit and margin.
