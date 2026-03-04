# TWG Portal

A secure, enterprise-grade internal portal for **The Wheel Group**, built with Flask and integrated with Microsoft Entra ID (SSO) for authentication and Microsoft SQL Server for real-time ERP data.

The portal serves as a centralized dashboard hub for multiple departments — starting with **Sales** — providing live KPIs, territory rankings, salesman rankings, real-time currency conversion, Excel data exports, and auto-refreshing displays optimized for desktop monitors, tablets, phones, and unattended TV/kiosk screens.

---

## Table of Contents

- [Architecture Overview](#architecture-overview)
- [Tech Stack](#tech-stack)
- [Project Structure](#project-structure)
- [Authentication Flow](#authentication-flow)
- [Role-Based Access Control (RBAC)](#role-based-access-control-rbac)
- [Data Architecture](#data-architecture)
- [Sales Module](#sales-module)
  - [Daily Bookings Dashboard](#daily-bookings-dashboard)
  - [Open Sales Orders Dashboard](#open-sales-orders-dashboard)
- [Amount Calculation & Discount Handling](#amount-calculation--discount-handling)
- [Currency Conversion](#currency-conversion)
- [Excel Exports](#excel-exports)
  - [Bookings Export Columns](#bookings-export-columns-26-columns)
  - [Open Orders Export Columns](#open-orders-export-columns-26-columns)
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
3. **Web App (Flask)** — Serves the UI. Route handlers **never** query SQL directly — they read exclusively from cache, ensuring sub-millisecond response times regardless of SQL Server load. This applies to both the dashboard pages AND the Excel export downloads. Even if 100 users click Export simultaneously, the SQL Server sees zero additional queries.
4. **Auto-Refresh (Client-Side)** — The bookings page includes a `<meta http-equiv="refresh">` tag that reloads the page every 10 minutes, plus a live JavaScript countdown timer. This is designed for TVs/monitors in the sales area that display the dashboard unattended. The open orders page does **not** auto-refresh — it is designed for on-demand desktop use and simply shows the "Last updated" timestamp.

---

## Tech Stack

| Layer            | Technology                                        |
|------------------|---------------------------------------------------|
| **Backend**      | Python 3.12+, Flask 3.0                           |
| **Auth (SSO)**   | Microsoft Entra ID (Azure AD), MSAL for Python    |
| **RBAC**         | Entra ID App Roles, custom `@require_role` decorator |
| **Database**     | Microsoft SQL Server (US: PRO05, Canada: PRO06), pyodbc |
| **Caching**      | Flask-Caching (FileSystemCache)                   |
| **Scheduler**    | Flask-APScheduler (background data refresh)       |
| **Exchange Rate**| frankfurter.app (primary), open.er-api.com (fallback) |
| **Frontend**     | Jinja2 templates, vanilla CSS/JS                  |
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
├── config.py                 # All configuration (auth, DB, cache, scheduler intervals, REDIRECT_URI_OVERRIDE)
├── extensions.py             # Shared Flask extensions (Cache, APScheduler)
├── requirements.txt          # Python dependencies
├── .env                      # Environment variables (secrets — never committed)
├── .gitignore                # Git exclusions
├── README.md                 # This file
│
├── auth/
│   ├── __init__.py
│   ├── entra_auth.py         # MSAL helper: build app, token exchange
│   └── decorators.py         # @require_role decorator for App Role enforcement
│
├── routes/
│   ├── __init__.py
│   ├── main.py               # Home page (department hub), login page
│   └── sales.py              # Sales blueprint: bookings + open orders dashboards, Excel exports
│
├── services/
│   ├── __init__.py
│   ├── constants.py          # Shared territory maps, customer exclusion sets, helper functions
│   ├── db_connection.py      # pyodbc connection factory
│   ├── db_service.py         # Legacy bookings service (retained for reference)
│   ├── bookings_service.py   # Bookings SQL queries + Python aggregation (snapshot + raw export)
│   ├── open_orders_service.py# Open orders SQL queries + Python aggregation (snapshot + raw export)
│   ├── data_worker.py        # Background cache refresh logic, exchange rate fetching, scheduler functions
│   └── excel_helper.py       # Shared Excel workbook builder (openpyxl formatting)
│
├── static/
│   └── logo/
│       └── TWG.png           # Company logo used in nav and login page
│
├── templates/
│   ├── base.html             # Shared layout: nav, avatar, breadcrumbs, responsive CSS
│   ├── login.html            # Microsoft SSO login page
│   ├── index.html            # Department hub (Sales, Warehouse, Accounting, HR) — role-aware card visibility
│   └── sales/
│       ├── index.html        # Sales report menu (Bookings, Open Orders, Shipments, etc.) — role-aware
│       ├── bookings.html     # Daily Bookings dashboard (US + CA, auto-refresh for TV)
│       └── open_orders.html  # Open Sales Orders dashboard (US + CA, territory + salesman ranking)
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
  ├── Extract id_token_claims (name, email, oid, tid, roles)
  ├── Store user info + roles in session (signed cookie)
  └── Redirect to home page (/)
        │
        ▼
  GET /logout
  ├── Clear Flask session
  └── Redirect to Microsoft logout endpoint
```

**Key implementation details:**

- `acquire_token_by_auth_code_flow()` is used instead of `acquire_token_by_authorization_code()` to properly handle PKCE verification, preventing AADSTS50148 errors.
- Redirect URIs are built by `_build_redirect_uri()` which **forces `https://`** for any non-localhost host. This is critical when running behind a reverse proxy (IIS, nginx) with SSL termination — Flask sees `http://` from `request.url_root` but Azure Entra ID requires `https://` for all redirect URIs except localhost.
- An optional `REDIRECT_URI_OVERRIDE` environment variable allows hardcoding the full redirect URI for edge cases where the dynamic builder doesn't produce the correct result.
- The `.env` file loader has a fallback from `.env` to `_env` to handle Windows filename quirks.
- Config validation runs at startup and raises `SystemExit` with clear error messages if any required values are missing.
- User session stores `name`, `email`, `oid`, `tid`, and `roles` from the Microsoft ID token claims.

**Required Azure App Registration settings:**

- **Platform:** Web
- **Redirect URIs (all three):**
  - `http://localhost:5000/auth/redirect` (local development)
  - `https://dev.thewheelgroup.info/auth/redirect` (dev/staging)
  - `https://portal.thewheelgroup.info/auth/redirect` (production)
- **API Permissions:** `User.Read` (Microsoft Graph)
- **Client Secret:** Generate under Certificates & secrets
- **App Roles:** Defined under App roles (see RBAC section below)

---

## Role-Based Access Control (RBAC)

The portal enforces page-level access control using **Microsoft Entra ID App Roles**. Roles are assigned to users (or groups) in the Azure portal and are included in the `id_token_claims.roles` array on every login. The `@require_role` decorator in `auth/decorators.py` checks for the required role before allowing access to a route.

### How It Works

```
User logs in via Microsoft SSO
        │
        ▼
  id_token_claims includes "roles": ["Sales.Base", "Sales.Bookings"]
        │
        ▼
  session["user"]["roles"] = ["Sales.Base", "Sales.Bookings"]
        │
        ▼
  User navigates to /sales/bookings
        │
        ▼
  @require_role('Sales.Bookings') checks session roles
  ├── Role found → Allow access
  ├── 'Admin' role found → Allow access (Admin bypasses all checks)
  └── Role missing → HTTP 403 Forbidden
```

### App Roles

| Role Name          | Purpose                                | Grants Access To                     |
|--------------------|----------------------------------------|--------------------------------------|
| `Admin`            | Full access to everything              | All pages (bypasses all role checks) |
| `Sales.Base`       | Access to the Sales department hub     | `/sales` (report menu)               |
| `Sales.Bookings`   | View daily bookings + export           | `/sales/bookings`, `/sales/bookings/export/*` |
| `Sales.OpenOrders` | View open orders + export              | `/sales/open-orders`, `/sales/open-orders/export/*` |

### Role Enforcement Points

| Route Pattern                | Decorator                    | Notes                          |
|------------------------------|------------------------------|--------------------------------|
| `/sales`                     | `@require_role('Sales.Base')`| Sales department hub           |
| `/sales/bookings`            | `@require_role('Sales.Bookings')` | Dashboard view            |
| `/sales/bookings/export`     | `@require_role('Sales.Bookings')` | Excel download            |
| `/sales/bookings/export/us`  | `@require_role('Sales.Bookings')` | Excel download (US only)  |
| `/sales/bookings/export/ca`  | `@require_role('Sales.Bookings')` | Excel download (CA only)  |
| `/sales/open-orders`         | `@require_role('Sales.OpenOrders')` | Dashboard view          |
| `/sales/open-orders/export`  | `@require_role('Sales.OpenOrders')` | Excel download          |
| `/sales/open-orders/export/us`| `@require_role('Sales.OpenOrders')` | Excel download (US)    |
| `/sales/open-orders/export/ca`| `@require_role('Sales.OpenOrders')` | Excel download (CA)    |

### UI Behavior

The department hub (`index.html`) and sales report menu (`sales/index.html`) use Jinja2 conditionals to show or hide cards based on the user's roles:

- Users **with** the required role (or `Admin`) see a clickable card with a green "Live" badge.
- Users **without** the required role see a dimmed, non-clickable card with a "No Access" badge.
- Departments not yet built (Warehouse, Accounting, HR) show a "Coming Soon" badge for everyone.

### Setting Up App Roles in Azure

1. Go to **Azure Portal** → **App registrations** → select your app (`effc40c2-...`)
2. Click **App roles** in the left sidebar
3. Click **Create app role** for each role:
   - Display name: `Sales Base Access`
   - Allowed member types: `Users/Groups`
   - Value: `Sales.Base`
   - Description: `Access to the Sales department hub`
4. Repeat for `Sales.Bookings`, `Sales.OpenOrders`, and `Admin`
5. Go to **Enterprise applications** → select your app → **Users and groups**
6. Click **Add user/group** → select the user → select the role(s) → **Assign**

**Important:** Roles are assigned under **Enterprise applications**, not App registrations. The App registration defines what roles exist; the Enterprise application assigns them to users.

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

1. **SQL pulls minimal filtered rows** — Queries fetch only the columns needed for aggregation (sono, qty, amount, territory code, salesman, plinid). No `GROUP BY`, `SUM`, or other aggregation functions run on the database.
2. **Python handles all aggregation** — Service modules (`bookings_service.py`, `open_orders_service.py`) process the raw rows in Python, filtering out excluded records, mapping territory codes to display names, computing sums, counting distinct orders, and building the ranked lists.
3. **All monetary amounts are rounded up** — `math.ceil()` is applied to every dollar figure (summary totals and individual territory/salesman totals) so the dashboard always shows whole numbers with no decimal places.
4. **Discount is applied in SQL** — Amount calculations use `qty × price × (1 - disc / 100)` directly in the SQL query to properly account for line-level discounts before any aggregation happens.

**Why this approach?**

- Keeps SQL Server resource usage minimal (single table scan with simple joins)
- Avoids blocking the ERP's transactional tables (`WITH (NOLOCK)` on every join)
- Shifts CPU-intensive work (aggregation, sorting, ranking) to the app server
- Makes it easy to change business logic (territory mapping, exclusions, rounding) without modifying SQL

### Territory Mapping

Territory codes from the ERP are mapped to human-readable names in Python (`services/constants.py`):

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

The following customers are filtered out of **both bookings and open orders**:

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

To minimize SQL Server load, bookings and open orders run on **separate schedules**:

| Job ID              | Interval   | What It Refreshes                     | Cache TTL |
|---------------------|------------|---------------------------------------|-----------|
| `bookings_refresh`  | 10 minutes | US bookings, CA bookings, exchange rate | 900s (15 min) |
| `open_orders_refresh`| 60 minutes | US open orders, CA open orders        | 3900s (65 min) |

On **app startup**, both jobs run once immediately via `refresh_all_on_startup()` so the cache is never empty when the first user hits the page.

**Why different intervals?**

- **Bookings** change throughout the day as new orders come in — 10-minute refresh keeps the TV display current.
- **Open orders** represent the entire backlog and change slowly — hourly refresh is sufficient and reduces SQL Server queries by 6×.

### Caching Strategy

| Setting              | Bookings        | Open Orders     | Purpose                                    |
|----------------------|-----------------|-----------------|---------------------------------------------|
| Cache type           | FileSystemCache | FileSystemCache | Persists across brief app restarts           |
| Cache directory      | `cache-data/`   | `cache-data/`   | Auto-created, gitignored                     |
| Cache timeout        | 900s (15 min)   | 3900s (65 min)  | Safety net — overwritten each refresh cycle  |
| Refresh interval     | 600s (10 min)   | 3600s (60 min)  | Background worker schedule                   |
| Startup behavior     | Immediate       | Immediate       | Both run on startup via `refresh_all_on_startup()` |
| Cache miss fallback  | Synchronous     | Synchronous     | If cache is empty, fetches once before rendering   |

**Cache keys:**

| Key                          | Type       | Description                                 |
|------------------------------|------------|---------------------------------------------|
| `bookings_snapshot_us`       | `dict`     | US bookings summary + territory ranking     |
| `bookings_snapshot_ca`       | `dict`     | Canada bookings summary + territory ranking |
| `bookings_raw_us`            | `list`     | US bookings raw line-item data for Excel export |
| `bookings_raw_ca`            | `list`     | Canada bookings raw line-item data for Excel export |
| `bookings_last_updated`      | `datetime` | Timestamp of last successful bookings refresh |
| `open_orders_snapshot_us`    | `dict`     | US open orders summary + territory + salesman ranking |
| `open_orders_snapshot_ca`    | `dict`     | Canada open orders summary + territory + salesman ranking |
| `open_orders_raw_us`         | `list`     | US open orders raw line-item data for Excel export |
| `open_orders_raw_ca`         | `list`     | Canada open orders raw line-item data for Excel export |
| `open_orders_last_updated`   | `datetime` | Timestamp of last successful open orders refresh |
| `cad_to_usd_rate`            | `float`    | Latest CAD → USD exchange rate              |

---

## Sales Module

### Department Hub (`/`)

After login, users land on the department hub — a card-based grid showing all departments. Currently **Sales** is live; Warehouse, Accounting, and HR are shown as "Coming Soon" with disabled cards. Each card has a unique accent color (Sales: blue, Warehouse: amber, Accounting: green, HR: purple).

**Role-aware visibility:** The Sales card is only clickable if the user has the `Sales.Base` or `Admin` role. Users without access see a dimmed card with a "No Access" badge instead of "Live."

### Sales Report Menu (`/sales`)

A report selection page with cards for each available report. Currently **Daily Bookings** and **Open Sales Orders** are live; Daily Shipments and Territory Performance are shown as "Coming Soon." Each card shows a status badge ("Live" in green, "No Access" in gray, or "Coming Soon" in gray).

**Role-aware visibility:** Bookings cards require `Sales.Bookings` (or `Admin`). Open Orders cards require `Sales.OpenOrders` (or `Admin`). Cards for reports the user cannot access are shown as disabled with "No Access."

---

### Daily Bookings Dashboard

**Route:** `/sales/bookings`
**Required Role:** `Sales.Bookings` or `Admin`
**Refresh:** Auto-refresh every 10 minutes (designed for TV/monitor display)
**Data Source:** `sotran` rows where `ordate = today`

The main bookings dashboard page, designed for both desktop use and unattended TV/monitor display. The page is split into two distinct regional sections: **United States** and **Canada**.

**Page Header:**

- **Title** — "Daily Bookings"
- **Date Tag** — Shows the current booking date in a styled pill (e.g., "Tuesday, February 25, 2026")
- **Export All Button** — Downloads a combined US + Canada Excel file with all raw line-item data

**Refresh Bar:**

- **Left side** — Green pulsing dot + "Last updated: 09:40 AM" timestamp
- **Right side** — Live countdown timer: "Next refresh in 8:42"

**US Section (United States — PRO05):**

1. **Region Header** — US flag icon, title "United States (PRO05)", and a per-region "Export US" download button
2. **Summary Cards** — Four KPI cards in a row:
   - **Total Booking Amount** (green, `$` prefix) — whole number, no decimals
   - **Total Units Ordered** (blue)
   - **Sales Orders** (amber) — count of distinct sales order numbers
   - **Territories Active** (white) — count of distinct territories with bookings
3. **Top 3 Podium** — The top three territories displayed as styled cards with gold/silver/bronze medal icons, gradient borders, glowing effects, and color-coded amounts (all whole numbers)
4. **Ranking Table** — Territories ranked 4th and below in a clean table with Location, Total (`$` prefix, whole numbers), and Rank columns

**Canada Section (Canada — PRO06):**

1. **Region Header** — Canadian flag icon, title "Canada (PRO06)", an "Export CA" download button, and a **live exchange rate badge** showing "1 CAD = 0.7200 USD" (purple pill with a currency swap icon)
2. **Summary Cards** — Same four KPI cards as US, but:
   - The Total Booking Amount card shows **`CAD $12,345`** with a "CAD" prefix label
   - Below the CAD amount, a smaller line shows **`≈ USD $8,888`** (converted using the live exchange rate, rounded up to whole number)
3. **Territory Ranking Table** — All Canadian territories in a table with four columns:
   - **Location** — Territory name (Vancouver, Toronto, Montreal, Others)
   - **Total (CAD)** — Amount in Canadian dollars (whole numbers)
   - **≈ USD** — Converted amount in US dollars (whole numbers, muted color)
   - **Rank** — Position number

**Bookings SQL Filter Logic:**

```sql
WHERE tr.ordate = CAST(GETDATE() AS DATE)    -- today's orders only
  AND tr.currhist <> 'X'                     -- not cancelled/historical
  AND tr.sostat  NOT IN ('V', 'X')           -- not voided or cancelled
  AND tr.sotype  NOT IN ('B', 'R')           -- no blankets or returns
```

Plus Python-side filtering: excluded customers (7 internal accounts) and TAX product lines.

**Bookings Amount Formula:**

```
Booking $ = origqtyord × price × (1 - disc / 100)
```

---

### Open Sales Orders Dashboard

**Route:** `/sales/open-orders`
**Required Role:** `Sales.OpenOrders` or `Admin`
**Refresh:** Every 60 minutes (background), no auto-refresh on client — designed for on-demand desktop use
**Data Source:** All currently open `sotran` lines (no date filter)

Displays the total value of all open (unfulfilled) sales order lines across both regions. Unlike bookings which shows today only, this report covers **all** open orders regardless of when they were placed.

**Page Header:**

- **Title** — "Open Sales Orders"
- **Tag** — "All Open Lines" (amber pill)
- **Export All Button** — Downloads a combined US + Canada Excel file

**Refresh Bar:**

- **Left side** — Green pulsing dot + "Last updated: 2:30 PM · Refreshes every hour"
- **No countdown timer** — this page is not designed for TV display

**US Section (United States — PRO05):**

1. **Region Header** — US flag icon, title, "Export US" button
2. **Summary Cards** — Four KPI cards:
   - **Total Open Amount** (green) — sum of all open line values, with a **Released** sub-line showing the dollar amount and percentage of open orders that have `somast.release = 'Y'`
   - **Open Units** (blue) — sum of `qtyord` across all open lines
   - **Open Orders** (amber) — count of distinct sales order numbers (`sono`)
   - **Open Lines** (purple) — count of individual line items
3. **Side-by-Side Rankings** — Two tables displayed in a 50/50 grid:
   - **By Territory** — Territories ranked by total open dollar value, with Open $ and Released $ columns
   - **By Salesman** — Salesmen ranked by total open dollar value, with Open $ and Released $ columns (raw salesman codes from `sotran.salesmn`)

**Canada Section (Canada — PRO06):**

1. **Region Header** — Canadian flag, "Export CA" button, live exchange rate badge (refreshed hourly)
2. **Summary Cards** — Same four cards with CAD prefix, USD equivalent on the amount card, and Released sub-line with CAD and USD equivalents
3. **Side-by-Side Rankings** — Territory and Salesman tables, each with Open $ (CAD), Released (CAD), ≈ USD, and Rank columns

**Open Orders SQL Filter Logic:**

```sql
WHERE tr.qtyord > 0                          -- still has remaining open quantity
  AND tr.sostat  NOT IN ('C', 'V', 'X')     -- line not closed, voided, or cancelled
  AND sm.sostat  <> 'C'                      -- order not fully closed
  AND tr.sotype  NOT IN ('B', 'R')           -- no blankets or returns
  -- NO date filter (all open orders regardless of age)
  -- NO currhist filter (not relevant for open orders)
```

Plus Python-side filtering: excluded customers (same 7 internal accounts as bookings) and TAX product lines.

**Open line definition explained:**

- `qtyord > 0` — The ERP updates `qtyord` as shipments go out. It represents the **remaining open quantity**, not the original quantity ordered. When `qtyord` reaches 0, the line is fully shipped.
- `sostat NOT IN ('C', 'V', 'X')` — Excludes lines that are closed (fully invoiced), voided, or cancelled at the line level.
- `somast.sostat <> 'C'` — Excludes lines belonging to orders that are fully closed at the header level (uses `INNER JOIN` to enforce this).
- `somast.release` — Tracked as a flag (`'Y'` = released). Released amounts are shown separately on the dashboard so users can see how much of the open order backlog has been approved for fulfillment.
- **No `currhist` filter** — Unlike bookings, open orders does not filter on `currhist`. The `qtyord > 0` and `sostat` filters are sufficient to identify genuinely open lines.
- **No date filter** — All open orders are included regardless of when they were placed.

**Open Amount Formula:**

```
Open $ = qtyord × price × (1 - disc / 100)
```

**Key differences from Bookings:**

| Aspect            | Bookings                    | Open Orders                    |
|-------------------|-----------------------------|--------------------------------|
| Required role     | `Sales.Bookings`            | `Sales.OpenOrders`             |
| Date filter       | Today only (`ordate = today`) | None — all open lines          |
| Refresh interval  | 10 minutes                  | 60 minutes                     |
| Auto-refresh UI   | Yes (TV/kiosk mode)         | No (on-demand desktop use)     |
| Customer exclusions| 7 internal accounts excluded| Same 7 internal accounts excluded |
| Qty field          | `origqtyord` (original qty) | `qtyord` (remaining open qty)  |
| sostat exclusion   | `V`, `X`                    | `C`, `V`, `X`                  |
| currhist filter    | `currhist <> 'X'`           | Not applied                    |
| Rankings           | Territory only              | Territory + Salesman side-by-side |
| Released tracking  | Not included                | Included (`somast.release`)    |
| Release column     | Not included                | Included in export             |
| `somast` join      | `LEFT JOIN`                 | `INNER JOIN` (enforces order-level filter) |

---

## Amount Calculation & Discount Handling

All monetary amount calculations in both reports account for line-level discounts stored in `sotran.disc`:

**Formula (both reports):**

```
Amount = quantity × price × (1 - disc / 100)
```

**Example — SO 5110994:**

| Line | Qty | Price   | Disc % | Without Discount | With Discount | ERP Extension |
|------|-----|---------|--------|------------------|---------------|---------------|
| 1    | 4   | 219.05  | 5.000  | $876.20          | **$832.39**   | $832.39 ✓     |
| 3    | 4   | 228.80  | 5.000  | $915.20          | **$869.44**   | $869.44 ✓     |

**Where the discount is applied:**

| Context                  | Formula Applied In | Notes                              |
|--------------------------|--------------------|------------------------------------|
| Bookings snapshot (dashboard) | SQL query      | `origqtyord * price * (1 - disc/100)` |
| Bookings raw export (Excel)  | SQL query      | Same formula, plus `disc` column exported |
| Open orders snapshot (dashboard) | SQL query  | `qtyord * price * (1 - disc/100)`     |
| Open orders raw export (Excel)   | SQL query  | Same formula, plus `Discount %` column exported |

When `disc = 0` (no discount), the formula simplifies to `qty × price × 1`, so orders without discounts are unaffected.

**Rounding:** After discount calculation and aggregation, all monetary totals are rounded **up** using `math.ceil()` to the nearest whole dollar. This applies to summary totals, territory totals, salesman totals, and USD equivalents.

---

## Currency Conversion

The portal provides real-time CAD to USD conversion for all Canadian amounts, allowing the team to immediately understand the US dollar equivalent of Canadian sales.

### How It Works

1. **Exchange rate is fetched by the background worker** — Every 10 minutes (with the bookings refresh), the worker calls a public exchange rate API and caches the result. The open orders page also reads this cached rate.
2. **Two APIs with automatic failover:**

| Priority | API                        | Source Data | Auth Required |
|----------|----------------------------|-------------|---------------|
| Primary  | `api.frankfurter.app`      | ECB rates   | No            |
| Fallback | `open.er-api.com`          | ECB rates   | No            |

3. **Sanity check** — The returned rate is validated to be between 0.50 and 1.00 (reasonable range for CAD to USD). Out-of-range values are rejected.
4. **Hardcoded fallback** — If all APIs fail, a default rate of `0.72` is used so the dashboard never breaks.
5. **Conversion happens in Python** — The route handler (`sales.py`, `_build_region_data()`) multiplies each CAD amount by the cached rate and rounds up with `math.ceil()`. Both the CAD original and USD equivalent are passed to the template.

### Where Conversions Appear

| Page         | Location                    | CAD Amount     | USD Equivalent   |
|--------------|-----------------------------|----------------|------------------|
| Bookings     | Canada summary card         | `CAD $12,345`  | `≈ USD $8,888`   |
| Bookings     | Canada territory table rows | `$12,345`      | `$8,888`         |
| Open Orders  | Canada summary card         | `CAD $500,000` | `≈ USD $360,000` |
| Open Orders  | Canada territory table rows | `$100,000`     | `$72,000`        |
| Open Orders  | Canada salesman table rows  | `$80,000`      | `$57,600`        |
| Both         | Exchange rate badge         | —              | `1 CAD = 0.7200 USD` |

---

## Excel Exports

The portal provides Excel export endpoints for downloading raw line-item data as formatted `.xlsx` files. Unlike earlier versions that queried SQL on every click, **all exports now read from cache** — the background worker pre-fetches and caches the raw export data alongside the dashboard snapshots. This means 100 users can click Export simultaneously with **zero SQL queries** hitting the database.

**Cache-miss safety net:** If the raw cache is empty (e.g., app just restarted and hasn't completed its first refresh), the export route triggers one synchronous fetch to populate the cache, then serves from cache for all subsequent requests.

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

### Excel Formatting (Shared)

All exported files are built by `services/excel_helper.py` with consistent formatting:

- **Title row** — Report name and today's date (bold, 13pt)
- **Metadata row** — "Exported by {user} on {date/time}" (italic, 9pt, gray)
- **Header row** — Dark background (`#1F2937`), white text, centered, wrap text
- **Alternating row shading** — Every other row gets a light gray (`#F9FAFB`) fill
- **Money columns** — Green font (`#0A7A4F`) for ExtAmount, UnitPrice, OpenAmount, ExtPrice
- **Number formatting** — Currency columns use `$#,##0.00`, quantity columns use `#,##0`, dates use `MM/DD/YYYY`, discount uses `0.000`
- **Frozen header** — Row 4 is frozen so headers stay visible while scrolling
- **Auto-filter** — Excel filter dropdowns on every column header
- **Column widths** — Pre-set for readability (e.g., CustomerName = 30, Description = 32)

**Combined exports (US + Canada)** add a "Region" column as the first column with values `US` or `CA`.

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

Both dashboard pages are fully responsive across three breakpoints. The bookings page is optimized for unattended TV display; the open orders page is optimized for desktop/tablet use.

### Desktop (1024px+)

- Full layout with 4-column stat cards and ranking tables
- Bookings: 3-column podium for top territories
- Open Orders: side-by-side territory + salesman ranking tables (50/50 grid)
- Navigation shows full brand logo, "Portal" text, breadcrumbs, user name, and sign-out button
- Page container max-width: 1400px with 32px padding
- Both US and Canada sections visible with full detail
- Exchange rate badge displays inline next to Canada region title

### Tablet / iPad (768–1024px)

- Scaled-down fonts and padding to fit everything in one screen
- Stat card values: 20px, podium amounts: 16px
- Ranking tables remain side-by-side on open orders
- Navigation hides breadcrumbs on smaller tablets
- Exchange rate badge scales down (10px font)
- USD equivalent text: 11px

### Phone / iPhone (under 480px)

- Stat cards switch to 2x2 grid layout
- Bookings podium medals shrink to 28px, text to 11–13px
- Open orders ranking tables **stack vertically** (territory above salesman)
- All spacing reduced (12px container padding)
- Navigation shows only the logo icon, avatar circle, and sign-out button
- Export button labels hidden (icon-only); per-region export buttons also collapse to icon-only
- Exchange rate badge wraps below the region title (9px font)
- `user-scalable=no` prevents accidental pinch-zoom on TV/kiosk displays

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
| `REDIRECT_URI_OVERRIDE` | No       | (empty — dynamic)             | Hardcode full redirect URI if needed |
| `SCOPE`                 | No       | `User.Read`                   | Microsoft Graph permissions      |

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
- ODBC Driver 18 for SQL Server ([Download](https://learn.microsoft.com/en-us/sql/connect/odbc/download-odbc-driver-for-sql-server))
- An Azure App Registration with `User.Read` permission, a client secret, and App Roles defined
- Network access to the SQL Server instance (port 1433)
- Outbound HTTPS access to `api.frankfurter.app` and `open.er-api.com` for exchange rates

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

# Redirect URI Override (optional — leave empty for dynamic auto-detection)
# Only set this if the auto-detection doesn't work in your environment.
# The app automatically forces https:// for non-localhost hosts.
# REDIRECT_URI_OVERRIDE=https://portal.thewheelgroup.info/auth/redirect

# SQL Server
DB_DRIVER={ODBC Driver 18 for SQL Server}
DB_SERVER=your-sql-server.domain.com
DB_UID=your-sql-username
DB_PWD=your-sql-password
DB_TRUST_CERT=yes
DB_AUTH=PRO12
DB_ORDERS=PRO05
DB_ORDERS_CA=PRO06
```

> **Important:** The `.env` file is listed in `.gitignore` and must never be committed.

> **Windows note:** If Windows won't let you create a file named `.env`, name it `_env` instead. The config loader automatically tries `_env` as a fallback.

---

## Running the Application

### Development

```bash
python app.py
```

The app starts on `http://localhost:5000`. On startup you will see:

```
INFO:config:Config validated. CLIENT_ID=effc40c2...
INFO:config:Config: No REDIRECT_URI_OVERRIDE — will build redirect_uri dynamically from request.
INFO:__main__:Scheduler started.
INFO:__main__:Scheduled 'bookings_refresh' every 600s
INFO:__main__:Scheduled 'open_orders_refresh' every 3600s
INFO:__main__:Running initial data refresh (all sources)...
INFO:services.data_worker:Worker: ═══ Initial startup refresh (all data) ═══
INFO:services.data_worker:Exchange rate fetched: 1 CAD = 0.7198 USD (from https://api.frankfurter.app/latest?from=CAD&to=USD)
INFO:services.data_worker:Worker: Refreshing bookings cache (US + CA)...
INFO:services.bookings_service:US Bookings snapshot: $125,430 across 12 territories (847 raw rows processed)
INFO:services.data_worker:Worker: US bookings snapshot cache updated.
INFO:services.bookings_service:CA Bookings snapshot: $18,200 across 3 territories (96 raw rows processed)
INFO:services.data_worker:Worker: CA bookings snapshot cache updated.
INFO:services.data_worker:Worker: Refreshing open orders cache (US + CA)...
INFO:services.open_orders_service:US Open Orders snapshot: $2,345,678 ($1,890,000 released) across 892 orders, 4120 lines (5200 raw rows processed)
INFO:services.data_worker:Worker: US open orders snapshot cache updated.
INFO:services.open_orders_service:CA Open Orders snapshot: $456,789 ($320,000 released) across 210 orders, 890 lines (1100 raw rows processed)
INFO:services.data_worker:Worker: CA open orders snapshot cache updated.
INFO:services.data_worker:Worker: ═══ Initial startup refresh complete ═══
```

### Production (Waitress)

```bash
waitress-serve --host=0.0.0.0 --port=5000 app:create_app
```

Or create a `run.py`:

```python
from waitress import serve
from app import create_app

app = create_app()
serve(app, host='0.0.0.0', port=5000)
```

---

## Deployment Notes

- **Session security:** In production, set `SECRET_KEY` to a strong random value (e.g., `python -c "import secrets; print(secrets.token_hex(32))"`)
- **Redirect URIs:** Register all environment URLs in the Azure App Registration under Authentication → Web platform. The app forces `https://` automatically for non-localhost hosts, so you only need `https://` URIs registered in Azure (plus `http://localhost:5000/auth/redirect` for local dev).
- **HTTPS:** Use a reverse proxy (nginx, IIS) with SSL termination in front of Waitress. The app's `_build_redirect_uri()` function handles the http→https conversion automatically.
- **Firewall:** Ensure the app server can reach the SQL Server on port 1433.
- **Exchange Rate APIs:** Ensure outbound HTTPS (port 443) is open to `api.frankfurter.app` and `open.er-api.com`. If blocked, the fallback rate of 0.72 will be used automatically.
- **TV Displays:** Open `https://your-server/sales/bookings` in a full-screen browser (kiosk mode). The page auto-refreshes every 10 minutes with no user interaction needed. The open orders page does NOT auto-refresh and is intended for on-demand use.
- **SQL Server Load:** The background worker handles ALL SQL queries — neither dashboards nor Excel exports hit the database at request time. Bookings refresh runs 4 queries per cycle (2 snapshot + 2 raw) × 6 cycles/hour = 24 queries/hour. Open orders runs 4 queries per cycle × 1 cycle/hour = 4 queries/hour. Total: ~28 lightweight `SELECT` queries per hour with `NOLOCK`, regardless of how many users are viewing dashboards or downloading exports.
- **App Roles:** After defining roles in the App Registration, assign them to users under Enterprise Applications → Users and groups. Users who log in before being assigned roles will see "No Access" badges on all report cards.

---

## HTTPS & Redirect URI Handling

This section documents a common deployment issue and how the app handles it.

### The Problem

When Flask runs behind a reverse proxy (IIS, nginx) with SSL termination, `request.url_root` returns `http://` even though the user accessed the site via `https://`. Microsoft Entra ID requires `https://` for all redirect URIs except `localhost`, so the OAuth flow fails with error `AADSTS50011: The redirect URI does not match`.

### The Solution

The `_build_redirect_uri()` function in `app.py` handles this automatically:

```
1. If REDIRECT_URI_OVERRIDE is set in .env → use it verbatim (for edge cases)
2. Else build from request.url_root:
   a. Extract the hostname
   b. If hostname is NOT localhost/127.0.0.1 → force https://
   c. If hostname IS localhost/127.0.0.1 → keep http:// (for local dev)
3. Append /auth/redirect
```

This means:

| Environment                          | `request.url_root`                     | Redirect URI Sent to Azure              |
|--------------------------------------|----------------------------------------|-----------------------------------------|
| Local dev                            | `http://localhost:5000/`               | `http://localhost:5000/auth/redirect`   |
| Dev server (behind proxy)            | `http://dev.thewheelgroup.info/`       | `https://dev.thewheelgroup.info/auth/redirect` |
| Production (behind proxy)            | `http://portal.thewheelgroup.info/`    | `https://portal.thewheelgroup.info/auth/redirect` |

### Azure Redirect URIs to Register

All three must be added in Azure Portal → App registrations → Authentication → Web:

- `http://localhost:5000/auth/redirect`
- `https://dev.thewheelgroup.info/auth/redirect`
- `https://portal.thewheelgroup.info/auth/redirect`

---

## URL Reference

| Route                          | Method | Auth Required | Role Required        | Description                                |
|--------------------------------|--------|---------------|----------------------|--------------------------------------------|
| `/login_page`                  | GET    | No            | —                    | Login page with Microsoft SSO button       |
| `/login`                       | GET    | No            | —                    | Initiates OAuth flow                       |
| `/auth/redirect`               | GET    | No            | —                    | OAuth callback                             |
| `/logout`                      | GET    | No            | —                    | Clears session, redirects to MS logout     |
| `/`                            | GET    | Yes           | —                    | Department hub (role-aware card visibility)|
| `/sales`                       | GET    | Yes           | `Sales.Base`         | Sales report menu (role-aware)             |
| `/sales/bookings`              | GET    | Yes           | `Sales.Bookings`     | Daily bookings dashboard (US + CA)         |
| `/sales/bookings/export`       | GET    | Yes           | `Sales.Bookings`     | Excel export: bookings US + Canada combined|
| `/sales/bookings/export/us`    | GET    | Yes           | `Sales.Bookings`     | Excel export: bookings US only             |
| `/sales/bookings/export/ca`    | GET    | Yes           | `Sales.Bookings`     | Excel export: bookings Canada only         |
| `/sales/open-orders`           | GET    | Yes           | `Sales.OpenOrders`   | Open orders dashboard (US + CA)            |
| `/sales/open-orders/export`    | GET    | Yes           | `Sales.OpenOrders`   | Excel export: open orders US + CA combined |
| `/sales/open-orders/export/us` | GET    | Yes           | `Sales.OpenOrders`   | Excel export: open orders US only          |
| `/sales/open-orders/export/ca` | GET    | Yes           | `Sales.OpenOrders`   | Excel export: open orders Canada only      |

> **Note:** The `Admin` role bypasses all role checks. Users with `Admin` can access every route.

---

## Troubleshooting

### AADSTS50011 — Redirect URI Mismatch

**Symptom:** Error page says "The redirect URI 'http://...' does not match the redirect URIs configured for the application."

**Cause:** The redirect URI sent to Azure uses `http://` but only `https://` URIs are registered (Azure requires `https://` for non-localhost).

**Fix:** The `_build_redirect_uri()` function should handle this automatically. If it doesn't:
1. Check that `app.py` has the `_build_redirect_uri()` function with the `https://` forcing logic.
2. Verify all three redirect URIs are registered in Azure (see HTTPS section above).
3. As a last resort, set `REDIRECT_URI_OVERRIDE=https://your-domain/auth/redirect` in `.env`.

### AADSTS50148 — PKCE Mismatch

**Symptom:** Authentication fails after the Microsoft login page.

**Cause:** Using `acquire_token_by_authorization_code()` instead of `acquire_token_by_auth_code_flow()`.

**Fix:** Ensure `auth/entra_auth.py` uses `acquire_token_by_auth_code_flow()` which handles PKCE automatically.

### 403 Forbidden — Access Denied

**Symptom:** User sees "Access Denied: You need the 'Sales.Bookings' role to view this page."

**Cause:** The user doesn't have the required App Role assigned.

**Fix:**
1. Go to Azure Portal → Enterprise applications → select your app → Users and groups
2. Click Add user/group → select the user → select the missing role → Assign
3. User must log out and log back in for new roles to take effect

### Empty Dashboard — "Unable to load data"

**Symptom:** Dashboard shows error banner and no data.

**Cause:** SQL Server is unreachable, or the initial startup refresh failed.

**Fix:**
1. Check SQL Server connectivity: `telnet twg-sql-01.thewheelgroup.com 1433`
2. Verify credentials in `.env`
3. Check the console logs for specific SQL error messages
4. The next scheduled refresh (10 min for bookings, 60 min for open orders) will retry automatically

### Exchange Rate Shows 0.7200

**Symptom:** The exchange rate badge always shows exactly `0.7200`.

**Cause:** Both exchange rate APIs are unreachable (likely a firewall blocking outbound HTTPS).

**Fix:** Allow outbound HTTPS to `api.frankfurter.app` and `open.er-api.com`. The 0.72 fallback rate is still a reasonable approximation and won't break the dashboard.

### Windows: Cannot Create .env File

**Symptom:** Windows Explorer won't let you create a file named `.env`.

**Fix:** Name it `_env` instead. The config loader tries `.env` first, then falls back to `_env` automatically.

---

## Roadmap

| Module           | Status        | Description                                    |
|------------------|---------------|------------------------------------------------|
| **Sales**        |               |                                                |
| Daily Bookings   | ✅ Live        | Today's bookings by territory, auto-refresh for TV, CAD→USD, Excel export, role-protected |
| Open Sales Orders| ✅ Live        | All open order lines by territory + salesman, released tracking, hourly refresh, CAD→USD, Excel export, role-protected |
| Shipments        | 🔜 Planned    | Daily shipments by warehouse                   |
| Territory Perf   | 🔜 Planned    | Monthly trends with period comparison          |
| **Warehouse**    | 🔜 Planned    | Inventory levels, fulfillment tracking         |
| **Accounting**   | 🔜 Planned    | Invoices, payments, financial reporting        |
| **HR**           | 🔜 Planned    | Employee directory, attendance                 |

---

## License

Internal use only — The Wheel Group.