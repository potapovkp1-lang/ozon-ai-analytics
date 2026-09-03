# Ozon AI Analytics

Private analytics service for an Ozon Seller shop. It collects Seller API and
Performance API data, calculates operational KPIs and serves a visual dashboard
and a safe read-only API for a custom GPT Action.

## Included

- Seller API client: products, stocks, FBO/FBS postings and finance reports.
- Performance API OAuth client, ready for advertising reports.
- Scheduled collection (hourly operations, daily finance/advertising).
- Date filters for 7 days, 30 days or any stored custom period.
- Orders, realised sales and returns in rubles and units.
- Ozon finance transactions for commissions, logistics and service charges.
- Purchase-cost CSV import with historical prices and per-SKU VAT rates.
- Estimated profit, buyout rate and markup before/after tax.
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

## Railway deployment

Production: https://ozon-ai-analytics-production.up.railway.app/

Railway deploys the latest commit from the `main` branch. Keep all Ozon and
dashboard credentials only in Railway service variables.

## Custom GPT Action

After the service is deployed at a protected HTTPS domain, use
`GET /openapi.json` as the GPT Action schema and configure authentication with
the separate `GPT_ACTION_TOKEN`. Keep only the `GET /api/v1/*` endpoints
available to GPT; Ozon write operations are deliberately not exposed.

## Data needed for accurate profit

Ozon provides orders, finance operations, commissions, logistics and returns.
The dashboard has a **Себестоимость** panel: download its CSV template, fill one
row per Ozon SKU and upload it back. The required fields are:

- `ozon_sku`: numeric Ozon SKU;
- `себестоимость_с_ндс`: unit purchase price from the supplier document;
- `ндс_поставщика`: 0, 10, 20 or 22;
- `доп_затраты_без_ндс`: packaging, marking and inbound delivery per unit;
- `ндс_продажи`: VAT rate used when that SKU is sold;
- `действует_с`: first date for the cost in `YYYY-MM-DD` format.

Add a new row with a new effective date when purchase cost changes. Tax figures
are management estimates and must be reconciled with supplier invoices, Ozon
UPDs and the accounting system before filing. The default income-tax rate is
25%; override `INCOME_TAX_RATE` for a different legal form or tax treatment.
