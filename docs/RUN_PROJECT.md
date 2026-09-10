# How to run this project

Exact steps, in order, with the output you should expect at each one. Written for VS Code on
Windows, macOS or Linux.

Total time: about 15 minutes, most of it waiting for installs.

---

## Contents

1. [Prerequisites](#1-prerequisites)
2. [Open the project](#2-open-the-project)
3. [Set up Supabase](#3-set-up-supabase)
4. [Get AI provider API keys](#4-get-ai-provider-api-keys)
5. [Run the backend](#5-run-the-backend)
6. [Run the frontend](#6-run-the-frontend)
7. [Use the application](#7-use-the-application)
8. [Run the tests](#8-run-the-tests)
9. [Everyday commands](#9-everyday-commands)
10. [If something goes wrong](#10-if-something-goes-wrong)

---

## 1. Prerequisites

Check all four before starting. Open a terminal and run each command.

### Python 3.10 or newer

```bash
python --version
```

Expect `Python 3.11.5` or similar. If the command is not found, try `python3 --version`. Download
from <https://www.python.org/downloads/>. **On Windows, tick "Add Python to PATH" during
installation.**

### Node.js 18 or newer

```bash
node --version
npm --version
```

Expect `v20.11.0` and `10.2.4` or similar. Download from <https://nodejs.org> (the LTS version).

### FFmpeg

```bash
ffmpeg -version
```

Expect a first line like:

```
ffmpeg version 6.1.1 Copyright (c) 2000-2023 the FFmpeg developers
```

If it is not found:

- **Windows:** download from <https://www.gyan.dev/ffmpeg/builds/> (the "release essentials" zip),
  unzip to `C:\ffmpeg`, then add `C:\ffmpeg\bin` to your PATH and **restart your terminal**. If you
  would rather not touch PATH, set `FFMPEG_PATH=C:/ffmpeg/bin/ffmpeg.exe` in `backend/.env` later.
- **macOS:** `brew install ffmpeg`
- **Ubuntu / Debian:** `sudo apt update && sudo apt install ffmpeg`

### Accounts

- A free **Supabase** account: <https://supabase.com>
- At least one AI provider key. They are tried in this order:
  - **xAI (Grok)** — primary: <https://console.x.ai>
  - **Google Gemini** — fallback 1: <https://aistudio.google.com/apikey>
  - **Groq** — fallback 2: <https://console.groq.com/keys>

---

## 2. Open the project

1. Unzip the project folder.
2. Open **VS Code** → **File → Open Folder** → select `ai-career-intelligence-platform`.
3. Open a terminal inside VS Code with **Terminal → New Terminal** (or `` Ctrl+` ``).

You should see the folder structure in the sidebar: `backend`, `frontend`, `docs`, `README.md`.

**Recommended VS Code extensions:** Python (Microsoft), Pylance, ESLint.

---

## 3. Set up Supabase

The application stores everything here, so do this before starting the backend.

### 3.1 Create a project

1. Sign in at <https://supabase.com> and click **New project**.
2. Give it a name, for example `meeting-intelligence`.
3. Set a database password (save it somewhere, though the app does not need it).
4. Pick the region closest to you and click **Create new project**.
5. Wait a minute or two while it provisions.

### 3.2 Copy the credentials

1. In your project, go to **Project Settings** (the gear icon) → **API**.
2. Copy two values:

| On the page | Goes into |
| --- | --- |
| **Project URL** (like `https://abcdefgh.supabase.co`) | `SUPABASE_URL` |
| **`service_role`** secret key (click reveal) | `SUPABASE_SERVICE_ROLE_KEY` |

**Use the `service_role` key, not the `anon` key.** The `anon` key cannot write to these tables.

**This key is a full-access password to your database.** It goes in `backend/.env` only, never in
the frontend, and never into Git. The provided `.gitignore` already excludes `.env`.

### 3.3 Create the tables

1. In Supabase, open the **SQL Editor** in the left sidebar.
2. Click **New query**.
3. Open `backend/database/schema.sql` in VS Code, select all (`Ctrl+A`), copy.
4. Paste it into the Supabase editor and click **Run**.

You should see **Success. No rows returned.**

### 3.4 Check it worked

Open **Table Editor** in the sidebar. You should see seven tables:

```
meetings
transcript_segments
participants
action_items
decisions
key_points
meeting_summaries
```

---

## 4. Get AI provider API keys

The meeting analysis is tried against three providers in a fixed order, stopping at the first one
that answers:

```text
xAI Grok  →  Google Gemini  →  Groq
 PRIMARY      FALLBACK 1     FALLBACK 2
```

1. **xAI (Grok)** — sign in at <https://console.x.ai>, go to **API Keys**, create a key.
   Goes into `backend/.env` as `XAI_API_KEY`.
2. **Google Gemini** — create a key at <https://aistudio.google.com/apikey> (free tier available).
   Goes in as `GEMINI_API_KEY`.
3. **Groq** — create a key at <https://console.groq.com/keys> (free tier available).
   Goes in as `GROQ_API_KEY`.

Copy each key immediately — they are usually shown only once.

**At least one key is required.** Adding all three is what keeps the app working when a key expires
or a free-tier quota runs out: if Grok returns `401` or `429`, the request automatically continues
to Gemini, then to Groq. A provider whose key you leave blank is skipped entirely and never called,
so it costs nothing to omit one.

Without any key, transcription still works fully; only the summary and action items are
unavailable.

---

## 5. Run the backend

### 5.1 Move into the backend folder

```bash
cd backend
```

### 5.2 Create a virtual environment

A virtual environment keeps this project's packages separate from the rest of your system.

```bash
python -m venv venv
```

This creates a `venv` folder. It takes a few seconds.

### 5.3 Activate it

**Windows (Command Prompt):**
```bash
venv\Scripts\activate
```

**Windows (PowerShell):**
```powershell
venv\Scripts\Activate.ps1
```

If PowerShell blocks this with an execution policy error, run:
```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
```
then activate again.

**macOS / Linux:**
```bash
source venv/bin/activate
```

Your prompt should now start with `(venv)`. **You need to do this every time you open a new
terminal to work on the backend.**

### 5.4 Install the dependencies

```bash
pip install -r requirements.txt
```

This takes two to five minutes. It is downloading FastAPI, Whisper and the Supabase client.

### 5.5 Create the .env file

**Windows:**
```bash
copy .env.example .env
```

**macOS / Linux:**
```bash
cp .env.example .env
```

### 5.6 Fill in the .env file

Open `backend/.env` in VS Code and set the Supabase values plus at least one AI provider key:

```env
SUPABASE_URL=https://your-project-id.supabase.co
SUPABASE_SERVICE_ROLE_KEY=paste-your-service-role-key-here

# AI providers, tried in this order. Blank = that provider is skipped.
XAI_API_KEY=paste-your-grok-key-here
XAI_MODEL=grok-4-fast
GEMINI_API_KEY=paste-your-gemini-key-here
GEMINI_MODEL=gemini-2.0-flash
GROQ_API_KEY=paste-your-groq-key-here
GROQ_MODEL=llama-3.3-70b-versatile
```

All of these stay in `backend/.env`. **Never** put an AI provider key in `frontend/.env` — anything
there is public once the app is built.

Everything else has a working default. Two you might want to change:

```env
WHISPER_MODEL=base      # tiny is faster for testing, small is more accurate
WHISPER_LANGUAGE=       # set to "en" if your recordings are English: faster and more accurate
```

Save the file.

### 5.7 Start the server

```bash
uvicorn app.main:app --reload --port 8000
```

Expected output:

```
2026-05-01 10:00:00 | INFO | app.main | Starting AI Career Intelligence Platform v1.0.0 (development)
2026-05-01 10:00:00 | INFO | app.main | Whisper: faster-whisper / model 'base' on cpu | CORS: http://localhost:5173
INFO:     Uvicorn running on http://0.0.0.0:8000 (Press CTRL+C to quit)
```

If you see warnings about Supabase or the AI providers not being configured, your `.env` is not filled in
correctly. Check for typos and restart.

### 5.8 Verify it

Open <http://localhost:8000/api/health> in a browser. You want:

```json
{
  "success": true,
  "data": {
    "status": "ok",
    "ffmpeg_available": true,
    "grok_configured": true,
    "llm_configured": true,
    "llm_provider_chain": ["grok", "gemini", "groq"],
    "supabase_configured": true,
    "warnings": []
  }
}
```

An empty `warnings` list means everything is configured. If not, the warnings tell you exactly what
is missing.

Two more useful checks:

- <http://localhost:8000/api/health/database> — confirms the tables exist
- <http://localhost:8000/api/health/llm> — makes one real call to the primary configured
  provider and reports the rest of the chain from configuration (add `?all=true` to test them all)
- <http://localhost:8000/docs> — the full interactive API documentation

**Leave this terminal running.** The backend must stay up while you use the app.

---

## 6. Run the frontend

Open a **second terminal** in VS Code (click the `+` in the terminal panel). Leave the backend
running in the first one.

### 6.1 Move into the frontend folder

```bash
cd frontend
```

If you are still inside `backend`, use `cd ../frontend`.

### 6.2 Install the dependencies

```bash
npm install
```

One to three minutes on the first run.

### 6.3 Create the .env file

**Windows:**
```bash
copy .env.example .env
```

**macOS / Linux:**
```bash
cp .env.example .env
```

For local development, **leave `VITE_API_BASE_URL` blank**. Vite proxies `/api` to
`http://localhost:8000` automatically. You only set it when deploying.

### 6.4 Start the dev server

```bash
npm run dev
```

Expected output:

```
  VITE v5.4.11  ready in 400 ms

  ➜  Local:   http://localhost:5173/
```

### 6.5 Open it

Go to <http://localhost:5173>.

You should see the dashboard with a green **"All services ready"** badge in the top right. If it
says "Backend unreachable", the backend terminal is not running.

---

## 7. Use the application

1. Click **Upload meeting** in the sidebar.
2. Drag in an audio or video recording, or click to browse. Any meeting recording works; a
   two-to-five-minute file is ideal for a first test.
3. Optionally give it a title.
4. Click **Start processing**.
5. Watch the stages complete. **The first run is slow** because Whisper downloads its model
   (150 MB for `base`). Later runs skip this.
6. When it finishes, click **View results**.
7. On the **Transcript** tab: switch between Paragraph and Timeline, and try the download buttons.
8. Scroll down to **Check transcription accuracy**, paste a correct transcript of the same
   recording, and click **Compare transcripts**.
9. Switch to the **Meeting intelligence** tab to see the summary, action items, decisions and
   participants.

### How long should it take?

Roughly, on a normal laptop CPU with the `base` model:

| Recording length | Transcription | Analysis |
| --- | --- | --- |
| 2 minutes | 30-60 seconds | 5-15 seconds |
| 10 minutes | 3-5 minutes | 10-30 seconds |
| 60 minutes | 15-30 minutes | 30-90 seconds |

Add a few minutes to the very first run for the model download.

---

## 8. Run the tests

### Backend

```bash
cd backend
venv\Scripts\activate          # Windows
source venv/bin/activate       # macOS / Linux
pytest
```

Expected: `175 passed`.

For more detail: `pytest -v`. For one file: `pytest tests/test_accuracy.py -v`.

These tests need neither Supabase nor any AI provider key, and make no real API calls. Tests requiring FFmpeg skip themselves cleanly if
it is missing.

### Frontend

```bash
cd frontend
npm run test
```

Expected: `36 passed`.

---

## 9. Everyday commands

Once set up, this is all you need each session.

**Terminal 1 — backend:**
```bash
cd backend
venv\Scripts\activate          # Windows
source venv/bin/activate       # macOS / Linux
uvicorn app.main:app --reload --port 8000
```

**Terminal 2 — frontend:**
```bash
cd frontend
npm run dev
```

Then open <http://localhost:5173>.

Stop either server with `Ctrl+C`.

| Task | Command |
| --- | --- |
| Backend tests | `pytest` (in `backend`, venv active) |
| Frontend tests | `npm run test` (in `frontend`) |
| Production build | `npm run build` (in `frontend`) |
| Preview that build | `npm run preview` |
| API documentation | <http://localhost:8000/docs> |

---

## 10. If something goes wrong

### "python is not recognised" (Windows)

Python is not on your PATH. Reinstall it with **"Add Python to PATH"** ticked, or use `py` instead
of `python`.

### PowerShell will not activate the virtual environment

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
```
Then activate again. This only affects the current terminal.

### `ModuleNotFoundError: No module named 'fastapi'`

The virtual environment is not active — your prompt should start with `(venv)` — or the install did
not finish. Activate it and re-run `pip install -r requirements.txt`.

### "Backend unreachable" in the browser

The backend terminal is not running, or it crashed. Check terminal 1 and confirm
<http://localhost:8000/api/health> loads.

### `DATABASE_NOT_CONFIGURED`

`SUPABASE_URL` or `SUPABASE_SERVICE_ROLE_KEY` is missing or wrong in `backend/.env`. Note the file
must be named exactly `.env`, not `.env.txt`. Restart the backend after editing it.

### `DATABASE_TABLE_MISSING`

`backend/database/schema.sql` has not been run. Go back to [step 3.3](#33-create-the-tables).

### `FFMPEG_NOT_AVAILABLE`

Run `ffmpeg -version` in a new terminal. If it fails, FFmpeg is not installed or not on your PATH.
Reinstall it, restart your terminal, or set `FFMPEG_PATH` in `backend/.env`.

### The first transcription seems frozen

It is downloading the Whisper model, which is 150 MB for `base`. Watch the backend terminal; it
logs when the model is loading. This happens only once per model.

### Transcription is too slow

Set `WHISPER_MODEL=tiny` for testing, and `WHISPER_LANGUAGE=en` to skip language detection.
Restart the backend after changing `.env`.

### `EMPTY_TRANSCRIPT`

Whisper found no speech. Play the file and confirm you can hear talking. A video whose audio track
is silent produces this.

### `LLM_NOT_CONFIGURED`

No provider key is set, or the one that was tried rejected the key. Check it at <http://localhost:8000/api/health/llm>. Adding `GEMINI_API_KEY` or `GROQ_API_KEY` gives the analysis a fallback.

### Accuracy is below 90%

Expected on noisy audio with the small models. Try `WHISPER_MODEL=small`, set the language
explicitly, and test with a clearer recording. The point of the accuracy tool is to measure this
honestly rather than assume it.

### Port already in use

Something else is on 8000 or 5173. Use a different port:

```bash
uvicorn app.main:app --reload --port 8001
npm run dev -- --port 5174
```

If you change the backend port, update `VITE_DEV_PROXY_TARGET` in `frontend/.env`. If you change
the frontend port, add the new origin to `CORS_ORIGINS` in `backend/.env`.

### Ports and URLs at a glance

| What | URL |
| --- | --- |
| Frontend | <http://localhost:5173> |
| Backend | <http://localhost:8000> |
| API docs | <http://localhost:8000/docs> |
| Health check | <http://localhost:8000/api/health> |
