# TWG Portal

A secure, enterprise-grade internal portal for **The Wheel Group**, built with Flask and integrated with Microsoft Entra ID (SSO) for authentication and Microsoft SQL Server for real-time ERP data.

The portal serves as a centralized dashboard hub for multiple departments — starting with **Sales** — providing live KPIs, territory/salesman/customer ranking tabs with podium displays, interactive executive dashboards with Chart.js visualizations, real-time CAD→USD currency conversion, formatted Excel data exports, dark/light theme switching with OLED support, and auto-refreshing displays optimized for desktop monitors, tablets, iPhones/iPads, and unattended TV/kiosk screens.

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

The application follows a **Decoupled Caching Architecture** to ensure instant page loads without overloading the ERP SQL Server.

```
┌─────────────┐      ┌──────────────┐      ┌───────────────┐
│   Browser    │◄────►│  Flask App   │◄────►│  File Cache   │
│  (User/TV)   │      │  (Routes)    │      │  (In-Memory)  │
└─────────────┘      └──────────────┘      └───────┬───────┘
                                                    │
                                    ┌───────────────┴───────────────┐
                                    │        APScheduler             │
                                    │   ┌──────────┐ ┌────────────┐ │
                                    │   │ Bookings │ │ Open Orders│ │
                                    │   │ 10 min   │ │  60 min    │ │
                                    │   └────┬─────┘ └─────┬──────┘ │
                                    └────────┼─────────────┼────────┘
                                             │             │
                                   ┌─────────┴──┐  ┌──────┴────────┐
                                   │ SQL Server  │  │ Exchange Rate │
                                   │ PRO05 (US)  │  │ API (CAD→USD) │
                                   │ PRO06 (CA)  │  └───────────────┘
                                   └─────────────┘
```

**How it works:**

1. **Background Workers (APScheduler)** — Two independent scheduled jobs run at different intervals to minimize SQL Server load:
   - **Bookings refresh** — Every **10 minutes**. Queries today's bookings from both US (PRO05) and Canada (PRO06) databases, fetches the live CAD→USD exchange rate, and caches all results. Also runs once immediately on app startup.
   - **Open orders refresh** — Every **60 minutes**. Queries all currently open sales order lines from both databases. Open orders data changes less frequently than daily bookings, so the longer interval significantly reduces SQL Server load.
2. **Cache Layer (Flask-Caching)** — Stores the latest data snapshots using `FileSystemCache`. Survives brief app restarts. Each cache entry includes a `last_updated` timestamp so users know the data freshness. Separate cache keys and timeouts are used for bookings data (15-min TTL), open orders data (65-min TTL), the exchange rate, and refresh timestamps.
3. **Web App (Flask)** — Serves the UI. Route handlers **never** query SQL directly — they read exclusively from cache, ensuring sub-millisecond response times regardless of SQL Server load. This applies to all dashboard pages, ranking tabs, the executive dashboard, AND the Excel export downloads. Even if 100 users click Export simultaneously, the SQL Server sees zero additional queries.
4. **Auto-Refresh (Client-Side)** — The bookings page includes a `<meta http-equiv="refresh">` tag that reloads the page every 10 minutes, plus a live JavaScript countdown timer. This is designed for TVs/monitors in the sales area that display the dashboard unattended. The open orders page does **not** auto-refresh — it is designed for on-demand desktop use and simply shows the "Last updated" timestamp.

---

## Tech Stack

| Layer            | Technology                                        |
|------------------|---------------------------------------------------|
| **Backend**      | Python 3.12+, Flask 3.0                           |
| **Auth (SSO)**   | Microsoft Entra ID (Azure AD), MSAL for Python    |
| **RBAC**         | Entra ID Security Groups, custom `@require_role` decorator, per-report View/Export permissions, role hierarchy |
| **Database**     | Microsoft SQL Server (US: PRO05, Canada: PRO06), pyodbc |
| **Caching**      | Flask-Caching (FileSystemCache)                   |
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
├── app.py                    # Application factory, SSO routes, HTTPS redirect URI builder, scheduler init
├── config.py                 # All configuration (auth, DB, cache, scheduler intervals, per-report GROUP_ROLE_MAP)
├── extensions.py             # Shared Flask extensions (Cache, APScheduler)
├── requirements.txt          # Python dependencies
├── .env                      # Environment variables (secrets — never committed)
├── .gitignore                # Git exclusions
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
│   └── sales.py              # Sales blueprint: bookings + open orders + executive dashboard + Excel exports + dashboard AJAX filter
│
├── services/
│   ├── __init__.py
│   ├── constants.py          # Shared territory maps (US + CA), customer exclusion set, map_territory(), resolve_territory_code()
│   ├── db_connection.py      # pyodbc connection factory with 30s timeout
│   ├── db_service.py         # Legacy bookings service (retained for reference — not used by current routes)
│   ├── bookings_service.py   # Bookings SQL queries + Python aggregation (territory + salesman + customer rankings, snapshot + raw)
│   ├── open_orders_service.py# Open orders SQL queries + Python aggregation (territory + salesman rankings, released tracking, snapshot + raw)
│   ├── dashboard_service.py  # Executive dashboard aggregation: build_filter_options(), aggregate_dashboard_data() with filter support
│   ├── data_worker.py        # Background cache refresh logic, exchange rate fetching with failover, all cache keys centralized
│   └── excel_helper.py       # Shared Excel workbook builder (openpyxl): title row, metadata, headers, alternating rows, money formatting
│
├── static/
│   ├── logo/
│   │   ├── TWG.png           # Company logo used in nav and login page
│   │   ├── apple-touch-icon.png   # iOS home screen icon (180×180)
│   │   ├── icon-192x192.png       # Android/PWA icon
│   │   └── icon-512x512.png       # PWA splash icon
│   ├── css/
│   │   └── dashboard.css     # Executive dashboard styles (reference copy — also inlined in template via extra_styles block)
│   ├── js/
│   │   └── dashboard.js      # Chart.js rendering, KPI updates, filter panel toggle/apply/clear, AJAX POST, theme-aware recoloring
│   └── manifest.json         # PWA manifest (display: browser, theme_color: #111827)
│
├── templates/
│   ├── base.html             # Shared layout: nav bar, theme toggle (sun/moon), avatar, breadcrumbs, dark/light CSS variables, no-flash script
│   ├── login.html            # Microsoft SSO login page (standalone — not extending base.html, own theme support with floating toggle)
│   ├── index.html            # Department hub (Sales, Warehouse, Accounting, HR cards) — role-aware visibility
│   └── sales/
│       ├── index.html        # Sales report menu (Dashboard, Bookings, Open Orders, Coming Soon) — shows Live/Export/View Only badges per role
│       ├── bookings.html     # Daily Bookings: US + CA sections, 4 KPI cards each, ranking tabs (Territory/Salesman/Customer), podium + table, export buttons gated by can_export
│       ├── open_orders.html  # Open Sales Orders: US + CA sections, 4 KPI cards with Released sub-line, side-by-side territory + salesman rankings, export buttons gated by can_export
│       └── dashboard.html    # Executive Dashboard: 5 KPI cards, region split bar, 3 Chart.js charts, top 20 customers table, collapsible filter panel with AJAX
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
- Redirect URIs are built by `_build_redirect_uri()` which **forces `https://`** for any non-localhost host. This is critical when running behind a reverse proxy (IIS, nginx) with SSL termination — Flask sees `http://` from `request.url_root` but Azure Entra ID requires `https://` for all redirect URIs except localhost.
- An optional `REDIRECT_URI_OVERRIDE` environment variable allows hardcoding the full redirect URI for edge cases where the dynamic builder doesn't produce the correct result.
- The `.env` file loader has a fallback from `.env` to `_env` to handle Windows filename quirks.
- Config validation runs at startup and raises `SystemExit` with clear error messages if any required values are missing.
- User session stores `name`, `email`, `oid`, `tid`, `groups` (raw Entra group Object IDs), and `roles` (resolved internal role names).

**Required Azure App Registration settings:**

- **Platform:** Web
- **Redirect URIs (all three):**
  - `http://localhost:5000/auth/redirect` (local development)
  - `https://dev.thewheelgroup.info/auth/redirect` (dev/staging)
  - `https://portal.thewheelgroup.info/auth/redirect` (production)
- **API Permissions:** `User.Read` (Microsoft Graph)
- **Token configuration:** Add **groups** optional claim — go to Token Configuration → Add groups claim → select Security groups. This causes the `groups` array to appear in the id_token with the Object IDs of the user's security groups.
- **Client Secret:** Generate under Certificates & secrets

---

## Role-Based Access Control (RBAC)

The portal enforces page-level and feature-level access control using **Microsoft Entra ID Security Groups**. Groups are assigned to users in the Entra portal, and their Object IDs are included in the `id_token_claims.groups` array on every login. The `_resolve_roles_from_groups()` function in `app.py` maps these IDs to internal role names using `Config.GROUP_ROLE_MAP`. The `@require_role` decorator in `auth/decorators.py` checks for the required role before allowing access.

### How It Works

```
User logs in via Microsoft SSO
        │
        ▼
  id_token_claims includes "groups": ["77a1f712...", "b2555943...", "b67b5c7a..."]
        │
        ▼
  _resolve_roles_from_groups() maps each group Object ID → internal role name
  using GROUP_ROLE_MAP built from GROUP_* env vars in .env
  e.g. ["Admin", "Sales.Bookings.View", "Sales.Bookings.Export"]
        │
        ▼
  session["user"]["roles"] = ["Admin", "Sales.Bookings.View", "Sales.Bookings.Export"]
        │
        ▼
  User navigates to /sales/bookings
        │
        ▼
  @require_role('Sales.Bookings.View') checks session roles
  ├── Role found directly → Allow access
  ├── 'Admin' role found → Allow access (Admin bypasses all checks)
  ├── Role implied by hierarchy → Allow access
  └── Role missing → HTTP 403 Forbidden
```

### Per-Report View & Export Permissions

Every report has two separate permission levels:

- **View** (`Sales.<Report>.View`) — Grants access to see the dashboard page. Required to access the route.
- **Export** (`Sales.<Report>.Export`) — Enables the Excel download buttons on that report. Does **not** grant view access on its own — it only unlocks the download buttons on reports the user can already see.

This means a user with `Sales.Bookings.View` but without `Sales.Bookings.Export` can see the Daily Bookings dashboard but all three export buttons (Export All, Export US, Export CA) are completely invisible — not grayed out, not disabled, just gone from the DOM via `{% if can_export %}` Jinja2 conditionals.

**Security Groups → Roles:**

| Entra ID Security Group Name | Env Var | Internal Role | What It Grants |
|---|---|---|---|
| `TWG-Portal-Admin` | `GROUP_ADMIN` | `Admin` | Full access to everything (view + export all reports, bypasses all checks) |
| `TWG-Portal-Sales-Dashboard-View` | `GROUP_SALES_DASHBOARD_VIEW` | `Sales.Dashboard.View` | View the executive dashboard with charts and filters |
| `TWG-Portal-Sales-Bookings-View` | `GROUP_SALES_BOOKINGS_VIEW` | `Sales.Bookings.View` | View the Daily Bookings dashboard |
| `TWG-Portal-Sales-Bookings-Export` | `GROUP_SALES_BOOKINGS_EXPORT` | `Sales.Bookings.Export` | Download Bookings Excel files (must also have View to see the page) |
| `TWG-Portal-Sales-OpenOrders-View` | `GROUP_SALES_OPENORDERS_VIEW` | `Sales.OpenOrders.View` | View the Open Sales Orders dashboard |
| `TWG-Portal-Sales-OpenOrders-Export` | `GROUP_SALES_OPENORDERS_EXPORT` | `Sales.OpenOrders.Export` | Download Open Orders Excel files (must also have View to see the page) |

### Role Hierarchy

The hierarchy is defined in `auth/decorators.py` in the `ROLE_HIERARCHY` dict:

```python
ROLE_HIERARCHY = {
    'Sales.Base': [
        'Sales.Bookings.View',
        'Sales.OpenOrders.View',
        'Sales.Dashboard.View',
    ],
}
```

- **`Sales.Base`** — Never assigned directly. It is an internal role automatically implied by **any** `Sales.*.View` role, so that any Sales user can access the `/sales` hub page.
- **`Admin`** — Always bypasses all role checks. Checked first in `_user_has_role()`.
- **Export roles** — Do not appear in the hierarchy. They are standalone flags that only control button visibility.

### Route Enforcement

| Route Pattern | Decorator | Notes |
|---|---|---|
| `/sales` | `@require_role('Sales.Base')` | Sales department hub — implied by any Sales.*.View |
| `/sales/dashboard` | `@require_role('Sales.Dashboard.View')` | Executive dashboard |
| `/sales/dashboard/filter` | `@require_role('Sales.Dashboard.View')` | AJAX POST endpoint for filtered data |
| `/sales/bookings` | `@require_role('Sales.Bookings.View')` | Daily bookings dashboard view |
| `/sales/bookings/export` | `@require_role('Sales.Bookings.Export')` | Excel download: US + CA combined |
| `/sales/bookings/export/us` | `@require_role('Sales.Bookings.Export')` | Excel download: US only |
| `/sales/bookings/export/ca` | `@require_role('Sales.Bookings.Export')` | Excel download: CA only |
| `/sales/open-orders` | `@require_role('Sales.OpenOrders.View')` | Open orders dashboard view |
| `/sales/open-orders/export` | `@require_role('Sales.OpenOrders.Export')` | Excel download: US + CA combined |
| `/sales/open-orders/export/us` | `@require_role('Sales.OpenOrders.Export')` | Excel download: US only |
| `/sales/open-orders/export/ca` | `@require_role('Sales.OpenOrders.Export')` | Excel download: CA only |

### UI Behavior

The department hub (`index.html`) and sales report menu (`sales/index.html`) use Jinja2 conditionals to show or hide cards based on the user's roles:

- **Sales department card** on the home page is only visible if the user has any `Sales.*.View` role (or `Admin`).
- **Report cards** on the Sales menu show a green **"Live"** badge if the user has view access, plus a blue **"Export"** badge if they also have export permission, or a gray **"View Only"** badge if they can view but not export.
- **Coming Soon** reports (Shipments, Territory Performance) show a gray badge for everyone.
- **Export buttons** on bookings and open orders pages are wrapped in `{% if can_export %}` — completely absent from the HTML if the user lacks the export role. The `can_export` boolean is computed in the route handler using `user_has_role(session["user"], 'Sales.Bookings.Export')`.

### Setting Up Security Groups in Entra ID

1. Go to **Microsoft Entra ID** → **Groups** → **New group**
2. Set Group type to **Security**, give it a descriptive name (e.g., `TWG-Portal-Sales-Bookings-View`)
3. Click **Create**
4. Go to the new group → **Overview** → copy the **Object ID**
5. Paste the Object ID into the corresponding `GROUP_*` variable in `.env`
6. Add users to the group under **Members** → **Add members**
7. **Important:** Users must log out and log back in for new group memberships to take effect (groups are included in the token at login time)

**Adding a new report** requires:
1. Create 2 new Entra Security Groups (e.g., `TWG-Portal-Sales-Shipments-View` + `TWG-Portal-Sales-Shipments-Export`)
2. Add 2 new env vars in `.env` with their Object IDs
3. Add 2 new entries to `group_vars` in `config.py`'s `_build_group_role_map()`
4. Add the new `.View` role to the `ROLE_HIERARCHY['Sales.Base']` list in `decorators.py`
5. Build the route and template — no framework changes needed

---

## Data Architecture

### Dual-Region Database

The portal queries two separate SQL Server databases for US and Canadian operations:

| Region | Database | Description             |
|--------|----------|-------------------------|
| US     | PRO05    | US orders and sales data |
| Canada | PRO06    | Canadian orders and sales data |

Both databases share the same schema and are hosted on the same SQL Server instance. The app uses `Config.DB_ORDERS` (PRO05) and `Config.DB_ORDERS_CA` (PRO06) to target each one.

### SQL Server Connection

Connections use `pyodbc` with ODBC Driver 18 for SQL Server. The connection string is built dynamically from environment variables in `Config.get_connection_string()`.

```
Driver:   ODBC Driver 18 for SQL Server
Server:   twg-sql-01.thewheelgroup.com
Database: PRO05 (US) / PRO06 (Canada)
Auth:     SQL Server authentication (UID/PWD)
Options:  TrustServerCertificate=yes, Timeout=30s
Locking:  All queries use WITH (NOLOCK) to avoid blocking ERP operations
```

### Query Strategy

The portal uses a **lean SQL, heavy Python** strategy to minimize SQL Server load:

1. **SQL pulls minimal filtered rows** — Queries fetch only the columns needed for aggregation (sono, qty, amount, territory code, salesman, customer name, product line, release flag). No `GROUP BY`, `SUM`, or other aggregation functions run on the database.
2. **Python handles all aggregation** — Service modules (`bookings_service.py`, `open_orders_service.py`, `dashboard_service.py`) process the raw rows in Python, filtering out excluded records, mapping territory codes to display names, computing sums, counting distinct orders, and building ranked lists for territory, salesman, and customer.
3. **All monetary amounts are rounded up** — `math.ceil()` is applied to every dollar figure (summary totals and individual territory/salesman/customer totals) so the dashboard always shows whole numbers with no decimal places.
4. **Discount is applied in SQL** — Amount calculations use `qty × price × (1 - disc / 100)` directly in the SQL query to properly account for line-level discounts before any aggregation happens.

### Territory Mapping

Territory codes from the ERP are mapped to human-readable names in `services/constants.py`:

**US Territories (PRO05):**

| Code | Territory      |
|------|----------------|
| 000  | LA             |
| 001  | LA             |
| 010  | China          |
| 114  | Seattle        |
| 126  | Denver         |
| 204  | Columbus       |
| 206  | Jacksonville   |
| 210  | Houston        |
| 211  | Dallas         |
| 218  | San Antonio    |
| 221  | Kansas City    |
| 302  | Nashville      |
| 305  | Levittown, PA  |
| 307  | Charlotte      |
| 312  | Atlanta        |
| 324  | Indianapolis   |
| 900  | Central Billing|
| *    | Others         |

**Canada Territories (PRO06):**

| Code | Territory  |
|------|------------|
| 501  | Vancouver  |
| 502  | Toronto    |
| 503  | Montreal   |
| *    | Others     |

**Territory resolution logic:** If the customer's territory (`arcust.terr`) is `'900'` (Central Billing), that code is used. Otherwise, the sales order master territory (`somast.terr`) is used. This is handled in the SQL `CASE` expression and also validated in the Python aggregation.

### Excluded Data

#### Both Reports — Excluded Customers

| Customer Code | Reason                    |
|---------------|---------------------------|
| W1VAN         | Internal warehouse account |
| W1TOR         | Internal warehouse account |
| W1MON         | Internal warehouse account |
| MISC          | Miscellaneous/test         |
| TWGMARKET     | Internal marketing         |
| EMP-US        | Employee orders            |
| TEST123       | Test account               |

#### Both Reports — Excluded Product Lines

| Product Line | Reason           |
|--------------|------------------|
| TAX          | Tax line items   |

#### Both Reports — Excluded Order Statuses/Types

| Field      | Excluded Values | Reason                     | Applies To       |
|------------|-----------------|----------------------------|------------------|
| `currhist` | `X`             | Cancelled/historical       | Bookings only    |
| `sostat` (line) | `V`, `X`   | Voided, cancelled lines    | Bookings         |
| `sostat` (line) | `C`, `V`, `X` | Closed, voided, cancelled lines | Open Orders |
| `sostat` (order)| `C`        | Fully closed order         | Open Orders      |
| `sotype`   | `B`, `R`        | Blanket orders, returns    | Both             |

### Scheduler Strategy (Two Independent Jobs)

| Job ID              | Interval   | What It Refreshes                     | Cache TTL |
|---------------------|------------|---------------------------------------|-----------|
| `bookings_refresh`  | 10 minutes | US bookings, CA bookings, exchange rate, raw export data | 900s (15 min) |
| `open_orders_refresh`| 60 minutes | US open orders, CA open orders, raw export data | 3900s (65 min) |

On **app startup**, both jobs run once immediately via `refresh_all_on_startup()` so the cache is never empty when the first user hits the page.

### Caching Strategy

| Setting              | Bookings        | Open Orders     | Purpose                                    |
|----------------------|-----------------|-----------------|---------------------------------------------|
| Cache type           | FileSystemCache | FileSystemCache | Persists across brief app restarts           |
| Cache directory      | `cache-data/`   | `cache-data/`   | Auto-created, gitignored                     |
| Cache timeout        | 900s (15 min)   | 3900s (65 min)  | Safety net — overwritten each refresh cycle  |
| Refresh interval     | 600s (10 min)   | 3600s (60 min)  | Background worker schedule                   |
| Startup behavior     | Immediate       | Immediate       | Both run on startup via `refresh_all_on_startup()` |
| Cache miss fallback  | Synchronous     | Synchronous     | If cache is empty, fetches once before rendering   |

**Cache keys (defined in `data_worker.py`):**

| Key                          | Type       | Description                                 |
|------------------------------|------------|---------------------------------------------|
| `bookings_snapshot_us`       | `dict`     | US bookings summary + territory/salesman/customer rankings |
| `bookings_snapshot_ca`       | `dict`     | Canada bookings summary + territory/salesman/customer rankings |
| `bookings_raw_us`            | `list`     | US bookings raw line-item data for Excel export + dashboard |
| `bookings_raw_ca`            | `list`     | Canada bookings raw line-item data for Excel export + dashboard |
| `bookings_last_updated`      | `datetime` | Timestamp of last successful bookings refresh |
| `open_orders_snapshot_us`    | `dict`     | US open orders summary + territory + salesman ranking |
| `open_orders_snapshot_ca`    | `dict`     | Canada open orders summary + territory + salesman ranking |
| `open_orders_raw_us`         | `list`     | US open orders raw line-item data for Excel export |
| `open_orders_raw_ca`         | `list`     | Canada open orders raw line-item data for Excel export |
| `open_orders_last_updated`   | `datetime` | Timestamp of last successful open orders refresh |
| `cad_to_usd_rate`            | `float`    | Latest CAD → USD exchange rate              |

---

## Dark / Light Mode

The portal supports full dark and light theme switching across all pages, including the standalone login page.

### How It Works

1. The `<html>` element carries a `data-theme` attribute set to `"light"` (default) or `"dark"`.
2. All colors are defined as CSS custom properties (variables) in `base.html` with two complete sets: one under `:root, [data-theme="light"]` and one under `[data-theme="dark"]`.
3. A **sun/moon toggle button** appears in the top navigation bar on all authenticated pages. On the login page, it appears as a floating button in the top-right corner (since login.html is a standalone page that does not extend base.html).
4. Clicking the toggle switches the `data-theme` attribute and saves the choice to `localStorage` under key `twg-theme`.
5. A **synchronous inline `<script>`** in the `<head>` of every page reads the saved preference from `localStorage` and applies it to the `<html>` element **before any rendering occurs**, preventing a flash of the wrong theme on page load.

### Theme Color Values

| CSS Variable | Light Mode | Dark Mode (OLED) |
|---|---|---|
| `--bg-primary` | `#F5F6FA` | `#000000` (true black) |
| `--bg-secondary` | `#FFFFFF` | `#0A0A0A` |
| `--bg-card` | `#FFFFFF` | `#111111` |
| `--bg-card-hover` | `#F8F9FC` | `#1A1A1A` |
| `--border` | `#E2E5EF` | `#1F1F1F` |
| `--text-primary` | `#111827` | `#F1F3F8` |
| `--text-secondary` | `#4B5563` | `#8B95B0` |
| `--text-muted` | `#9CA3AF` | `#5C6584` |
| `--accent-blue` | `#2563EB` | `#3B82F6` |
| `--accent-green` | `#059669` | `#10B981` |
| `--accent-amber` | `#D97706` | `#F59E0B` |
| `--accent-red` | `#DC2626` | `#EF4444` |
| `--accent-purple` | `#7C3AED` | `#8B5CF6` |

The dark mode uses **true OLED black** (`#000000`) for the page background, maximizing contrast and saving power on OLED displays. Card backgrounds use `#111111` for subtle depth separation.

Additional theme-specific tokens cover shadows, podium medal gradients, ranking table header backgrounds, download button states, error banners, exchange rate badges, and logo brightness filters.

### Chart.js Theme Awareness

The executive dashboard's Chart.js charts (`dashboard.js`) detect theme changes via a `MutationObserver` on the `data-theme` attribute. When the theme switches, all charts are destroyed and re-rendered with the appropriate color palette, grid colors, tooltip styles, and background colors.

---

## Sales Module

### Department Hub (`/`)

After login, users land on the department hub — a card-based grid showing all departments. Currently **Sales** is live; Warehouse, Accounting, and HR are shown as "Coming Soon" with disabled cards. Each card has a unique accent color (Sales: blue, Warehouse: amber, Accounting: green, HR: purple).

**Role-aware visibility:** The Sales card is only visible if the user has any `Sales.*.View` role (which implies `Sales.Base`) or `Admin`. Users without any Sales role don't see the Sales card at all.

### Sales Report Menu (`/sales`)

A report selection page with cards for each available report. Currently **Dashboard**, **Daily Bookings**, and **Open Sales Orders** are live; Daily Shipments and Territory Performance are shown as "Coming Soon."

Each card shows status badges based on the user's roles:
- **"Live"** (green) — user has the `.View` role
- **"Export"** (blue) — user also has the `.Export` role
- **"View Only"** (gray) — user can view but has no export permission
- **"Coming Soon"** (gray) — report not yet built

### Daily Bookings Dashboard

**Route:** `/sales/bookings`
**Required Role:** `Sales.Bookings.View`
**Export buttons visible:** Only if `Sales.Bookings.Export` (or `Admin`)
**Refresh:** Auto-refresh every 10 minutes (designed for TV/monitor display)
**Data Source:** `sotran` rows where `ordate = today`

The main bookings dashboard page, designed for both desktop use and unattended TV/monitor display. The page is split into two distinct regional sections: **United States** and **Canada**.

**Page Header:**

- **Title** — "Daily Bookings"
- **Date Tag** — Shows the current booking date in a styled pill (e.g., "Tuesday, February 25, 2026")
- **Export All Button** — Downloads a combined US + Canada Excel file (invisible without export permission)

**Refresh Bar:**

- **Left side** — Green pulsing dot + "Last updated: 09:40 AM" timestamp
- **Right side** — Live countdown timer: "Next refresh in 8:42"

**Per-Region Layout (US and Canada):**

1. **Region Header** — Flag icon, region title with database name, per-region Export button (invisible without export permission). Canada also shows a live exchange rate badge.
2. **Summary Cards** — Four KPI cards: Total Booking Amount (green), Total Units Ordered (blue), Sales Orders (amber), Territories Active (white). Canada shows `CAD $12,345` with `≈ USD $8,888` below.
3. **Ranking Tabs** — Toggle between Territory, Salesman, and Customer views (see below).

**Bookings SQL Filter Logic:**

```sql
WHERE tr.ordate = CAST(GETDATE() AS DATE)    -- today's orders only
  AND tr.currhist <> 'X'                     -- not cancelled/historical
  AND tr.sostat  NOT IN ('V', 'X')           -- not voided or cancelled
  AND tr.sotype  NOT IN ('B', 'R')           -- no blankets or returns
```

Plus Python-side filtering: excluded customers (7 internal accounts) and TAX product lines.

**Bookings Snapshot Query Fields:**

```sql
SELECT tr.sono, tr.origqtyord AS units,
       tr.origqtyord * tr.price * (1 - tr.disc / 100.0) AS amount,
       CASE WHEN cu.terr = '900' THEN cu.terr ELSE sm.terr END AS terr_code,
       tr.custno, ic.plinid, tr.salesmn, cu.company AS cust_name
```

### Ranking Tabs (Territory / Salesman / Customer)

Both the US and Canada sections of the Daily Bookings page feature a **tabbed ranking selector** with three views. The tabs appear in a pill-style toggle group to the right of the "Ranking" section title.

| Tab | Default | Data Source | Podium Shows | Table Columns (US) | Table Columns (CA) |
|---|---|---|---|---|---|
| **Territory** | Yes | `territory_totals` from `_aggregate_bookings()` | Top 3 territory names + amounts | Location, Total, Rank | Location, Total (CAD), ≈ USD, Rank |
| **Salesman** | No | `salesman_totals` from `_aggregate_bookings()` | Top 3 salesman codes + amounts | Salesman, Total, Rank | Salesman, Total (CAD), ≈ USD, Rank |
| **Customer** | No | `customer_totals` from `_aggregate_bookings()` | Top 3 customer names + amounts | Customer, Total, Rank | Customer, Total (CAD), ≈ USD, Rank |

**Visual format (same for all three tabs):**

- **Top 3 Podium** — Gold (#1), Silver (#2), Bronze (#3) cards with medal icons, gradient borders, glowing effects. Each shows the name and dollar amount.
- **Remaining Entries** — Clean table below the podium for entries ranked 4th and below.
- **Canada** — All tables include an additional `≈ USD` column showing the converted amount.

**Tab switching** is instant with no page reload. All three rankings are server-rendered into separate `<div class="ranking-panel">` elements. JavaScript toggles the `.active` class on click. Each tab group is scoped by `data-region` attribute (us/ca) so the US and Canada sections switch independently.

**CSS for tabs:**

```css
.ranking-tabs { display: flex; gap: 4px; background: var(--bg-primary); border: 1px solid var(--border); border-radius: var(--radius-md); padding: 3px; }
.ranking-tab { padding: 6px 14px; font-size: 12px; font-weight: 600; ... }
.ranking-tab.active { color: var(--text-primary); background: var(--bg-card); box-shadow: var(--shadow-sm); }
.ranking-panel { display: none; }
.ranking-panel.active { display: block; }
```

**Phone (480px):** Tabs shrink to `padding: 5px 10px; font-size: 11px` so all three fit on one line.

### Open Sales Orders Dashboard

**Route:** `/sales/open-orders`
**Required Role:** `Sales.OpenOrders.View`
**Export buttons visible:** Only if `Sales.OpenOrders.Export` (or `Admin`)
**Refresh:** Every 60 minutes (background), no auto-refresh on client
**Data Source:** All currently open `sotran` lines (no date filter)

Displays the total value of all open (unfulfilled) sales order lines across both regions.

**Per-Region Layout:**

1. **Summary Cards** — Four KPI cards:
   - **Total Open Amount** (green) — with Released sub-line showing `$amount` and `XX%`
   - **Open Units** (blue)
   - **Open Orders** (amber) — count of distinct `sono`
   - **Open Lines** (purple) — count of individual line items
2. **Side-by-Side Rankings** — Territory ranking and Salesman ranking in a 50/50 grid, each with Open $ and Released $ columns.

**Open Orders SQL Filter Logic:**

```sql
WHERE tr.qtyord > 0                          -- still has remaining open quantity
  AND tr.sostat  NOT IN ('C', 'V', 'X')     -- line not closed, voided, or cancelled
  AND sm.sostat  <> 'C'                      -- order not fully closed (INNER JOIN enforces this)
  AND tr.sotype  NOT IN ('B', 'R')           -- no blankets or returns
  -- NO date filter (all open orders regardless of age)
  -- NO currhist filter (not relevant for open orders)
```

**Key differences from Bookings:**

| Aspect            | Bookings                    | Open Orders                    |
|-------------------|-----------------------------|--------------------------------|
| Required role     | `Sales.Bookings.View`       | `Sales.OpenOrders.View`        |
| Date filter       | Today only                  | None — all open lines          |
| Refresh interval  | 10 minutes                  | 60 minutes                     |
| Auto-refresh UI   | Yes (TV/kiosk mode)         | No (on-demand desktop use)     |
| Qty field          | `origqtyord` (original qty) | `qtyord` (remaining open qty)  |
| sostat exclusion   | `V`, `X`                    | `C`, `V`, `X`                  |
| currhist filter    | `currhist <> 'X'`           | Not applied                    |
| Rankings           | Territory/Salesman/Customer tabs | Territory + Salesman side-by-side |
| Released tracking  | Not included                | Included (`somast.release`)    |
| `somast` join      | `LEFT JOIN`                 | `INNER JOIN` (enforces order-level filter) |

### Executive Dashboard

**Route:** `/sales/dashboard`
**Required Role:** `Sales.Dashboard.View`
**Data Source:** Raw cached bookings line-item data (same data used for Excel exports), aggregated by `dashboard_service.py`

A data-dense executive overview built with Chart.js, aggregating today's raw bookings data from cache into interactive visualizations. **Zero SQL queries at request time** — all data comes from the existing bookings raw cache.

**Page Layout:**

1. **5 KPI Cards** (row) — Each with a colored top accent bar:
   - **Total Sales (USD)** — green, sum of all line amounts (CA converted to USD)
   - **Total Units** — blue
   - **Sales Orders** — amber, distinct `sono` count
   - **Avg Order Value** — purple, total amount / order count
   - **Line Items** — red accent, total line count

2. **Region Split Bar** — An 8px segmented bar showing US (blue) vs Canada (amber) proportion, with dollar amounts in a legend below.

3. **Charts (2-column grid):**
   - **Sales by Territory** — Horizontal bar chart, top 15 territories, each bar a different color from the palette
   - **Sales by Product Line** — Donut chart with legend showing percentages, 55% cutout
   
4. **Charts (full-width):**
   - **Sales by Salesman** — Full-width horizontal bar chart, top 15 salesmen, blue bars

5. **Top 20 Customers Table** — Scrollable table (max-height 400px) with columns: #, Customer, Amount, Units, Orders.

6. **Filter Panel** — Collapsible (hidden by default), toggled by a "Filters" button in the header. Contains:
   - **Territory** — Multi-select dropdown
   - **Salesman** — Multi-select dropdown
   - **Product Line** — Multi-select dropdown
   - **Apply Filters** button → sends AJAX POST to `/sales/dashboard/filter`
   - **Clear All** button → resets all filters and re-fetches unfiltered data

**AJAX Filter Flow:**

```
User selects filters → clicks "Apply Filters"
        │
        ▼
  JavaScript collects selected values from all multi-selects
  POST /sales/dashboard/filter  (JSON body: {territories: [...], salesmen: [...], product_lines: [...]})
        │
        ▼
  Server reads raw data from cache, applies filters in Python (dashboard_service.py)
  Returns filtered aggregation as JSON
        │
        ▼
  JavaScript updates all KPIs, redraws all charts, rebuilds customer table
  (no page reload)
```

**Dashboard Service (`services/dashboard_service.py`):**

- `build_filter_options(rows_us, rows_ca)` — Extracts all unique territory names, salesman codes, product line codes, and customer names from the raw data. Returns sorted lists for populating the filter dropdowns.
- `aggregate_dashboard_data(rows_us, rows_ca, filters, cad_rate)` — Processes all raw line items in one pass, applying optional filters, converting CA amounts to USD, and building: summary dict, by_territory list, by_salesman list, by_product_line list, by_customer list (top 20), and region_split dict.

---

## Amount Calculation & Discount Handling

All monetary amount calculations in both reports account for line-level discounts stored in `sotran.disc`:

**Formula (both reports):**

```
Amount = quantity × price × (1 - disc / 100)
```

- **Bookings:** `origqtyord × price × (1 - disc / 100)` — original quantity ordered
- **Open Orders:** `qtyord × price × (1 - disc / 100)` — remaining open quantity
- **Rounding:** After calculation and aggregation, all monetary totals are rounded **up** using `math.ceil()` to the nearest whole dollar

---

## Currency Conversion

Real-time CAD → USD conversion for all Canadian amounts.

**Two APIs with automatic failover:**

| Priority | API                        | Source Data | Auth Required |
|----------|----------------------------|-------------|---------------|
| Primary  | `api.frankfurter.app`      | ECB rates   | No            |
| Fallback | `open.er-api.com`          | ECB rates   | No            |

**Sanity check:** Rate validated to be between 0.50 and 1.00. Out-of-range values rejected.
**Hardcoded fallback:** If all APIs fail, default rate of `0.72` used.
**Where conversions appear:** Canadian summary cards, territory ranking tables, salesman ranking tables, customer ranking tables, executive dashboard region split.

---

## Excel Exports

All exports read from cache — zero SQL at download time.

### Bookings Export Endpoints

| Route                        | Scope       | Filename Pattern                        |
|------------------------------|-------------|-----------------------------------------|
| `/sales/bookings/export`     | US + Canada | `Bookings_Raw_US_CA_YYYYMMDD.xlsx`     |
| `/sales/bookings/export/us`  | US only     | `Bookings_Raw_US_YYYYMMDD.xlsx`        |
| `/sales/bookings/export/ca`  | Canada only | `Bookings_Raw_CA_YYYYMMDD.xlsx`        |

### Open Orders Export Endpoints

| Route                          | Scope       | Filename Pattern                       |
|--------------------------------|-------------|----------------------------------------|
| `/sales/open-orders/export`    | US + Canada | `Open_Orders_US_CA_YYYYMMDD.xlsx`     |
| `/sales/open-orders/export/us` | US only     | `Open_Orders_US_YYYYMMDD.xlsx`        |
| `/sales/open-orders/export/ca` | Canada only | `Open_Orders_CA_YYYYMMDD.xlsx`        |

### Excel Formatting (Shared via `excel_helper.py`)

- **Title row** — Report name and today's date (bold, 13pt)
- **Metadata row** — "Exported by {user} on {date/time}" (italic, 9pt, gray)
- **Header row** — Dark background (`#1F2937`), white text, centered, wrap text
- **Alternating row shading** — Every other row gets light gray (`#F9FAFB`)
- **Money columns** — Green font (`#0A7A4F`) for ExtAmount, UnitPrice, OpenAmount, ExtPrice
- **Number formatting** — Currency: `$#,##0.00`, quantity: `#,##0`, dates: `MM/DD/YYYY`, discount: `0.000`
- **Frozen header** — Row 4 frozen so headers stay visible
- **Auto-filter** — Excel filter dropdowns on every column
- **Combined exports** add a "Region" column as first column with `US` or `CA`

### Bookings Export Columns (26 columns)

| #  | Header                      | Source Field           | Format       |
|----|-----------------------------|------------------------|--------------|
| 1  | Sales Order (sono)          | `tr.sono`              | Text         |
| 2  | Line# (tranlineno)          | `tr.tranlineno`        | `#,##0`      |
| 3  | Order Date (ordate)         | `tr.ordate`            | `MM/DD/YYYY` |
| 4  | Customer No (custno)        | `tr.custno`            | Text         |
| 5  | Customer Name (company)     | `cu.company`           | Text         |
| 6  | Item (item)                 | `tr.item`              | Text         |
| 7  | Description (descrip)       | `tr.descrip`           | Text         |
| 8  | Product Line (plinid)       | `ic.plinid`            | Text         |
| 9  | Qty Ordered (origqtyord)    | `tr.origqtyord`        | `#,##0`      |
| 10 | Qty Shipped (qtyshp)        | `tr.qtyshp`            | `#,##0`      |
| 11 | Unit Price (price)          | `tr.price`             | `$#,##0.00`  |
| 12 | Discount % (disc)           | `tr.disc`              | `0.000`      |
| 13 | Ext Amount (calculated)     | `origqtyord × price × (1-disc/100)` | `$#,##0.00` |
| 14 | Ext Price (extprice)        | `tr.extprice`          | `$#,##0.00`  |
| 15 | Line Status (sostat)        | `tr.sostat`            | Text         |
| 16 | Order Type (sotype)         | `tr.sotype`            | Text         |
| 17 | Territory (mapped)          | Python-mapped          | Text         |
| 18 | Terr Code (resolved)        | `CASE cu.terr/sm.terr` | Text         |
| 19 | Tran Terr (tr.terr)         | `tr.terr`              | Text         |
| 20 | SO Mast Terr (sm.terr)      | `sm.terr`              | Text         |
| 21 | Cust Terr (cu.terr)         | `cu.terr`              | Text         |
| 22 | Salesman (salesmn)          | `tr.salesmn`           | Text         |
| 23 | Location (loctid)           | `tr.loctid`            | Text         |
| 24 | Request Date (rqdate)       | `tr.rqdate`            | `MM/DD/YYYY` |
| 25 | Ship Date (shipdate)        | `tr.shipdate`          | `MM/DD/YYYY` |
| 26 | Ship Via (shipvia)          | `sm.shipvia`           | Text         |

### Open Orders Export Columns (26 columns)

| #  | Header                      | Source Field           | Format       |
|----|-----------------------------|------------------------|--------------|
| 1  | Sales Order (sono)          | `tr.sono`              | Text         |
| 2  | Line# (tranlineno)          | `tr.tranlineno`        | `#,##0`      |
| 3  | Order Date (ordate)         | `tr.ordate`            | `MM/DD/YYYY` |
| 4  | Customer No (custno)        | `tr.custno`            | Text         |
| 5  | Customer Name (company)     | `cu.company`           | Text         |
| 6  | Item (item)                 | `tr.item`              | Text         |
| 7  | Description (descrip)       | `tr.descrip`           | Text         |
| 8  | Product Line (plinid)       | `ic.plinid`            | Text         |
| 9  | Orig Qty Ordered (origqtyord)| `tr.origqtyord`       | `#,##0`      |
| 10 | Open Qty (qtyord)           | `tr.qtyord`            | `#,##0`      |
| 11 | Qty Shipped (qtyshp)        | `tr.qtyshp`            | `#,##0`      |
| 12 | Unit Price (price)          | `tr.price`             | `$#,##0.00`  |
| 13 | Discount % (disc)           | `tr.disc`              | `0.000`      |
| 14 | Open Amount (calculated)    | `qtyord × price × (1-disc/100)` | `$#,##0.00` |
| 15 | Line Status (sostat)        | `tr.sostat`            | Text         |
| 16 | Order Type (sotype)         | `tr.sotype`            | Text         |
| 17 | Release (release)           | `sm.release`           | Text         |
| 18 | Salesman (salesmn)          | `tr.salesmn`           | Text         |
| 19 | Territory (mapped)          | Python-mapped          | Text         |
| 20 | Terr Code (resolved)        | `CASE cu.terr/sm.terr` | Text         |
| 21 | SO Mast Terr (sm.terr)      | `sm.terr`              | Text         |
| 22 | Cust Terr (cu.terr)         | `cu.terr`              | Text         |
| 23 | Location (loctid)           | `tr.loctid`            | Text         |
| 24 | Request Date (rqdate)       | `tr.rqdate`            | `MM/DD/YYYY` |
| 25 | Ship Date (shipdate)        | `tr.shipdate`          | `MM/DD/YYYY` |
| 26 | Ship Via (shipvia)          | `sm.shipvia`           | Text         |

---

## Responsive Design

All pages are fully responsive across three breakpoints. The bookings page is optimized for unattended TV display; the open orders page is optimized for desktop/tablet use; the executive dashboard is optimized for data-dense desktop use with good mobile fallback.

### Desktop (1024px+)

- Full layout with 4-column stat cards (bookings/open orders) or 5-column KPI cards (dashboard)
- Bookings: 3-column podium for top 3 in each ranking tab
- Open Orders: side-by-side territory + salesman ranking tables (50/50 grid)
- Dashboard: 2-column chart grid, full-width salesman chart
- Navigation shows full brand logo, "Portal" text, breadcrumbs, theme toggle, user name, and sign-out button
- Page container max-width: 1400px with 32px padding
- Exchange rate badge displays inline next to Canada region title

### Tablet / iPad (768–1024px)

- Scaled-down fonts and padding
- Stat/KPI card values: 20px, podium amounts: 16px
- Ranking tables remain side-by-side on open orders
- Dashboard chart cards: 16px padding, 280px min-height
- Filter grid: 3-column layout
- Navigation hides breadcrumbs on smaller tablets
- Exchange rate badge scales down (10px font)

### Phone / iPhone (under 480px)

- **Stat/KPI cards** switch to **2×2 grid** with large **26px values** for easy reading
- Dashboard 5th KPI card spans full width via `grid-column: 1 / -1`
- Bookings podium medals shrink to 28px, text to 11–13px
- **Ranking tabs** shrink to `padding: 5px 10px; font-size: 11px` — all three tabs fit on one line
- Open orders ranking tables **stack vertically** (territory above salesman)
- Dashboard charts **stack vertically** (1-column grid)
- **Back links** render as **button-style touch targets**: `font-size: 14px; padding: 10px 14px; background: var(--bg-card); border: 1px solid var(--border); border-radius: var(--radius-md)` — easy to tap
- Export button labels hidden (icon-only); per-region export buttons also collapse to icon-only
- Filter panel: 2-column select layout, 14px padding
- Navigation shows only logo icon, theme toggle, avatar circle, and sign-out button
- Exchange rate badge wraps below region title (9px font)
- `user-scalable=no` prevents accidental pinch-zoom on TV/kiosk displays
- All spacing reduced (12px container padding)

---

## Configuration Reference

All configuration lives in `config.py`, loaded from environment variables via `python-dotenv`.

### Authentication

| Variable                | Required | Default                       | Description                      |
|-------------------------|----------|-------------------------------|----------------------------------|
| `SECRET_KEY`            | Yes      | `dev-key-change-in-production`| Flask session signing key        |
| `CLIENT_ID`             | Yes      | —                             | Azure App Registration client ID |
| `CLIENT_SECRET`         | Yes      | —                             | Azure App Registration secret    |
| `TENANT_ID`             | Yes      | —                             | Azure AD tenant ID               |
| `AUTHORITY`             | No       | Auto-built from tenant        | Full authority URL               |
| `REDIRECT_PATH`         | No       | `/auth/redirect`              | OAuth callback path              |
| `REDIRECT_URI_OVERRIDE` | No       | (empty — dynamic)             | Hardcode full redirect URI       |
| `SCOPE`                 | No       | `User.Read`                   | Microsoft Graph permissions      |

### Security Groups (Per-Report View/Export)

| Variable                       | Internal Role             | Description                     |
|--------------------------------|---------------------------|---------------------------------|
| `GROUP_ADMIN`                  | `Admin`                   | Full access to everything       |
| `GROUP_SALES_DASHBOARD_VIEW`   | `Sales.Dashboard.View`    | Executive dashboard             |
| `GROUP_SALES_BOOKINGS_VIEW`    | `Sales.Bookings.View`     | View Daily Bookings             |
| `GROUP_SALES_BOOKINGS_EXPORT`  | `Sales.Bookings.Export`   | Download Bookings Excel         |
| `GROUP_SALES_OPENORDERS_VIEW`  | `Sales.OpenOrders.View`   | View Open Orders                |
| `GROUP_SALES_OPENORDERS_EXPORT`| `Sales.OpenOrders.Export`  | Download Open Orders Excel      |

### Database

| Variable        | Required | Default                          | Description                    |
|-----------------|----------|----------------------------------|--------------------------------|
| `DB_DRIVER`     | No       | `{ODBC Driver 18 for SQL Server}`| SQL Server ODBC driver         |
| `DB_SERVER`     | Yes      | —                                | SQL Server hostname            |
| `DB_UID`        | Yes      | —                                | SQL Server username            |
| `DB_PWD`        | Yes      | —                                | SQL Server password            |
| `DB_TRUST_CERT` | No       | `yes`                            | Trust self-signed SSL certs    |
| `DB_AUTH`       | No       | `PRO12`                          | Auth database name             |
| `DB_ORDERS`     | No       | `PRO05`                          | US orders database name        |
| `DB_ORDERS_CA`  | No       | `PRO06`                          | Canada orders database name    |

### Application

| Variable                    | Required | Default    | Description                             |
|-----------------------------|----------|------------|-----------------------------------------|
| `DATA_REFRESH_INTERVAL`     | No       | `600`      | Bookings refresh interval (seconds)     |
| `OPEN_ORDERS_REFRESH_INTERVAL` | No    | `3600`     | Open orders refresh interval (seconds)  |

### Internal Constants (config.py)

| Setting                  | Value           | Description                             |
|--------------------------|-----------------|-----------------------------------------|
| `CACHE_TYPE`             | FileSystemCache | Cache backend type                      |
| `CACHE_DIR`              | `cache-data/`   | Cache file directory                    |
| `CACHE_DEFAULT_TIMEOUT`  | `900` (15 min)  | Safety net TTL for cached items         |
| `SCHEDULER_API_ENABLED`  | `False`         | APScheduler REST API disabled           |

### Internal Constants (data_worker.py)

| Setting             | Value  | Description                                   |
|---------------------|--------|-----------------------------------------------|
| `DEFAULT_CAD_TO_USD`| `0.72` | Fallback exchange rate if all APIs fail        |
| `OO_CACHE_TIMEOUT`  | `3900` | Open orders cache TTL (65 min safety buffer)   |

---

## Setup & Installation

### Prerequisites

- Python 3.12 or higher
- ODBC Driver 18 for SQL Server
- An Azure App Registration with `User.Read` permission, a client secret, and `groups` claim configured in Token Configuration
- Entra ID Security Groups created and their Object IDs set in `.env`
- Network access to the SQL Server instance (port 1433)
- Outbound HTTPS access to `api.frankfurter.app`, `open.er-api.com` (exchange rates), and `cdnjs.cloudflare.com` (Chart.js CDN)

### 1. Clone the Repository

```bash
git clone https://github.com/your-org/twg_portal.git
cd twg_portal
```

### 2. Create Virtual Environment

```bash
python -m venv venv
source venv/bin/activate        # macOS/Linux
venv\Scripts\activate           # Windows
```

### 3. Install Dependencies

```bash
pip install -r requirements.txt
```

### 4. Verify ODBC Driver

```bash
python -c "import pyodbc; print([d for d in pyodbc.drivers() if 'SQL Server' in d])"
```

Expected output: `['ODBC Driver 18 for SQL Server']`

---

## Environment Variables

Create a `.env` file in the project root:

```env
# Flask
SECRET_KEY=your-secure-random-key

# Microsoft Entra ID
CLIENT_ID=your-azure-client-id
CLIENT_SECRET=your-azure-client-secret
TENANT_ID=your-azure-tenant-id
AUTHORITY=https://login.microsoftonline.com/your-tenant-id
SCOPE=User.Read

# SQL Server
DB_DRIVER={ODBC Driver 18 for SQL Server}
DB_SERVER=your-sql-server.domain.com
DB_UID=your-sql-username
DB_PWD=your-sql-password
DB_TRUST_CERT=yes
DB_AUTH=PRO12
DB_ORDERS=PRO05
DB_ORDERS_CA=PRO06

# Security Group Object IDs (per-report View/Export)
GROUP_ADMIN=xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
GROUP_SALES_DASHBOARD_VIEW=xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
GROUP_SALES_BOOKINGS_VIEW=xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
GROUP_SALES_BOOKINGS_EXPORT=xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
GROUP_SALES_OPENORDERS_VIEW=xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
GROUP_SALES_OPENORDERS_EXPORT=xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
```

> **Important:** The `.env` file is listed in `.gitignore` and must never be committed.

> **Windows note:** If Windows won't let you create a file named `.env`, name it `_env` instead. The config loader automatically tries `_env` as a fallback.

---

## Running the Application

### Development

```bash
python app.py
```

The app starts on `http://localhost:5000`. On startup you will see config validation, group mapping logs, scheduler initialization, exchange rate fetch, and both bookings + open orders cache population.

### Production (Waitress)

```bash
waitress-serve --host=0.0.0.0 --port=5000 app:create_app
```

---

## Deployment Notes

- **Session security:** In production, set `SECRET_KEY` to a strong random value (e.g., `python -c "import secrets; print(secrets.token_hex(32))"`)
- **Redirect URIs:** Register all environment URLs in the Azure App Registration under Authentication → Web platform.
- **HTTPS:** Use a reverse proxy (nginx, IIS) with SSL termination in front of Waitress. The app handles http→https conversion automatically.
- **Firewall:** Ensure outbound HTTPS (port 443) is open to `api.frankfurter.app`, `open.er-api.com`, and `cdnjs.cloudflare.com`. Ensure inbound to SQL Server on port 1433.
- **TV Displays:** Open `/sales/bookings` in a full-screen browser (kiosk mode). Auto-refreshes every 10 minutes. The theme preference persists — set to dark mode for OLED TVs.
- **SQL Server Load:** Bookings: 4 queries per cycle × 6 cycles/hour = 24/hour. Open orders: 4 queries per cycle × 1 cycle/hour = 4/hour. Total: ~28 lightweight `SELECT` queries per hour with `NOLOCK`, regardless of user count.
- **Groups claim:** After adding the `groups` optional claim in the App Registration's Token Configuration, and assigning users to Security Groups, users must log out and log back in for new group memberships to appear in the token.
- **Chart.js CDN:** The executive dashboard loads Chart.js from `cdnjs.cloudflare.com`. If this is blocked by your firewall, charts will not render.

---

## HTTPS & Redirect URI Handling

When Flask runs behind a reverse proxy with SSL termination, `request.url_root` returns `http://` even though the user accessed via `https://`. The `_build_redirect_uri()` function handles this:

```
1. If REDIRECT_URI_OVERRIDE is set in .env → use it verbatim
2. Else build from request.url_root:
   a. Extract the hostname
   b. If hostname is NOT localhost/127.0.0.1 → force https://
   c. If hostname IS localhost/127.0.0.1 → keep http://
3. Append /auth/redirect
```

| Environment                          | `request.url_root`                     | Redirect URI Sent to Azure              |
|--------------------------------------|----------------------------------------|-----------------------------------------|
| Local dev                            | `http://localhost:5000/`               | `http://localhost:5000/auth/redirect`   |
| Dev server (behind proxy)            | `http://dev.thewheelgroup.info/`       | `https://dev.thewheelgroup.info/auth/redirect` |
| Production (behind proxy)            | `http://portal.thewheelgroup.info/`    | `https://portal.thewheelgroup.info/auth/redirect` |

---

## URL Reference

| Route                          | Method | Auth Required | Role Required              | Description                                |
|--------------------------------|--------|---------------|----------------------------|--------------------------------------------|
| `/login_page`                  | GET    | No            | —                          | Login page with Microsoft SSO button       |
| `/login`                       | GET    | No            | —                          | Initiates OAuth flow                       |
| `/auth/redirect`               | GET    | No            | —                          | OAuth callback                             |
| `/logout`                      | GET    | No            | —                          | Clears session, redirects to MS logout     |
| `/`                            | GET    | Yes           | —                          | Department hub (role-aware card visibility)|
| `/sales`                       | GET    | Yes           | `Sales.Base`               | Sales report menu (role-aware badges)      |
| `/sales/dashboard`             | GET    | Yes           | `Sales.Dashboard.View`     | Executive dashboard with charts + filters  |
| `/sales/dashboard/filter`      | POST   | Yes           | `Sales.Dashboard.View`     | AJAX: filtered dashboard data              |
| `/sales/bookings`              | GET    | Yes           | `Sales.Bookings.View`      | Daily bookings (ranking tabs, auto-refresh)|
| `/sales/bookings/export`       | GET    | Yes           | `Sales.Bookings.Export`    | Excel: bookings US + CA combined           |
| `/sales/bookings/export/us`    | GET    | Yes           | `Sales.Bookings.Export`    | Excel: bookings US only                    |
| `/sales/bookings/export/ca`    | GET    | Yes           | `Sales.Bookings.Export`    | Excel: bookings CA only                    |
| `/sales/open-orders`           | GET    | Yes           | `Sales.OpenOrders.View`    | Open orders (territory + salesman rankings)|
| `/sales/open-orders/export`    | GET    | Yes           | `Sales.OpenOrders.Export`  | Excel: open orders US + CA combined        |
| `/sales/open-orders/export/us` | GET    | Yes           | `Sales.OpenOrders.Export`  | Excel: open orders US only                 |
| `/sales/open-orders/export/ca` | GET    | Yes           | `Sales.OpenOrders.Export`  | Excel: open orders CA only                 |

> **Note:** The `Admin` role bypasses all role checks. Users with `Admin` can access every route.

---

## Troubleshooting

### AADSTS50011 — Redirect URI Mismatch

**Symptom:** Azure error page says redirect URI doesn't match.
**Cause:** `http://` sent but only `https://` registered in Azure.
**Fix:** `_build_redirect_uri()` should handle this. If not, set `REDIRECT_URI_OVERRIDE` in `.env`.

### AADSTS50148 — PKCE Mismatch

**Symptom:** Authentication fails after the Microsoft login page.
**Cause:** Using wrong MSAL method.
**Fix:** Ensure `acquire_token_by_auth_code_flow()` is used (not `acquire_token_by_authorization_code()`).

### 403 Forbidden — Access Denied

**Symptom:** User sees "Access Denied" message.
**Cause:** Missing security group membership.
**Fix:** Add user to the correct Entra Security Group. User must log out and log back in.

### No Report Cards Visible on Sales Menu

**Symptom:** Sales menu page is empty (no cards shown).
**Cause:** `GROUP_*` env vars not set, or user not added to any Sales groups.
**Fix:** Check `.env` has correct Object IDs. Verify user is in at least one `Sales-*-View` group.

### Empty Dashboard — "Unable to load data"

**Symptom:** Dashboard shows error banner and no data.
**Cause:** SQL Server unreachable, or initial startup refresh failed.
**Fix:** Check SQL Server connectivity, credentials, console logs. Next scheduled refresh will retry.

### Exchange Rate Shows 0.7200

**Symptom:** Badge always shows exactly `0.7200`.
**Cause:** Both exchange rate APIs unreachable (firewall blocking outbound HTTPS).
**Fix:** Allow outbound to `api.frankfurter.app` and `open.er-api.com`. The 0.72 fallback is still reasonable.

### Charts Not Loading on Executive Dashboard

**Symptom:** Dashboard shows empty chart areas, no visualizations.
**Cause:** Chart.js CDN (`cdnjs.cloudflare.com`) blocked by firewall.
**Fix:** Allow outbound HTTPS to `cdnjs.cloudflare.com`.

### Theme Flashes Wrong Color on Page Load

**Symptom:** Brief flash of light mode before dark mode applies (or vice versa).
**Cause:** Synchronous theme script missing from `<head>`.
**Fix:** Ensure every page has the inline `<script>` block in `<head>` that reads `localStorage.getItem('twg-theme')` before paint.

### Windows: Cannot Create .env File

**Symptom:** Windows Explorer won't let you create a file named `.env`.
**Fix:** Name it `_env` instead. The config loader tries `.env` first, then falls back to `_env`.

---

## Roadmap

| Module           | Status        | Description                                    |
|------------------|---------------|------------------------------------------------|
| **Sales**        |               |                                                |
| Executive Dashboard | ✅ Live     | KPIs, Chart.js charts (territory/product line/salesman), filter panel with AJAX, top 20 customers, region split |
| Daily Bookings   | ✅ Live        | Territory/Salesman/Customer ranking tabs with podium, auto-refresh for TV, CAD→USD, Excel export (role-gated) |
| Open Sales Orders| ✅ Live        | Territory + salesman side-by-side rankings, released tracking, hourly refresh, CAD→USD, Excel export (role-gated) |
| Shipments        | 🔜 Planned    | Daily shipments by warehouse                   |
| Territory Perf   | 🔜 Planned    | Monthly trends with period comparison          |
| **Warehouse**    | 🔜 Planned    | Inventory levels, fulfillment tracking         |
| **Accounting**   | 🔜 Planned    | Invoices, payments, financial reporting        |
| **HR**           | 🔜 Planned    | Employee directory, attendance                 |

---

## License

Internal use only — The Wheel Group.