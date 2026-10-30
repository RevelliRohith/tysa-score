# TYSA Score — Telangana Yogasana Championship Management System

Official Championship Platform for the Telangana Yogasana Association (TYSA), built in strict compliance with the **Yogasana Bharat Code of Points (COP)**.

---

## 1. Technology Architecture

- **Backend**: Python 3.13 + Django 5.1 (PostgreSQL with SQLite local dev fallback, atomic transactions, row-level locking via `select_for_update`).
- **Frontend**: High-fidelity Django Templates matching the 40 visual reference specifications with Tailwind utility styling, HTMX for dynamic partial updates and drawers, and focused JavaScript for hold timers, keyboard scoring, and canvas layout editing.
- **Realtime & Audience Displays**: Django Channels (ASGI + Daphne) and Redis for stage manager batch broadcasts, judges submission tracking, and public live leaderboard synchronization.
- **Worker & Export Engine**: Celery + ReportLab + Pillow for certificate batch PDF generation, ZIP bundle generation, and official championship reports.
- **Testing & QA**: `pytest` and `pytest-django` for automated domain logic, quota constraints, draw cycling, CD correction grants, and view endpoint tests; Playwright for visual regression and screenshot capture.

---

## 2. Quick Setup & Run Instructions

### 2.1 Virtual Environment & Dependencies
```bash
# Windows PowerShell
python -m venv venv
.\venv\Scripts\activate
pip install -r requirements.txt
```

### 2.2 Database Migrations & Initial Setup
```bash
python manage.py migrate
```

### 2.3 Idempotent Asana Catalogue Import (250 Poses + 5 Base References)
```bash
python manage.py import_asanas
```

### 2.4 Seed Development Fixtures
```bash
python manage.py seed_dev_data
```

### 2.5 Start Development Server
```bash
python manage.py runserver 127.0.0.1:8000
```
Open your browser at `http://127.0.0.1:8000/`.

### 2.6 Docker Deployment (PostgreSQL 16 + Redis 7 + Daphne + Celery)
```bash
# Build and run with Docker Compose
docker compose up -d --build

# Run migrations & seed within container (if not automated in entrypoint)
docker compose exec web python manage.py migrate
docker compose exec web python manage.py import_asanas
docker compose exec web python manage.py seed_dev_data
```
Services will be accessible at:
- Web Application: `http://localhost:8000`
- PostgreSQL: `localhost:5432`
- Redis: `localhost:6379`


---

## 3. Development Test Accounts

| Role | Email | Password | Scope / Capabilities |
|---|---|---|---|
| **System Admin** | `admin@tysa.org` | `admin123` | Full access across all 40 modules, judge approvals, user roles |
| **Competition Director (CD)** | `cd@tysa.org` | `cd123` | Draw, master roll, stage push, panel assignment, CD corrections, score finalisation |
| **D1 Judge** | `d1@tysa.org` | `judge123` | Execution mark entry for Traditional and Artistic routines |
| **Time Judge** | `time@tysa.org` | `judge123` | Hold duration timers and whole routine stopwatch |
| **Evaluator** | `evaluator@tysa.org` | `judge123` | Declared vs observed asana validity verification |
| **Chief Judge** | `chief@tysa.org` | `judge123` | Completeness review and provisional score release |
| **Jury Official** | `jury@tysa.org` | `judge123` | Read-only score audit search |
| **Sample Athlete** | Public ID: `TYSA-ATH-00101` | Private Key: `TYSA-A1B2-C3D4-E5F6` | Accreditation, ID card, status |

---

## 4. Key Business & Domain Decisions Resolved

1. **Athlete Authentication**: Permanent Public Athlete ID (`TYSA-ATH-XXXXX`) + permanent high-entropy Private Access Key. No passwords or OTP flows for athletes. Private key is never displayed on public ID cards, results, or QR codes.
2. **Coach Registry**: Admin-managed coach registry with attendance and certificate status. Coaches receive no public login account or coaching portal.
3. **Judge Approvals**: System Admin directly approves universal judges. Unapproved judges cannot access scoring desks.
4. **Independent Event Configurations**: Age categories and gender divisions are configured independently for each of the 4 events (Traditional, Artistic Single, Artistic Pair, Rhythmic Pair). Pair events strictly exclude mixed pairs.
5. **CD Correction Scope**: When CD reopens a sheet, it unlocks **one selected judge's entire submission** for that one performance. All other judges, performances, and athletes remain locked. Re-locks automatically on resubmission.
6. **Public Live Display**: Pinned current performer at top ("score awaiting release"), pinned top 3 standings immediately below, and rotating cumulative table of all completed released routines.
7. **Certificate Management**: Visual coordinate canvas editor (`certifictae templete.jpg`, `edit templete.jpg`, `geerate certifictae export.jpg`). `28_Certificates_and_Printing.png` is an additional cumulative scoreboard view.

---

## 5. Automated Verification & Test Commands

```bash
# Run pytest domain & view suite
pytest

# Capture all 40 reference screenshots
python scripts/capture_reference_screenshots.py
```
All 40 captured reference verification screenshots are saved in `docs/screenshots/`.
Detailed screen-by-screen route mapping is documented in `docs/route-reference-manifest.md`.
