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
- [Admin Navigation](#admin-navigation)
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
5. **Auto-Refresh (Client-Side)** — The bookings page auto-refreshes every 10 minutes via `<meta http-equiv="refresh" content="600">` with a visible countdown timer for TV/kiosk displays. Open orders and the executive dashboard do not auto-refresh.

---

## Tech Stack

| Layer            | Technology                                        |
|------------------|---------------------------------------------------|
| **Backend**      | Python 3.12+, Flask 3.0                           |
| **Auth (SSO)**   | Microsoft Entra ID (Azure AD), MSAL for Python    |
| **RBAC**         | Entra ID Security Groups, custom `@require_role` decorator, per-report View/Export permissions, role hierarchy |
| **Database**     | Microsoft SQL Server (US: PRO05, Canada: PRO06), pyodbc, dual tables: sotran (current month) + soytrn (historical, identical schema) |
| **Caching**      | Flask-Caching (FileSystemCache), persisted to `cache-data/` directory |
| **Frozen Data**  | gzip-compressed JSON files in `dashboard_data/` folder — portable offline storage for historical years |
| **Scheduler**    | Flask-APScheduler (background data refresh), 3 independent jobs |
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
│                             #   scheduler init (3 jobs), startup data refresh,
│                             #   PWA apple-touch-icon routes
│
├── config.py                 # All configuration: auth, DB, cache, scheduler intervals,
│                             #   GROUP_ROLE_MAP builder from env vars,
│                             #   connection string builder, config validation with clear errors,
│                             #   .env loader with _env fallback
│
├── extensions.py             # Shared Flask extensions (Cache, APScheduler) — avoids circular imports
│
├── requirements.txt          # Python dependencies (pinned versions)
├── .env                      # Environment variables (secrets — never committed)
├── .gitignore                # Git exclusions (includes dashboard_data/, cache-data/, venv/, __pycache__)
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
│   │                         #   - Open orders (/sales/open-orders) — open orders dashboard
│   │                         #   - Open orders export (3 routes: all, US, CA)
│   │                         #   - Executive dashboard (/sales/dashboard?year=) — Chart.js
│   │                         #   - Dashboard refresh (/sales/dashboard/refresh) — AJAX cache invalidation
│   │                         #   - _build_region_data() helper for CAD→USD conversion
│   │                         #   - BOOKINGS_EXPORT_COLUMNS (26 columns with ERP field names)
│   │                         #   - OPEN_ORDERS_EXPORT_COLUMNS (26 columns with ERP field names)
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
│   ├── constants.py          # Shared constants used by all query modules:
│   │                         #   - TERRITORY_MAP_US (17 territory codes → display names)
│   │                         #   - TERRITORY_MAP_CA (3 territory codes → display names)
│   │                         #   - BOOKINGS_EXCLUDED_CUSTOMERS (frozenset of 7 customer codes)
│   │                         #   - map_territory(code, region) — maps code to display name
│   │                         #   - resolve_territory_code(cu_terr, sm_terr) — 900=Central Billing logic
│   │
│   ├── db_connection.py      # pyodbc connection factory: get_connection(database) with 30s timeout,
│   │                         #   uses Config.get_connection_string()
│   │
│   ├── bookings_service.py   # Daily bookings data layer:
│   │                         #   - _build_bookings_query() — snapshot SELECT from sotran with JOINs
│   │                         #   - _aggregate_bookings() — Python aggregation into 3 rankings
│   │                         #     (territory, salesman, customer) with math.ceil() rounding
│   │                         #   - fetch_bookings_snapshot_us() / _ca() — snapshot for dashboard
│   │                         #   - _build_bookings_raw_query() — full 26-column SELECT for Excel
│   │                         #   - _process_bookings_raw_rows() — row dicts with territory mapping
│   │                         #   - fetch_bookings_raw_us() / _ca() — raw data for Excel export
│   │
│   ├── open_orders_service.py# Open orders data layer:
│   │                         #   - _build_open_orders_query() — SELECT with INNER JOIN somast
│   │                         #     (checks both line-level and order-level status)
│   │                         #   - _aggregate_open_orders() — territory + salesman rankings,
│   │                         #     tracks released amount (somast.release = 'Y') separately
│   │                         #   - fetch_open_orders_snapshot_us() / _ca()
│   │                         #   - _build_open_orders_raw_query() — 26-column SELECT for Excel
│   │                         #   - _process_open_orders_raw_rows() — row dicts with territory mapping
│   │                         #   - fetch_open_orders_raw_us() / _ca()
│   │
│   ├── dashboard_data_service.py
│   │                         # Executive dashboard data layer (most complex service):
│   │                         #   - Frozen file I/O: save/load/delete gzip JSON
│   │                         #   - get_frozen_status() — status of all year/region files
│   │                         #   - _build_dashboard_query() — queries soytrn or sotran
│   │                         #   - _aggregate_rows() — single-pass aggregation into monthly,
│   │                         #     territory, salesman, product line, customer breakdowns
│   │                         #   - _merge_summaries() — combines historical + current month data
│   │                         #   - 3-tier resolution: _get_historical_data() (disk→cache→SQL),
│   │                         #     _get_current_month_data() (cache→SQL)
│   │                         #   - get_dashboard_data() — public API, merges US+CA with CAD→USD
│   │                         #   - download_year_data() — admin download, SQL→aggregate→save
│   │                         #   - refresh_dashboard_current_month() — scheduler job
│   │                         #   - invalidate_historical_cache() — cache busting for refresh
│   │                         #   - get_available_years() — current year back 5 years
│   │
│   ├── dashboard_service.py  # Legacy dashboard aggregation service (retained for reference —
│   │                         #   replaced by dashboard_data_service.py). Processes raw cached
│   │                         #   bookings data into dashboard metrics with filtering support.
│   │                         #   NOT used by any active routes.
│   │
│   ├── data_worker.py        # Background cache refresh logic:
│   │                         #   - All cache keys centralized (14 keys total)
│   │                         #   - _fetch_cad_to_usd_rate() — dual-API with failover + validation
│   │                         #   - refresh_bookings_cache() — US+CA snapshot + raw in one pass
│   │                         #   - refresh_open_orders_cache() — US+CA snapshot + raw in one pass
│   │                         #   - get_bookings_from_cache() — read with sync fallback fetch
│   │                         #   - get_bookings_raw_from_cache() — read with sync fallback fetch
│   │                         #   - get_open_orders_from_cache() — read with sync fallback fetch
│   │                         #   - get_open_orders_raw_from_cache() — read with sync fallback fetch
│   │                         #   - refresh_bookings_and_rate() — scheduler: 10 min
│   │                         #   - refresh_open_orders_scheduled() — scheduler: 60 min
│   │                         #   - refresh_all_on_startup() — one-time init
│   │
│   └── excel_helper.py       # Shared Excel workbook builder:
│                              #   - build_export_workbook() — openpyxl workbook with:
│                              #     title row, exported-by metadata, dark header row,
│                              #     alternating row fills, green money font, frozen header,
│                              #     auto-filter, column widths, number formats
│                              #   - send_workbook() — BytesIO buffer → Flask send_file response
│
├── static/
│   ├── logo/
│   │   ├── TWG.png           # Company logo used in nav bar and login page
│   │   ├── apple-touch-icon.png   # iOS home screen icon (180×180)
│   │   ├── icon-192x192.png       # Android/PWA icon
│   │   └── icon-512x512.png       # PWA splash icon
│   │
│   ├── css/
│   │   └── dashboard.css     # Executive dashboard styles (reference copy — also inlined in template)
│   │
│   ├── js/
│   │   └── dashboard.js      # Executive dashboard Chart.js rendering:
│   │                         #   - renderMonthlyChart() — 12-month bar chart (hero)
│   │                         #   - renderTerritoryChart() — horizontal bar (top 15)
│   │                         #   - renderProductLineChart() — donut with legend
│   │                         #   - renderSalesmanChart() — horizontal bar (top 15)
│   │                         #   - updateCustomerTable() — dynamic top 50 table
│   │                         #   - handleRefresh() — AJAX cache invalidation + reload
│   │                         #   - Theme-aware color palettes (light/dark sets)
│   │                         #   - MutationObserver on data-theme for live theme switching
│   │                         #   - Year selector navigation (change → page reload)
│   │
│   └── manifest.json         # PWA manifest (display: browser, theme_color: #111827,
│                              #   icons: 192×192 + 512×512)
│
├── templates/
│   ├── base.html             # Shared layout for all authenticated pages:
│   │                         #   - Sticky top nav with logo, breadcrumbs, theme toggle,
│   │                         #     admin gear icon (Admin role only), avatar, sign out
│   │                         #   - Complete CSS variable system (60+ variables) for light/dark themes
│   │                         #   - Synchronous inline theme script (prevents flash of wrong theme)
│   │                         #   - Google Fonts preconnect (DM Sans + JetBrains Mono)
│   │                         #   - Fade-in animation utilities with stagger delays
│   │                         #   - Mobile responsive breakpoints (768px, 480px)
│   │                         #   - Template blocks: title, breadcrumb, extra_styles, content, scripts
│   │
│   ├── login.html            # Standalone login page (does NOT extend base.html):
│   │                         #   - Own complete theme support with floating toggle button
│   │                         #   - Animated gradient background (subtle drift animation)
│   │                         #   - Shimmer accent line on card
│   │                         #   - Microsoft SSO button with hover effects
│   │                         #   - Security badge ("Microsoft Entra ID · Enterprise SSO")
│   │                         #   - Error display for auth failures
│   │                         #   - Mobile responsive (420px breakpoint)
│   │
│   ├── index.html            # Department hub (home page):
│   │                         #   - Personalized greeting ("Welcome back, {first_name}")
│   │                         #   - Card grid: Sales (live), Warehouse/Accounting/HR (coming soon)
│   │                         #   - Sales card only visible with any Sales.*.View role
│   │                         #   - Coming soon cards shown to everyone as disabled
│   │                         #   - Color-coded icons per department (blue/amber/green/purple)
│   │                         #   - Hover effects with top accent line per department
│   │
│   ├── sales/
│   │   ├── index.html        # Sales report menu:
│   │                         #   - Cards for Dashboard, Bookings, Open Orders
│   │                         #   - Each card conditionally shown based on View role
│   │                         #   - Live/Export/View Only badges per user role
│   │                         #   - Coming Soon cards (Shipments, Territory Performance)
│   │                         #   - Back to Home link
│   │
│   │   ├── bookings.html     # Daily Bookings dashboard:
│   │                         #   - Auto-refresh meta tag (600s)
│   │                         #   - Countdown timer to next refresh (JS)
│   │                         #   - Last updated timestamp with green pulse dot
│   │                         #   - US section: 4 KPI cards, 3 ranking tabs (Territory/Salesman/Customer)
│   │                         #     each with podium (top 3 gold/silver/bronze) + remaining table
│   │                         #   - Canada section: same layout + exchange rate badge + USD equivalents
│   │                         #   - Tab switching: instant CSS class toggle in JS (no page reload)
│   │                         #   - All rankings server-rendered, scoped by data-region attribute
│   │                         #   - Export buttons (all/US/CA) gated by can_export
│   │                         #   - SVG flags for US and Canada
│   │                         #   - Phone: icon-only export buttons, 2×2 stat grid, compact podium
│   │
│   │   ├── open_orders.html  # Open Sales Orders dashboard:
│   │                         #   - US section: 4 KPI cards with Released sub-line
│   │                         #     (released amount, percentage, blue checkmark icon)
│   │                         #   - Side-by-side territory + salesman ranking grids
│   │                         #   - Canada section: same + exchange rate badge + USD columns
│   │                         #   - No auto-refresh (hourly data doesn't change fast)
│   │                         #   - Export buttons (all/US/CA) gated by can_export
│   │                         #   - Phone: stacked rankings, compact cards
│   │
│   │   └── dashboard.html    # Executive Dashboard:
│   │                         #   - Year selector dropdown (navigates on change, 5 years back)
│   │                         #   - 5 KPI cards: Total Sales, Units, Orders, Avg Order, Line Items
│   │                         #   - Region split bar (US vs CA proportion with dollar amounts)
│   │                         #   - Chart.js loaded from CDN (cdnjs.cloudflare.com)
│   │                         #   - window.__DASH_DATA__ JSON injection for chart rendering
│   │                         #   - Sales by Month hero chart (12 months, full width)
│   │                         #   - Sales by Territory horizontal bar (top 15)
│   │                         #   - Sales by Product Line donut chart with legend
│   │                         #   - Sales by Salesman horizontal bar (top 15, full width)
│   │                         #   - Top 50 Customers scrollable table
│   │                         #   - Refresh Data button (AJAX → invalidate cache → reload)
│   │                         #   - Phone: 2×2 KPI grid, stacked charts, compact table
│   │
│   └── admin/
│       └── dashboard_data.html
│                              # Admin: Dashboard Data Management:
│                              #   - Year cards (current year → 5 years back)
│                              #   - Each card has US and CA region rows
│                              #   - Current year: green dot "Live from SQL" (no actions)
│                              #   - Historical: Downloaded status (size + date) or "Not downloaded"
│                              #   - Per-region: Download/Re-download/Delete buttons
│                              #   - Per-year: "Download US + CA" button (fetches both)
│                              #   - AJAX operations with toast notifications
│                              #   - Info box explaining how frozen files work
│                              #   - Back to Home link
│                              #   - Phone: stacked layout, full-width action buttons
│
├── dashboard_data/           # Frozen gzip JSON files for historical years (gitignored, portable)
│   ├── us_2025.json.gz       # Example: US 2025 pre-aggregated summary (~1-2KB)
│   ├── ca_2025.json.gz       # Example: CA 2025 pre-aggregated summary
│   └── ...                   # One file per region per year
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
| `TWG-Portal-Sales-Bookings-View` | `GROUP_SALES_BOOKINGS_VIEW` | `Sales.Bookings.View` | View Daily Bookings dashboard |
| `TWG-Portal-Sales-Bookings-Export` | `GROUP_SALES_BOOKINGS_EXPORT` | `Sales.Bookings.Export` | Download Bookings Excel files |
| `TWG-Portal-Sales-OpenOrders-View` | `GROUP_SALES_OPENORDERS_VIEW` | `Sales.OpenOrders.View` | View Open Orders dashboard |
| `TWG-Portal-Sales-OpenOrders-Export` | `GROUP_SALES_OPENORDERS_EXPORT` | `Sales.OpenOrders.Export` | Download Open Orders Excel files |

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

The admin page is accessible via a **gear icon** in the top navigation bar, positioned between the theme toggle and the user avatar. This icon is **only visible to users with the `Admin` role** — it is completely removed from the DOM for non-admin users via a Jinja2 conditional:

```html
{% if user and user_has_role(user, 'Admin') %}
<a href="/admin/dashboard-data" class="btn-admin" title="Admin — Dashboard Data Management">
    <!-- gear SVG icon -->
</a>
{% endif %}
```

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
| `sotran` | Current month line items (live transactional data) | Data stays here for the current month | Daily Bookings, Open Orders, Dashboard (current month) |
| `soytrn` | Historical line items (completed months, identical schema to sotran) | ERP moves data from sotran → soytrn when a month closes | Dashboard (past months/years) |

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

**Excluded Customers** (applied to all reports):

`W1VAN`, `W1TOR`, `W1MON`, `MISC`, `TWGMARKET`, `EMP-US`, `TEST123`

These are internal/test accounts filtered out in Python after the SQL query returns rows.

**Excluded Product Lines:** `TAX` — filtered in Python.

**Bookings Filters (SQL WHERE):**
- `currhist <> 'X'` (not excluded)
- `sostat NOT IN ('V', 'X')` (not voided or cancelled)
- `sotype NOT IN ('B', 'R')` (not blanket orders or returns)
- `ordate = CAST(GETDATE() AS DATE)` (today only)

**Open Orders Filters (SQL WHERE):**
- `tr.qtyord > 0` (has remaining open quantity)
- `tr.sostat NOT IN ('C', 'V', 'X')` (not closed/voided/cancelled at line level)
- `sm.sostat <> 'C'` (order not fully closed at order level)
- `tr.sotype NOT IN ('B', 'R')` (not blanket/return)
- NO date filter (all open orders regardless of age)
- NO currhist filter (not relevant for open orders)

**Dashboard Filters (SQL WHERE):**
- Same as bookings but without the date filter (uses year range or full table)

### Scheduler Strategy (Three Independent Jobs)

| Job ID | Function | Interval | What It Refreshes | Cache TTL | Run on Startup |
|---|---|---|---|---|---|
| `bookings_refresh` | `refresh_bookings_and_rate()` | 10 min | Bookings snapshots + raw (US + CA), CAD→USD exchange rate | 900s (15 min) | Yes |
| `open_orders_refresh` | `refresh_open_orders_scheduled()` | 60 min | Open orders snapshots + raw (US + CA) | 3900s (65 min) | Yes |
| `dashboard_current_refresh` | `refresh_dashboard_current_month()` | 60 min | Dashboard current month only (sotran, US + CA) | 3900s (65 min) | No* |

*Dashboard historical data is NOT fetched on startup — it is loaded on demand when the first user visits the dashboard page, or from frozen files. This keeps startup fast.

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

**Dashboard cache keys (defined in `dashboard_data_service.py`):**

| Key Pattern | Type | TTL | Description |
|---|---|---|---|
| `dash_hist_{region}_{year}` | `dict` | 24 hr | Historical year summary (e.g., `dash_hist_us_2025`) |
| `dash_current_{region}` | `dict` | 65 min | Current month summary (e.g., `dash_current_us`) |
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

Dark mode uses **true OLED black** (`#000000`) for the page background, saving battery on OLED screens and providing maximum contrast.

---

## Sales Module

### Department Hub (`/`)

Card-based grid showing available departments. Sales is live with a green "Live" badge; Warehouse, Accounting, HR show "Coming Soon" badges and are disabled (opacity 0.5, no pointer events). The Sales card is only visible to users with any `Sales.*.View` role (checked via `user_has_role(user, 'Sales.Base')`).

### Sales Report Menu (`/sales`)

Cards for Dashboard, Bookings, and Open Orders. Each card is conditionally rendered based on the user's View role. Each shows badges indicating available features:

- **"Live"** — always shown (green badge)
- **"Export"** — shown if user has the Export role (blue badge)
- **"View Only"** — shown if user lacks the Export role (gray badge)

Coming Soon cards (Shipments, Territory Performance) shown to everyone as disabled.

### Daily Bookings Dashboard

**Route:** `/sales/bookings` — **Role:** `Sales.Bookings.View`
**Data source:** `sotran` where `ordate = today` — auto-refreshes every 10 min
**Cache read:** Route reads from `bookings_snapshot_us`, `bookings_snapshot_ca`, `bookings_last_updated`, `cad_to_usd_rate`

**Per region (US + Canada):**

| Component | Description |
|---|---|
| **4 KPI cards** | Total Amount, Total Units, Sales Orders, Territories Active |
| **3 ranking tabs** | Territory / Salesman / Customer (instant CSS toggle, no reload) |
| **Podium** | Top 3 with gold/silver/bronze cards, rank medals, hover lift effect |
| **Remaining table** | 4th place and below with sortable columns |
| **Export buttons** | All / US / CA (gated by `can_export`, hidden from DOM if no permission) |

**Canada-specific:** All monetary amounts show both CAD and USD equivalent. Exchange rate badge displayed in the region header. USD columns in ranking tables.

**Auto-refresh:** `<meta http-equiv="refresh" content="600">` reloads the page every 10 minutes. A JavaScript countdown timer shows time until next refresh (`10:00` → `0:00`).

### Ranking Tabs (Territory / Salesman / Customer)

Three tabs per region on the bookings page. All three rankings are server-rendered in the HTML (no AJAX loading). Tab switching toggles CSS `active` class on both the tab button and the corresponding panel — instant with zero network requests. Tabs are scoped by `data-region` attribute to avoid cross-region conflicts.

### Open Sales Orders Dashboard

**Route:** `/sales/open-orders` — **Role:** `Sales.OpenOrders.View`
**Data source:** All open `sotran` lines (no date filter) — refreshes hourly
**Cache read:** Route reads from `open_orders_snapshot_us`, `open_orders_snapshot_ca`, `open_orders_last_updated`, `cad_to_usd_rate`

**Per region (US + Canada):**

| Component | Description |
|---|---|
| **4 KPI cards** | Total Open Amount, Open Units, Open Orders, Open Lines |
| **Released sub-line** | Below the Open Amount card: released dollar amount, percentage, blue checkmark icon |
| **Side-by-side rankings** | Territory (left) + Salesman (right) in a 2-column grid |
| **Released column** | Each ranking row shows both Open $ and Released $ |
| **Export buttons** | All / US / CA (gated by `can_export`) |

**Open amount formula:** `qtyord × price × (1 - disc/100)` where `qtyord` is the remaining open quantity after shipments.

**Released tracking:** `somast.release = 'Y'` identifies released orders. Released amounts are tracked separately in both the summary KPI and per-territory/salesman rankings.

### Executive Dashboard

**Route:** `/sales/dashboard?year=2026` — **Role:** `Sales.Dashboard.View`
**Data source:** soytrn (historical months) + sotran (current month) — merged in Python
**Cache read:** Uses 3-tier resolution (frozen file → cache → SQL)

**Page layout (top to bottom):**

| # | Component | Chart Type | Width |
|---|---|---|---|
| 1 | **Year selector dropdown** | Select menu | — |
| 2 | **5 KPI cards** | Total Sales (USD), Total Units, Sales Orders, Avg Order Value, Line Items | 5-column grid |
| 3 | **Region split bar** | Stacked proportion bar with US vs CA dollar amounts | Full width |
| 4 | **Sales by Month** | Bar chart (12 months, empty months shown in gray) | Full width (hero) |
| 5 | **Sales by Territory** | Horizontal bar chart (top 15) | Half width |
| 6 | **Sales by Product Line** | Donut chart with right-aligned legend | Half width |
| 7 | **Sales by Salesman** | Horizontal bar chart (top 15) | Full width |
| 8 | **Top 50 Customers** | Scrollable table (rank, name, amount, units, orders) | Full width |
| 9 | **Refresh Data button** | Invalidates cache for selected year, re-fetches from SQL | Top right |

**Data flow:** `get_dashboard_data(year, cad_rate)` fetches US historical + US current month + CA historical + CA current month, merges US data (soytrn + sotran), merges CA data (soytrn + sotran), converts all CA amounts to USD, then merges US + CA into a single unified dataset.

---

## Executive Dashboard — Data Layer

### Dual-Table Strategy (sotran + soytrn)

The ERP stores current month line items in `sotran` and moves them to `soytrn` when the month closes. Both tables have identical schemas. The dashboard queries both:

- **soytrn** — Historical months (Jan through previous month for current year, full year for past years)
- **sotran** — Current month only (no date filter needed — the table IS the current month for all active data)

For past years (e.g., viewing 2024 when it's currently 2026), only `soytrn` is queried.

### Frozen Data Files

For completed years, the data never changes. Instead of hitting SQL Server every time, an admin "downloads" the year once via the admin page. The app saves the pre-aggregated summary as a gzip-compressed JSON file:

```
dashboard_data/
├── us_2025.json.gz    (~1-2KB compressed — summary of ~392K raw rows)
├── ca_2025.json.gz    (~1KB compressed — summary of ~88K raw rows)
├── us_2024.json.gz
├── ca_2024.json.gz
└── ...
```

**File format:**

```json
{
  "meta": {
    "region": "US",
    "year": 2025,
    "frozen_at": "2026-01-15T14:30:00",
    "version": 2
  },
  "data": {
    "summary": { "total_amount": 45000000, "total_units": 850000, "total_orders": 12000, "total_lines": 95000 },
    "monthly_totals": [ { "yr": 2025, "mo": 1, "amount": 3500000, "units": 70000, "orders": 1000 }, ... ],
    "by_territory": [ { "name": "LA", "amount": 12000000, "units": 200000, "orders": 3000, "rank": 1 }, ... ],
    "by_salesman": [ { "name": "JD", "amount": 8000000, "units": 150000, "orders": 2000, "rank": 1 }, ... ],
    "by_product_line": [ { "name": "WHEELS", "amount": 20000000, "units": 400000, "rank": 1 }, ... ],
    "by_customer": [ { "custno": "CUST001", "name": "Big Customer Inc", "amount": 5000000, "units": 100000, "orders": 500, "rank": 1 }, ... ]
  }
}
```

**Portability:** Copy the entire `dashboard_data/` folder to a new server and all historical years load instantly without any SQL queries. The folder is gitignored — manage it separately from code deployments.

### Data Resolution Priority

When the dashboard needs data for a year + region:

**For historical data (past months/years):**

```
1. Check frozen file on disk (dashboard_data/{region}_{year}.json.gz)
   → Found? Return immediately (<1ms). Done.

2. Check in-memory cache (dash_hist_{region}_{year})
   → Found? Return immediately. Done.

3. Fetch from SQL Server (soytrn SELECT with NOLOCK)
   → Aggregate in Python (single pass, ~15-20 seconds for full US year)
   → Cache the summary (24hr TTL)
   → Return. Raw rows garbage collected.
```

**For current month data:**

```
1. Check in-memory cache (dash_current_{region})
   → Found? Return. Done.

2. Fetch from SQL Server (sotran SELECT)
   → Aggregate, cache (65min TTL), return.
```

The scheduler refreshes the current month cache every 60 minutes. Historical data is only refreshed on demand (admin download or cache expiry).

### Admin Page — Dashboard Data Management

**Route:** `/admin/dashboard-data` — **Role:** `Admin` only
**Navigation:** Gear icon in the top nav bar (visible only to Admin users)

Shows a card for each year (current year back 5 years) with US and CA rows:

| Column | Content |
|---|---|
| **Year** | Year number with "Current Year — Live" (green badge) or "Historical" (blue badge) |
| **Region** | US or CA |
| **Status** | Green dot "Downloaded (1,847 bytes) · 2026-03-09 14:30" or gray dot "Not downloaded — will fetch from SQL on demand (~15-20s)" |
| **Actions** | Download / Re-download / Delete buttons (disabled for current year) |

**"Download US + CA" button** per year fetches both regions sequentially (~30-40 seconds total), aggregates in Python, saves to disk, and updates the cache.

**Current year row:** Shows green dot with "Live from SQL (sotran + soytrn) — cached every 60 min". No download/delete actions available since the data changes daily.

**AJAX endpoints:**

| Method | Endpoint | Payload | Response |
|---|---|---|---|
| POST | `/admin/dashboard-data/download` | `{"year": 2025, "region": "US"}` | `{"status": "ok", "result": {...}, "message": "..."}` |
| POST | `/admin/dashboard-data/download-both` | `{"year": 2025}` | `{"status": "ok", "results": [...], "message": "..."}` |
| POST | `/admin/dashboard-data/delete` | `{"year": 2025, "region": "US"}` | `{"status": "ok", "message": "..."}` |

Download-both returns HTTP 207 (Multi-Status) if one region succeeds and the other fails.

**Toast notifications:** Success (green) and error (red) toast messages appear at the top of the page after each operation, auto-dismiss after 8 seconds. Page reloads 1-1.5 seconds after successful operations to show updated status.

---

## Amount Calculation & Discount Handling

All monetary amounts across the portal use the same discount formula:

```
Amount = quantity × price × (1 - disc / 100)
```

| Report | Quantity Field | Formula |
|---|---|---|
| **Bookings** | `origqtyord` (original quantity ordered) | `origqtyord × price × (1 - disc/100)` |
| **Open Orders** | `qtyord` (remaining open quantity) | `qtyord × price × (1 - disc/100)` |
| **Dashboard** | `origqtyord` | `origqtyord × price × (1 - disc/100)` |

**Rounding:** All aggregated totals are rounded up using `math.ceil()` to whole dollar amounts. This applies to territory totals, salesman totals, customer totals, and grand totals.

**Discount field:** `disc` is stored as a percentage (0-100). A `disc` value of `15` means a 15% discount.

---

## Currency Conversion

All Canadian dollar amounts are converted to USD for unified reporting. The exchange rate is fetched by the bookings refresh job every 10 minutes and shared by all reports.

| Priority | API | URL | Extraction |
|---|---|---|---|
| Primary | Frankfurter | `https://api.frankfurter.app/latest?from=CAD&to=USD` | `data["rates"]["USD"]` |
| Secondary | Open Exchange Rates | `https://open.er-api.com/v6/latest/CAD` | `data["rates"]["USD"]` |
| Hardcoded fallback | — | — | `0.72` if all APIs fail |

**Rate validation:** The fetched rate must be between 0.50 and 1.00 to be accepted. Rates outside this range are rejected as invalid.

**Where conversions appear:**

- **Bookings:** Canada KPI card shows CAD amount + "≈ USD $X" below. Ranking tables include a "≈ USD" column.
- **Open Orders:** Same pattern as bookings. Released amounts also show USD equivalents.
- **Dashboard:** All Canadian amounts are converted to USD before merging with US data. The region split bar shows both US (native USD) and CA (CAD + USD equivalent).

---

## Excel Exports

All exports read from cache — **zero SQL queries at download time**. Exports are available for Bookings and Open Orders, each with 3 routes (combined US+CA, US only, CA only).

### Export Column Definitions

**Bookings Export (26 columns):**

Each column is defined as `(Header Label (ERP field), Dict Key, Column Width, Number Format)`:

`Sales Order (sono)`, `Line# (tranlineno)`, `Order Date (ordate)`, `Customer No (custno)`, `Customer Name (company)`, `Item (item)`, `Description (descrip)`, `Product Line (plinid)`, `Qty Ordered (origqtyord)`, `Qty Shipped (qtyshp)`, `Unit Price (price)`, `Discount % (disc)`, `Ext Amount (calculated)`, `Ext Price (extprice)`, `Line Status (sostat)`, `Order Type (sotype)`, `Territory (mapped)`, `Terr Code (resolved)`, `Tran Terr (tr.terr)`, `SO Mast Terr (sm.terr)`, `Cust Terr (cu.terr)`, `Salesman (salesmn)`, `Location (loctid)`, `Request Date (rqdate)`, `Ship Date (shipdate)`, `Ship Via (shipvia)`

**Open Orders Export (26 columns):**

Same structure but with open-orders-specific fields: `Orig Qty Ordered (origqtyord)`, `Open Qty (qtyord)`, `Open Amount (calculated)`, `Release (release)`.

### Excel Formatting

| Feature | Implementation |
|---|---|
| **Title row** | Merged cells, 13pt bold, report name + date |
| **Metadata row** | "Exported by {user} on {date}" in italic gray |
| **Header row** | Dark background (#1F2937), white bold text, centered, wrap text |
| **Alternating rows** | Every other row has light gray (#F9FAFB) fill |
| **Money columns** | Green font (#0A7A4F), `$#,##0.00` format |
| **Date columns** | `MM/DD/YYYY` format |
| **Discount column** | `0.000` format (3 decimal places) |
| **Quantity columns** | `#,##0` format with thousands separator |
| **Frozen header** | Header row frozen so it stays visible while scrolling |
| **Auto-filter** | Filter dropdowns on all header columns |
| **Column widths** | Pre-set per column (10-32 characters) |
| **Region column** | Optional, prepended when exporting combined US+CA |

### Export Routes

| Route | Role | Content |
|---|---|---|
| `/sales/bookings/export` | `Sales.Bookings.Export` | US + CA combined (Region column included) |
| `/sales/bookings/export/us` | `Sales.Bookings.Export` | US only |
| `/sales/bookings/export/ca` | `Sales.Bookings.Export` | Canada only |
| `/sales/open-orders/export` | `Sales.OpenOrders.Export` | US + CA combined (Region column included) |
| `/sales/open-orders/export/us` | `Sales.OpenOrders.Export` | US only |
| `/sales/open-orders/export/ca` | `Sales.OpenOrders.Export` | Canada only |

**Filename pattern:** `{Report}_Raw_{Region}_{YYYYMMDD}.xlsx` (e.g., `Bookings_Raw_US_CA_20260309.xlsx`)

---

## Responsive Design

The portal is optimized for four display contexts:

### Desktop (1024px+)
- Full layout, 4/5-column KPI grids, side-by-side rankings
- Podium display with gold/silver/bronze cards
- Breadcrumbs visible in nav
- Max-width 1400px page container with 32px padding
- All export button text visible

### Tablet (768–1024px)
- Scaled fonts (20px KPI values, down from 24px)
- Tighter padding on cards
- Rankings remain side-by-side
- Nav breadcrumbs still visible
- User name still visible in nav

### Phone — Standard (481–768px)
- Brand text and breadcrumbs hidden in nav
- User name hidden (avatar only)
- Smaller gaps between nav elements

### Phone — Compact (under 480px)
- **KPI cards:** 2×2 grid with **26px values** for readability
- **Ranking tabs:** `11px font, 5px 10px padding` — all 3 tabs fit on one line
- **Back links:** Button-style touch targets (14px text, 10px padding, card background with border)
- **Charts:** Stack vertically (single column)
- **Export buttons:** Icon-only (text hidden via `display: none` on `.btn-download-text`)
- **Rankings:** Stack vertically on open orders (territory above salesman)
- **Podium:** Compact (12px padding, 28px medals, 13px amounts)
- **Tables:** 12px font, 6px padding
- **Nav:** Logo + theme toggle + admin gear + avatar + sign-out only

---

## Configuration Reference

### Authentication

| Variable | Required | Default | Description |
|---|---|---|---|
| `SECRET_KEY` | Yes | `dev-key-change-in-production` | Flask session signing key. Must be strong random value in production. |
| `CLIENT_ID` | Yes | — | Azure App Registration Application (client) ID |
| `CLIENT_SECRET` | Yes | — | Azure App Registration client secret value |
| `TENANT_ID` | Yes | — | Azure AD tenant ID |
| `AUTHORITY` | No | `https://login.microsoftonline.com/{TENANT_ID}` | OAuth authority URL |
| `REDIRECT_PATH` | No | `/auth/redirect` | OAuth callback path |
| `SCOPE` | No | `User.Read` | OAuth scope |
| `REDIRECT_URI_OVERRIDE` | No | (dynamic) | Hardcode full redirect URI for proxy environments |

### Security Groups

| Variable | Internal Role | Description |
|---|---|---|
| `GROUP_ADMIN` | `Admin` | Full bypass — all views, all exports, admin pages |
| `GROUP_SALES_DASHBOARD_VIEW` | `Sales.Dashboard.View` | View executive dashboard |
| `GROUP_SALES_BOOKINGS_VIEW` | `Sales.Bookings.View` | View daily bookings |
| `GROUP_SALES_BOOKINGS_EXPORT` | `Sales.Bookings.Export` | Download bookings Excel |
| `GROUP_SALES_OPENORDERS_VIEW` | `Sales.OpenOrders.View` | View open orders |
| `GROUP_SALES_OPENORDERS_EXPORT` | `Sales.OpenOrders.Export` | Download open orders Excel |

Each variable's value should be the Entra ID Security Group's **Object ID** (a GUID like `a1b2c3d4-e5f6-...`).

### Database

| Variable | Required | Default | Description |
|---|---|---|---|
| `DB_DRIVER` | No | `{ODBC Driver 18 for SQL Server}` | ODBC driver name |
| `DB_SERVER` | Yes | — | SQL Server hostname |
| `DB_UID` | Yes | — | SQL Server username |
| `DB_PWD` | Yes | — | SQL Server password |
| `DB_TRUST_CERT` | No | `yes` | Trust server certificate (for self-signed certs) |
| `DB_AUTH` | No | `PRO12` | Authentication database (not currently used) |
| `DB_ORDERS` | No | `PRO05` | US orders database |
| `DB_ORDERS_CA` | No | `PRO06` | Canada orders database |

### Application Timing

| Variable | Default | Description |
|---|---|---|
| `DATA_REFRESH_INTERVAL` | `600` (10 min) | Bookings + exchange rate refresh interval (seconds) |
| `OPEN_ORDERS_REFRESH_INTERVAL` | `3600` (60 min) | Open orders refresh interval (seconds) |
| `DASHBOARD_REFRESH_INTERVAL` | `3600` (60 min) | Dashboard current month refresh interval (seconds) |

### Cache

| Setting | Value | Description |
|---|---|---|
| `CACHE_TYPE` | `FileSystemCache` | Persists to disk, survives restarts |
| `CACHE_DIR` | `cache-data` | Directory for cache files |
| `CACHE_DEFAULT_TIMEOUT` | `900` (15 min) | Safety net timeout |

---

## Setup & Installation

### Prerequisites

- Python 3.12+
- ODBC Driver 18 for SQL Server (install from Microsoft)
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

Create a `.env` file in the project root:

```bash
# Authentication
SECRET_KEY=your-strong-random-secret-key-here
CLIENT_ID=your-azure-app-client-id
CLIENT_SECRET=your-azure-app-client-secret
TENANT_ID=your-azure-tenant-id
# REDIRECT_URI_OVERRIDE=https://portal.thewheelgroup.info/auth/redirect

# Database
DB_SERVER=twg-sql-01.thewheelgroup.com
DB_UID=your-sql-username
DB_PWD=your-sql-password
DB_ORDERS=PRO05
DB_ORDERS_CA=PRO06

# Security Groups (Entra ID Object IDs)
GROUP_ADMIN=xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
GROUP_SALES_DASHBOARD_VIEW=xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
GROUP_SALES_BOOKINGS_VIEW=xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
GROUP_SALES_BOOKINGS_EXPORT=xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
GROUP_SALES_OPENORDERS_VIEW=xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
GROUP_SALES_OPENORDERS_EXPORT=xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx

# Optional: Refresh intervals (seconds)
# DATA_REFRESH_INTERVAL=600
# OPEN_ORDERS_REFRESH_INTERVAL=3600
# DASHBOARD_REFRESH_INTERVAL=3600
```

---

## Running the Application

```bash
# Development (Flask built-in server)
python app.py

# Production (Waitress WSGI server)
waitress-serve --host=0.0.0.0 --port=5000 app:create_app
```

**Startup sequence:**

1. Flask app created via `create_app()` factory
2. Config validation runs (checks CLIENT_ID, CLIENT_SECRET, AUTHORITY)
3. GROUP_ROLE_MAP built from environment variables
4. Flask-Caching initialized (FileSystemCache)
5. APScheduler started with 3 jobs registered
6. Initial data refresh runs synchronously (bookings + open orders + exchange rate)
7. Dashboard note logged: "Historical data will be fetched on first visit"
8. Blueprints registered (main, sales, admin)
9. Server starts listening on port 5000

---

## Deployment Notes

| Topic | Details |
|---|---|
| **SECRET_KEY** | Set to a strong random value (use `python -c "import secrets; print(secrets.token_hex(32))"`) |
| **Redirect URIs** | Register all environment redirect URIs in Azure App Registration |
| **Reverse proxy** | Use IIS/nginx with SSL termination. Set `REDIRECT_URI_OVERRIDE` if dynamic URI building doesn't match. |
| **Outbound HTTPS** | Required: `api.frankfurter.app`, `open.er-api.com`, `cdnjs.cloudflare.com`, `fonts.googleapis.com`, `fonts.gstatic.com` |
| **SQL load** | ~28 queries/hour for bookings + open orders (10min × 4 queries + 60min × 4 queries). Dashboard queries are on-demand only. |
| **Frozen data** | Copy `dashboard_data/` folder when migrating servers for instant historical data loading. This folder is gitignored — manage separately from code deployments. |
| **Cache directory** | `cache-data/` is auto-created. Gitignored. Can be safely deleted to force a full refresh. |
| **Windows** | If `.env` filename is problematic, rename to `_env` — the loader falls back to it automatically. |
| **Memory** | Dashboard aggregation for a full US year (~400K rows) temporarily uses ~100-200MB of RAM during processing. After aggregation, only the tiny summary (~5KB) is kept. |

---

## HTTPS & Redirect URI Handling

Behind a reverse proxy with SSL termination, Flask sees `http://` from `request.url_root` even though users access via `https://`. Azure Entra ID requires `https://` for all redirect URIs except `localhost`.

**The `_build_redirect_uri()` function handles this automatically:**

1. If `REDIRECT_URI_OVERRIDE` is set → use it verbatim (highest priority)
2. Otherwise, build from `request.url_root`
3. If the host is not `localhost` or `127.0.0.1` and the URL starts with `http://` → force `https://`
4. Append `REDIRECT_PATH` (`/auth/redirect`)

**When to use REDIRECT_URI_OVERRIDE:**

- Running behind a load balancer where the public hostname differs from the internal hostname
- Custom domain (e.g., `portal.thewheelgroup.info`) that doesn't match what Flask sees
- Any environment where the automatic HTTPS forcing doesn't produce the correct URI

---

## URL Reference

| Route | Method | Role | Description |
|---|---|---|---|
| `/login_page` | GET | — | Login page (renders `login.html`) |
| `/login` | GET | — | Initiates OAuth flow → redirects to Microsoft |
| `/auth/redirect` | GET | — | OAuth callback → exchanges code for token |
| `/logout` | GET | — | Clears session → redirects to Microsoft logout |
| `/` | GET | any authenticated | Department hub (renders `index.html`) |
| `/sales` | GET | `Sales.Base` | Sales report menu |
| `/sales/dashboard` | GET | `Sales.Dashboard.View` | Executive dashboard (accepts `?year=` param) |
| `/sales/dashboard/refresh` | POST | `Sales.Dashboard.View` | AJAX: invalidate cache + return redirect URL |
| `/sales/bookings` | GET | `Sales.Bookings.View` | Daily bookings with ranking tabs |
| `/sales/bookings/export` | GET | `Sales.Bookings.Export` | Excel: US + CA combined |
| `/sales/bookings/export/us` | GET | `Sales.Bookings.Export` | Excel: US only |
| `/sales/bookings/export/ca` | GET | `Sales.Bookings.Export` | Excel: CA only |
| `/sales/open-orders` | GET | `Sales.OpenOrders.View` | Open orders dashboard |
| `/sales/open-orders/export` | GET | `Sales.OpenOrders.Export` | Excel: US + CA combined |
| `/sales/open-orders/export/us` | GET | `Sales.OpenOrders.Export` | Excel: US only |
| `/sales/open-orders/export/ca` | GET | `Sales.OpenOrders.Export` | Excel: CA only |
| `/admin/dashboard-data` | GET | `Admin` | Data management page |
| `/admin/dashboard-data/download` | POST | `Admin` | AJAX: download single region `{year, region}` |
| `/admin/dashboard-data/download-both` | POST | `Admin` | AJAX: download US + CA `{year}` |
| `/admin/dashboard-data/delete` | POST | `Admin` | AJAX: delete frozen file `{year, region}` |
| `/apple-touch-icon*` | GET | — | PWA: serves `apple-touch-icon.png` for Safari |

---

## Troubleshooting

| Issue | Cause | Fix |
|---|---|---|
| **AADSTS50011** — Redirect URI mismatch | `http://` sent but `https://` registered in Azure | Set `REDIRECT_URI_OVERRIDE` in `.env` to the exact registered URI |
| **AADSTS50148** — PKCE mismatch | Wrong MSAL method used for token exchange | Ensure code uses `acquire_token_by_auth_code_flow()` (not `acquire_token_by_authorization_code()`) |
| **403 Forbidden** | User not in the required Entra Security Group | Add user to the appropriate group in Azure AD, then have them log out and back in |
| **No report cards visible on /sales** | GROUP_* env vars not set or incorrect Object IDs | Check `.env` — each GROUP_* must contain the Security Group's Object ID (GUID) |
| **No department cards visible on /** | User has no Sales.*.View roles | Add user to at least one View security group |
| **Admin gear icon not showing** | User not in Admin group | Add user to the `TWG-Portal-Admin` security group |
| **Empty bookings dashboard** | SQL unreachable or no orders today | Check SQL Server connectivity in console logs. If no orders today, the "No bookings found" empty state is expected. |
| **Empty executive dashboard** | No frozen file + SQL unreachable + cache expired | Use admin page to download the year, or check SQL connectivity |
| **Dashboard slow first load** | No frozen file for that year — fetching ~400K rows from SQL | Use admin page to download historical years. First SQL fetch takes ~15-20 seconds, subsequent loads are instant from cache/disk. |
| **Dashboard shows no data for past year** | Frozen file missing + cache expired + SQL Server unreachable | Download via admin page when SQL is accessible |
| **Exchange rate shows 0.7200** | Both APIs blocked or unreachable | Allow outbound HTTPS to `api.frankfurter.app` and `open.er-api.com` |
| **Charts not loading** | CDN blocked | Allow outbound HTTPS to `cdnjs.cloudflare.com` |
| **Fonts not loading** | Google Fonts blocked | Allow outbound HTTPS to `fonts.googleapis.com` and `fonts.gstatic.com` |
| **Theme flash on page load** | Missing synchronous theme script in `<head>` | Ensure the inline `<script>` that reads `localStorage('twg-theme')` is present before any CSS in every page's `<head>` |
| **Export buttons not visible** | User has View role but not Export role | This is by design. Add user to the Export security group if they should be able to download. |
| **Stale data after app restart** | FileSystemCache still has old data in `cache-data/` | Delete the `cache-data/` directory to force a full refresh on next startup |
| **Config validation fails on startup** | Missing CLIENT_ID, CLIENT_SECRET, or AUTHORITY | Check `.env` file exists in the project root and contains all required values |
| **"Cannot start: missing authentication configuration"** | `.env` not found or not loaded | Verify `.env` file path. On Windows, try renaming to `_env`. Check console for "Could not load .env" warning. |

---

## Roadmap

| Module | Report | Status | Description |
|---|---|---|---|
| **Sales** | Executive Dashboard | ✅ Live | Year selector, Chart.js (monthly/territory/product line/salesman), top 50 customers, frozen data files, admin data management page, 3-tier data resolution |
| **Sales** | Daily Bookings | ✅ Live | Territory/Salesman/Customer ranking tabs with podium, auto-refresh for TV/kiosk, CAD→USD conversion, Excel export (role-gated) |
| **Sales** | Open Sales Orders | ✅ Live | Territory + salesman side-by-side rankings, released amount tracking, hourly refresh, CAD→USD, Excel export (role-gated) |
| **Sales** | Shipments | 🔜 Planned | Daily shipments by warehouse |
| **Sales** | Territory Performance | 🔜 Planned | Monthly trends with period comparison |
| **Admin** | Dashboard Data | ✅ Live | Download/delete frozen historical data files, status view with file sizes and dates, portable across servers, gear icon in nav bar |
| **Warehouse** | — | 🔜 Planned | Inventory levels, fulfillment tracking |
| **Accounting** | — | 🔜 Planned | Invoices, payments, financial reporting |
| **HR** | — | 🔜 Planned | Employee directory, attendance |

---

## Dependencies

All Python dependencies with pinned versions (from `requirements.txt`):

| Package | Version | Purpose |
|---|---|---|
| Flask | 3.0.0 | Web framework |
| Waitress | 2.1.2 | Production WSGI server |
| pyodbc | (latest) | SQL Server connectivity via ODBC |
| msal | 1.26.0 | Microsoft Authentication Library for Entra ID SSO |
| requests | 2.31.0 | HTTP client (used by MSAL internally) |
| python-dotenv | 1.0.0 | `.env` file loading |
| Flask-Caching | 2.1.0 | FileSystemCache for data snapshots |
| Flask-APScheduler | 1.13.1 | Background job scheduling |
| openpyxl | 3.1.2 | Excel file generation with formatting |

**Client-side CDN dependencies:**

| Library | Version | CDN | Used On |
|---|---|---|---|
| Chart.js | 4.4.1 | cdnjs.cloudflare.com | Executive dashboard only |
| DM Sans | — | fonts.googleapis.com | All pages (UI font) |
| JetBrains Mono | — | fonts.googleapis.com | All pages (numbers/code font) |

---

## License

Internal use only — The Wheel Group.