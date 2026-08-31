# Google Ads API — Tool Design Document

**Company:** Nevada Eco Peças (auto parts retailer, Brazil)
**Website:** https://nevadaautopecas.com.br
**Google Ads account:** 230-286-5972 (Nevada Ecopeças)
**Manager account (MCC):** 398-576-5846
**Google Cloud project number:** 608560018719
**API version:** v18 (REST interface)

---

## 1. Purpose

We operate a single Google Ads account for our own auto parts business. This
tool reads reporting data from that account and displays it on an internal
management dashboard used by our own staff.

Before this integration, the same numbers were imported manually from a
third-party connector. The purpose of using the API directly is accuracy and
freshness of our own reporting — not automation of campaign management.

## 2. Scope of access — read only

The tool performs **no write operations of any kind**. It does not create,
modify, pause or remove campaigns, ad groups, ads, keywords, budgets, bids,
audiences, conversion actions or account settings.

Every call is a `GoogleAdsService.SearchStream` request. The complete set of
queries the tool issues is:

**Query 1 — daily cost and performance by campaign**

```sql
SELECT campaign.id,
       campaign.name,
       segments.date,
       metrics.cost_micros,
       metrics.clicks,
       metrics.impressions,
       metrics.conversions
  FROM campaign
 WHERE segments.date BETWEEN '<start>' AND '<end>'
```

**Query 2 — account identification (used only by the connection test)**

```sql
SELECT customer.descriptive_name,
       customer.currency_code
  FROM customer
 LIMIT 1
```

No other resource is queried.

## 3. Frequency and volume

The collector runs **once per day**, as part of a scheduled routine at 07:30
(America/Sao_Paulo). Each run issues approximately **2 to 5 requests**,
covering the current calendar year to date.

Estimated volume: **fewer than 100 operations per day**.

## 4. Users and distribution

The dashboard is used **only by employees of Nevada Eco Peças** — the owner and
a small sales team. It is protected by a password login and is not offered,
sold, licensed or made available to any third party. We do not manage Google
Ads accounts for other companies, and we do not resell, redistribute or expose
the data outside the company.

## 5. Architecture

| Layer | Description |
|---|---|
| Collector | Python script (`app/google_ads_api.py`), runs locally on a scheduled task |
| Transport | Google Ads API REST endpoint via standard HTTPS (`urllib`), no third-party SDK |
| Auth | OAuth 2.0 authorization code flow; refresh token generated once by the account owner |
| Storage | Results written to local JSON files, then pushed to a private Postgres database |
| Presentation | Internal web dashboard (Flask), password-protected, used by staff only |

Data flow:

```
Google Ads API  →  collector (local)  →  JSON  →  private database  →  internal dashboard
```

## 6. Credential handling

The developer token, OAuth client ID, client secret and refresh token are
stored in a single file outside version control, on a machine controlled by the
company. They are never embedded in the application code, never sent to any
third party, and never exposed to the dashboard's users.

## 7. Data retained

Only aggregate advertising metrics: campaign name and ID, date, cost, clicks,
impressions and conversions. The tool does not request, receive or store any
personal data, user identifiers, or Customer Match audience data.

## 8. Compliance

- Read-only access; no mutate operations
- Data used solely for internal reporting about our own advertising spend
- No redistribution, resale or third-party access
- API contact email is a monitored inbox on the company domain
  (contato@nevadaautopecas.com.br)
