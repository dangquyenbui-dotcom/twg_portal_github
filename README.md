# TWG Portal

A secure, enterprise-grade internal portal for **The Wheel Group**, built with Flask and integrated with Microsoft Entra ID (SSO) for authentication and Microsoft SQL Server for real-time ERP data.

The portal serves as a centralized dashboard hub for multiple departments — starting with **Sales** — providing live KPIs, territory rankings, real-time currency conversion, Excel data exports, and auto-refreshing displays optimized for desktop monitors, tablets, phones, and unattended TV/kiosk screens.

---

## Table of Contents

- [Architecture Overview](#architecture-overview)
- [Tech Stack](#tech-stack)
- [Project Structure](#project-structure)
- [Authentication Flow](#authentication-flow)
- [Data Architecture](#data-architecture)
- [Sales Module](#sales-module)
- [Currency Conversion](#currency-conversion)
- [Excel Export](#excel-export)
- [Responsive Design](#responsive-design)
- [Configuration Reference](#configuration-reference)
- [Setup & Installation](#setup--installation)
- [Environment Variables](#environment-variables)
- [Running the Application](#running-the-application)
- [Deployment Notes](#deployment-notes)
- [URL Reference](#url-reference)
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
                                           ┌────────┴────────┐
                                           │  APScheduler     │
                                           │  Background      │
                                           │  Worker (10 min) │
                                           └──┬──────────┬───┘
                                              │          │
                                    ┌─────────┴──┐  ┌───┴──────────┐
                                    │ SQL Server  │  │ Exchange Rate│
                                    │ PRO05 (US)  │  │ API (CAD→USD)│
                                    │ PRO06 (CA)  │  └──────────────┘
                                    └─────────────┘
```

**How it works:**

1. **Background Worker (APScheduler)** — Runs every 10 minutes. Executes optimized SQL queries against both the US (PRO05) and Canada (PRO06) ERP databases, fetches the live CAD→USD exchange rate from a public API, and stores all results in a file-based cache. Also runs once immediately on app startup so the cache is never empty.
2. **Cache Layer (Flask-Caching)** — Stores the latest data snapshots using `FileSystemCache`. Survives brief app restarts. Each cache entry includes a `last_updated` timestamp so users know the data freshness. Separate cache keys are used for US data, Canada data, the exchange rate, and the refresh timestamp.
3. **Web App (Flask)** — Serves the UI. Route handlers **never** query SQL directly — they read exclusively from cache, ensuring sub-millisecond response times regardless of SQL Server load. The only exception is the Excel export routes, which query SQL directly to pull full line-item detail for download.
4. **Auto-Refresh (Client-Side)** — The bookings page includes a `<meta http-equiv="refresh">` tag that reloads the page every 10 minutes, plus a live JavaScript countdown timer. This is designed for TVs/monitors in the sales area that display the dashboard unattended.

---

## Tech Stack

| Layer            | Technology                                        |
|------------------|---------------------------------------------------|
| **Backend**      | Python 3.12+, Flask 3.0                           |
| **Auth (SSO)**   | Microsoft Entra ID (Azure AD), MSAL for Python    |
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
├── app.py                    # Application factory, SSO routes, scheduler init
├── config.py                 # All configuration (auth, DB, cache, scheduler)
├── extensions.py             # Shared Flask extensions (Cache, APScheduler)
├── requirements.txt          # Python dependencies
├── .env                      # Environment variables (secrets — never committed)
├── .gitignore                # Git exclusions
├── README.md                 # This file
│
├── auth/
│   └── entra_auth.py         # MSAL helper: build app, token exchange
│
├── routes/
│   ├── main.py               # Home page (department hub), login page
│   └── sales.py              # Sales blueprint: bookings dashboard + Excel exports
│
├── services/
│   ├── db_service.py         # SQL Server queries, Python-side aggregation, territory mapping
│   └── data_worker.py        # Background cache refresh logic + exchange rate fetching
│
├── static/
│   └── logo/
│       └── TWG.png           # Company logo used in nav and login page
│
├── templates/
│   ├── base.html             # Shared layout: nav, avatar, breadcrumbs, responsive
│   ├── login.html            # Microsoft SSO login page
│   ├── index.html            # Department hub (Sales, Warehouse, Accounting, HR)
│   └── sales/
│       ├── index.html        # Sales report menu (Bookings, Shipments, etc.)
│       └── bookings.html     # Daily Bookings dashboard (US + CA, auto-refresh)
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
  ├── Build MSAL ConfidentialClientApplication
  ├── initiate_auth_code_flow() with dynamic redirect_uri
  └── Redirect user to Microsoft login page
        │
        ▼
  User authenticates with Microsoft
        │
        ▼
  GET /auth/redirect  (callback)
  ├── Exchange auth code for tokens via acquire_token_by_auth_code_flow()
  ├── Extract id_token_claims (name, email, oid, tid)
  ├── Store user info in session (signed cookie)
  └── Redirect to home page (/)
        │
        ▼
  GET /logout
  ├── Clear Flask session
  └── Redirect to Microsoft logout endpoint
```

**Key implementation details:**

- `acquire_token_by_auth_code_flow()` is used instead of `acquire_token_by_authorization_code()` to properly handle PKCE verification, preventing AADSTS50148 errors.
- Redirect URIs are built dynamically from `request.url_root` — no hardcoded `localhost` URLs.
- The `.env` file loader has a fallback from `.env` to `_env` to handle Windows filename quirks.
- Config validation runs at startup and raises `SystemExit` with clear error messages if any required values are missing.
- User session stores `name`, `email`, `oid`, and `tid` from the Microsoft ID token claims.

**Required Azure App Registration settings:**

- **Platform:** Web
- **Redirect URI:** `http://localhost:5000/auth/redirect` (dev) or your production URL
- **API Permissions:** `User.Read` (Microsoft Graph)
- **Client Secret:** Generate under Certificates & secrets

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

1. **SQL pulls minimal filtered rows** — A single query per region fetches `sono`, `units`, `amount`, `terr_code`, `custno`, and `plinid` for today's orders. No aggregation functions run on the database.
2. **Python handles all aggregation** — `_aggregate_bookings()` in `db_service.py` processes the raw rows, filtering out excluded customers and TAX line items, mapping territory codes to display names, computing sums, counting distinct orders, and building the ranked territory list.
3. **All monetary amounts are rounded up** — `math.ceil()` is applied to every dollar figure (summary totals and individual territory totals) so the dashboard always shows whole numbers with no decimal places.

**Why this approach?**

- Keeps SQL Server resource usage minimal (single table scan with simple joins)
- Avoids blocking the ERP's transactional tables (WITH NOLOCK on every join)
- Shifts CPU-intensive work (aggregation, sorting, ranking) to the app server
- Makes it easy to change business logic (territory mapping, exclusions, rounding) without modifying SQL

### Territory Mapping

Territory codes from the ERP are mapped to human-readable names in Python:

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

**Territory resolution logic:** If the customer's territory (`cu.terr`) is `'900'` (Central Billing), that code is used. Otherwise, the sales order master territory (`sm.terr`) is used. This is handled in the SQL `CASE` expression and also in the Python aggregation.

### Excluded Data

The following are filtered out at the Python aggregation level:

**Excluded Customers:**

| Customer Code | Reason                    |
|---------------|---------------------------|
| W1VAN         | Internal warehouse account |
| W1TOR         | Internal warehouse account |
| W1MON         | Internal warehouse account |
| MISC          | Miscellaneous/test         |
| TWGMARKET     | Internal marketing         |
| EMP-US        | Employee orders            |
| TEST123       | Test account               |

**Excluded Product Lines:**

| Product Line | Reason           |
|--------------|------------------|
| TAX          | Tax line items   |

**Excluded Order Statuses/Types:**

| Field      | Excluded Values | Reason                     |
|------------|-----------------|----------------------------|
| `currhist` | `X`             | Cancelled/historical       |
| `sostat`   | `V`, `X`        | Voided, cancelled lines    |
| `sotype`   | `B`, `R`        | Blanket orders, returns    |

### Caching Strategy

| Setting              | Value           | Purpose                                              |
|----------------------|-----------------|------------------------------------------------------|
| Cache type           | FileSystemCache | Persists across brief app restarts                   |
| Cache directory      | `cache-data/`   | Auto-created, gitignored                             |
| Cache timeout        | 900s (15 min)   | Safety net — data is overwritten every 10 min anyway |
| Refresh interval     | 600s (10 min)   | Background worker schedule                           |
| Startup behavior     | Immediate       | `refresh_bookings_cache()` runs in `create_app()`    |
| Cache miss fallback  | Synchronous     | If cache is empty, fetches once before rendering     |

**Cache keys:**

| Key                      | Type       | Description                                 |
|--------------------------|------------|---------------------------------------------|
| `bookings_snapshot_us`   | `dict`     | US summary + territory ranking              |
| `bookings_snapshot_ca`   | `dict`     | Canada summary + territory ranking          |
| `bookings_last_updated`  | `datetime` | Timestamp of last successful refresh        |
| `cad_to_usd_rate`        | `float`    | Latest CAD → USD exchange rate              |

---

## Sales Module

### Department Hub (`/`)

After login, users land on the department hub — a card-based grid showing all departments. Currently **Sales** is live; Warehouse, Accounting, and HR are shown as "Coming Soon" with disabled cards. Each card has a unique accent color (Sales: blue, Warehouse: amber, Accounting: green, HR: purple).

### Sales Report Menu (`/sales`)

A report selection page with cards for each available report. Currently **Daily Bookings** is live; Daily Shipments, Territory Performance, and Top Customers are shown as "Coming Soon." Each card shows a status badge ("Live" in green or "Coming Soon" in gray).

### Daily Bookings Dashboard (`/sales/bookings`)

The main dashboard page, designed for both desktop use and unattended TV/monitor display. The page is split into two distinct regional sections: **United States** and **Canada**.

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

**Auto-refresh behavior:**

- `<meta http-equiv="refresh" content="600">` reloads the page every 10 minutes
- JavaScript countdown timer updates every second, showing minutes and seconds remaining
- Background worker refreshes cached data (US + CA + exchange rate) on the same 10-minute interval
- On app startup, an immediate refresh ensures the cache is populated with fresh data

---

## Currency Conversion

The portal provides real-time CAD to USD conversion for all Canadian booking amounts, allowing the team to immediately understand the US dollar equivalent of Canadian sales.

### How It Works

1. **Exchange rate is fetched by the background worker** — Every 10 minutes (alongside the SQL data refresh), the worker calls a public exchange rate API and caches the result.
2. **Two APIs with automatic failover:**

| Priority | API                        | Source Data | Auth Required |
|----------|----------------------------|-------------|---------------|
| Primary  | `api.frankfurter.app`      | ECB rates   | No            |
| Fallback | `open.er-api.com`          | ECB rates   | No            |

3. **Sanity check** — The returned rate is validated to be between 0.50 and 1.00 (reasonable range for CAD to USD). Out-of-range values are rejected.
4. **Hardcoded fallback** — If all APIs fail, a default rate of `0.72` is used so the dashboard never breaks.
5. **Conversion happens in Python** — The route handler (`sales.py`) multiplies each CAD amount by the cached rate and rounds up with `math.ceil()`. Both the CAD original and USD equivalent are passed to the template.

### Where Conversions Appear

| Location                    | CAD Amount     | USD Equivalent   |
|-----------------------------|----------------|------------------|
| Canada summary card         | `CAD $12,345`  | `≈ USD $8,888`   |
| Canada territory table rows | `$12,345`      | `$8,888`         |
| Exchange rate badge         | —              | `1 CAD = 0.7200 USD` |

### Amount Rounding

All monetary amounts across the entire dashboard (US and Canada) are rounded **up** using `math.ceil()`:

- `$998.55` → `$999`
- `$1,000.01` → `$1,001`
- `$0.00` → `$0`

This rounding is applied at the aggregation level in `db_service.py` (`_aggregate_bookings()`), so both the summary totals and individual territory totals are already whole numbers before they reach the template. USD equivalents for Canada are also ceiling-rounded after conversion.

---

## Excel Export

The portal provides three Excel export endpoints for downloading today's raw bookings data as formatted `.xlsx` files.

### Export Endpoints

| Route                        | Scope       | Filename Pattern                        |
|------------------------------|-------------|-----------------------------------------|
| `/sales/bookings/export`     | US + Canada | `Bookings_Raw_US_CA_YYYYMMDD.xlsx`     |
| `/sales/bookings/export/us`  | US only     | `Bookings_Raw_US_YYYYMMDD.xlsx`        |
| `/sales/bookings/export/ca`  | Canada only | `Bookings_Raw_CA_YYYYMMDD.xlsx`        |

### How Exports Work

Unlike the dashboard (which reads from cache), the export routes **query SQL Server directly** to pull the full raw line-item data. This is because the cached data is pre-aggregated and doesn't contain the line-level detail needed for a proper data export.

**Query flow:**

1. `fetch_bookings_raw()` / `fetch_bookings_raw_ca()` in `db_service.py` runs a detailed query returning 24+ columns per line item
2. `_process_raw_rows()` filters out excluded customers and TAX lines, maps territory codes, and cleans string fields
3. `_build_export_workbook()` in `sales.py` builds a formatted openpyxl workbook
4. The workbook is saved to an in-memory `BytesIO` buffer and returned as a download

### Excel Formatting

Each exported file includes:

- **Title row** — Report name and today's date (bold, 13pt)
- **Metadata row** — "Exported by {user} on {date/time}" (italic, 9pt, gray)
- **Header row** — Dark background (`#1F2937`), white text, centered, wrap text
- **Alternating row shading** — Every other row gets a light gray (`#F9FAFB`) fill
- **Money columns** — Green font (`#0A7A4F`) for ExtAmount and UnitPrice
- **Number formatting** — Currency columns use `$#,##0.00`, quantity columns use `#,##0`, dates use `MM/DD/YYYY`
- **Frozen header** — Row 4 is frozen so headers stay visible while scrolling
- **Auto-filter** — Excel filter dropdowns on every column header
- **Column widths** — Pre-set for readability (e.g., CustomerName = 30, Description = 32)

**Combined export (US + Canada)** adds a "Region" column as the first column with values `US` or `CA`.

### Export Columns (25 columns)

| #  | Header                      | Key           | Format       |
|----|-----------------------------|---------------|--------------|
| 1  | Sales Order (sono)          | SalesOrder    | Text         |
| 2  | Line# (tranlineno)          | LineNo        | `#,##0`      |
| 3  | Order Date (ordate)         | OrderDate     | `MM/DD/YYYY` |
| 4  | Customer No (custno)        | CustomerNo    | Text         |
| 5  | Customer Name (company)     | CustomerName  | Text         |
| 6  | Item (item)                 | Item          | Text         |
| 7  | Description (descrip)       | Description   | Text         |
| 8  | Product Line (plinid)       | ProductLine   | Text         |
| 9  | Qty Ordered (origqtyord)    | QtyOrdered    | `#,##0`      |
| 10 | Qty Shipped (qtyshp)        | QtyShipped    | `#,##0`      |
| 11 | Unit Price (price)          | UnitPrice     | `$#,##0.00`  |
| 12 | Ext Amount (calculated)     | ExtAmount     | `$#,##0.00`  |
| 13 | Ext Price (extprice)        | ExtPrice      | `$#,##0.00`  |
| 14 | Line Status (sostat)        | LineStatus    | Text         |
| 15 | Order Type (sotype)         | OrderType     | Text         |
| 16 | Territory (mapped)          | Territory     | Text         |
| 17 | Terr Code (resolved)        | TerrCode      | Text         |
| 18 | Tran Terr (tr.terr)         | TranTerr      | Text         |
| 19 | SO Mast Terr (sm.terr)      | SOMastTerr    | Text         |
| 20 | Cust Terr (cu.terr)         | CustTerr      | Text         |
| 21 | Salesman (salesmn)          | Salesman      | Text         |
| 22 | Location (loctid)           | Location      | Text         |
| 23 | Request Date (rqdate)       | RequestDate   | `MM/DD/YYYY` |
| 24 | Ship Date (shipdate)        | ShipDate      | `MM/DD/YYYY` |
| 25 | Ship Via (shipvia)          | ShipVia       | Text         |

---

## Responsive Design

The bookings dashboard is fully responsive across three breakpoints, designed so all content fits comfortably on any screen.

### Desktop (1024px+)

- Full layout with 4-column stat cards, 3-column podium, and ranking table
- Navigation shows full brand logo, "Portal" text, breadcrumbs, user name, and sign-out button
- Page container max-width: 1400px with 32px padding
- Both US and Canada sections visible with full detail
- Exchange rate badge displays inline next to Canada region title

### Tablet / iPad (768–1024px)

- Scaled-down fonts and padding to fit everything in one screen
- Stat card values: 20px, podium amounts: 16px
- Internal scrolling on ranking table if content overflows
- Navigation hides breadcrumbs on smaller tablets
- Exchange rate badge scales down (10px font)
- USD equivalent text: 11px

### Phone / iPhone (under 480px)

- Stat cards switch to 2x2 grid layout
- Podium medals shrink to 28px, text to 11–13px
- All spacing reduced (12px container padding)
- Navigation shows only the logo icon, avatar circle, and sign-out button
- Export button labels hidden (icon-only); per-region export buttons also collapse to icon-only
- Exchange rate badge wraps below the region title (9px font)
- USD equivalent text scales to 10px on stat cards, 9px on podium
- `user-scalable=no` prevents accidental pinch-zoom on TV/kiosk displays

---

## Configuration Reference

All configuration lives in `config.py`, loaded from environment variables via `python-dotenv`.

### Authentication

| Variable        | Required | Default                       | Description                      |
|-----------------|----------|-------------------------------|----------------------------------|
| `SECRET_KEY`    | Yes      | `dev-key-change-in-production`| Flask session signing key        |
| `CLIENT_ID`     | Yes      | —                             | Azure App Registration client ID |
| `CLIENT_SECRET` | Yes      | —                             | Azure App Registration secret    |
| `TENANT_ID`     | Yes      | —                             | Azure AD tenant ID               |
| `AUTHORITY`     | No       | Auto-built from tenant        | Full authority URL               |
| `REDIRECT_PATH` | No       | `/auth/redirect`              | OAuth callback path              |
| `SCOPE`         | No       | `User.Read`                   | Microsoft Graph permissions      |

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

| Variable               | Required | Default    | Description                           |
|------------------------|----------|------------|---------------------------------------|
| `DATA_REFRESH_INTERVAL`| No       | `600`      | Background refresh interval (seconds) |

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

---

## Setup & Installation

### Prerequisites

- Python 3.12 or higher
- ODBC Driver 18 for SQL Server ([Download](https://learn.microsoft.com/en-us/sql/connect/odbc/download-odbc-driver-for-sql-server))
- An Azure App Registration with `User.Read` permission and a client secret
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

---

## Running the Application

### Development

```bash
python app.py
```

The app starts on `http://localhost:5000`. On startup you will see:

```
INFO:config:Config validated. CLIENT_ID=effc40c2...
INFO:__main__:Scheduler started.
INFO:__main__:Scheduled 'bookings_refresh' every 600s
INFO:__main__:Running initial data refresh...
INFO:services.data_worker:Worker: Refreshing bookings cache (US + CA)...
INFO:services.db_service:US Bookings snapshot: $125,430 across 12 territories (847 raw rows processed)
INFO:services.data_worker:Worker: US bookings cache updated successfully.
INFO:services.db_service:CA Bookings snapshot: CAD $18,200 across 3 territories (96 raw rows processed)
INFO:services.data_worker:Worker: CA bookings cache updated successfully.
INFO:services.data_worker:Exchange rate fetched: 1 CAD = 0.7198 USD (from https://api.frankfurter.app/latest?from=CAD&to=USD)
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
- **Redirect URI:** Update the Azure App Registration redirect URI to match your production domain
- **HTTPS:** Use a reverse proxy (nginx, IIS) with SSL termination in front of Waitress
- **Firewall:** Ensure the app server can reach `twg-sql-01.thewheelgroup.com` on port 1433
- **Exchange Rate APIs:** Ensure outbound HTTPS (port 443) is open to `api.frankfurter.app` and `open.er-api.com`. If blocked, the fallback rate of 0.72 will be used automatically.
- **TV Displays:** Open `http://your-server:5000/sales/bookings` in a full-screen browser (kiosk mode). The page auto-refreshes every 10 minutes with no user interaction needed.

---

## URL Reference

| Route                        | Method | Auth Required | Description                          |
|------------------------------|--------|---------------|--------------------------------------|
| `/login_page`                | GET    | No            | Login page with Microsoft SSO button |
| `/login`                     | GET    | No            | Initiates OAuth flow                 |
| `/auth/redirect`             | GET    | No            | OAuth callback                       |
| `/logout`                    | GET    | No            | Clears session, redirects to MS logout |
| `/`                          | GET    | Yes           | Department hub                       |
| `/sales`                     | GET    | Yes           | Sales report menu                    |
| `/sales/bookings`            | GET    | Yes           | Daily bookings dashboard (US + CA)   |
| `/sales/bookings/export`     | GET    | Yes           | Excel export: US + Canada combined   |
| `/sales/bookings/export/us`  | GET    | Yes           | Excel export: US only                |
| `/sales/bookings/export/ca`  | GET    | Yes           | Excel export: Canada only            |

---

## Roadmap

| Module         | Status        | Description                                    |
|----------------|---------------|------------------------------------------------|
| **Sales**      | ✅ Live        | Daily bookings (US + CA), territory ranking, CAD→USD conversion, Excel export |
| Shipments      | 🔜 Planned    | Daily shipments by warehouse                   |
| Territory Perf | 🔜 Planned    | Monthly trends with period comparison          |
| Top Customers  | 🔜 Planned    | Customer ranking by revenue                    |
| **Warehouse**  | 🔜 Planned    | Inventory levels, fulfillment tracking         |
| **Accounting** | 🔜 Planned    | Invoices, payments, financial reporting        |
| **HR**         | 🔜 Planned    | Employee directory, attendance                 |

---

## License

Internal use only — The Wheel Group.