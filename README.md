# TWG Portal

A secure, enterprise-grade internal portal for **The Wheel Group**, built with Flask and integrated with Microsoft Entra ID (SSO) for authentication and Microsoft SQL Server for real-time ERP data.

The portal serves as a centralized dashboard hub for multiple departments — starting with **Sales** — providing live KPIs, territory rankings, and auto-refreshing displays optimized for both desktop monitors and mobile devices.

---

## Table of Contents

- [Architecture Overview](#architecture-overview)
- [Tech Stack](#tech-stack)
- [Project Structure](#project-structure)
- [Authentication Flow](#authentication-flow)
- [Data Architecture](#data-architecture)
- [Sales Module](#sales-module)
- [Responsive Design](#responsive-design)
- [Configuration Reference](#configuration-reference)
- [Setup & Installation](#setup--installation)
- [Environment Variables](#environment-variables)
- [Running the Application](#running-the-application)
- [Deployment Notes](#deployment-notes)
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
                                           └────────┬────────┘
                                                    │
                                           ┌────────┴────────┐
                                           │  SQL Server      │
                                           │  (PRO05 ERP)     │
                                           └─────────────────┘
```

**How it works:**

1. **Background Worker (APScheduler)** — Runs every 10 minutes. Executes optimized SQL queries against the ERP database and stores the results in a file-based cache. Also runs once immediately on app startup so the cache is never empty.
2. **Cache Layer (Flask-Caching)** — Stores the latest data snapshot using `FileSystemCache`. Survives brief app restarts. Each cache entry includes a `last_updated` timestamp so users know the data freshness.
3. **Web App (Flask)** — Serves the UI. Route handlers **never** query SQL directly — they read exclusively from cache, ensuring sub-millisecond response times regardless of SQL Server load.
4. **Auto-Refresh (Client-Side)** — The bookings page includes a `<meta http-equiv="refresh">` tag that reloads the page every 10 minutes, plus a live JavaScript countdown timer. This is designed for TVs/monitors in the sales area that display the dashboard unattended.

---

## Tech Stack

| Layer            | Technology                                      |
|------------------|--------------------------------------------------|
| **Backend**      | Python 3.12+, Flask 3.0                          |
| **Auth (SSO)**   | Microsoft Entra ID (Azure AD), MSAL for Python   |
| **Database**     | Microsoft SQL Server (ERP: PRO05), pyodbc         |
| **Caching**      | Flask-Caching (FileSystemCache)                   |
| **Scheduler**    | Flask-APScheduler (background data refresh)       |
| **Frontend**     | Jinja2 templates, vanilla CSS/JS                  |
| **Fonts**        | DM Sans (UI), JetBrains Mono (numbers/code)       |
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
│   └── sales.py              # Sales blueprint: /sales, /sales/bookings
│
├── services/
│   ├── db_service.py         # SQL Server queries (optimized, pre-aggregated)
│   └── data_worker.py        # Background cache refresh logic
│
├── templates/
│   ├── base.html             # Shared layout: nav, avatar, breadcrumbs, responsive
│   ├── login.html            # Microsoft SSO login page
│   ├── index.html            # Department hub (Sales, Warehouse, Accounting, HR)
│   └── sales/
│       ├── index.html        # Sales report menu (Bookings, Shipments, etc.)
│       └── bookings.html     # Daily Bookings dashboard (auto-refresh, responsive)
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

**Required Azure App Registration settings:**

- **Platform:** Web
- **Redirect URI:** `http://localhost:5000/auth/redirect` (dev) or your production URL
- **API Permissions:** `User.Read` (Microsoft Graph)
- **Client Secret:** Generate under Certificates & secrets

---

## Data Architecture

### SQL Server Connection

Connections use `pyodbc` with ODBC Driver 18 for SQL Server. The connection string is built dynamically from environment variables in `Config.get_connection_string()`.

```
Driver:   ODBC Driver 18 for SQL Server
Server:   twg-sql-01.thewheelgroup.com
Database: PRO05 (ERP orders/sales data)
Auth:     SQL Server authentication (UID/PWD)
Options:  TrustServerCertificate=yes, Timeout=30s
Locking:  All queries use WITH (NOLOCK) to avoid blocking ERP operations
```

### Query Optimization

The daily bookings query is optimized to minimize SQL Server resource usage:

- **Single round-trip, two result sets** — One `cursor.execute()` call returns both the summary row and the territory ranking. No multiple queries.
- **CTE (Common Table Expression)** — The base filter logic (date, exclusions, territory mapping) is defined once in a `WITH bookings AS (...)` CTE and reused for both result sets.
- **Server-side aggregation** — `SUM()`, `COUNT(DISTINCT)`, `GROUP BY`, and `ROW_NUMBER()` run on SQL Server. Python receives pre-aggregated data only.
- **NOLOCK hints** — Every table join uses `WITH (NOLOCK)` to prevent read locks on the ERP's transactional tables.
- **Excluded customers** — Internal/test accounts (`TWGMARKET`, `TWG`, `WHEEL1`, etc.) are filtered out at the SQL level.
- **Excluded product lines** — Tax line items (`plinid = 'TAX'`) are excluded.

**Result Set 1 — Summary:**

| Field              | Description                     |
|--------------------|---------------------------------|
| `order_date`       | Today's date                    |
| `total_amount`     | Sum of all booking amounts      |
| `total_units`      | Sum of all units ordered        |
| `total_orders`     | Count of distinct sales orders  |
| `total_territories`| Count of distinct territories   |

**Result Set 2 — Territory Ranking:**

| Field      | Description                          |
|------------|--------------------------------------|
| `location` | Territory name (mapped from code)    |
| `total`    | Sum of booking amounts               |
| `rank`     | ROW_NUMBER() ordered by total DESC   |

### Caching Strategy

| Setting              | Value         | Purpose                                              |
|----------------------|---------------|------------------------------------------------------|
| Cache type           | FileSystemCache | Persists across brief app restarts                 |
| Cache directory      | `cache-data/` | Auto-created, gitignored                             |
| Cache timeout        | 900s (15 min) | Safety net — data is overwritten every 10 min anyway |
| Refresh interval     | 600s (10 min) | Background worker schedule                           |
| Startup behavior     | Immediate     | `refresh_bookings_cache()` runs in `create_app()`    |
| Cache miss fallback  | Synchronous   | If cache is empty, fetches once before rendering     |

Cache keys:

- `bookings_snapshot` — The full data dict (`summary` + `ranking`)
- `bookings_last_updated` — Python `datetime` of the last successful refresh

---

## Sales Module

### Department Hub (`/`)

After login, users land on the department hub — a card-based grid showing all departments. Currently **Sales** is live; Warehouse, Accounting, and HR are shown as "Coming Soon" with disabled cards.

### Sales Report Menu (`/sales`)

A report selection page with cards for each available report. Currently **Daily Bookings** is live; Daily Shipments, Territory Performance, and Top Customers are shown as "Coming Soon."

### Daily Bookings (`/sales/bookings`)

The main dashboard page, designed for both desktop use and unattended TV/monitor display.

**Components:**

1. **Date Tag** — Shows the current booking date in a styled pill (e.g., "Tuesday, February 24, 2026").
2. **Last Updated / Countdown** — Left side shows the cache timestamp ("Last updated: 09:40 AM"), right side shows a live JS countdown to the next page refresh ("Next refresh in 8:42"). A green pulsing dot indicates the data feed is active.
3. **Summary Cards** — Four KPI cards in a row: Total Booking Amount (green), Total Units Ordered (blue), Sales Orders (amber), Territories Active (white).
4. **Top 3 Podium** — The top three territories displayed as styled cards with gold/silver/bronze medal icons, gradient borders, glowing effects, and color-coded amounts.
5. **Ranking Table** — Territories ranked 4th and below in a clean table with location, total, and rank columns. Sticky headers, hover highlights, monospace numbers.

**Auto-refresh behavior:**

- `<meta http-equiv="refresh" content="600">` reloads the page every 10 minutes
- JavaScript countdown timer updates every second
- Background worker refreshes cached data on the same 10-minute interval
- On app startup, an immediate refresh ensures the cache is populated with fresh data

---

## Responsive Design

The bookings dashboard is fully responsive across three breakpoints, designed so all content fits within the viewport without scrolling.

### Desktop (1024px+)

- Full layout with 4-column stat cards, 3-column podium, and ranking table
- Navigation shows full brand name, breadcrumbs, user name, and sign-out button
- Page container max-width: 1400px with 32px padding

### Tablet / iPad (768–1024px)

- Scaled-down fonts and padding to fit everything in one screen
- Stat card values: 20px, podium amounts: 16px
- Internal scrolling on ranking table if content overflows
- Navigation hides breadcrumbs on smaller tablets

### Phone / iPhone (under 480px)

- Stat cards switch to 2×2 grid layout
- Podium medals shrink to 28px, text to 11–13px
- All spacing reduced (12px container padding)
- Navigation shows only the logo icon, avatar circle, and sign-out button
- `user-scalable=no` prevents accidental pinch-zoom on TV/kiosk displays
- Full page uses `height: calc(100vh - navbar)` with `overflow: hidden`

---

## Configuration Reference

All configuration lives in `config.py`, loaded from environment variables via `python-dotenv`.

| Variable               | Required | Default                  | Description                           |
|------------------------|----------|--------------------------|---------------------------------------|
| `SECRET_KEY`           | Yes      | `dev-key-change-in-prod` | Flask session signing key             |
| `CLIENT_ID`            | Yes      | —                        | Azure App Registration client ID      |
| `CLIENT_SECRET`        | Yes      | —                        | Azure App Registration secret         |
| `TENANT_ID`            | Yes      | —                        | Azure AD tenant ID                    |
| `AUTHORITY`            | No       | Auto-built from tenant   | Full authority URL                    |
| `REDIRECT_PATH`        | No       | `/auth/redirect`         | OAuth callback path                   |
| `SCOPE`                | No       | `User.Read`              | Microsoft Graph permissions           |
| `DB_DRIVER`            | No       | `{ODBC Driver 18...}`    | SQL Server ODBC driver                |
| `DB_SERVER`            | Yes      | —                        | SQL Server hostname                   |
| `DB_UID`               | Yes      | —                        | SQL Server username                   |
| `DB_PWD`               | Yes      | —                        | SQL Server password                   |
| `DB_TRUST_CERT`        | No       | `yes`                    | Trust self-signed SSL certs           |
| `DB_AUTH`              | No       | `PRO12`                  | Auth database name                    |
| `DB_ORDERS`            | No       | `PRO05`                  | Orders/sales database name            |
| `DATA_REFRESH_INTERVAL`| No       | `600` (10 min)           | Background refresh interval (seconds) |

---

## Setup & Installation

### Prerequisites

- Python 3.12 or higher
- ODBC Driver 18 for SQL Server ([Download](https://learn.microsoft.com/en-us/sql/connect/odbc/download-odbc-driver-for-sql-server))
- An Azure App Registration with `User.Read` permission and a client secret

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
INFO:services.data_worker:Worker: Refreshing bookings cache...
INFO:services.data_worker:Worker: Bookings cache updated successfully.
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
- **TV Displays:** Open `http://your-server:5000/sales/bookings` in a full-screen browser (kiosk mode). The page auto-refreshes every 10 minutes with no user interaction needed

---

## Roadmap

| Module         | Status        | Description                                    |
|----------------|---------------|------------------------------------------------|
| **Sales**      | ✅ Live        | Daily bookings with territory ranking          |
| Shipments      | 🔜 Planned    | Daily shipments by warehouse                   |
| Territory Perf | 🔜 Planned    | Monthly trends with period comparison          |
| Top Customers  | 🔜 Planned    | Customer ranking by revenue                    |
| **Warehouse**  | 🔜 Planned    | Inventory levels, fulfillment tracking         |
| **Accounting** | 🔜 Planned    | Invoices, payments, financial reporting        |
| **HR**         | 🔜 Planned    | Employee directory, attendance                 |

---

## License

Internal use only — The Wheel Group.