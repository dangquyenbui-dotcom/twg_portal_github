# TWG Portal

A secure, scalable enterprise portal integrated with Microsoft Entra ID (SSO) and SQL Server.

## 🏗 Architecture
This application uses a **Decoupled Caching Architecture** to ensure high performance without overloading the ERP SQL Server.

1.  **Web App (Flask)**: Serves the UI to users. It **never** queries SQL directly for dashboards.
2.  **Cache Layer (Flask-Caching)**: Stores the latest data snapshot in memory/file.
3.  **Background Worker (APScheduler)**: Runs every X minutes (default: 1 min) to fetch fresh data from SQL and update the Cache.

## 🚀 Setup & Run

### 1. Prerequisites
* Python 3.12+
* Virtual Environment

### 2. Installation
```bash
pip install -r requirements.txt