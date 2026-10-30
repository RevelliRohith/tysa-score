# TYSA Score — Requirements Decisions & Architecture Log

## 1. Authority & Conflict Resolutions

This log documents official engineering and sporting requirements decisions for TYSA Score (Telangana Yogasana Championship Management System).

| Decision Area | Previous Ambiguity / Conflict | Final Resolved Decision | Rationale |
|---|---|---|---|
| **Coach Management** | Earlier draft omitted coach registry | **Admin-entered coach registry included** with attendance, academy/org, district, contact, certificate status. No coach self-service login/portal. | Authorized Admin entry matches official championship accreditation without creating credential surface. |
| **Athlete Authentication** | Email/password or SMS OTP suggested | **Permanent Public Athlete ID + Permanent Private Access Key**. No OTP, no password. Key issued upon registration with verifier stored securely. | COP & championship privacy requirements; prevents password fatigue while keeping access keys unexposed in QR/cards. |
| **Judge Approval** | Separate "Judge Approval" role in some drafts | **System Admin approves Universal Judges directly**. No separate Judge Approval role. | Simplifies governance; pending judges cannot access scoring desks until Admin approval. |
| **Event Category & Gender Configuration** | Global championship age/gender settings | **Configured independently for EACH of the 4 events** (Traditional, Artistic Single, Artistic Pair, Rhythmic Pair). | Different events have distinct age suitability and participation divisions (e.g. Traditional vs Artistic Pair). |
| **CD Correction Scope** | Single asana edit vs whole submission | **CD opens one selected judge's ENTIRE submission for one performance** (all 7 or 10 items / full criteria). | CD does not edit raw marks directly; judge resubmits their complete sheet. Other judges/athletes remain locked. |
| **Public Live Screen Layout** | Rotating list only vs pinned current | **Current performer/batch pinned at top** ("awaiting release" until approved), followed by **Top Scores pinned below**, followed by **Cumulative released table** with auto-rotation. | Essential for auditorium viewers and live stream broadcast. |
| **`28_Certificates_and_Printing.png`** | File named certificates but shows scoreboard | **Treated as an additional Cumulative Scoreboard view mode**. Real certificate design uses `certifictae templete.jpg`, `edit templete.jpg`, and `geerate certifictae export.jpg`. | Visual content in screenshot 28 is explicitly a leaderboard/scoreboard. |
| **Asana Catalogue Structure** | 255 source records | **250 selectable pose records + 5 base reference records (`BAC-A` through `BAC-E`)**. | 5 base records represent difficulty scale reference points, not selectable competition poses. |
| **Pair Event Model** | Separate entries or single pair | **One atomic pair entry containing two athlete members**. Shared BV for Rhythmic, distinct chest marks for Artistic Pair. Exclude mixed-gender pairs under current preset. | Prevents duplicate reversed pairs, broken references, or independent scheduling of pair members. |
| **Database Architecture** | LocalStorage vs Server DB | **True PostgreSQL engine (`@electric-sql/pglite` / PostgreSQL connection)** with ACID transactions, foreign keys, migration scripts, and real disk persistence. LocalStorage only stores UI preferences. | Prevents data loss; guarantees integrity for scores, quotas, and audit events. |

## 2. Event & Rules Specifications

- **Events**:
  1. Traditional Yogasana: 7 asanas (compulsory + optional based on round). Batch size: 1–5 simultaneous athletes in Prelims/Semi, max 3 in Final.
  2. Artistic Yogasana Single: 10 asanas, individual execution marks, BV multiplier/aggregation, evaluator validity.
  3. Artistic Yogasana Pair: 10 asanas, Chest A + Chest B execution marks + Formation execution mark, 10-item sequence.
  4. Rhythmic Yogasana Pair: 10 asanas, synchronized execution, shared BV, compact computed score.
- **Scoring Desks**: Competitors identified strictly by chest number. Declared vs observed poses recorded separately. Real-time hold duration capture with pause/resume and monotonic clock.
- **Stage Operations**: Finalized master roll cycling districts; Stage Manager controls batch push to assigned judges; live synchronization to second screen.
