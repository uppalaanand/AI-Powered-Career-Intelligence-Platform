# AI Career Intelligence Platform

Turn a meeting recording into a transcript, a summary, and a list of who agreed to do what by when.

Upload an audio or video recording. The backend validates it, extracts clean audio with FFmpeg,
transcribes it with Whisper, then sends the transcript to Grok, which returns a structured summary,
the decisions taken, and the action items with owners, deadlines and priorities. Everything is
stored in Supabase and displayed in a React interface.

**Milestone 1** (audio processing and transcription) and **Milestone 2** (LLM processing) are both
fully implemented.

---

## Table of contents

1. [What this project does](#1-what-this-project-does)
2. [Internship context](#2-internship-context)
3. [Milestone 1](#3-milestone-1--audio-processing-and-transcription)
4. [Milestone 2](#4-milestone-2--llm-processing)
5. [Features](#5-features)
6. [Architecture](#6-architecture)
7. [Technology stack](#7-technology-stack)
8. [Project structure](#8-project-structure)
9. [Prerequisites](#9-prerequisites)
10. [Environment variables](#10-environment-variables)
11. [Local setup](#11-local-setup)
12. [Supabase setup](#12-supabase-setup)
13. [AI provider setup (Grok, Gemini, Groq)](#13-ai-provider-setup-grok-gemini-groq)
14. [Whisper setup](#14-whisper-setup)
15. [FFmpeg setup](#15-ffmpeg-setup)
16. [API documentation](#16-api-documentation)
17. [Testing](#17-testing)
18. [Deployment](#18-deployment)
19. [Troubleshooting](#19-troubleshooting)
20. [Scope notes](#20-scope-notes)

---

## 1. What this project does

Meetings produce decisions and commitments that get forgotten because nobody writes them down.
This tool does the writing down.

You give it a recording. It gives you back:

- a full transcript, readable as paragraphs or as a timestamped timeline
- a summary of what the meeting was about
- the decisions the group actually made
- the action items, each with an owner, a deadline, a priority and a status
- the list of participants, de-duplicated and consistently named
- a way to measure how accurate the transcription was against a reference transcript

A deliberate design rule runs through the whole system: **when the recording does not say
something, the application says so rather than guessing.** If nobody was named as the owner of a
task, the owner field reads "Not stated". It is never filled in with a plausible-sounding name.

---

## 2. Internship context

This is an internship project built to satisfy two milestones. The original brief specified
Streamlit for the interface; the requirement here replaces it with a React single-page application,
which is what this repository implements. Nothing else about the stack was changed.

---

## 3. Milestone 1 — audio processing and transcription

| Requirement | Where it lives |
| --- | --- |
| Upload audio and video recordings | `POST /api/meetings/upload`, `frontend/src/pages/UploadPage.jsx` |
| Support MP3, WAV, M4A, AAC, FLAC, OGG, MP4, MOV, AVI, MKV, WEBM (+ more) | `backend/app/models/media_formats.py` |
| Reject unsupported, empty, oversized and corrupted files | `backend/app/services/file_validation_service.py` |
| Process media with FFmpeg | `backend/app/services/audio_service.py` |
| Transcribe with Whisper | `backend/app/services/transcription_service.py` |
| Validate the transcript before storing it | `backend/app/services/transcript_validation_service.py` |
| Store the transcript and its timed segments | `backend/app/repositories/` |
| Paragraph view and timeline view | `frontend/src/components/transcript/` |
| Download as TXT, timeline TXT, JSON and CSV | `backend/app/services/export_service.py` |
| Measure accuracy against a reference transcript | `backend/app/services/accuracy_service.py` |

### Supported formats

**Audio:** MP3, WAV, M4A, AAC, FLAC, OGG, Opus, WMA
**Video:** MP4, MOV, AVI, MKV, WEBM, WMV, MPEG, MPG, 3GP

Validation never trusts the file extension on its own. Four checks run in order, cheapest first:

1. **Extension** — is this a format the app claims to support?
2. **Size** — not empty, not larger than `MAX_UPLOAD_SIZE_MB`
3. **Content sniff** — do the file's actual first bytes agree with its extension?
4. **ffprobe** — is there really a decodable audio stream inside?

A text file renamed to `.mp3` fails step 3 or 4. A half-downloaded MP4 fails step 4. A video with
no audio track is rejected with a message explaining there is nothing to transcribe.

### Transcription accuracy

The application does not promise 90% accuracy. It gives you the tool to **measure** whether a given
recording reaches it. Paste a correct transcript, and the backend aligns it with the generated
transcript word by word and reports:

```
Transcription accuracy: 93.4%
Target: >= 90%
Status: PASSED
```

Accuracy is derived from Word Error Rate:

```
WER = (substitutions + deletions + insertions) / words in the reference transcript
Accuracy = 100 - WER
```

- **Substitutions** are words heard incorrectly ("Ravi" transcribed as "Robbie")
- **Deletions** are words that were said but missing from the transcript
- **Insertions** are words in the transcript that were never said

The interface lists every one of these individually, so a low score can be diagnosed rather than
just observed. Because insertions are counted against the reference length, a transcript that adds
many extra words can produce a WER above 100%; accuracy is therefore floored at 0 rather than going
negative. Casing, punctuation and filler words ("um", "uh") are ignored by default, so only real
wording differences count as errors.

---

## 4. Milestone 2 — LLM processing

| Requirement | Where it lives |
| --- | --- |
| Grok API integration | `backend/app/ai/providers/grok_provider.py` |
| Gemini and Groq fallback providers | `backend/app/ai/providers/` |
| Provider fallback chain | `backend/app/ai/llm_orchestrator.py` |
| Reusable LLM service | `backend/app/ai/llm_service.py` |
| Prompt templates | `backend/app/ai/prompts/meeting_intelligence_prompt.py` |
| Structured JSON output with a fixed schema | `backend/app/schemas/intelligence.py` |
| Pydantic validation of every AI response | `LLMMeetingIntelligence` |
| Long transcript handling (chunking) | `backend/app/ai/chunking.py` |
| Retry and failure handling | `LLMProvider`, `LLMService`, `LLMOrchestrator` |
| Participant mapping and de-duplication | `backend/app/services/participant_service.py` |
| Persistence to Supabase | `backend/app/repositories/intelligence_repository.py` |

### AI provider strategy

The platform uses a multi-model strategy so a single vendor's outage, expired key or exhausted
free-tier quota cannot end the workflow:

```text
xAI Grok  →  Google Gemini  →  Groq  →  controlled error
 PRIMARY      FALLBACK 1      FALLBACK 2
```

Providers are tried **one at a time, in that order**, and the chain **stops at the first valid
response** — a successful Grok call never touches Gemini or Groq. A provider whose API key is not
set is skipped without being called at all. "Valid" means the reply parsed *and* passed Pydantic
validation, so malformed AI output is never stored: it is a provider failure and the chain moves on.

| Failure | What happens |
| --- | --- |
| `401` / `403` / invalid key | next provider immediately, no retry |
| `429` / quota exhausted | next provider immediately, no further calls to that provider |
| model unavailable (`404`) | next provider |
| timeout / network failure | at most one controlled retry, then next provider |
| unusable or schema-violating JSON | one corrective re-prompt, then next provider |
| every configured provider failed | `LLM_ALL_PROVIDERS_FAILED` (HTTP 502) with a readable message |
| no provider configured at all | `LLM_NOT_CONFIGURED` (HTTP 503) naming the variables to set |

Because the accounts are on free tiers, the implementation is deliberately quota-conscious: the
normal analysis costs **one** request, providers are never called in parallel or "to compare",
there is no health-check call before real work, and an already-analysed meeting is served from
Supabase without calling any provider unless the user explicitly re-analyses it.

`intelligence.provider` in the API response says which provider answered, and the meeting detail
page shows it under the results.

Full design notes: [`docs/MULTI_MODEL_AI_FALLBACK.md`](docs/MULTI_MODEL_AI_FALLBACK.md).

### The schema the AI must satisfy

```json
{
  "summary": "",
  "key_points": [],
  "decisions": [],
  "participants": [],
  "action_items": [
    {
      "task": "",
      "assigned_to": null,
      "deadline": null,
      "priority": "medium",
      "status": "pending"
    }
  ]
}
```

If the model returns something that does not validate, it is asked again with a corrective
instruction. If it fails twice, the request returns a controlled error. Malformed AI output is
never stored.

### How hallucination is prevented

Four independent layers, because prompting alone is not enough:

1. **The prompt** states the rule explicitly and repeatedly: use only what the transcript says,
   return `null` for anything not stated, and an empty list is a correct answer.
2. **Temperature is 0.1** by default, which makes the model far more literal.
3. **The schema allows null.** `assigned_to` and `deadline` are nullable, so "not stated" is
   representable. A schema that forced a string would force the model to invent one.
4. **Post-validation** converts the many ways a model says "nothing here" (`"unknown"`, `"N/A"`,
   `"TBD"`, `"not specified"`) into a real `null`, so the interface can display "Not stated".

### Long transcripts

Long meetings are split with a sentence-aware sliding window: about 9,000 characters per chunk with
a 600-character overlap, never cutting mid-sentence. Each chunk is analysed (at most three at a
time, to stay friendly with rate limits), then the partial results are merged.

The overlap matters: an action item assigned at the end of one chunk often has its deadline stated
at the start of the next.

The merge is attempted by the model first, because it writes a better connected summary. If that
call fails or returns junk, a deterministic local merge takes over, so a long meeting still
produces a usable result. Either way the action item lists are combined and de-duplicated, because
losing a real action item is worse than showing a near-duplicate.

### Participant mapping

Names arrive from the model in inconsistent forms. The mapping rules run from safest to loosest:

| Rule | Example | Result |
| --- | --- | --- |
| Exact match after normalisation | `ravi`, `Ravi`, `RAVI`, `Dr. Ravi` | one participant |
| First name inside a full name | `Ravi` + `Ravi Kumar` | one participant, displayed as `Ravi Kumar` |
| Near-identical spelling (>= 0.90) | `Priya` + `Priyaa` | one participant |
| Everything else | `Ravi` + `Rahul` | **two** participants |

That last row is the important one. Merging two real people is a worse failure than listing one
person twice, so the similarity threshold is strict and short names are never fuzzy-matched.
Placeholder names (`Unknown`, `Speaker`, `someone`, `team`) never become participant records.

A database constraint, `unique (meeting_id, normalized_name)`, enforces this at the storage layer
too, so duplicates cannot be created even by a buggy caller.

---

## 5. Features

**Upload and processing**
- Drag-and-drop or click to browse, with client-side pre-checks
- Real upload progress (measured bytes, not an animation)
- A processing screen showing each pipeline stage as it genuinely completes
- Clear, specific error messages for every failure mode

**Transcript**
- Paragraph view for reading, with sensible paragraph breaks at pauses
- Timeline view with start and end timestamps for every segment
- Speaker labels displayed when the transcription backend provides them
- Four download formats, all generated from the real stored transcript
- Accuracy testing against a pasted reference transcript

**Meeting intelligence**
- Summary, key points and decisions
- Action items in a table with owner, deadline, priority and status
- Visual indicators for priority, status and genuinely overdue dates
- Participant list with merged aliases
- Deadlines derived from action items, never extracted separately

**Engineering**
- Consistent response envelope on every endpoint
- Stable machine-readable error codes the frontend switches on
- Generated OpenAPI documentation at `/docs`
- 175 backend tests and 36 frontend tests
- Health endpoint reporting exactly which services are configured

---

## 6. Architecture

```
                    Browser
                       |
              React (Vite) frontend
                       |  HTTPS / JSON
                       v
              FastAPI backend
                       |
     +-----------------+------------------+
     |                 |                  |
   FFmpeg           Whisper            Grok API
 (extract and     (speech to        (structured
  normalise)         text)          intelligence)
                       |
                       v
                   Supabase
                  (PostgreSQL)
```

The backend is layered, and each layer only talks to the one below it:

```
Route        HTTP concerns: paths, status codes, OpenAPI docs
  |
Controller   Per-request logic, threadpool dispatch for blocking work
  |
Service      Business logic: validation, transcription, analysis, mapping
  |
Repository   Database access, error translation
  |
Supabase
```

This is why swapping providers is a contained change. Everything vendor-specific lives in
`app/ai/providers/`, one small file per vendor behind a shared `LLMProvider` interface, and
`llm_orchestrator.py` is the only file that decides which one to call. Adding a fourth provider is
one new file plus one registry entry — no service, controller, schema or UI changes.
`transcription_service.py` plays the same role for Whisper, and already supports two different
Whisper implementations behind one interface.

### Processing states

`UPLOADED` → `VALIDATING` → `AUDIO_PROCESSING` → `TRANSCRIBING` → `TRANSCRIPT_VALIDATED` →
`AI_ANALYSIS` → `PERSISTING` → `COMPLETED`, or `FAILED` at any point.

The same strings are used in the database, the API and the React code, so there is no translation
layer where they can drift apart.

---

## 7. Technology stack

| Technology | Role | Why this one |
| --- | --- | --- |
| **React 18 + Vite** | Frontend | The required replacement for Streamlit. Vite gives instant reloads and a small production build. |
| **React Router** | Navigation | Standard client-side routing for a multi-page single-page app. |
| **Python 3.10+** | Backend language | Whisper, FFmpeg bindings and the AI ecosystem are all Python-first. |
| **FastAPI** | Web framework | Async, and it generates the OpenAPI documentation from the same type hints used for validation. |
| **Pydantic v2** | Validation | One schema definition validates API requests *and* the AI's JSON output. |
| **Uvicorn** | ASGI server | The standard production server for FastAPI. |
| **FFmpeg** | Media processing | Handles every container and codec, and normalises them to one predictable audio format. |
| **Whisper** (faster-whisper) | Speech to text | Strong multilingual accuracy, runs locally, no per-minute cost. The CTranslate2 build is ~4x faster on CPU with no PyTorch download. |
| **Grok (xAI)** | LLM | The required provider. Isolated behind a service so it could be swapped. |
| **Supabase** | Database | Managed PostgreSQL with a simple Python client. Real tables, real constraints, real foreign keys. |
| **pytest / Vitest** | Testing | The standard choice on each side. |

---

## 8. Project structure

```
ai-career-intelligence-platform/
├── backend/
│   ├── app/
│   │   ├── ai/                  LLM orchestration, chunking, prompts
│   │   │   ├── llm_orchestrator.py   Grok -> Gemini -> Groq fallback chain
│   │   │   ├── llm_service.py        prompt -> parse -> validate, for one provider
│   │   │   ├── providers/            grok / gemini / groq behind one interface
│   │   │   └── prompts/
│   │   ├── config/              Settings and logging
│   │   ├── controllers/         Per-request logic
│   │   ├── middleware/          Error handling, request ids
│   │   ├── models/              Enums and the supported-format registry
│   │   ├── repositories/        Supabase access
│   │   ├── routes/              FastAPI endpoints
│   │   ├── schemas/             Pydantic models
│   │   ├── services/            Business logic
│   │   ├── utils/               Errors, files, text, JSON, time
│   │   └── main.py              Application entrypoint
│   ├── database/
│   │   └── schema.sql           Complete Supabase schema
│   ├── tests/                   175 tests
│   ├── requirements.txt
│   └── .env.example
│
├── frontend/
│   ├── src/
│   │   ├── components/          ui, upload, processing, transcript,
│   │   │                        intelligence, meetings
│   │   ├── hooks/               useAsync, useMeetings, useProcessingPipeline,
│   │   │                        useSystemHealth, useToast
│   │   ├── layouts/             AppLayout
│   │   ├── pages/               Dashboard, Upload, Meetings, MeetingDetail
│   │   ├── services/api/        Centralised API layer
│   │   ├── test/                36 tests
│   │   ├── types/               JSDoc type definitions
│   │   └── utils/               Formatting, status, constants
│   ├── package.json
│   ├── vite.config.js
│   ├── vercel.json
│   └── .env.example
│
├── docs/
│   ├── PIPELINE_AND_FEATURES.md   How everything works, in plain language
│   └── RUN_PROJECT.md             Exact steps to run it
│
├── README.md
└── .gitignore
```

---

## 9. Prerequisites

| Tool | Version | Check with |
| --- | --- | --- |
| Python | 3.10+ | `python --version` |
| Node.js | 18+ | `node --version` |
| npm | 9+ | `npm --version` |
| FFmpeg | any recent | `ffmpeg -version` |

You will also need a free **Supabase** project and an **xAI (Grok)** API key.

---

## 10. Environment variables

Full documentation lives in `backend/.env.example` and `frontend/.env.example`. The ones that
matter most:

### Backend (`backend/.env`)

| Variable | Default | What it does |
| --- | --- | --- |
| `SUPABASE_URL` | — | Your Supabase project URL. **Required.** |
| `SUPABASE_SERVICE_ROLE_KEY` | — | Service-role key. **Required. Backend only, never the frontend.** |
| `XAI_API_KEY` | — | Grok API key. Primary AI provider. |
| `XAI_MODEL` | `grok-4-fast` | Which Grok model to call. |
| `GEMINI_API_KEY` | — | Google Gemini key. Fallback 1. Blank = Gemini is skipped. |
| `GEMINI_MODEL` | `gemini-2.0-flash` | Which Gemini model to call. |
| `GROQ_API_KEY` | — | Groq key. Fallback 2. Blank = Groq is skipped. |
| `GROQ_MODEL` | `llama-3.3-70b-versatile` | Which Groq model to call. |
| `LLM_PROVIDER_ORDER` | `grok,gemini,groq` | Fallback order. |
| `LLM_PROVIDER_MAX_ATTEMPTS` | `2` | Requests one provider may spend on one prompt. `1` = no retry. |
| `LLM_SCHEMA_RETRY_ATTEMPTS` | `2` | Corrective re-prompts when a provider returns unusable JSON. |
| `WHISPER_MODEL` | `base` | `tiny`, `base`, `small`, `medium`, `large-v3`. Bigger is more accurate and slower. |
| `WHISPER_BACKEND` | `faster-whisper` | Or `openai` for the reference implementation. |
| `WHISPER_LANGUAGE` | blank | Blank auto-detects. Setting `en` improves both speed and accuracy. |
| `MAX_UPLOAD_SIZE_MB` | `200` | Upload limit. |
| `CORS_ORIGINS` | localhost:5173 | Comma-separated allowed frontend origins. |
| `FFMPEG_PATH` | `ffmpeg` | Only needed if FFmpeg is not on your PATH. |
| `LLM_CHUNK_CHAR_SIZE` | `9000` | Characters per chunk for long transcripts. |

### Frontend (`frontend/.env`)

| Variable | Default | What it does |
| --- | --- | --- |
| `VITE_API_BASE_URL` | blank | Blank uses the Vite dev proxy. In production, the full backend URL. |
| `VITE_API_TIMEOUT_MS` | `900000` | 15 minutes, because transcription is slow. |

**Only `VITE_*` variables reach the browser, and everything in them is public once built. No secret
key ever belongs in the frontend.**

---

## 11. Local setup

Step-by-step instructions with expected output are in **[docs/RUN_PROJECT.md](docs/RUN_PROJECT.md)**.
The short version:

### Backend

```bash
cd backend
python -m venv venv

# Windows
venv\Scripts\activate
# macOS / Linux
source venv/bin/activate

pip install -r requirements.txt

# Windows
copy .env.example .env
# macOS / Linux
cp .env.example .env

# now fill in SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY and at least one AI provider key
# (XAI_API_KEY, GEMINI_API_KEY or GROQ_API_KEY - tried in that order)

uvicorn app.main:app --reload --port 8000
```

Backend: <http://localhost:8000> · API docs: <http://localhost:8000/docs>

### Frontend

```bash
cd frontend
npm install
cp .env.example .env      # or: copy .env.example .env
npm run dev
```

Frontend: <http://localhost:5173>

Open <http://localhost:8000/api/health> first. It tells you exactly what is still unconfigured.

---

## 12. Supabase setup

1. Create a free project at <https://supabase.com>.
2. Open **Project Settings → API** and copy:
   - **Project URL** → `SUPABASE_URL`
   - **`service_role` secret key** → `SUPABASE_SERVICE_ROLE_KEY`
3. Open the **SQL Editor**, click **New query**, paste the entire contents of
   `backend/database/schema.sql`, and press **Run**.
4. Verify by visiting <http://localhost:8000/api/health/database>. You want
   `"reachable": true`.

The schema creates seven tables:

| Table | Holds |
| --- | --- |
| `meetings` | One row per recording: metadata, status, transcript text |
| `transcript_segments` | Timed segments for the timeline view |
| `participants` | De-duplicated people, one row per person per meeting |
| `action_items` | Tasks with owner, deadline, priority, status |
| `decisions` | Decisions taken |
| `key_points` | Discussion points |
| `meeting_summaries` | The summary, one row per meeting |

Row Level Security is enabled on all of them with no public policies, so the anon key cannot read
anything. Only the backend, holding the service-role key, has access.

---

## 13. AI provider setup (Grok, Gemini, Groq)

Analysis is tried in a fixed order — **Grok → Gemini → Groq** — stopping at the first valid
response. **At least one key is required.** Setting all three is what makes the platform survive an
expired key or an exhausted free-tier quota; a provider with a blank key is skipped and never
called, so leaving one out costs nothing.

| Order | Provider | Get a key | Variables |
| --- | --- | --- | --- |
| 1 (primary) | xAI Grok | <https://console.x.ai> → API Keys | `XAI_API_KEY`, `XAI_MODEL` |
| 2 (fallback) | Google Gemini | <https://aistudio.google.com/apikey> | `GEMINI_API_KEY`, `GEMINI_MODEL` |
| 3 (fallback) | Groq | <https://console.groq.com/keys> | `GROQ_API_KEY`, `GROQ_MODEL` |

1. Put the keys in `backend/.env`. They are **backend-only** — never in `frontend/.env`, never
   committed.
2. Change `*_MODEL` if you want different models than the defaults; model names are configuration,
   never hardcoded in the business logic.
3. Test with <http://localhost:8000/api/health/llm>. That makes **one** tiny real call to the
   primary configured provider and reports the rest from configuration. Add `?all=true` to
   live-check every configured provider (one request each).
4. <http://localhost:8000/api/health> shows the configured chain without calling anything.

Common errors:

| Response | Meaning |
| --- | --- |
| `LLM_NOT_CONFIGURED` | No provider key is set, or the provider rejected the key |
| `LLM_MODEL_NOT_FOUND` | The configured `*_MODEL` is not one your account can use |
| `LLM_RATE_LIMITED` | The provider is throttling; the chain moves to the next provider |
| `LLM_INVALID_RESPONSE` | A provider returned unusable JSON and the chain moved on |
| `LLM_ALL_PROVIDERS_FAILED` | Every **configured** provider was tried and none returned a valid response. The message names what was tried and what was skipped for lack of a key. |

Full design notes: [`docs/MULTI_MODEL_AI_FALLBACK.md`](docs/MULTI_MODEL_AI_FALLBACK.md).

---

## 14. Whisper setup

Nothing to install by hand: `pip install -r requirements.txt` covers it, and the model weights
download automatically the first time you transcribe something. That first run is slower.

Model sizes, roughly, on CPU:

| Model | Size | Speed | Use it when |
| --- | --- | --- | --- |
| `tiny` | ~75 MB | fastest | Quick testing |
| `base` | ~150 MB | fast | **Default.** Good balance |
| `small` | ~500 MB | moderate | Better accuracy, worth it for demos |
| `medium` | ~1.5 GB | slow | High accuracy |
| `large-v3` | ~3 GB | slowest | Best accuracy, needs a good machine |

If accuracy testing shows you below the 90% target, moving from `base` to `small` is the single
most effective change, followed by setting `WHISPER_LANGUAGE=en`.

---

## 15. FFmpeg setup

Verify your installation:

```bash
ffmpeg -version
```

You should see something like `ffmpeg version 6.1.1 Copyright (c) 2000-2023 the FFmpeg developers`.

If the command is not found:

- **Windows:** download from <https://www.gyan.dev/ffmpeg/builds/>, unzip, and add the `bin`
  folder to your PATH. Alternatively set `FFMPEG_PATH=C:/ffmpeg/bin/ffmpeg.exe` in `backend/.env`.
- **macOS:** `brew install ffmpeg`
- **Ubuntu/Debian:** `sudo apt install ffmpeg`

FFmpeg is used to read the media container, extract the audio track from video, and convert it to
the 16 kHz mono WAV that Whisper expects. Doing this conversion once up front removes every
container and codec difference between an MKV screen recording and an M4A phone memo.

Check it from the running backend at <http://localhost:8000/api/health/ffmpeg>.

---

## 16. API documentation

Interactive documentation is generated automatically:

- **Swagger UI:** <http://localhost:8000/docs>
- **ReDoc:** <http://localhost:8000/redoc>
- **OpenAPI JSON:** <http://localhost:8000/openapi.json>

| Method | Endpoint | Purpose |
| --- | --- | --- |
| `POST` | `/api/meetings/upload` | Upload and validate a recording |
| `POST` | `/api/meetings/{id}/transcribe` | Run FFmpeg + Whisper |
| `GET` | `/api/meetings` | List meetings |
| `GET` | `/api/meetings/dashboard` | Dashboard statistics |
| `GET` | `/api/meetings/{id}` | One meeting |
| `GET` | `/api/meetings/{id}/transcript` | Transcript with segments |
| `GET` | `/api/meetings/{id}/transcript/download` | Download as `txt`, `timeline`, `json` or `csv` |
| `PATCH` | `/api/meetings/{id}` | Rename |
| `DELETE` | `/api/meetings/{id}` | Delete the meeting and everything under it |
| `POST` | `/api/meetings/{id}/analyze` | Analyse with the AI chain (Grok → Gemini → Groq) |
| `GET` | `/api/meetings/{id}/intelligence` | Stored analysis |
| `POST` | `/api/transcription/accuracy` | Measure accuracy against a reference |
| `GET` | `/api/health` | Health and configuration |
| `GET` | `/api/health/ffmpeg` `/database` `/llm` | Individual service checks |
| `GET` | `/api/config/formats` | Supported formats and size limit |

Every response uses the same envelope:

```json
{ "success": true, "data": { }, "message": "Operation completed successfully" }
```

```json
{ "success": false, "error": { "code": "UNSUPPORTED_FILE_TYPE", "message": "..." } }
```

Status codes: `400` bad request, `404` not found, `409` conflict, `413` too large,
`415` unsupported type, `422` validation error, `429` rate limited, `500` server error,
`503` external service unavailable.

---

## 17. Testing

```bash
# Backend  (175 tests)
cd backend
source venv/bin/activate     # Windows: venv\Scripts\activate
pytest

# Frontend  (36 tests)
cd frontend
npm run test
```

Backend coverage by area:

| File | Covers |
| --- | --- |
| `test_file_validation.py` | Formats, path traversal, empty/oversized/corrupted files, renamed text files, video with no audio |
| `test_transcript_validation.py` | Empty transcripts, bad timestamps, wrong meeting id, paragraph building |
| `test_participant_mapping.py` | Case variants, alias merging, unknown handling, **not merging different people** |
| `test_llm_output.py` | JSON recovery, schema violations, retries, priority/status coercion, chunk merging |
| `test_llm_fallback.py` | Provider fallback order, stop-on-success, skipping unconfigured providers, auth/quota/timeout/invalid-response classification, controlled all-failed error, no key leakage |
| `test_accuracy.py` | WER maths, substitutions/deletions/insertions, target pass/fail, edge cases |
| `test_chunking.py` | Chunk sizes, overlap, ordering, the max-chunks ceiling |
| `test_api.py` | Response envelope, status codes, upload rejection paths |

Tests that need FFmpeg generate their media fixtures at runtime and skip themselves cleanly if
FFmpeg is not installed. No test requires Supabase or any AI provider key, and **no test makes a
real API call** — the LLM tests use scripted stub providers that exercise the real parsing,
validation, retry and fallback logic, precisely so a test run cannot spend a free-tier quota.

---

## 18. Deployment

```
Frontend  →  Vercel
Backend   →  Render / Railway / Fly.io / any VM or container host
Database  →  Supabase
```

### Why the backend cannot go on Vercel

Whisper transcription is CPU-heavy and long-running, FFmpeg is a system binary, and the Whisper
model weights are hundreds of megabytes. Serverless functions have short execution limits, limited
memory and a read-only filesystem. Forcing this workload into a serverless function would fail on
any recording longer than a minute.

So the frontend deploys as static files to Vercel, and the backend deploys separately to a host
that gives it a real filesystem, FFmpeg, and no request timeout.

### Frontend on Vercel

1. Push the repository to GitHub.
2. Import it in Vercel and set the **root directory** to `frontend`.
3. Vercel detects Vite automatically (`npm run build`, output `dist`).
4. Add the environment variable `VITE_API_BASE_URL` = your deployed backend URL.
5. Deploy.

`frontend/vercel.json` already handles SPA routing so a refresh on `/meetings/abc` works.

### Backend

Any host that runs Python and has FFmpeg available:

```bash
pip install -r requirements.txt
uvicorn app.main:app --host 0.0.0.0 --port $PORT
```

On the host, set the production environment variables, and importantly:

```env
APP_ENV=production
DEBUG=false
CORS_ORIGINS=https://your-frontend.vercel.app
```

Give the service at least 2 GB of RAM for the `base` model, more for larger ones.

---

## 19. Troubleshooting

| Symptom | Cause and fix |
| --- | --- |
| Badge reads "Backend unreachable" | The API is not running, or `VITE_API_BASE_URL` is wrong. Check <http://localhost:8000/api/health>. |
| `DATABASE_NOT_CONFIGURED` | `SUPABASE_URL` or `SUPABASE_SERVICE_ROLE_KEY` is missing from `backend/.env`. |
| `DATABASE_TABLE_MISSING` | `backend/database/schema.sql` has not been run in the Supabase SQL editor. |
| `FFMPEG_NOT_AVAILABLE` | FFmpeg is not installed or not on the PATH. Run `ffmpeg -version`, or set `FFMPEG_PATH`. |
| `LLM_NOT_CONFIGURED` | `XAI_API_KEY` is empty or rejected. |
| First transcription hangs for minutes | The Whisper weights are downloading. This only happens once per model. |
| `EMPTY_TRANSCRIPT` | The recording is silent, or the audio track has no speech. |
| Transcription is very slow | Use a smaller `WHISPER_MODEL`, or set `WHISPER_LANGUAGE=en` to skip language detection. |
| Accuracy below the target | Try `WHISPER_MODEL=small`, set the language explicitly, and use a cleaner recording. |
| CORS errors in the browser console | Add the frontend origin to `CORS_ORIGINS` in `backend/.env` and restart the backend. |
| `MEDIA_UNAVAILABLE` on transcribe | The recording is deleted after transcription. Upload it again to re-run. |
| `ModuleNotFoundError: requests` | Reinstall dependencies: `pip install -r requirements.txt`. |

---

## 20. Scope notes

**No authentication.** The milestones do not ask for user accounts, so there is no login. Every
visitor sees every meeting. This is fine for a local demo but must be addressed before any real
deployment. The database is ready for it: Row Level Security is already enabled on all tables, so
adding per-user policies is the natural next step rather than a rewrite.

**Speaker labels.** The transcript schema, the database, the API and the timeline UI all carry a
`speaker` field, and it is displayed whenever it is populated. Whisper by itself does not perform
speaker diarization, so the field is currently `null` for Whisper-generated transcripts. Adding a
diarization step later fills it in without any schema or UI change.

**Recordings are not kept.** The uploaded media file is deleted as soon as transcription finishes.
Only the transcript and the analysis are stored. This keeps storage costs and privacy exposure low,
and it is why re-transcribing requires uploading the file again.
