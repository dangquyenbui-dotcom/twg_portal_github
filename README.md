# TWG Portal

A secure, enterprise-grade internal portal for **The Wheel Group**, built with Flask and integrated with Microsoft Entra ID (SSO) for authentication and Microsoft SQL Server for real-time ERP data.

The portal serves as a centralized dashboard hub for multiple departments — starting with **Sales** — providing live KPIs, territory/salesman/customer ranking tabs with podium displays, an interactive executive dashboard with Chart.js visualizations and yearly data from dual SQL tables (sotran + soytrn), frozen offline data files for instant historical year loading, an admin page for data management, real-time CAD→USD currency conversion, formatted Excel data exports, dark/light theme switching with OLED support, and auto-refreshing displays optimized for desktop monitors, tablets, iPhones/iPads, and unattended TV/kiosk screens.

---

## Table of Contents

- [Architecture Overview](#architecture-overview)
- [Tech Stack](#tech-stack)
- [Project Structure](#project-structure)
- [Authentication Flow](#authentication-flow)
- [Role-Based Access Control (RBAC)](#role-based-access-control-rbac)
- [Data Architecture](#data-architecture)
- [Dark / Light Mode](#dark--light-mode)
- [Sales Module](#sales-module)
  - [Department Hub](#department-hub-)
  - [Sales Report Menu](#sales-report-menu-sales)
  - [Daily Bookings Dashboard](#daily-bookings-dashboard)
  - [Ranking Tabs (Territory / Salesman / Customer)](#ranking-tabs-territory--salesman--customer)
  - [Open Sales Orders Dashboard](#open-sales-orders-dashboard)
  - [Executive Dashboard](#executive-dashboard)
- [Executive Dashboard — Data Layer](#executive-dashboard--data-layer)
  - [Dual-Table Strategy (sotran + soytrn)](#dual-table-strategy-sotran--soytrn)
  - [Frozen Data Files](#frozen-data-files)
  - [Data Resolution Priority](#data-resolution-priority)
  - [Admin Page — Dashboard Data Management](#admin-page--dashboard-data-management)
- [Amount Calculation & Discount Handling](#amount-calculation--discount-handling)
- [Currency Conversion](#currency-conversion)
- [Excel Exports](#excel-exports)
- [Responsive Design](#responsive-design)
- [Configuration Reference](#configuration-reference)
- [Setup & Installation](#setup--installation)
- [Environment Variables](#environment-variables)
- [Running the Application](#running-the-application)
- [Deployment Notes](#deployment-notes)
- [HTTPS & Redirect URI Handling](#https--redirect-uri-handling)
- [URL Reference](#url-reference)
- [Troubleshooting](#troubleshooting)
- [Roadmap](#roadmap)

---

## Architecture Overview

The application follows a **Decoupled Caching Architecture** with a **three-tier data resolution** strategy to ensure instant page loads without overloading the ERP SQL Server.

```
┌─────────────┐      ┌──────────────┐      ┌─────────────────────────────┐
│   Browser    │◄────►│  Flask App   │◄────►│  Data Resolution (3 tiers)  │
│  (User/TV)   │      │  (Routes)    │      │                             │
└─────────────┘      └──────────────┘      │  1. Frozen files on disk    │
                                            │     (dashboard_data/*.gz)   │
                                            │  2. In-memory cache         │
                                            │     (Flask-Caching)         │
                                            │  3. SQL Server (fallback)   │
                                            │     soytrn + sotran         │
                                            └──────────┬──────────────────┘
                                                       │
                                    ┌──────────────────┴──────────────────┐
                                    │          APScheduler                 │
                                    │   ┌──────────┐ ┌────────────┐       │
                                    │   │ Bookings │ │ Open Orders│       │
                                    │   │ 10 min   │ │  60 min    │       │
                                    │   └────┬─────┘ └─────┬──────┘       │
                                    │        │     ┌───────┴────────┐     │
                                    │        │     │ Dashboard Curr │     │
                                    │        │     │ Month (60 min) │     │
                                    │        │     └───────┬────────┘     │
                                    └────────┼─────────────┼──────────────┘
                                             │             │
                                   ┌─────────┴──┐  ┌──────┴────────┐
                                   │ SQL Server  │  │ Exchange Rate │
                                   │ PRO05 (US)  │  │ API (CAD→USD) │
                                   │ PRO06 (CA)  │  └───────────────┘
                                   │             │
                                   │ sotran      │ ← current month line items
                                   │ soytrn      │ ← historical line items (same schema)
                                   └─────────────┘
```

**How it works:**

1. **Background Workers (APScheduler)** — Three independent scheduled jobs:
   - **Bookings refresh** — Every **10 minutes**. Queries today's bookings from sotran for both US (PRO05) and Canada (PRO06), fetches the live CAD→USD exchange rate, and caches all results. Also runs once immediately on app startup.
   - **Open orders refresh** — Every **60 minutes**. Queries all currently open sales order lines from sotran.
   - **Dashboard current month refresh** — Every **60 minutes**. Queries current month from sotran for the executive dashboard. Historical data is NOT auto-refreshed — it comes from frozen files on disk or on-demand SQL.
2. **Frozen Data Files** — For the executive dashboard, completed years of data are "downloaded" by an admin and saved as gzip-compressed JSON files on disk (`dashboard_data/*.json.gz`). These load in <1ms — zero SQL, zero network. Portable: copy the folder to any server.
3. **Cache Layer (Flask-Caching)** — Stores the latest data snapshots using `FileSystemCache`. Survives brief app restarts. Each cache entry includes a `last_updated` timestamp.
4. **Web App (Flask)** — Route handlers **never** query SQL directly for bookings/open orders — they read from cache. The executive dashboard reads from frozen files first, then cache, then SQL as a last resort.
5. **Auto-Refresh (Client-Side)** — The bookings page auto-refreshes every 10 minutes with a countdown timer for TV/kiosk displays. Open orders and dashboard do not auto-refresh.

---

## Tech Stack

| Layer            | Technology                                        |
|------------------|---------------------------------------------------|
| **Backend**      | Python 3.12+, Flask 3.0                           |
| **Auth (SSO)**   | Microsoft Entra ID (Azure AD), MSAL for Python    |
| **RBAC**         | Entra ID Security Groups, custom `@require_role` decorator, per-report View/Export permissions, role hierarchy |
| **Database**     | Microsoft SQL Server (US: PRO05, Canada: PRO06), pyodbc, dual tables: sotran (current month) + soytrn (historical, identical schema) |
| **Caching**      | Flask-Caching (FileSystemCache)                   |
| **Frozen Data**  | gzip-compressed JSON files in `dashboard_data/` folder — portable offline storage for historical years |
| **Scheduler**    | Flask-APScheduler (background data refresh)       |
| **Exchange Rate**| frankfurter.app (primary), open.er-api.com (fallback) |
| **Frontend**     | Jinja2 templates, vanilla CSS/JS, CSS custom properties for theming |
| **Charts**       | Chart.js 4.4 (loaded from cdnjs.cloudflare.com CDN) — executive dashboard |
| **Theme**        | Dark/Light mode via `data-theme` HTML attribute + `localStorage` persistence |
| **Fonts**        | DM Sans (UI), JetBrains Mono (numbers/code)       |
| **Excel Export** | openpyxl (formatted .xlsx generation)             |
| **Production**   | Waitress (WSGI server)                            |
| **Environment**  | python-dotenv (.env file)                         |

---

## Project Structure

```
twg_portal/
│
├── app.py                    # Application factory, SSO routes, HTTPS redirect URI builder, scheduler init (3 jobs)
├── config.py                 # All configuration (auth, DB, cache, scheduler intervals, per-report GROUP_ROLE_MAP, DASHBOARD_REFRESH_INTERVAL)
├── extensions.py             # Shared Flask extensions (Cache, APScheduler)
├── requirements.txt          # Python dependencies
├── .env                      # Environment variables (secrets — never committed)
├── .gitignore                # Git exclusions (includes dashboard_data/)
├── README.md                 # This file
│
├── auth/
│   ├── __init__.py
│   ├── entra_auth.py         # MSAL helper: build app, token exchange via acquire_token_by_auth_code_flow
│   └── decorators.py         # @require_role decorator, ROLE_HIERARCHY, _user_has_role, user_has_role template helper
│
├── routes/
│   ├── __init__.py
│   ├── main.py               # Home page (department hub), login page
│   ├── sales.py              # Sales blueprint: bookings + open orders + executive dashboard + Excel exports + dashboard refresh
│   └── admin.py              # Admin blueprint: dashboard data management (download/delete/status via AJAX)
│
├── services/
│   ├── __init__.py
│   ├── constants.py          # Shared territory maps (US + CA), customer exclusion set, map_territory(), resolve_territory_code()
│   ├── db_connection.py      # pyodbc connection factory with 30s timeout
│   ├── db_service.py         # Legacy bookings service (retained for reference — not used by current routes)
│   ├── bookings_service.py   # Bookings SQL queries against sotran + Python aggregation (territory + salesman + customer rankings)
│   ├── open_orders_service.py# Open orders SQL queries against sotran + Python aggregation (territory + salesman, released tracking)
│   ├── dashboard_service.py  # Legacy dashboard service (retained for reference — replaced by dashboard_data_service.py)
│   ├── dashboard_data_service.py # Executive dashboard data layer: frozen file I/O, SQL fetch (soytrn + sotran), Python aggregation, 3-tier resolution, admin download/delete, scheduler refresh
│   ├── data_worker.py        # Background cache refresh logic for bookings + open orders, exchange rate fetching with failover, all cache keys centralized
│   └── excel_helper.py       # Shared Excel workbook builder (openpyxl): title row, metadata, headers, alternating rows, money formatting
│
├── static/
│   ├── logo/
│   │   ├── TWG.png           # Company logo used in nav and login page
│   │   ├── apple-touch-icon.png   # iOS home screen icon (180×180)
│   │   ├── icon-192x192.png       # Android/PWA icon
│   │   └── icon-512x512.png       # PWA splash icon
│   ├── css/
│   │   └── dashboard.css     # Executive dashboard styles (reference copy — also inlined in template)
│   ├── js/
│   │   └── dashboard.js      # Chart.js rendering (monthly/territory/product line/salesman), year selector, refresh button, theme-aware recoloring
│   └── manifest.json         # PWA manifest (display: browser, theme_color: #111827)
│
├── templates/
│   ├── base.html             # Shared layout: nav bar, theme toggle (sun/moon), avatar, breadcrumbs, dark/light CSS variables, no-flash script
│   ├── login.html            # Microsoft SSO login page (standalone, own theme support with floating toggle)
│   ├── index.html            # Department hub (Sales, Warehouse, Accounting, HR cards) — role-aware visibility
│   ├── sales/
│   │   ├── index.html        # Sales report menu (Dashboard, Bookings, Open Orders, Coming Soon) — Live/Export/View Only badges
│   │   ├── bookings.html     # Daily Bookings: US + CA, 4 KPI cards, ranking tabs (Territory/Salesman/Customer), podium + table, export buttons gated by can_export
│   │   ├── open_orders.html  # Open Sales Orders: US + CA, 4 KPI cards with Released sub-line, side-by-side territory + salesman rankings, export buttons gated
│   │   └── dashboard.html    # Executive Dashboard: year selector, 5 KPI cards, region split bar, Sales by Month hero chart, territory/product line/salesman charts, top 50 customers, refresh button
│   └── admin/
│       └── dashboard_data.html # Admin: Dashboard Data Management — year cards with US/CA status, download/re-download/delete buttons, toast notifications
│
├── dashboard_data/           # Frozen gzip JSON files for historical years (gitignored, portable)
│   ├── us_2025.json.gz       # Example: US 2025 pre-aggregated summary (~1-2KB)
│   ├── ca_2025.json.gz       # Example: CA 2025 pre-aggregated summary
│   └── ...                   # One file per region per year
│
└── cache-data/               # Auto-generated cache directory (gitignored)
```

---

## Authentication Flow

The portal uses **Microsoft Entra ID (formerly Azure AD)** for Single Sign-On via the OAuth 2.0 Authorization Code Flow with PKCE.

```
User clicks "Sign in with Microsoft"
        │
        ▼
  GET /login
  ├── _build_redirect_uri()
  │   ├── If REDIRECT_URI_OVERRIDE is set in .env → use it verbatim
  │   ├── Else build from request.url_root
  │   └── Force https:// for non-localhost hosts (proxy-safe)
  ├── Build MSAL ConfidentialClientApplication
  ├── initiate_auth_code_flow() with computed redirect_uri
  └── Redirect user to Microsoft login page
        │
        ▼
  User authenticates with Microsoft
        │
        ▼
  GET /auth/redirect  (callback)
  ├── Exchange auth code for tokens via acquire_token_by_auth_code_flow()
  ├── Extract id_token_claims (name, email, oid, tid, groups)
  ├── _resolve_roles_from_groups(): map Security Group Object IDs → internal role names via GROUP_ROLE_MAP
  ├── Store in session: name, email, oid, tid, groups (raw IDs), roles (resolved names)
  └── Redirect to home page (/)
        │
        ▼
  GET /logout
  ├── Clear Flask session
  └── Redirect to Microsoft logout endpoint with post_logout_redirect_uri
```

**Key implementation details:**

- `acquire_token_by_auth_code_flow()` is used instead of `acquire_token_by_authorization_code()` to properly handle PKCE verification, preventing AADSTS50148 errors.
- Redirect URIs are built by `_build_redirect_uri()` which **forces `https://`** for any non-localhost host. This is critical when running behind a reverse proxy (IIS, nginx) with SSL termination.
- An optional `REDIRECT_URI_OVERRIDE` environment variable allows hardcoding the full redirect URI.
- The `.env` file loader has a fallback from `.env` to `_env` for Windows filename quirks.
- Config validation runs at startup and raises `SystemExit` with clear error messages if required values are missing.
- User session stores `name`, `email`, `oid`, `tid`, `groups` (raw Entra group Object IDs), and `roles` (resolved internal role names).

**Required Azure App Registration settings:**

- **Platform:** Web
- **Redirect URIs:** `http://localhost:5000/auth/redirect`, `https://dev.thewheelgroup.info/auth/redirect`, `https://portal.thewheelgroup.info/auth/redirect`
- **API Permissions:** `User.Read` (Microsoft Graph)
- **Token configuration:** Add **groups** optional claim — Token Configuration → Add groups claim → select Security groups
- **Client Secret:** Generate under Certificates & secrets

---

## Role-Based Access Control (RBAC)

The portal enforces page-level and feature-level access control using **Microsoft Entra ID Security Groups** mapped to internal role names via `GROUP_ROLE_MAP` in `config.py`.

### Per-Report View & Export Permissions

Every report has two separate permission levels:

- **View** (`Sales.<Report>.View`) — Grants access to see the dashboard page.
- **Export** (`Sales.<Report>.Export`) — Enables Excel download buttons. Does NOT grant view access on its own.

Export buttons are completely invisible (removed from DOM via `{% if can_export %}`) if the user lacks the export role.

**Security Groups → Roles:**

| Entra ID Security Group Name | Env Var | Internal Role | What It Grants |
|---|---|---|---|
| `TWG-Portal-Admin` | `GROUP_ADMIN` | `Admin` | Full access to everything (view + export all, admin pages, bypasses all checks) |
| `TWG-Portal-Sales-Dashboard-View` | `GROUP_SALES_DASHBOARD_VIEW` | `Sales.Dashboard.View` | View the executive dashboard |
| `TWG-Portal-Sales-Bookings-View` | `GROUP_SALES_BOOKINGS_VIEW` | `Sales.Bookings.View` | View Daily Bookings |
| `TWG-Portal-Sales-Bookings-Export` | `GROUP_SALES_BOOKINGS_EXPORT` | `Sales.Bookings.Export` | Download Bookings Excel |
| `TWG-Portal-Sales-OpenOrders-View` | `GROUP_SALES_OPENORDERS_VIEW` | `Sales.OpenOrders.View` | View Open Orders |
| `TWG-Portal-Sales-OpenOrders-Export` | `GROUP_SALES_OPENORDERS_EXPORT` | `Sales.OpenOrders.Export` | Download Open Orders Excel |

### Role Hierarchy

```python
ROLE_HIERARCHY = {
    'Sales.Base': [
        'Sales.Bookings.View',
        'Sales.OpenOrders.View',
        'Sales.Dashboard.View',
    ],
}
```

- `Sales.Base` — Never assigned directly, implied by any `Sales.*.View` role, grants access to `/sales` hub.
- `Admin` — Bypasses all checks (first check in `_user_has_role()`).

### Route Enforcement

| Route | Decorator | Notes |
|---|---|---|
| `/sales` | `@require_role('Sales.Base')` | Implied by any Sales.*.View |
| `/sales/dashboard` | `@require_role('Sales.Dashboard.View')` | Year-based executive dashboard |
| `/sales/dashboard/refresh` | `@require_role('Sales.Dashboard.View')` | AJAX: invalidate cache, reload |
| `/sales/bookings` | `@require_role('Sales.Bookings.View')` | Daily bookings with ranking tabs |
| `/sales/bookings/export/*` | `@require_role('Sales.Bookings.Export')` | Excel downloads |
| `/sales/open-orders` | `@require_role('Sales.OpenOrders.View')` | Open orders dashboard |
| `/sales/open-orders/export/*` | `@require_role('Sales.OpenOrders.Export')` | Excel downloads |
| `/admin/dashboard-data` | `@require_role('Admin')` | Data management page |
| `/admin/dashboard-data/download` | `@require_role('Admin')` | AJAX: download single region |
| `/admin/dashboard-data/download-both` | `@require_role('Admin')` | AJAX: download US + CA |
| `/admin/dashboard-data/delete` | `@require_role('Admin')` | AJAX: delete frozen file |

### Adding a New Report

1. Create 2 Entra Security Groups (View + Export)
2. Add 2 env vars in `.env` with Object IDs
3. Add 2 entries to `group_vars` in `config.py`
4. Add `.View` role to `ROLE_HIERARCHY['Sales.Base']` in `decorators.py`
5. Build route + template — no framework changes needed

---

## Data Architecture

### Dual-Region Database

| Region | Database | Description |
|--------|----------|-------------|
| US     | PRO05    | US orders and sales data |
| Canada | PRO06    | Canadian orders and sales data |

### Dual-Table Strategy (sotran + soytrn)

| Table | Contains | Used By |
|-------|----------|---------|
| `sotran` | Current month line items (live transactional data) | Daily Bookings, Open Orders, Dashboard (current month) |
| `soytrn` | Historical line items (completed months, identical schema to sotran) | Dashboard (past months/years) |

Both tables have identical field structures: `sono`, `origqtyord`, `price`, `disc`, `ordate`, `custno`, `salesmn`, `sostat`, `sotype`, `currhist`, `plinid` (via icitem join), `terr` (via somast/arcust joins).

### SQL Server Connection

```
Driver:   ODBC Driver 18 for SQL Server
Server:   twg-sql-01.thewheelgroup.com
Database: PRO05 (US) / PRO06 (Canada)
Auth:     SQL Server authentication (UID/PWD)
Options:  TrustServerCertificate=yes, Timeout=30s
Locking:  All queries use WITH (NOLOCK)
```

### Query Strategy

**Lean SQL, heavy Python:** SQL does simple `SELECT` with filters and `NOLOCK`. No `GROUP BY`, no `SUM` — all aggregation happens in Python on the web server.

- Daily Bookings/Open Orders: ~5K rows, aggregated instantly
- Dashboard (full year): ~400K+ rows for US, ~90K for CA — Python aggregates in a single pass (~15-20 seconds), then discards raw rows. Only the tiny summary dict (~5KB) is cached.

### Territory Mapping

Territory codes mapped in `services/constants.py`:

**US:** `000/001` → LA, `010` → China, `114` → Seattle, `126` → Denver, `204` → Columbus, `206` → Jacksonville, `210` → Houston, `211` → Dallas, `218` → San Antonio, `221` → Kansas City, `302` → Nashville, `305` → Levittown PA, `307` → Charlotte, `312` → Atlanta, `324` → Indianapolis, `900` → Central Billing, others → Others.

**CA:** `501` → Vancouver, `502` → Toronto, `503` → Montreal, others → Others.

### Excluded Data

**Customers:** W1VAN, W1TOR, W1MON, MISC, TWGMARKET, EMP-US, TEST123.
**Product lines:** TAX.
**Statuses:** Bookings excludes `currhist=X`, `sostat IN (V,X)`, `sotype IN (B,R)`. Open orders excludes `sostat IN (C,V,X)` at line level, `somast.sostat=C` at order level, `sotype IN (B,R)`.

### Scheduler Strategy (Three Jobs)

| Job ID | Interval | What It Refreshes | Cache TTL |
|---|---|---|---|
| `bookings_refresh` | 10 min | Bookings snapshots + raw (US + CA), exchange rate | 900s (15 min) |
| `open_orders_refresh` | 60 min | Open orders snapshots + raw (US + CA) | 3900s (65 min) |
| `dashboard_current_refresh` | 60 min | Dashboard current month only (sotran, US + CA) | 3900s (65 min) |

All bookings + open orders jobs run once on startup. Dashboard historical data is NOT fetched on startup — loaded on demand or from frozen files.

### Caching Strategy

**Cache keys (bookings + open orders, defined in `data_worker.py`):**

| Key | Type | Description |
|---|---|---|
| `bookings_snapshot_us` | `dict` | US bookings summary + territory/salesman/customer rankings |
| `bookings_snapshot_ca` | `dict` | Canada bookings summary + rankings |
| `bookings_raw_us` | `list` | US raw line-item data for Excel + dashboard |
| `bookings_raw_ca` | `list` | Canada raw data |
| `bookings_last_updated` | `datetime` | Last refresh timestamp |
| `open_orders_snapshot_us` | `dict` | US open orders summary + rankings |
| `open_orders_snapshot_ca` | `dict` | Canada open orders summary |
| `open_orders_raw_us` | `list` | US raw data |
| `open_orders_raw_ca` | `list` | Canada raw data |
| `open_orders_last_updated` | `datetime` | Last refresh timestamp |
| `cad_to_usd_rate` | `float` | Latest CAD → USD rate |

**Cache keys (dashboard, defined in `dashboard_data_service.py`):**

| Key Pattern | Type | Description |
|---|---|---|
| `dash_hist_{region}_{year}` | `dict` | Historical year summary (e.g., `dash_hist_us_2025`) — 24hr TTL |
| `dash_current_{region}` | `dict` | Current month summary (e.g., `dash_current_us`) — 65min TTL |
| `dashboard_last_updated` | `datetime` | Last current-month refresh timestamp |

---

## Dark / Light Mode

Full dark/light theme switching across all pages including the standalone login page.

- `data-theme` attribute on `<html>` element, toggled by sun/moon button in nav bar
- All colors via CSS custom properties with complete light and dark sets
- `localStorage` key `twg-theme` persists choice across sessions
- Synchronous inline `<script>` in `<head>` prevents flash of wrong theme
- Dark mode uses **true OLED black** (`#000000`) background
- Chart.js charts detect theme changes via `MutationObserver` and re-render with appropriate colors

---

## Sales Module

### Department Hub (`/`)

Card-based grid. Sales is live; Warehouse, Accounting, HR show "Coming Soon." Sales card only visible with any `Sales.*.View` role.

### Sales Report Menu (`/sales`)

Cards for Dashboard, Bookings, Open Orders. Each shows "Live" + "Export" or "View Only" badges per role.

### Daily Bookings Dashboard

**Route:** `/sales/bookings` — **Role:** `Sales.Bookings.View`
**Data:** `sotran` where `ordate = today` — auto-refreshes every 10 min

Per region (US + CA): 4 KPI cards, ranking tabs (Territory/Salesman/Customer) with podium + table, export buttons gated by `can_export`.

**Bookings Snapshot Query:** `SELECT sono, origqtyord AS units, origqtyord * price * (1 - disc/100.0) AS amount, CASE terr, custno, plinid, salesmn, cu.company AS cust_name FROM sotran...`

### Ranking Tabs (Territory / Salesman / Customer)

Three tabs per region on the bookings page. Tab switching is instant (CSS class toggle in JS, no page reload). All three rankings server-rendered, scoped by `data-region` attribute. Podium (top 3 gold/silver/bronze) + table (4th and below). Canada includes `≈ USD` column.

### Open Sales Orders Dashboard

**Route:** `/sales/open-orders` — **Role:** `Sales.OpenOrders.View`
**Data:** All open `sotran` lines (no date filter) — refreshes hourly

Per region: 4 KPI cards with Released sub-line, side-by-side territory + salesman rankings.

### Executive Dashboard

**Route:** `/sales/dashboard?year=2026` — **Role:** `Sales.Dashboard.View`
**Data:** Full year from soytrn (historical) + sotran (current month)

**Page layout:**
1. **Year selector dropdown** (5 years back) — navigates on change
2. **5 KPI cards** — Total Sales (USD), Total Units, Sales Orders, Avg Order Value, Line Items
3. **Region split bar** — US vs CA proportion with dollar amounts
4. **Sales by Month** — Full-width hero bar chart (12 months, Chart.js)
5. **Sales by Territory** — Horizontal bar chart (top 15)
6. **Sales by Product Line** — Donut chart with legend
7. **Sales by Salesman** — Full-width horizontal bar chart (top 15)
8. **Top 50 Customers** — Scrollable table
9. **Refresh Data button** — Invalidates cache for selected year, re-fetches from SQL

---

## Executive Dashboard — Data Layer

### Dual-Table Strategy (sotran + soytrn)

The ERP stores current month line items in `sotran` and moves them to `soytrn` when the month closes. Both tables have identical schemas. The dashboard queries both:

- **soytrn** — Historical months (Jan through previous month for current year, full year for past years)
- **sotran** — Current month only (no date filter needed, the table IS the current month)

### Frozen Data Files

For completed years, the data never changes. Instead of hitting SQL Server every time, an admin "downloads" the year once via the admin page. The app saves the pre-aggregated summary as a gzip-compressed JSON file:

```
dashboard_data/
├── us_2025.json.gz    (~1-2KB — summary of ~392K raw rows)
├── ca_2025.json.gz    (~1KB — summary of ~88K raw rows)
├── us_2024.json.gz
├── ca_2024.json.gz
└── ...
```

**File format:** gzip JSON containing `{meta: {region, year, frozen_at, version}, data: {summary, monthly_totals, by_territory, by_salesman, by_product_line, by_customer}}`

**Portability:** Copy the entire `dashboard_data/` folder to a new server and all historical years load instantly without any SQL queries.

### Data Resolution Priority

When the dashboard needs data for a year + region:

```
1. Check frozen file on disk (dashboard_data/{region}_{year}.json.gz)
   → Found? Return immediately (<1ms). Done.
   
2. Check in-memory cache (dash_hist_{region}_{year})
   → Found? Return immediately. Done.

3. Fetch from SQL Server (soytrn SELECT with NOLOCK)
   → Aggregate in Python (single pass, ~15-20 seconds)
   → Cache the summary (24hr TTL)
   → Return. Raw rows garbage collected.
```

For the current year's current month, the resolution is:

```
1. Check in-memory cache (dash_current_{region})
   → Found? Return. Done.

2. Fetch from SQL Server (sotran SELECT)
   → Aggregate, cache (65min TTL), return.
```

The scheduler refreshes the current month cache every 60 minutes.

### Admin Page — Dashboard Data Management

**Route:** `/admin/dashboard-data` — **Role:** `Admin` only

Shows a card for each year (current year back 5 years) with US and CA rows:

| Column | Content |
|---|---|
| **Year** | Year number with "Current Year — Live" or "Historical" badge |
| **Region** | US or CA |
| **Status** | Green dot "Downloaded (1,847 bytes) · 2026-03-09 14:30" or gray dot "Not downloaded — will fetch from SQL on demand (~15-20s)" |
| **Actions** | Download / Re-download / Delete buttons |

**"Download US + CA" button** per year fetches both regions sequentially (~30-40 seconds total), aggregates in Python, saves to disk.

**AJAX endpoints:**
- `POST /admin/dashboard-data/download` — Download single region `{year, region}`
- `POST /admin/dashboard-data/download-both` — Download both US + CA `{year}`
- `POST /admin/dashboard-data/delete` — Delete frozen file `{year, region}`

---

## Amount Calculation & Discount Handling

```
Amount = quantity × price × (1 - disc / 100)
```

- Bookings: `origqtyord × price × (1 - disc/100)` — original quantity ordered
- Open Orders: `qtyord × price × (1 - disc/100)` — remaining open quantity
- All totals rounded up with `math.ceil()`

---

## Currency Conversion

| Priority | API | Fallback |
|---|---|---|
| Primary | `api.frankfurter.app` | — |
| Secondary | `open.er-api.com` | — |
| Hardcoded | — | `0.72` if all fail |

Rate validated 0.50–1.00. Conversions appear on all Canadian amounts across all reports.

---

## Excel Exports

All exports read from cache — zero SQL at download time. 26 columns each for bookings and open orders. Formatted with openpyxl: dark header, alternating rows, green money font, frozen header, auto-filter.

**Bookings:** `/sales/bookings/export`, `/sales/bookings/export/us`, `/sales/bookings/export/ca`
**Open Orders:** `/sales/open-orders/export`, `/sales/open-orders/export/us`, `/sales/open-orders/export/ca`

---

## Responsive Design

### Desktop (1024px+)
Full layout, 4/5-column grids, side-by-side rankings, podium, breadcrumbs, max-width 1400px.

### Tablet (768–1024px)
Scaled fonts (20px KPI values), tighter padding, rankings remain side-by-side.

### Phone (under 480px)
- **KPI/stat cards:** 2×2 grid, **26px values** for readability
- **Ranking tabs:** `11px font, 5px 10px padding` — all 3 fit on one line
- **Back links:** Button-style touch targets (14px, 10px padding, card background)
- **Charts:** Stack vertically
- **Export buttons:** Icon-only
- **Nav:** Logo + theme toggle + avatar + sign-out only

---

## Configuration Reference

### Authentication

| Variable | Required | Default | Description |
|---|---|---|---|
| `SECRET_KEY` | Yes | `dev-key-change-in-production` | Flask session signing key |
| `CLIENT_ID` | Yes | — | Azure App Registration client ID |
| `CLIENT_SECRET` | Yes | — | Azure App Registration secret |
| `TENANT_ID` | Yes | — | Azure AD tenant ID |
| `REDIRECT_URI_OVERRIDE` | No | (dynamic) | Hardcode full redirect URI |

### Security Groups

| Variable | Internal Role |
|---|---|
| `GROUP_ADMIN` | `Admin` |
| `GROUP_SALES_DASHBOARD_VIEW` | `Sales.Dashboard.View` |
| `GROUP_SALES_BOOKINGS_VIEW` | `Sales.Bookings.View` |
| `GROUP_SALES_BOOKINGS_EXPORT` | `Sales.Bookings.Export` |
| `GROUP_SALES_OPENORDERS_VIEW` | `Sales.OpenOrders.View` |
| `GROUP_SALES_OPENORDERS_EXPORT` | `Sales.OpenOrders.Export` |

### Database

| Variable | Required | Default |
|---|---|---|
| `DB_SERVER` | Yes | — |
| `DB_UID` | Yes | — |
| `DB_PWD` | Yes | — |
| `DB_ORDERS` | No | `PRO05` |
| `DB_ORDERS_CA` | No | `PRO06` |

### Application

| Variable | Default | Description |
|---|---|---|
| `DATA_REFRESH_INTERVAL` | `600` | Bookings refresh (seconds) |
| `OPEN_ORDERS_REFRESH_INTERVAL` | `3600` | Open orders refresh (seconds) |
| `DASHBOARD_REFRESH_INTERVAL` | `3600` | Dashboard current month refresh (seconds) |

---

## Setup & Installation

```bash
git clone https://github.com/your-org/twg_portal.git
cd twg_portal
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

Prerequisites: Python 3.12+, ODBC Driver 18, Azure App Registration with groups claim, Entra Security Groups, outbound HTTPS to exchange rate APIs + cdnjs.cloudflare.com.

---

## Running the Application

```bash
# Development
python app.py

# Production
waitress-serve --host=0.0.0.0 --port=5000 app:create_app
```

---

## Deployment Notes

- Set `SECRET_KEY` to a strong random value in production
- Register redirect URIs in Azure App Registration
- Use reverse proxy with SSL termination
- Outbound HTTPS needed: `api.frankfurter.app`, `open.er-api.com`, `cdnjs.cloudflare.com`
- SQL Server ~28 queries/hour for bookings + open orders; dashboard queries on-demand only
- Copy `dashboard_data/` folder when migrating servers for instant historical data loading
- `dashboard_data/` is gitignored — manage separately from code deployments

---

## URL Reference

| Route | Method | Role | Description |
|---|---|---|---|
| `/login_page` | GET | — | Login page |
| `/login` | GET | — | OAuth flow |
| `/auth/redirect` | GET | — | OAuth callback |
| `/logout` | GET | — | Clear session |
| `/` | GET | any | Department hub |
| `/sales` | GET | `Sales.Base` | Report menu |
| `/sales/dashboard` | GET | `Sales.Dashboard.View` | Executive dashboard |
| `/sales/dashboard/refresh` | POST | `Sales.Dashboard.View` | Invalidate + reload |
| `/sales/bookings` | GET | `Sales.Bookings.View` | Daily bookings |
| `/sales/bookings/export` | GET | `Sales.Bookings.Export` | Excel US+CA |
| `/sales/bookings/export/us` | GET | `Sales.Bookings.Export` | Excel US |
| `/sales/bookings/export/ca` | GET | `Sales.Bookings.Export` | Excel CA |
| `/sales/open-orders` | GET | `Sales.OpenOrders.View` | Open orders |
| `/sales/open-orders/export` | GET | `Sales.OpenOrders.Export` | Excel US+CA |
| `/sales/open-orders/export/us` | GET | `Sales.OpenOrders.Export` | Excel US |
| `/sales/open-orders/export/ca` | GET | `Sales.OpenOrders.Export` | Excel CA |
| `/admin/dashboard-data` | GET | `Admin` | Data management |
| `/admin/dashboard-data/download` | POST | `Admin` | Download region |
| `/admin/dashboard-data/download-both` | POST | `Admin` | Download US+CA |
| `/admin/dashboard-data/delete` | POST | `Admin` | Delete frozen file |

---

## Troubleshooting

| Issue | Cause | Fix |
|---|---|---|
| AADSTS50011 — Redirect URI mismatch | http:// sent, https:// registered | Set `REDIRECT_URI_OVERRIDE` in .env |
| AADSTS50148 — PKCE mismatch | Wrong MSAL method | Use `acquire_token_by_auth_code_flow()` |
| 403 Forbidden | Missing group membership | Add user to Entra group, re-login |
| No report cards visible | GROUP_* env vars not set | Check .env Object IDs |
| Empty dashboard | SQL unreachable | Check connectivity + console logs |
| Exchange rate 0.7200 | APIs blocked | Allow outbound to frankfurter + er-api |
| Charts not loading | CDN blocked | Allow outbound to cdnjs.cloudflare.com |
| Theme flash | Missing head script | Ensure synchronous localStorage read |
| Dashboard slow first load | No frozen file, fetching from SQL | Use admin page to download the year |
| Dashboard shows no data for past year | Frozen file missing + SQL unreachable | Download via admin page |

---

## Roadmap

| Module | Status | Description |
|---|---|---|
| **Sales** | | |
| Executive Dashboard | ✅ Live | Year selector, Chart.js (monthly/territory/product line/salesman), top 50 customers, frozen data files, admin data management page |
| Daily Bookings | ✅ Live | Territory/Salesman/Customer ranking tabs with podium, auto-refresh for TV, CAD→USD, Excel export (role-gated) |
| Open Sales Orders | ✅ Live | Territory + salesman side-by-side, released tracking, hourly refresh, CAD→USD, Excel export (role-gated) |
| Shipments | 🔜 Planned | Daily shipments by warehouse |
| Territory Perf | 🔜 Planned | Monthly trends with period comparison |
| **Admin** | | |
| Dashboard Data | ✅ Live | Download/delete frozen historical data files, status view, portable across servers |
| **Warehouse** | 🔜 Planned | Inventory levels, fulfillment tracking |
| **Accounting** | 🔜 Planned | Invoices, payments, financial reporting |
| **HR** | 🔜 Planned | Employee directory, attendance |

---

## License

Internal use only — The Wheel Group.