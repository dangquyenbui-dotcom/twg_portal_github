# TWG Portal

A secure, enterprise-grade internal portal for **The Wheel Group**, built with Flask and integrated with Microsoft Entra ID (SSO) for authentication and Microsoft SQL Server for real-time ERP data.

The portal serves as a centralized dashboard hub for multiple departments — starting with **Sales** — providing live KPIs, territory/salesman/customer ranking tabs with podium displays, an interactive executive dashboard with Chart.js visualizations and yearly data from dual SQL tables (sotran + soytrn), a bookings summary report with MTD/QTD/YTD horizons and year-over-year comparison indicators, frozen offline data files for instant historical loading at both yearly and monthly granularity, an admin page for data management, real-time CAD→USD currency conversion, formatted Excel data exports, dark/light theme switching with OLED support, and auto-refreshing displays optimized for desktop monitors, tablets, iPhones/iPads, and unattended TV/kiosk screens.

---

## Table of Contents

- [Architecture Overview](#architecture-overview)
- [Tech Stack](#tech-stack)
- [Project Structure](#project-structure)
- [Authentication Flow](#authentication-flow)
- [Role-Based Access Control (RBAC)](#role-based-access-control-rbac)
- [Admin Navigation](#admin-navigation)
- [Data Architecture](#data-architecture)
- [Dark / Light Mode](#dark--light-mode)
- [Sales Module](#sales-module)
  - [Department Hub](#department-hub-)
  - [Sales Report Menu](#sales-report-menu-sales)
  - [Daily Bookings Dashboard](#daily-bookings-dashboard)
  - [Ranking Tabs (Territory / Salesman / Customer)](#ranking-tabs-territory--salesman--customer)
  - [Open Sales Orders Dashboard](#open-sales-orders-dashboard)
  - [Bookings Summary (MTD / QTD / YTD)](#bookings-summary-mtd--qtd--ytd)
  - [Executive Dashboard](#executive-dashboard)
- [Bookings Summary — Data Layer](#bookings-summary--data-layer)
  - [Monthly Frozen File Strategy](#monthly-frozen-file-strategy)
  - [Prior Year Data (YoY Comparison)](#prior-year-data-yoy-comparison)
  - [Year-over-Year Comparison Logic](#year-over-year-comparison-logic)
  - [Dashboard Cache Sharing](#dashboard-cache-sharing)
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

The application follows a **Decoupled Caching Architecture** with a **multi-tier data resolution** strategy using both yearly and monthly frozen files to ensure instant page loads without overloading the ERP SQL Server.

```
┌─────────────┐      ┌──────────────┐      ┌─────────────────────────────────────┐
│   Browser    │◄────►│  Flask App   │◄────►│  Data Resolution (multi-tier)       │
│  (User/TV)   │      │  (Routes)    │      │                                     │
└─────────────┘      └──────────────┘      │  1. Frozen files on disk             │
                                            │     dashboard_data/*.json.gz (yearly)│
                                            │     summary_data/*.json.gz (monthly) │
                                            │  2. In-memory cache                  │
                                            │     (Flask-Caching / FileSystemCache)│
                                            │  3. SQL Server (fallback)            │
                                            │     soytrn + sotran                  │
                                            └──────────┬──────────────────────────┘
                                                       │
                                    ┌──────────────────┴──────────────────────────┐
                                    │              APScheduler                     │
                                    │   ┌──────────────┐ ┌────────────────┐       │
                                    │   │ Bookings     │ │ Open Orders    │       │
                                    │   │ 10 min       │ │ 60 min         │       │
                                    │   └──────────────┘ └────────────────┘       │
                                    │   ┌──────────────┐ ┌────────────────┐       │
                                    │   │ Bookings     │ │ Dashboard Curr │       │
                                    │   │ Summary      │ │ Month (60 min) │       │
                                    │   │ 30 min       │ └────────────────┘       │
                                    │   └──────────────┘                          │
                                    └─────────────────────────────────────────────┘
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

1. **Background Workers (APScheduler)** — Four independent scheduled jobs:
   - **Bookings refresh** — Every **10 minutes**. Queries today's bookings from sotran for both US (PRO05) and Canada (PRO06), fetches the live CAD→USD exchange rate, and caches all results. Also runs once immediately on app startup.
   - **Open orders refresh** — Every **60 minutes**. Queries all currently open sales order lines from sotran.
   - **Bookings Summary refresh** — Every **30 minutes**. Reads monthly frozen files from disk for completed months, queries only the current month from sotran (2 SQL queries total: US + CA), assembles MTD/QTD/YTD horizons with year-over-year comparison from dashboard yearly files, and populates the Executive Dashboard cache as a side effect.
   - **Dashboard current month refresh** — Every **60 minutes**. Queries current month from sotran for the executive dashboard. (Also fed by the Bookings Summary refresh, so the dashboard is often already warm.)
2. **Frozen Data Files** — Two levels of offline storage:
   - **Yearly files** (`dashboard_data/*.json.gz`) — For the executive dashboard. Completed years of data are downloaded by an admin via the admin page. Loads in <1ms — zero SQL, zero network.
   - **Monthly files** (`summary_data/*.json.gz`) — For the bookings summary. Completed months in the current year are auto-frozen when a new month starts. No admin action needed. Also loads in <1ms.
3. **Cache Layer (Flask-Caching)** — Stores the latest data snapshots using `FileSystemCache`. Survives brief app restarts. Each cache entry includes a `last_updated` timestamp.
4. **Web App (Flask)** — Route handlers **never** query SQL directly for bookings/open orders — they read from cache. The executive dashboard reads from frozen files first, then cache, then SQL as a last resort. The bookings summary reads from monthly frozen files + live current month.
5. **Auto-Refresh (Client-Side)** — The daily bookings page auto-refreshes every 10 minutes via `<meta http-equiv="refresh" content="600">` with a visible countdown timer for TV/kiosk displays. Other reports do not auto-refresh.

---

## Tech Stack

| Layer            | Technology                                        |
|------------------|---------------------------------------------------|
| **Backend**      | Python 3.12+, Flask 3.0                           |
| **Auth (SSO)**   | Microsoft Entra ID (Azure AD), MSAL for Python    |
| **RBAC**         | Entra ID Security Groups, custom `@require_role` decorator, per-report View/Export permissions, role hierarchy |
| **Database**     | Microsoft SQL Server (US: PRO05, Canada: PRO06), pyodbc, dual tables: sotran (current month) + soytrn (historical, identical schema) |
| **Caching**      | Flask-Caching (FileSystemCache), persisted to `cache-data/` directory |
| **Frozen Data**  | Yearly: gzip-compressed JSON in `dashboard_data/` (admin-managed, portable). Monthly: gzip-compressed JSON in `summary_data/` (auto-managed, portable) |
| **Scheduler**    | Flask-APScheduler (background data refresh), 4 independent jobs |
| **Exchange Rate**| frankfurter.app (primary), open.er-api.com (fallback), 0.72 hardcoded fallback |
| **Frontend**     | Jinja2 templates, vanilla CSS/JS, CSS custom properties for theming |
| **Charts**       | Chart.js 4.4 (loaded from cdnjs.cloudflare.com CDN) — executive dashboard only |
| **Theme**        | Dark/Light mode via `data-theme` HTML attribute + `localStorage` persistence, OLED-black dark mode |
| **Fonts**        | DM Sans (UI), JetBrains Mono (numbers/code), loaded from Google Fonts |
| **Icons**        | Inline SVG (Heroicons style) — no external icon library dependency |
| **Excel Export** | openpyxl (formatted .xlsx generation with styled headers, alternating rows, money formatting) |
| **Production**   | Waitress (WSGI server)                            |
| **Environment**  | python-dotenv (.env file), fallback to `_env` for Windows filename quirks |
| **PWA**          | `manifest.json` + `apple-touch-icon.png` for iOS/Android home screen support |

---

## Project Structure

```
twg_portal/
│
├── app.py                    # Application factory, SSO routes (/login, /auth/redirect, /logout),
│                             #   HTTPS redirect URI builder (_build_redirect_uri),
│                             #   role resolver (_resolve_roles_from_groups),
│                             #   scheduler init (4 jobs), startup data refresh,
│                             #   PWA apple-touch-icon routes
│
├── config.py                 # All configuration: auth, DB, cache, scheduler intervals,
│                             #   GROUP_ROLE_MAP builder from env vars (8 security groups),
│                             #   connection string builder, config validation with clear errors,
│                             #   .env loader with _env fallback
│
├── extensions.py             # Shared Flask extensions (Cache, APScheduler) — avoids circular imports
│
├── requirements.txt          # Python dependencies (pinned versions)
├── .env                      # Environment variables (secrets — never committed)
├── .gitignore                # Git exclusions (includes dashboard_data/, summary_data/, cache-data/)
├── .gitattributes            # LF normalization
├── README.md                 # This file
│
├── auth/
│   ├── __init__.py
│   ├── entra_auth.py         # MSAL helper: _build_msal_app() creates ConfidentialClientApplication,
│   │                         #   get_token_from_code() exchanges auth code via
│   │                         #   acquire_token_by_auth_code_flow (PKCE-safe, prevents AADSTS50148)
│   │
│   └── decorators.py         # @require_role(role_name) decorator for route protection,
│                              #   ROLE_HIERARCHY dict (Sales.Base implied by any Sales.*.View),
│                              #   _user_has_role() with Admin bypass + hierarchy check,
│                              #   user_has_role() Jinja2 template helper
│
├── routes/
│   ├── __init__.py
│   ├── main.py               # Home page (/) — department hub with role-aware card visibility,
│   │                         #   login page (/login_page) with session check redirect
│   │
│   ├── sales.py              # Sales blueprint (/sales/*):
│   │                         #   - Sales home (/sales) — report menu cards
│   │                         #   - Bookings (/sales/bookings) — daily bookings dashboard
│   │                         #   - Bookings export (3 routes: all, US, CA)
│   │                         #   - Bookings Summary (/sales/bookings-summary) — MTD/QTD/YTD
│   │                         #   - Bookings Summary export (3 routes per horizon: all, US, CA)
│   │                         #   - Open orders (/sales/open-orders) — open orders dashboard
│   │                         #   - Open orders export (3 routes: all, US, CA)
│   │                         #   - Executive dashboard (/sales/dashboard?year=) — Chart.js
│   │                         #   - Dashboard refresh (/sales/dashboard/refresh) — AJAX cache invalidation
│   │                         #   - Dashboard export (3 routes: all, US, CA) — from frozen files
│   │                         #   - _build_region_data() helper for CAD→USD conversion
│   │                         #   - Column definitions: BOOKINGS_EXPORT_COLUMNS (26 columns),
│   │                         #     OPEN_ORDERS_EXPORT_COLUMNS (26 columns),
│   │                         #     BOOKINGS_SUMMARY_EXPORT_COLUMNS (same 26 as bookings)
│   │
│   └── admin.py              # Admin blueprint (/admin/*):
│                              #   - Dashboard data page (/admin/dashboard-data) — year cards with status
│                              #   - Download single region (POST /admin/dashboard-data/download) — AJAX
│                              #   - Download both US+CA (POST /admin/dashboard-data/download-both) — AJAX
│                              #   - Delete frozen file (POST /admin/dashboard-data/delete) — AJAX
│                              #   All routes require Admin role
│
├── services/
│   ├── __init__.py
│   │
│   ├── constants.py          # Shared constants: TERRITORY_MAP_US (17 mappings),
│   │                         #   TERRITORY_MAP_CA (3 mappings), BOOKINGS_EXCLUDED_CUSTOMERS (7),
│   │                         #   map_territory(), resolve_territory_code()
│   │
│   ├── db_connection.py      # pyodbc connection factory with 30s timeout
│   │
│   ├── bookings_service.py   # Daily bookings data layer: snapshot + raw export queries,
│   │                         #   Python aggregation into 3 rankings (territory, salesman, customer)
│   │
│   ├── open_orders_service.py# Open orders data layer: snapshot + raw export queries,
│   │                         #   territory + salesman rankings with released amount tracking
│   │
│   ├── bookings_summary_service.py
│   │                         # Bookings Summary data layer (MTD / QTD / YTD):
│   │                         #   - Monthly frozen file I/O: save/load/delete gzip JSON (summary_data/)
│   │                         #   - Auto-freeze: detects and freezes completed months on startup/refresh
│   │                         #   - Prior year YoY: reads from dashboard yearly files (dashboard_data/)
│   │                         #   - _aggregate_rows() — simple format for rankings
│   │                         #   - _aggregate_rows_dashboard_format() — dashboard format with
│   │                         #     monthly_totals, by_territory, by_salesman, by_product_line, by_customer
│   │                         #   - _extract_prior_year_summary() — extracts month range from yearly file
│   │                         #   - _merge_regions() — combines US + CA with CAD→USD conversion
│   │                         #   - _compute_yoy() — year-over-year % change with direction indicators
│   │                         #   - refresh_bookings_summary() — main refresh: auto-freeze → read files
│   │                         #     → live current month → assemble horizons → populate dashboard cache
│   │                         #   - _populate_dashboard_cache() — shares data with executive dashboard
│   │                         #   - fetch_raw_export_data() — raw 26-column data for Excel export
│   │                         #   - get_bookings_summary_from_cache() — public API with cache-miss fallback
│   │
│   ├── dashboard_data_service.py
│   │                         # Executive dashboard data layer:
│   │                         #   - Yearly frozen file I/O (dashboard_data/*.json.gz)
│   │                         #   - get_frozen_status() — admin page status
│   │                         #   - 3-tier resolution: disk → cache → SQL
│   │                         #   - download_year_data() — admin download with raw rows + summary
│   │                         #   - get_dashboard_data() — public API, merges US+CA with CAD→USD
│   │                         #   - refresh_dashboard_current_month() — scheduler job
│   │
│   ├── dashboard_service.py  # Legacy dashboard aggregation service (retained for reference —
│   │                         #   replaced by dashboard_data_service.py). NOT used by active routes.
│   │
│   ├── data_worker.py        # Background cache refresh logic:
│   │                         #   - Exchange rate fetching with dual-API failover
│   │                         #   - refresh_bookings_cache() — daily bookings US+CA
│   │                         #   - refresh_open_orders_cache() — open orders US+CA
│   │                         #   - refresh_all_on_startup() — complete startup sequence:
│   │                         #     exchange rate → bookings → open orders → bookings summary
│   │                         #     (bookings summary also populates dashboard cache)
│   │                         #   - All cache keys centralized (14+ keys total)
│   │
│   └── excel_helper.py       # Shared Excel workbook builder: formatted headers, alternating rows,
│                              #   green money font, frozen header, auto-filter, column widths
│
├── static/
│   ├── logo/
│   │   ├── TWG.png                # Company logo (nav bar + login page)
│   │   ├── apple-touch-icon.png   # iOS home screen icon (180×180)
│   │   ├── icon-192x192.png       # Android/PWA icon
│   │   └── icon-512x512.png       # PWA splash icon
│   │
│   ├── css/
│   │   └── dashboard.css     # Executive dashboard styles (reference copy)
│   │
│   ├── js/
│   │   └── dashboard.js      # Executive dashboard Chart.js rendering:
│   │                         #   renderMonthlyChart(), renderTerritoryChart(),
│   │                         #   renderProductLineChart(), renderSalesmanChart(),
│   │                         #   updateCustomerTable(), handleRefresh(),
│   │                         #   theme-aware palettes, MutationObserver
│   │
│   └── manifest.json         # PWA manifest
│
├── templates/
│   ├── base.html             # Shared layout: sticky nav, theme toggle, admin gear, 60+ CSS variables
│   ├── login.html            # Standalone login page with animated gradient background
│   ├── index.html            # Department hub (Sales live, Warehouse/Accounting/HR coming soon)
│   │
│   ├── sales/
│   │   ├── index.html        # Sales report menu (Dashboard, Bookings Summary, Daily Bookings,
│   │   │                     #   Open Orders, + coming soon cards)
│   │   ├── bookings.html     # Daily Bookings: auto-refresh, countdown, podium, ranking tabs
│   │   ├── bookings_summary.html  # Bookings Summary: MTD/QTD/YTD tabs, YoY indicators,
│   │   │                     #   podium rankings, region split bar, export per horizon
│   │   ├── open_orders.html  # Open Orders: released tracking, side-by-side rankings
│   │   └── dashboard.html    # Executive Dashboard: Chart.js, year selector, KPIs, top 50 customers
│   │
│   └── admin/
│       └── dashboard_data.html  # Admin: download/delete yearly frozen files, status cards
│
├── dashboard_data/           # Yearly frozen gzip JSON files (gitignored, portable, admin-managed)
│   ├── us_2025.json.gz       # Full year pre-aggregated summary + raw rows
│   ├── ca_2025.json.gz
│   └── ...
│
├── summary_data/             # Monthly frozen gzip JSON files (gitignored, portable, auto-managed)
│   ├── us_2026_01.json.gz    # Jan 2026 US — summary + dashboard format (~2-5KB)
│   ├── us_2026_02.json.gz    # Feb 2026 US
│   ├── ca_2026_01.json.gz    # Jan 2026 CA
│   ├── ca_2026_02.json.gz    # Feb 2026 CA
│   └── ...                   # Auto-created when a new month starts
│
└── cache-data/               # Auto-generated FileSystemCache directory (gitignored)
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
  ├── Store flow object in session (contains PKCE verifier)
  └── Redirect user to Microsoft login page
        │
        ▼
  User authenticates with Microsoft
        │
        ▼
  GET /auth/redirect  (callback)
  ├── Retrieve flow object from session
  ├── Exchange auth code for tokens via acquire_token_by_auth_code_flow()
  │   (this method auto-extracts code + PKCE verifier from flow object)
  ├── Extract id_token_claims (name, email, oid, tid, groups)
  ├── _resolve_roles_from_groups():
  │   └── Map Security Group Object IDs → internal role names via GROUP_ROLE_MAP
  ├── Store in session: name, email, oid, tid, groups (raw IDs), roles (resolved names)
  ├── Log: user email, group count, resolved roles
  ├── Clear flow from session
  └── Redirect to home page (/)
        │
        ▼
  GET /logout
  ├── Clear Flask session
  └── Redirect to Microsoft logout endpoint with post_logout_redirect_uri → /login_page
```

**Key implementation details:**

- **PKCE handling:** `acquire_token_by_auth_code_flow()` is used instead of `acquire_token_by_authorization_code()` to properly handle PKCE verification. The flow object (stored in session during `/login`) contains the PKCE code verifier, and this method automatically extracts it. Using the wrong method causes AADSTS50148 errors.
- **HTTPS enforcement:** `_build_redirect_uri()` forces `https://` for any non-localhost host. This is critical when running behind a reverse proxy (IIS, nginx) with SSL termination, where Flask sees `http://` from `request.url_root` but Azure requires `https://` redirect URIs.
- **Redirect URI override:** An optional `REDIRECT_URI_OVERRIDE` environment variable allows hardcoding the full redirect URI for environments where dynamic building doesn't match Azure's registered URI.
- **Environment loading:** The `.env` file loader has a fallback from `.env` to `_env` for Windows environments where `.env` filenames can be problematic.
- **Config validation:** Runs at startup via `Config.validate()` and raises `SystemExit` with clear error messages if required values (`CLIENT_ID`, `CLIENT_SECRET`, `AUTHORITY`) are missing. Also builds the GROUP_ROLE_MAP and warns if no groups are configured.
- **Session contents:** User session stores `name`, `email`, `oid` (Azure object ID), `tid` (tenant ID), `groups` (raw Entra group Object IDs as list), and `roles` (resolved internal role names as list).
- **Login page:** Standalone template (does not extend `base.html`) with its own theme toggle support, animated background, and security badge.

**Required Azure App Registration settings:**

| Setting | Value |
|---|---|
| **Platform** | Web |
| **Redirect URIs** | `http://localhost:5000/auth/redirect`, `https://dev.thewheelgroup.info/auth/redirect`, `https://portal.thewheelgroup.info/auth/redirect` |
| **API Permissions** | `User.Read` (Microsoft Graph) |
| **Token configuration** | Add **groups** optional claim → Token Configuration → Add groups claim → select Security groups |
| **Client Secret** | Generate under Certificates & secrets |

---

## Role-Based Access Control (RBAC)

The portal enforces page-level and feature-level access control using **Microsoft Entra ID Security Groups** mapped to internal role names via `GROUP_ROLE_MAP` in `config.py`.

### How It Works

1. User logs in via Microsoft SSO
2. Azure returns the user's Security Group Object IDs in the token's `groups` claim
3. `_resolve_roles_from_groups()` in `app.py` maps each Object ID to an internal role name using `GROUP_ROLE_MAP`
4. Resolved role names are stored in the session as `user.roles` (list of strings)
5. Route decorators (`@require_role`) and template helpers (`user_has_role`) check against this list

### Per-Report View & Export Permissions

Every report has two separate permission levels:

| Permission Level | What It Does | How It's Enforced |
|---|---|---|
| **View** (`Sales.<Report>.View`) | Grants access to see the dashboard page | `@require_role` decorator on route, 403 if missing |
| **Export** (`Sales.<Report>.Export`) | Enables Excel download buttons on the page | `{% if can_export %}` in template (buttons removed from DOM entirely) |

Export roles do NOT grant view access on their own — they only enable download buttons on reports the user can already see via the corresponding View role.

### Security Groups → Roles

| Entra ID Security Group Name | Env Var | Internal Role | What It Grants |
|---|---|---|---|
| `TWG-Portal-Admin` | `GROUP_ADMIN` | `Admin` | Full access to everything: all views, all exports, admin pages. Bypasses all role checks. |
| `TWG-Portal-Sales-Dashboard-View` | `GROUP_SALES_DASHBOARD_VIEW` | `Sales.Dashboard.View` | View the executive dashboard |
| `TWG-Portal-Sales-BookingsSummary-View` | `GROUP_SALES_BOOKINGSSUMMARY_VIEW` | `Sales.BookingsSummary.View` | View MTD/QTD/YTD bookings summary |
| `TWG-Portal-Sales-BookingsSummary-Export` | `GROUP_SALES_BOOKINGSSUMMARY_EXPORT` | `Sales.BookingsSummary.Export` | Download bookings summary Excel files |
| `TWG-Portal-Sales-Bookings-View` | `GROUP_SALES_BOOKINGS_VIEW` | `Sales.Bookings.View` | View Daily Bookings dashboard |
| `TWG-Portal-Sales-Bookings-Export` | `GROUP_SALES_BOOKINGS_EXPORT` | `Sales.Bookings.Export` | Download Bookings Excel files |
| `TWG-Portal-Sales-OpenOrders-View` | `GROUP_SALES_OPENORDERS_VIEW` | `Sales.OpenOrders.View` | View Open Orders dashboard |
| `TWG-Portal-Sales-OpenOrders-Export` | `GROUP_SALES_OPENORDERS_EXPORT` | `Sales.OpenOrders.Export` | Download Open Orders Excel files |

### Role Hierarchy

```python
ROLE_HIERARCHY = {
    'Sales.Base': [
        'Sales.Bookings.View',
        'Sales.BookingsSummary.View',
        'Sales.OpenOrders.View',
        'Sales.Dashboard.View',
    ],
}
```

- **`Sales.Base`** — Never assigned directly. It is an internal role that is automatically implied by ANY `Sales.*.View` role. Grants access to the `/sales` hub page. This means any user who can view any Sales report can also see the Sales report menu.
- **`Admin`** — Bypasses all role checks entirely (first check in `_user_has_role()`). Admin users can view and export every report and access all admin pages.

### Role Check Order (`_user_has_role`)

```
1. Is user.roles empty? → False
2. Is 'Admin' in user.roles? → True (bypass everything)
3. Is required_role directly in user.roles? → True
4. Does any role in user.roles appear in ROLE_HIERARCHY[required_role]? → True
5. Otherwise → False
```

### Route Enforcement

| Route | Decorator | Access Notes |
|---|---|---|
| `/` | None (session check only) | Any authenticated user sees the hub |
| `/sales` | `@require_role('Sales.Base')` | Implied by any `Sales.*.View` |
| `/sales/dashboard` | `@require_role('Sales.Dashboard.View')` | Year-based executive dashboard |
| `/sales/dashboard/refresh` | `@require_role('Sales.Dashboard.View')` | AJAX: invalidate cache, reload |
| `/sales/dashboard/export*` | `@require_role('Sales.Dashboard.View')` | 3 Excel download routes (from frozen files) |
| `/sales/bookings-summary` | `@require_role('Sales.BookingsSummary.View')` | MTD/QTD/YTD with YoY comparison |
| `/sales/bookings-summary/export/*` | `@require_role('Sales.BookingsSummary.Export')` | 3 Excel routes per horizon |
| `/sales/bookings` | `@require_role('Sales.Bookings.View')` | Daily bookings with ranking tabs |
| `/sales/bookings/export/*` | `@require_role('Sales.Bookings.Export')` | 3 Excel download routes |
| `/sales/open-orders` | `@require_role('Sales.OpenOrders.View')` | Open orders dashboard |
| `/sales/open-orders/export/*` | `@require_role('Sales.OpenOrders.Export')` | 3 Excel download routes |
| `/admin/dashboard-data` | `@require_role('Admin')` | Data management page |
| `/admin/dashboard-data/download` | `@require_role('Admin')` | AJAX: download single region |
| `/admin/dashboard-data/download-both` | `@require_role('Admin')` | AJAX: download US + CA |
| `/admin/dashboard-data/delete` | `@require_role('Admin')` | AJAX: delete frozen file |

### Adding a New Report

1. Create 2 Entra Security Groups (View + Export) in Azure AD
2. Add 2 env vars in `.env` with the group Object IDs
3. Add 2 entries to `group_vars` dict in `config.py` → `_build_group_role_map()`
4. Add the new `.View` role to `ROLE_HIERARCHY['Sales.Base']` list in `decorators.py`
5. Build your route + template — no framework changes needed

---

## Admin Navigation

The admin page is accessible via a **gear icon** in the top navigation bar, positioned between the theme toggle and the user avatar. This icon is **only visible to users with the `Admin` role** — it is completely removed from the DOM for non-admin users via a Jinja2 conditional.

**Nav bar layout (left to right):** Logo + Portal text → Breadcrumbs → Theme toggle → **Admin gear** (Admin only) → Avatar + Name → Sign Out

The gear icon has an amber hover effect and rotates 90° on hover for visual feedback. It links directly to `/admin/dashboard-data`.

---

## Data Architecture

### Dual-Region Database

| Region | Database | Server | Description |
|--------|----------|--------|-------------|
| US     | PRO05    | twg-sql-01.thewheelgroup.com | US orders and sales data |
| Canada | PRO06    | twg-sql-01.thewheelgroup.com | Canadian orders and sales data |

Both databases share the same SQL Server instance and have identical table structures.

### Dual-Table Strategy (sotran + soytrn)

| Table | Contains | When Data Moves | Used By |
|-------|----------|-----------------|---------|
| `sotran` | Current month line items (live transactional data) | Data stays here for the current month | Daily Bookings, Open Orders, Bookings Summary (current month), Dashboard (current month) |
| `soytrn` | Historical line items (completed months, identical schema to sotran) | ERP moves data from sotran → soytrn when a month closes | Bookings Summary (auto-freeze completed months), Dashboard (past months/years) |

Both tables have identical field structures. Key columns used across the portal:

| Column | Table | Description |
|--------|-------|-------------|
| `sono` | sotran/soytrn | Sales order number |
| `tranlineno` | sotran/soytrn | Line number within the order |
| `origqtyord` | sotran/soytrn | Original quantity ordered |
| `qtyord` | sotran | Remaining open quantity (after partial shipments) |
| `qtyshp` | sotran/soytrn | Quantity shipped |
| `price` | sotran/soytrn | Unit price |
| `disc` | sotran/soytrn | Discount percentage (0-100) |
| `ordate` | sotran/soytrn | Order date |
| `custno` | sotran/soytrn | Customer number |
| `salesmn` | sotran/soytrn | Salesman code |
| `sostat` | sotran/soytrn | Line status (V=voided, X=cancelled, C=closed) |
| `sotype` | sotran/soytrn | Order type (B=blanket, R=return) |
| `currhist` | sotran/soytrn | Currency/history flag (X=excluded) |
| `item` | sotran/soytrn | Item number (joins to icitem) |
| `terr` | sotran/soytrn/somast/arcust | Territory code (multiple sources, resolved by logic) |
| `release` | somast | Release flag (Y=released) |
| `plinid` | icitem | Product line ID (joined) |
| `company` | arcust | Customer company name (joined) |

### SQL Server Connection

```
Driver:   ODBC Driver 18 for SQL Server
Server:   twg-sql-01.thewheelgroup.com
Database: PRO05 (US) / PRO06 (Canada)
Auth:     SQL Server authentication (UID/PWD from .env)
Options:  TrustServerCertificate=yes, Timeout=30s
Locking:  All queries use WITH (NOLOCK) to avoid blocking ERP transactions
```

### Query Strategy

**Lean SQL, heavy Python:** SQL queries do simple `SELECT` with filters, `JOIN`s, and `NOLOCK`. There are no `GROUP BY`, `SUM`, or aggregation functions in SQL — all aggregation happens in Python on the web server. This keeps SQL Server load minimal and leverages the app server's CPU instead.

| Report | Typical Row Count | Aggregation Time |
|--------|------------------|-----------------|
| Daily Bookings (US+CA) | ~5K rows | Instant (<100ms) |
| Open Orders (US+CA) | ~5-10K rows | Instant (<100ms) |
| Bookings Summary current month | ~5K rows | Instant (<100ms) |
| Dashboard (full year US) | ~400K+ rows | ~15-20 seconds |
| Dashboard (full year CA) | ~90K rows | ~5-10 seconds |

After aggregation, only a tiny summary dict (~5KB) is cached. Raw rows are discarded and garbage collected.

### Territory Mapping

Territory codes are mapped to display names in `services/constants.py`:

**US Territories (17 mappings):**

| Code | Display Name | Code | Display Name |
|------|-------------|------|-------------|
| `000` | LA | `211` | Dallas |
| `001` | LA | `218` | San Antonio |
| `010` | China | `221` | Kansas City |
| `114` | Seattle | `302` | Nashville |
| `126` | Denver | `305` | Levittown,PA |
| `204` | Columbus | `307` | Charlotte |
| `206` | Jacksonville | `312` | Atlanta |
| `210` | Houston | `324` | Indianapolis |
| `900` | Central Billing | (others) | Others |

**Canada Territories (3 mappings):**

| Code | Display Name |
|------|-------------|
| `501` | Vancouver |
| `502` | Toronto |
| `503` | Montreal |
| (others) | Others |

**Territory resolution logic:** If the customer's territory (`arcust.terr`) is `900` (Central Billing), use it. Otherwise, use the sales order master territory (`somast.terr`). This is implemented in SQL via `CASE WHEN cu.terr = '900' THEN cu.terr ELSE sm.terr END` and in Python via `resolve_territory_code()`.

### Excluded Data

**Excluded Customers** (applied to all reports): `W1VAN`, `W1TOR`, `W1MON`, `MISC`, `TWGMARKET`, `EMP-US`, `TEST123`

**Excluded Product Lines:** `TAX` — filtered in Python.

**Bookings Filters (SQL WHERE):** `currhist <> 'X'`, `sostat NOT IN ('V', 'X')`, `sotype NOT IN ('B', 'R')`, `ordate = CAST(GETDATE() AS DATE)` (today only)

**Open Orders Filters (SQL WHERE):** `tr.qtyord > 0`, `tr.sostat NOT IN ('C', 'V', 'X')`, `sm.sostat <> 'C'`, `tr.sotype NOT IN ('B', 'R')` — NO date filter, NO currhist filter

**Dashboard / Bookings Summary Filters (SQL WHERE):** Same as bookings but without the date filter (uses year/month range)

### Scheduler Strategy (Four Independent Jobs)

| Job ID | Function | Interval | What It Refreshes | Cache TTL | Run on Startup |
|---|---|---|---|---|---|
| `bookings_refresh` | `refresh_bookings_and_rate()` | 10 min | Bookings snapshots + raw (US + CA), CAD→USD exchange rate | 900s (15 min) | Yes |
| `open_orders_refresh` | `refresh_open_orders_scheduled()` | 60 min | Open orders snapshots + raw (US + CA) | 3900s (65 min) | Yes |
| `bookings_summary_refresh` | `refresh_bookings_summary_scheduled()` | 30 min | MTD/QTD/YTD from frozen files + live current month, YoY from dashboard files, + dashboard cache for current year | 2100s (35 min) | Yes |
| `dashboard_current_refresh` | `refresh_dashboard_current_month()` | 60 min | Dashboard current month only (sotran, US + CA) | 3900s (65 min) | No* |

*Dashboard current month is also populated by the Bookings Summary refresh (as a side effect of the YTD assembly), so it's usually already warm when the dedicated 60-min job runs.

### Caching Strategy

**Cache backend:** `FileSystemCache` persisted to `cache-data/` directory. Survives brief app restarts.

**Bookings + Open Orders cache keys (defined in `data_worker.py`):**

| Key | Type | Description |
|---|---|---|
| `bookings_snapshot_us` | `dict` | US bookings summary + territory/salesman/customer rankings |
| `bookings_snapshot_ca` | `dict` | Canada bookings summary + rankings |
| `bookings_raw_us` | `list[dict]` | US raw line-item data for Excel export |
| `bookings_raw_ca` | `list[dict]` | Canada raw line-item data for Excel export |
| `bookings_last_updated` | `datetime` | Last bookings refresh timestamp |
| `open_orders_snapshot_us` | `dict` | US open orders summary + territory/salesman rankings |
| `open_orders_snapshot_ca` | `dict` | Canada open orders summary + rankings |
| `open_orders_raw_us` | `list[dict]` | US raw line-item data for Excel export |
| `open_orders_raw_ca` | `list[dict]` | Canada raw line-item data for Excel export |
| `open_orders_last_updated` | `datetime` | Last open orders refresh timestamp |
| `cad_to_usd_rate` | `float` | Latest CAD → USD exchange rate |

**Bookings Summary cache keys (defined in `bookings_summary_service.py`):**

| Key | Type | TTL | Description |
|---|---|---|---|
| `bookings_summary_mtd` | `dict` | 35 min | MTD merged data with YoY |
| `bookings_summary_qtd` | `dict` | 35 min | QTD merged data with YoY |
| `bookings_summary_ytd` | `dict` | 35 min | YTD merged data with YoY |
| `bookings_summary_mtd_prior` | `dict` | 35 min | MTD prior year data |
| `bookings_summary_qtd_prior` | `dict` | 35 min | QTD prior year data |
| `bookings_summary_ytd_prior` | `dict` | 35 min | YTD prior year data |
| `bookings_summary_last_updated` | `datetime` | 35 min | Last summary refresh timestamp |

**Dashboard cache keys (defined in `dashboard_data_service.py`):**

| Key Pattern | Type | TTL | Description |
|---|---|---|---|
| `dash_hist_{region}_{year}` | `dict` | 24 hr | Historical year summary (also populated by Bookings Summary for current year) |
| `dash_current_{region}` | `dict` | 65 min | Current month summary (also populated by Bookings Summary) |
| `dashboard_last_updated` | `datetime` | 65 min | Last current-month refresh timestamp |

**Cache miss behavior:** If a route reads from cache and finds nothing, it triggers a synchronous fetch as a fallback. This ensures the first request after a cold start still returns data (with a brief delay).

---

## Dark / Light Mode

Full dark/light theme switching across all pages including the standalone login page.

### How It Works

| Component | Implementation |
|---|---|
| **Storage** | `localStorage` key `twg-theme` persists choice across sessions |
| **Attribute** | `data-theme` attribute on `<html>` element (`"light"` or `"dark"`) |
| **Toggle** | Sun/moon button in nav bar (base.html) or floating button (login.html) |
| **Flash prevention** | Synchronous inline `<script>` in `<head>` reads `localStorage` before any CSS renders |
| **CSS** | All colors via CSS custom properties (60+ variables) with complete light and dark sets |
| **Charts** | `MutationObserver` on `<html>` `data-theme` attribute triggers full chart re-render with theme-appropriate colors |

### Color Tokens

| Token | Light | Dark |
|---|---|---|
| `--bg-primary` | `#F5F6FA` | `#000000` (OLED black) |
| `--bg-card` | `#FFFFFF` | `#111111` |
| `--text-primary` | `#111827` | `#F1F3F8` |
| `--text-secondary` | `#4B5563` | `#8B95B0` |
| `--accent-blue` | `#2563EB` | `#3B82F6` |
| `--accent-green` | `#059669` | `#10B981` |
| `--accent-amber` | `#D97706` | `#F59E0B` |
| `--accent-red` | `#DC2626` | `#EF4444` |

Dark mode uses **true OLED black** (`#000000`) for the page background.

---

## Sales Module

### Department Hub (`/`)

Card-based grid showing available departments. Sales is live with a green "Live" badge; Warehouse, Accounting, HR show "Coming Soon" badges and are disabled. The Sales card is only visible to users with any `Sales.*.View` role.

### Sales Report Menu (`/sales`)

Cards for Dashboard, Bookings Summary, Daily Bookings, and Open Orders. Each card is conditionally rendered based on the user's View role. Each shows badges indicating available features: **"Live"** (green), **"New"** (red, for Bookings Summary), **"Export"** (blue, if user has Export role), **"View Only"** (gray, if no Export role). Coming Soon cards (Shipments, Territory Performance) shown to everyone as disabled.

### Daily Bookings Dashboard

**Route:** `/sales/bookings` — **Role:** `Sales.Bookings.View`
**Data source:** `sotran` where `ordate = today` — auto-refreshes every 10 min
**Purpose:** TV/kiosk display — today's orders only, glance-friendly, auto-refreshing

**Per region (US + Canada):** 4 KPI cards (Total Amount, Total Units, Sales Orders, Territories Active), 3 ranking tabs (Territory / Salesman / Customer) with instant CSS toggle, podium display (top 3 with gold/silver/bronze), remaining table below, export buttons gated by role.

**Canada-specific:** All monetary amounts show both CAD and USD equivalent. Exchange rate badge displayed in the region header.

**Auto-refresh:** `<meta http-equiv="refresh" content="600">` reloads every 10 minutes. JavaScript countdown timer shows time until next refresh.

### Ranking Tabs (Territory / Salesman / Customer)

Three tabs per region on the bookings page (and bookings summary). All three rankings are server-rendered in the HTML (no AJAX loading). Tab switching toggles CSS `active` class — instant with zero network requests. Tabs are scoped by `data-region` attribute to avoid cross-region conflicts.

### Open Sales Orders Dashboard

**Route:** `/sales/open-orders` — **Role:** `Sales.OpenOrders.View`
**Data source:** All open `sotran` lines (no date filter) — refreshes hourly

**Per region:** 4 KPI cards with Released sub-line (released dollar amount, percentage, blue checkmark icon), side-by-side territory + salesman ranking grids, Released column in each ranking.

**Open amount formula:** `qtyord × price × (1 - disc/100)` where `qtyord` is the remaining open quantity after shipments.

### Bookings Summary (MTD / QTD / YTD)

**Route:** `/sales/bookings-summary` — **Role:** `Sales.BookingsSummary.View`
**Data source:** Monthly frozen files (completed months) + `sotran` (current month) + dashboard yearly files (prior year YoY)
**Refreshes:** Every 30 minutes. Only queries current month from SQL (2 queries: US + CA).

**Page layout:**
- **Horizon tabs:** MTD (Month-to-Date), QTD (Quarter-to-Date), YTD (Year-to-Date) — instant CSS toggle, all data server-rendered
- **Date range label:** Shows current period dates + prior period label (e.g., "vs March 2025 (full month)")
- **4 KPI cards:** Total Amount (USD), Total Units, Sales Orders, Territories Active
- **YoY indicators:** Each KPI shows ▲ green (up) / ▼ red (down) / — gray (flat) with percentage change and prior year value
- **Region split bar:** US vs CA dollar amounts with CAD original
- **3 ranking tabs:** Territory / Salesman / Customer with podium (top 3) + remaining table
- **Export buttons:** Per horizon (MTD/QTD/YTD), gated by `Sales.BookingsSummary.Export` role

**Year-over-Year comparison:** Prior year data is read from the dashboard's yearly frozen files (`dashboard_data/us_2025.json.gz`). For MTD/QTD where the current period is partial (e.g., 10 days into March), the prior period label clearly indicates it's comparing against the full month/quarter: "vs March 2025 (full month)".

**Combined US + CA:** All Canadian amounts are converted to USD using the shared exchange rate before merging with US data. The region split shows the breakdown.

### Executive Dashboard

**Route:** `/sales/dashboard?year=2026` — **Role:** `Sales.Dashboard.View`
**Data source:** soytrn (historical months) + sotran (current month) — merged in Python
**Cache read:** Uses 3-tier resolution (frozen file → cache → SQL). Current year cache is also populated by the Bookings Summary refresh.

**Page layout:** Year selector dropdown, 5 KPI cards (Total Sales USD, Total Units, Sales Orders, Avg Order Value, Line Items), region split bar, Sales by Month hero chart (Chart.js bar), Sales by Territory horizontal bar (top 15), Sales by Product Line donut, Sales by Salesman horizontal bar (top 15), Top 50 Customers scrollable table, Refresh Data button.

---

## Bookings Summary — Data Layer

### Monthly Frozen File Strategy

Completed months in the **current year** are automatically frozen as tiny gzip-compressed JSON files in `summary_data/`. This eliminates the need to re-query SQL Server for data that rarely changes after a month closes.

**Auto-freeze behavior:** On every startup and every 30-minute refresh, the app checks for missing frozen files. If today is March 10, 2026, and `us_2026_02.json.gz` doesn't exist yet, it automatically queries `soytrn` for February 2026 US data, aggregates it, and saves the file. This only happens once per month per region — subsequent startups read from disk in <1ms.

**File format:** Each `.json.gz` file contains:
```json
{
  "meta": { "region": "US", "year": 2026, "month": 2, "frozen_at": "2026-03-01T08:00:00", "version": 1 },
  "summary": { /* simple format: summary + territory/salesman/customer rankings */ },
  "dashboard": { /* dashboard format: monthly_totals, by_territory, by_salesman, by_product_line, by_customer */ }
}
```

The dual format allows the same frozen file to serve both the Bookings Summary page (simple rankings) and the Executive Dashboard (chart-ready data with monthly breakdowns and product line splits).

**Startup performance:**

| Scenario | What happens | Time |
|---|---|---|
| First startup (no frozen files) | Auto-freezes completed months from SQL | ~15-20s (one-time) |
| Normal restart (files exist) | Reads from disk + queries current month only | ~3-5s |
| New month started | Auto-freezes previous month, rest from disk | ~5-8s |

**Portability:** Copy the `summary_data/` folder to a new server and all current-year completed months load instantly.

### Prior Year Data (YoY Comparison)

Prior year data for the year-over-year comparison is **not** stored in `summary_data/`. Instead, it is read from the **dashboard's existing yearly frozen files** in `dashboard_data/` (e.g., `us_2025.json.gz`).

These files are already downloaded by the admin via the Dashboard Data Management page and contain `monthly_totals` breakdowns. The bookings summary service extracts the exact months needed for each horizon:
- **MTD YoY:** Extracts just the matching month from the prior year file
- **QTD YoY:** Extracts the matching quarter's months
- **YTD YoY:** Extracts Jan through the matching month

If the prior year frozen file doesn't exist (admin hasn't downloaded it yet), the YoY indicators simply don't show — no SQL fallback, no errors.

### Year-over-Year Comparison Logic

The YoY comparison computes percentage change for three KPIs: Total Amount, Total Units, and Sales Orders.

**Direction indicators:**
- **▲ green (up):** Current period > prior period by more than 0.5%
- **▼ red (down):** Current period < prior period by more than 0.5%
- **— gray (flat):** Change is within ±0.5%

**Partial period labeling:** Since prior year data comes from dashboard files with monthly granularity (not daily), a partial current month (e.g., March 1-10) is compared against the full prior month (all of March 2025). The label clearly communicates this: "vs March 2025 (full month)" for MTD, "vs Q1 2025 (full months)" for QTD, "vs Jan–Mar 2025 (full months)" for YTD.

### Dashboard Cache Sharing

When the Bookings Summary refresh assembles YTD data, it reads frozen monthly files and fetches the current month from SQL. These intermediate per-region results are exactly what the Executive Dashboard needs. Rather than letting the dashboard duplicate those SQL queries, the Bookings Summary refresh writes the data directly into the dashboard's cache keys:

- Completed months → merged into `dash_hist_{region}_{year}` (24hr TTL)
- Current month → written to `dash_current_{region}` (65min TTL)
- Timestamp → written to `dashboard_last_updated`

This means the Executive Dashboard for the current year loads instantly from cache — zero SQL of its own. Past years still use the dashboard's yearly frozen files as before.

---

## Executive Dashboard — Data Layer

### Dual-Table Strategy (sotran + soytrn)

The ERP stores current month line items in `sotran` and moves them to `soytrn` when the month closes. Both tables have identical schemas. The dashboard queries both:

- **soytrn** — Historical months (Jan through previous month for current year, full year for past years)
- **sotran** — Current month only

For past years (e.g., viewing 2024 when it's currently 2026), only `soytrn` is queried (or the frozen file is read).

### Frozen Data Files

For completed years, the data never changes. An admin "downloads" the year once via the admin page. The app saves the pre-aggregated summary as a gzip-compressed JSON file:

```
dashboard_data/
├── us_2025.json.gz    (~1-2KB compressed — summary of ~392K raw rows)
├── ca_2025.json.gz    (~1KB compressed — summary of ~88K raw rows)
├── us_2024.json.gz
├── ca_2024.json.gz
└── ...
```

**File format (version 3):** Contains `meta`, `data` (pre-aggregated summary for dashboard rendering), and `raw_rows` (26-column line items for Excel export).

**Portability:** Copy the entire `dashboard_data/` folder when migrating servers.

### Data Resolution Priority

**For historical data (past months/years):**
1. Check frozen file on disk → Found? Return immediately (<1ms)
2. Check in-memory cache → Found? Return immediately
3. Fetch from SQL Server → Aggregate → Cache (24hr TTL) → Return

**For current month data:**
1. Check in-memory cache (populated by Bookings Summary refresh) → Found? Return
2. Fetch from SQL Server → Aggregate → Cache (65min TTL) → Return

### Admin Page — Dashboard Data Management

**Route:** `/admin/dashboard-data` — **Role:** `Admin` only

Shows a card for each year (current year back 7 years) with US and CA rows. Each row shows download status (file size, row count, frozen date) or "Not downloaded" with download/re-download/delete buttons. Current year row shows "Live from SQL" with no actions. "Download US + CA" button per year fetches both regions sequentially.

---

## Amount Calculation & Discount Handling

All monetary amounts across the portal use the same discount formula:

```
Amount = quantity × price × (1 - disc / 100)
```

| Report | Quantity Field | Formula |
|---|---|---|
| **Bookings / Bookings Summary / Dashboard** | `origqtyord` (original quantity ordered) | `origqtyord × price × (1 - disc/100)` |
| **Open Orders** | `qtyord` (remaining open quantity) | `qtyord × price × (1 - disc/100)` |

All aggregated totals are rounded up using `math.ceil()` to whole dollar amounts.

---

## Currency Conversion

All Canadian dollar amounts are converted to USD for unified reporting. The exchange rate is fetched every 10 minutes and shared by all reports.

| Priority | API | Extraction |
|---|---|---|
| Primary | `api.frankfurter.app` | `data["rates"]["USD"]` |
| Secondary | `open.er-api.com` | `data["rates"]["USD"]` |
| Hardcoded fallback | — | `0.72` if all APIs fail |

**Rate validation:** Must be between 0.50 and 1.00 to be accepted.

---

## Excel Exports

All exports read from cache or frozen files — **zero SQL queries at download time**.

### Available Exports

| Report | Routes | Role | Horizons |
|---|---|---|---|
| Daily Bookings | `/sales/bookings/export`, `/us`, `/ca` | `Sales.Bookings.Export` | Today |
| Bookings Summary | `/sales/bookings-summary/export/<horizon>`, `/us`, `/ca` | `Sales.BookingsSummary.Export` | MTD, QTD, YTD |
| Open Orders | `/sales/open-orders/export`, `/us`, `/ca` | `Sales.OpenOrders.Export` | All open |
| Dashboard Historical | `/sales/dashboard/export`, `/us`, `/ca` | `Sales.Dashboard.View` | Past years (from frozen files) |

### Export Formatting

Title row (merged, 13pt bold), metadata row (exported by user + timestamp), dark header row (#1F2937 white text), alternating row fills (#F9FAFB), green money font (#0A7A4F, `$#,##0.00`), frozen header, auto-filter, pre-set column widths.

**Filename pattern:** `{Report}_Raw_{Region}_{YYYYMMDD}.xlsx`

---

## Responsive Design

The portal is optimized for four display contexts:

| Breakpoint | Layout |
|---|---|
| **Desktop (1024px+)** | Full layout, 4/5-column KPI grids, side-by-side rankings, podium |
| **Tablet (768–1024px)** | Scaled fonts, tighter padding, rankings side-by-side |
| **Phone (481–768px)** | Brand text and breadcrumbs hidden, avatar only |
| **Phone Compact (<480px)** | 2×2 KPI grid (26px values), 11px ranking tabs, icon-only export buttons, stacked charts/rankings, compact podium |

---

## Configuration Reference

### Authentication

| Variable | Required | Default | Description |
|---|---|---|---|
| `SECRET_KEY` | Yes | `dev-key-change-in-production` | Flask session signing key |
| `CLIENT_ID` | Yes | — | Azure App Registration Application (client) ID |
| `CLIENT_SECRET` | Yes | — | Azure App Registration client secret |
| `TENANT_ID` | Yes | — | Azure AD tenant ID |
| `AUTHORITY` | No | `https://login.microsoftonline.com/{TENANT_ID}` | OAuth authority URL |
| `REDIRECT_PATH` | No | `/auth/redirect` | OAuth callback path |
| `SCOPE` | No | `User.Read` | OAuth scope |
| `REDIRECT_URI_OVERRIDE` | No | (dynamic) | Hardcode full redirect URI for proxy environments |

### Security Groups

| Variable | Internal Role |
|---|---|
| `GROUP_ADMIN` | `Admin` |
| `GROUP_SALES_DASHBOARD_VIEW` | `Sales.Dashboard.View` |
| `GROUP_SALES_BOOKINGSSUMMARY_VIEW` | `Sales.BookingsSummary.View` |
| `GROUP_SALES_BOOKINGSSUMMARY_EXPORT` | `Sales.BookingsSummary.Export` |
| `GROUP_SALES_BOOKINGS_VIEW` | `Sales.Bookings.View` |
| `GROUP_SALES_BOOKINGS_EXPORT` | `Sales.Bookings.Export` |
| `GROUP_SALES_OPENORDERS_VIEW` | `Sales.OpenOrders.View` |
| `GROUP_SALES_OPENORDERS_EXPORT` | `Sales.OpenOrders.Export` |

### Database

| Variable | Required | Default | Description |
|---|---|---|---|
| `DB_DRIVER` | No | `{ODBC Driver 18 for SQL Server}` | ODBC driver name |
| `DB_SERVER` | Yes | — | SQL Server hostname |
| `DB_UID` | Yes | — | SQL Server username |
| `DB_PWD` | Yes | — | SQL Server password |
| `DB_TRUST_CERT` | No | `yes` | Trust server certificate |
| `DB_ORDERS` | No | `PRO05` | US orders database |
| `DB_ORDERS_CA` | No | `PRO06` | Canada orders database |

### Application Timing

| Variable | Default | Description |
|---|---|---|
| `DATA_REFRESH_INTERVAL` | `600` (10 min) | Bookings + exchange rate refresh |
| `OPEN_ORDERS_REFRESH_INTERVAL` | `3600` (60 min) | Open orders refresh |
| `DASHBOARD_REFRESH_INTERVAL` | `3600` (60 min) | Dashboard current month refresh |
| `BOOKINGS_SUMMARY_REFRESH_INTERVAL` | `1800` (30 min) | Bookings Summary MTD/QTD/YTD refresh |

---

## Setup & Installation

### Prerequisites

- Python 3.12+
- ODBC Driver 18 for SQL Server
- Azure App Registration with groups claim configured
- Entra ID Security Groups created and populated
- Network access to SQL Server (twg-sql-01.thewheelgroup.com)
- Outbound HTTPS to exchange rate APIs + Google Fonts + CDN

### Installation

```bash
git clone https://github.com/your-org/twg_portal.git
cd twg_portal
python -m venv venv
source venv/bin/activate          # Linux/Mac
# venv\Scripts\activate           # Windows
pip install -r requirements.txt
```

### Environment Setup

Create a `.env` file in the project root (see [Environment Variables](#environment-variables) for all values).

---

## Running the Application

```bash
# Development
python app.py

# Production
waitress-serve --host=0.0.0.0 --port=5000 app:create_app
```

**Startup sequence:**

1. Flask app created via `create_app()` factory
2. Config validation (CLIENT_ID, CLIENT_SECRET, AUTHORITY)
3. GROUP_ROLE_MAP built from 8 environment variables
4. Flask-Caching initialized (FileSystemCache)
5. APScheduler started with 4 jobs registered
6. **Initial data refresh (synchronous):**
   - Exchange rate fetched
   - Daily bookings (US + CA snapshot + raw)
   - Open orders (US + CA snapshot + raw)
   - Bookings Summary: auto-freeze completed months → read monthly files → query current month → assemble MTD/QTD/YTD → populate dashboard cache
7. All caches warm — every page loads instantly from first request
8. Blueprints registered (main, sales, admin)
9. Server starts listening on port 5000

**Startup timing:**
- First startup (no frozen files): ~15-20 seconds (auto-freezes all completed months)
- Normal restart (files exist): ~3-5 seconds (reads from disk)
- New month (one month to freeze): ~5-8 seconds

---

## Deployment Notes

| Topic | Details |
|---|---|
| **SECRET_KEY** | Use `python -c "import secrets; print(secrets.token_hex(32))"` |
| **Redirect URIs** | Register all environment URIs in Azure App Registration |
| **Reverse proxy** | Use IIS/nginx with SSL termination. Set `REDIRECT_URI_OVERRIDE` if needed. |
| **Outbound HTTPS** | `api.frankfurter.app`, `open.er-api.com`, `cdnjs.cloudflare.com`, `fonts.googleapis.com`, `fonts.gstatic.com` |
| **SQL load** | Daily bookings: ~28 queries/hour. Open orders: ~8 queries/hour. Bookings summary: 4 queries/hour (current month only). Dashboard: on-demand for past years. |
| **Frozen data** | Copy both `dashboard_data/` and `summary_data/` when migrating servers. Both are gitignored. |
| **Cache directory** | `cache-data/` is auto-created. Delete to force full refresh. |
| **Windows** | If `.env` filename is problematic, rename to `_env`. |
| **Memory** | Dashboard full-year aggregation temporarily uses ~100-200MB. After aggregation, only ~5KB summary kept. |

---

## HTTPS & Redirect URI Handling

Behind a reverse proxy with SSL termination, Flask sees `http://` from `request.url_root` even though users access via `https://`. Azure Entra ID requires `https://` for all redirect URIs except `localhost`.

**`_build_redirect_uri()` handles this automatically:**

1. If `REDIRECT_URI_OVERRIDE` is set → use it verbatim (highest priority)
2. Otherwise, build from `request.url_root`
3. If the host is not `localhost`/`127.0.0.1` and URL starts with `http://` → force `https://`
4. Append `REDIRECT_PATH` (`/auth/redirect`)

---

## URL Reference

| Route | Method | Role | Description |
|---|---|---|---|
| `/login_page` | GET | — | Login page |
| `/login` | GET | — | Initiates OAuth flow |
| `/auth/redirect` | GET | — | OAuth callback |
| `/logout` | GET | — | Clear session + Microsoft logout |
| `/` | GET | any authenticated | Department hub |
| `/sales` | GET | `Sales.Base` | Sales report menu |
| `/sales/dashboard` | GET | `Sales.Dashboard.View` | Executive dashboard |
| `/sales/dashboard/refresh` | POST | `Sales.Dashboard.View` | AJAX: invalidate + reload |
| `/sales/dashboard/export` | GET | `Sales.Dashboard.View` | Excel: US + CA (frozen file) |
| `/sales/dashboard/export/us` | GET | `Sales.Dashboard.View` | Excel: US only |
| `/sales/dashboard/export/ca` | GET | `Sales.Dashboard.View` | Excel: CA only |
| `/sales/bookings-summary` | GET | `Sales.BookingsSummary.View` | MTD/QTD/YTD with YoY |
| `/sales/bookings-summary/export/<horizon>` | GET | `Sales.BookingsSummary.Export` | Excel: US + CA |
| `/sales/bookings-summary/export/<horizon>/us` | GET | `Sales.BookingsSummary.Export` | Excel: US only |
| `/sales/bookings-summary/export/<horizon>/ca` | GET | `Sales.BookingsSummary.Export` | Excel: CA only |
| `/sales/bookings` | GET | `Sales.Bookings.View` | Daily bookings |
| `/sales/bookings/export` | GET | `Sales.Bookings.Export` | Excel: US + CA |
| `/sales/bookings/export/us` | GET | `Sales.Bookings.Export` | Excel: US only |
| `/sales/bookings/export/ca` | GET | `Sales.Bookings.Export` | Excel: CA only |
| `/sales/open-orders` | GET | `Sales.OpenOrders.View` | Open orders |
| `/sales/open-orders/export` | GET | `Sales.OpenOrders.Export` | Excel: US + CA |
| `/sales/open-orders/export/us` | GET | `Sales.OpenOrders.Export` | Excel: US only |
| `/sales/open-orders/export/ca` | GET | `Sales.OpenOrders.Export` | Excel: CA only |
| `/admin/dashboard-data` | GET | `Admin` | Data management |
| `/admin/dashboard-data/download` | POST | `Admin` | AJAX: download region |
| `/admin/dashboard-data/download-both` | POST | `Admin` | AJAX: download US + CA |
| `/admin/dashboard-data/delete` | POST | `Admin` | AJAX: delete frozen file |
| `/apple-touch-icon*` | GET | — | PWA icon for Safari |

---

## Troubleshooting

| Issue | Cause | Fix |
|---|---|---|
| **AADSTS50011** — Redirect URI mismatch | `http://` sent but `https://` registered | Set `REDIRECT_URI_OVERRIDE` in `.env` |
| **AADSTS50148** — PKCE mismatch | Wrong MSAL method | Ensure `acquire_token_by_auth_code_flow()` is used |
| **403 Forbidden** | User not in required Security Group | Add to group in Azure AD, re-login |
| **No report cards on /sales** | GROUP_* env vars not set | Check `.env` Object IDs |
| **Admin gear icon missing** | User not in Admin group | Add to `TWG-Portal-Admin` group |
| **Empty bookings dashboard** | No orders today or SQL unreachable | Check logs and SQL connectivity |
| **Bookings Summary shows 0 orders** | Stale cache from bug fix | Delete `cache-data/` and restart |
| **Bookings Summary YoY not showing** | Prior year not downloaded | Download prior year via admin page |
| **Bookings Summary YoY shows large drop on MTD** | Comparing partial month vs full prior month | This is expected — label says "vs March 2025 (full month)" |
| **Dashboard slow first load** | No frozen file for that year | Download via admin page |
| **Dashboard current year slow** | Bookings Summary hasn't run yet | Wait for 30-min refresh or restart |
| **Auto-freeze taking long on first startup** | Freezing all completed months for first time | One-time cost (~15-20s), subsequent starts are 3-5s |
| **Exchange rate shows 0.7200** | Both APIs unreachable | Allow outbound HTTPS to exchange rate APIs |
| **Charts not loading** | CDN blocked | Allow `cdnjs.cloudflare.com` |
| **Theme flash on page load** | Missing synchronous theme script | Ensure inline `<script>` is in `<head>` |
| **Stale data after restart** | FileSystemCache has old data | Delete `cache-data/` directory |

---

## Roadmap

| Module | Report | Status | Description |
|---|---|---|---|
| **Sales** | Executive Dashboard | ✅ Live | Year selector, Chart.js, frozen yearly files, admin data management, 3-tier resolution, cache sharing |
| **Sales** | Bookings Summary | ✅ Live | MTD/QTD/YTD with YoY comparison, monthly frozen files, auto-freeze, dashboard cache sharing, prior year from yearly files |
| **Sales** | Daily Bookings | ✅ Live | Territory/Salesman/Customer ranking tabs with podium, auto-refresh for TV/kiosk, CAD→USD, Excel export |
| **Sales** | Open Sales Orders | ✅ Live | Territory + salesman rankings, released amount tracking, hourly refresh, CAD→USD, Excel export |
| **Sales** | Shipments | 🔜 Planned | Daily shipments by warehouse |
| **Sales** | Territory Performance | 🔜 Planned | Monthly trends with period comparison |
| **Admin** | Dashboard Data | ✅ Live | Download/delete yearly frozen files, status view, portable across servers |
| **Warehouse** | — | 🔜 Planned | Inventory levels, fulfillment tracking |
| **Accounting** | — | 🔜 Planned | Invoices, payments, financial reporting |
| **HR** | — | 🔜 Planned | Employee directory, attendance |

---

## Dependencies

**Python (from `requirements.txt`):**

| Package | Version | Purpose |
|---|---|---|
| Flask | 3.0.0 | Web framework |
| Waitress | 2.1.2 | Production WSGI server |
| pyodbc | (latest) | SQL Server connectivity |
| msal | 1.26.0 | Microsoft Authentication Library |
| requests | 2.31.0 | HTTP client (used by MSAL) |
| python-dotenv | 1.0.0 | `.env` file loading |
| Flask-Caching | 2.1.0 | FileSystemCache |
| Flask-APScheduler | 1.13.1 | Background job scheduling |
| openpyxl | 3.1.2 | Excel file generation |

**Client-side CDN:**

| Library | Version | CDN | Used On |
|---|---|---|---|
| Chart.js | 4.4.1 | cdnjs.cloudflare.com | Executive dashboard |
| DM Sans | — | fonts.googleapis.com | All pages (UI font) |
| JetBrains Mono | — | fonts.googleapis.com | All pages (numbers/code) |

---

## License

Internal use only — The Wheel Group.