# AI Career Intelligence Platform

Turn a meeting recording into a transcript, a summary, and a list of who agreed to do what by when.

Upload an audio or video recording. The backend validates it, extracts clean audio with FFmpeg,
transcribes it with Whisper, then sends the transcript to **Groq** (the only LLM provider), which
returns a structured summary, the decisions taken, and the action items with owners, deadlines and
priorities. Everything is stored in Supabase and displayed in a React
interface.

Then it makes that history **searchable**: every past meeting is turned into embeddings by a free
local model and indexed in Pinecone, so you can search by meaning and ask questions that Groq
answers from your own meeting records, with the source meetings attached.

**Milestone 1** (audio processing and transcription), **Milestone 2** (LLM processing) and
**Milestone 3** (knowledge repository, semantic search and RAG) are all fully implemented.

---

## Table of contents

1. [What this project does](#1-what-this-project-does)
2. [Internship context](#2-internship-context)
3. [Milestone 1](#3-milestone-1--audio-processing-and-transcription)
4. [Milestone 2](#4-milestone-2--llm-processing)
5. [Milestone 3](#5-milestone-3--knowledge-search-and-rag)
6. [Features](#6-features)
7. [Architecture](#7-architecture)
8. [Technology stack](#8-technology-stack)
9. [Project structure](#9-project-structure)
10. [Prerequisites](#10-prerequisites)
11. [Environment variables](#11-environment-variables)
12. [Local setup](#12-local-setup)
13. [Supabase setup](#13-supabase-setup)
14. [Groq setup](#14-groq-setup)
15. [Whisper setup](#15-whisper-setup)
16. [FFmpeg setup](#16-ffmpeg-setup)
17. [API documentation](#17-api-documentation)
18. [Testing](#18-testing)
19. [Deployment](#19-deployment)
20. [Troubleshooting](#20-troubleshooting)
21. [Scope notes](#21-scope-notes)

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
- semantic search across every past meeting, and grounded answers to questions about them

A deliberate design rule runs through the whole system: **when the recording does not say
something, the application says so rather than guessing.** If nobody was named as the owner of a
task, the owner field reads "Not stated". It is never filled in with a plausible-sounding name.
The same rule governs question answering: if the meeting records do not contain the answer, the
system says it could not find it rather than inventing one.

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
| Groq API integration (the only LLM) | `backend/app/ai/groq_client.py` |
| Reusable LLM service | `backend/app/ai/llm_service.py` |
| Prompt templates | `backend/app/ai/prompts/meeting_intelligence_prompt.py` |
| Structured JSON output with a fixed schema | `backend/app/schemas/intelligence.py` |
| Pydantic validation of every AI response | `LLMMeetingIntelligence` |
| Long transcript handling (chunking) | `backend/app/ai/chunking.py` |
| Retry and failure handling | `GroqClient`, `LLMService` |
| Participant mapping and de-duplication | `backend/app/services/participant_service.py` |
| Persistence to Supabase | `backend/app/repositories/intelligence_repository.py` |

### LLM provider: Groq only

**Groq** is the application's one and only LLM provider. Every language-model task — meeting
summaries, key points, decisions, action items, participants, deadlines, priorities, and Milestone 3's
RAG answers — goes through one service (`LLMService`) and one client (`GroqClient`, the only file
that talks to an LLM API). There is no fallback provider. (Groq is the inference platform — not to be
confused with xAI's *Grok*, which, like Google Gemini, has been removed.)

```text
transcript ─► LLMService ─► GroqClient ─► api.groq.com ─► JSON ─► Pydantic validation ─► Supabase
```

A normal-length transcript costs **one** Groq request, which returns every field at once. The client
is built for a plan with request and token limits:

| Situation | What happens |
| --- | --- |
| network error, timeout, 5xx | at most one retry (`GROQ_MAX_ATTEMPTS=2`) |
| `429` with a short `retry-after` | waits as instructed and retries **once** |
| `429` with a long wait (daily quota) | fails fast with `LLM_RATE_LIMITED` |
| `401` / `403` | `LLM_NOT_CONFIGURED` naming `GROQ_API_KEY` — never retried |
| `404` / retired model | `LLM_MODEL_NOT_FOUND` naming `GROQ_MODEL` — never retried |
| `413` request too large | `LLM_REQUEST_TOO_LARGE` — never retried |
| invalid JSON | Groq's own generated text is repaired locally first; otherwise one corrective re-prompt |

Reasoning models (such as the configured `openai/gpt-oss-20b`) are sent `reasoning_effort=low`, so
fewer tokens go to reasoning. Long transcripts are chunked one at a time (`LLM_CHUNK_CONCURRENCY=1`)
to respect tokens-per-minute limits, and a chunk merge too large for one request is done locally
instead of being sent to fail. An already-analysed meeting is served from Supabase without any Groq
request unless the user explicitly re-analyses it.

`intelligence.provider` and `intelligence.model` in the API response say what produced a result.

Full explanation: [`MILESTONE_3_GROQ_ONLY.md`](MILESTONE_3_GROQ_ONLY.md).

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

## 5. Milestone 3 — knowledge search and RAG

Milestones 1 and 2 handle one meeting at a time. Milestone 3 turns the **history** into something
you can question.

```text
All past meetings in Supabase
        ↓
Knowledge documents  (transcript · summary · decisions · action items · key points · participants)
        ↓
Embeddings           (local BAAI/bge-small-en-v1.5 via fastembed - free, no API key)
        ↓
Pinecone             (vector index; Supabase stays the source of truth)
        ↓
Semantic search  →  relevant meetings
        ↓
RAG  →  Groq  →  grounded answer + sources
```

| Requirement | Where it lives |
| --- | --- |
| Meeting knowledge repository | `backend/app/repositories/knowledge_repository.py`, `backend/app/knowledge/documents.py` |
| Embedding generation | `backend/app/knowledge/embeddings/`, `backend/app/services/embedding_service.py` |
| Vector database (Pinecone) | `backend/app/repositories/vector_repository.py` |
| Indexing, re-indexing, deletion | `backend/app/services/knowledge_index_service.py` |
| Semantic search | `backend/app/services/semantic_search_service.py` |
| RAG question answering | `backend/app/services/rag_service.py`, `backend/app/ai/prompts/rag_prompt.py` |
| UI | `frontend/src/pages/AskPage.jsx` (the **Ask & search** page) |

### Why semantic search rather than `LIKE`

A search for *"database migration"* should find a meeting that said *"we need to move the Postgres
schema over"*. Keyword search cannot: the meaning matches, the letters do not. Every passage and
every query is converted into a 384-number **embedding**, and similarity is measured between those
vectors instead of between the words.

### Search vs Ask

| | Semantic search | RAG (Ask) |
| --- | --- | --- |
| Question | *"Which meeting discussed the database migration?"* | *"What deadline was decided for the mobile application?"* |
| Returns | the matching meetings | a written answer **plus its source meetings** |
| Cost | 1 embedding + 1 vector query + 1 database read | the same, plus **one** LLM call |
| Calls the LLM | **never** | once — one Groq request |

### Grounding — why the answers can be trusted

* Context blocks are labelled with meeting, id, date and source type, and grouped per meeting, so
  two meetings never blur into one answer.
* The prompt forbids outside knowledge and requires `answer_found: false` when the records do not
  contain the answer.
* The reply is validated against a schema; a malformed answer gets one corrective re-prompt and
  is otherwise rejected — never shown.
* Cited meetings are checked against what was actually retrieved — an invented source is discarded.
* If retrieval finds nothing, the AI is **not called at all** and the system says it could not find
  the information.

### Quota discipline

The knowledge content (and the embedding model's identity) is fingerprinted, so re-indexing an
unchanged meeting does **no** embedding work, while changing the model re-embeds everything. A query is embedded once and shared by search and RAG. Vector ids are deterministic, so
re-indexing updates rather than duplicates. Opening the page costs nothing.

### Setup

1. Add `PINECONE_API_KEY` to `backend/.env` (free at <https://app.pinecone.io>). Embeddings need no
   key: the model (~67 MB) downloads automatically the first time it is used.
2. Optionally run `backend/database/migrations/002_add_knowledge_index_columns.sql` in Supabase —
   four nullable bookkeeping columns that let re-indexing skip unchanged meetings.
3. Restart the backend, open **Ask & search**, and press **Index my meetings**.

Leave `PINECONE_API_KEY` blank and Milestones 1 and 2 work exactly as before; only the Ask & search
page reports that it is not configured.

**Why embeddings are not Groq.** Groq generates text; it does not produce embeddings. Embeddings come
from a separate local model, and the *same* model embeds stored passages and search queries — so
they live in one vector space. Every vector records which model made it, and searches are filtered
to the current model, so vectors from different models are never compared.

Full explanation, diagrams and test results:
[`MILESTONE_3_DOCUMENTATION.md`](MILESTONE_3_DOCUMENTATION.md).

---

## 6. Features

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

## 7. Architecture

```
                    Browser
                       |
              React (Vite) frontend
                       |  HTTPS / JSON
                       v
              FastAPI backend
                       |
     +-----------------+------------------+--------------------+
     |                 |                  |                    |
   FFmpeg           Whisper            Groq API          local embeddings
 (extract and     (speech to        (the only LLM:     (bge-small, 384-d)
  normalise)         text)          analysis + RAG)            |
                       |                                       v
                       v                                   Pinecone
                   Supabase  <---- meeting ids ----    (vector search)
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

This keeps every external dependency behind one file: `app/ai/groq_client.py` is the only code
that talks to an LLM, `app/repositories/vector_repository.py` the only code that talks to Pinecone,
and `app/knowledge/embeddings/` the only code that produces embeddings. Changing the Groq model is a
configuration change; changing the embedding model is one setting plus a re-index.
`transcription_service.py` plays the same role for Whisper, and already supports two different
Whisper implementations behind one interface.

### Processing states

`UPLOADED` → `VALIDATING` → `AUDIO_PROCESSING` → `TRANSCRIBING` → `TRANSCRIPT_VALIDATED` →
`AI_ANALYSIS` → `PERSISTING` → `COMPLETED`, or `FAILED` at any point.

The same strings are used in the database, the API and the React code, so there is no translation
layer where they can drift apart.

---

## 8. Technology stack

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
| **Groq** | LLM (the only one) | Fast OpenAI-compatible inference with JSON mode. All analysis and RAG answers. |
| **fastembed** (`BAAI/bge-small-en-v1.5`) | Embeddings | Free local ONNX model, no API key, no PyTorch; ~3 ms per query. |
| **Pinecone** | Vector database | Managed similarity search; Supabase stays the source of truth. |
| **Supabase** | Database | Managed PostgreSQL with a simple Python client. Real tables, real constraints, real foreign keys. |
| **pytest / Vitest** | Testing | The standard choice on each side. |

---

## 9. Project structure

```
ai-career-intelligence-platform/
├── backend/
│   ├── app/
│   │   ├── ai/                  LLM layer (Groq only), chunking, prompts
│   │   │   ├── groq_client.py        the only code that talks to an LLM API
│   │   │   ├── llm_service.py        every LLM task: analysis + RAG answers
│   │   │   └── prompts/              meeting analysis + RAG prompts
│   │   ├── knowledge/           Milestone 3: knowledge documents + embeddings
│   │   │   ├── documents.py          Supabase rows -> embeddable passages
│   │   │   └── embeddings/           local embedding model (fastembed)
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

## 10. Prerequisites

| Tool | Version | Check with |
| --- | --- | --- |
| Python | 3.10+ | `python --version` |
| Node.js | 18+ | `node --version` |
| npm | 9+ | `npm --version` |
| FFmpeg | any recent | `ffmpeg -version` |

You will also need a free **Supabase** project, a **Groq** API key, and (for Milestone 3) a free
**Pinecone** API key. Embeddings need no key.

---

## 11. Environment variables

Full documentation lives in `backend/.env.example` and `frontend/.env.example`. The ones that
matter most:

### Backend (`backend/.env`)

| Variable | Default | What it does |
| --- | --- | --- |
| `SUPABASE_URL` | — | Your Supabase project URL. **Required.** |
| `SUPABASE_SERVICE_ROLE_KEY` | — | Service-role key. **Required. Backend only, never the frontend.** |
| `GROQ_API_KEY` | — | Groq key. **Required for analysis and Q&A** (the only LLM provider). |
| `GROQ_MODEL` | `openai/gpt-oss-20b` | Which Groq model to call. |
| `GROQ_MAX_ATTEMPTS` | `2` | Requests per prompt on transient errors. `1` = never retry. |
| `GROQ_REASONING_EFFORT` | `low` | Sent only to reasoning models; saves tokens. |
| `LLM_SCHEMA_RETRY_ATTEMPTS` | `2` | Corrective re-prompts when Groq returns unusable JSON. |
| `LLM_CHUNK_CONCURRENCY` | `1` | Chunks of a long transcript analysed at once. |
| `WHISPER_MODEL` | `base` | `tiny`, `base`, `small`, `medium`, `large-v3`. Bigger is more accurate and slower. |
| `WHISPER_BACKEND` | `faster-whisper` | Or `openai` for the reference implementation. |
| `WHISPER_LANGUAGE` | blank | Blank auto-detects. Setting `en` improves both speed and accuracy. |
| `MAX_UPLOAD_SIZE_MB` | `200` | Upload limit. |
| `CORS_ORIGINS` | localhost:5173 | Comma-separated allowed frontend origins. |
| `FFMPEG_PATH` | `ffmpeg` | Only needed if FFmpeg is not on your PATH. |
| `LLM_CHUNK_CHAR_SIZE` | `9000` | Characters per chunk for long transcripts. |
| `PINECONE_API_KEY` | — | Vector database key. Milestone 3 only. Blank = search is disabled. |
| `PINECONE_INDEX_NAME` | `meeting-knowledge` | Pinecone index to use; created on first run. |
| `EMBEDDING_MODEL` | `BAAI/bge-small-en-v1.5` | Local embedding model (fastembed). No key. |
| `EMBEDDING_DIMENSIONS` | `384` | Must match the model **and** the Pinecone index. |
| `EMBEDDING_CACHE_DIR` | blank | Where the model is cached. Set a persistent path in production. |
| `KNOWLEDGE_AUTO_INDEX` | `true` | Index a meeting automatically once its analysis succeeds. |

### Frontend (`frontend/.env`)

| Variable | Default | What it does |
| --- | --- | --- |
| `VITE_API_BASE_URL` | blank | Blank uses the Vite dev proxy. In production, the full backend URL. |
| `VITE_API_TIMEOUT_MS` | `900000` | 15 minutes, because transcription is slow. |

**Only `VITE_*` variables reach the browser, and everything in them is public once built. No secret
key ever belongs in the frontend.**

---

## 12. Local setup

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

# now fill in SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY, GROQ_API_KEY
# and (for meeting search) PINECONE_API_KEY

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

## 13. Supabase setup

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

## 14. Groq setup

Groq is the only LLM provider: it powers meeting analysis (Milestone 2) and question answering
(Milestone 3).

1. Create a key at <https://console.groq.com/keys> (a free tier is available).
2. Put it in `backend/.env` as `GROQ_API_KEY`. It is **backend-only** — never in `frontend/.env`,
   never committed.
3. Optionally change `GROQ_MODEL` (default `openai/gpt-oss-20b`; `llama-3.3-70b-versatile` and
   `llama-3.1-8b-instant` also work). The model name is configuration, never hardcoded.
4. Test with <http://localhost:8000/api/health/llm>, which makes **one** tiny real call.
   <http://localhost:8000/api/health> shows whether Groq is configured without calling it.

Common errors:

| Response | Meaning |
| --- | --- |
| `LLM_NOT_CONFIGURED` | `GROQ_API_KEY` is empty, or Groq rejected it |
| `LLM_MODEL_NOT_FOUND` | `GROQ_MODEL` is not a model your Groq account can use |
| `LLM_RATE_LIMITED` | Groq's per-minute or daily limit was reached; wait and retry |
| `LLM_REQUEST_TOO_LARGE` | One request exceeds the plan's token limit; lower `LLM_CHUNK_CHAR_SIZE` |
| `LLM_INVALID_RESPONSE` | Groq's reply did not match the schema even after one correction |

Full explanation: [`MILESTONE_3_GROQ_ONLY.md`](MILESTONE_3_GROQ_ONLY.md).

---

## 15. Whisper setup

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

## 16. FFmpeg setup

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

## 17. API documentation

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
| `POST` | `/api/meetings/{id}/analyze` | Analyse the transcript with Groq |
| `POST` | `/api/meetings/search` | Semantic search across indexed meetings (no LLM call) |
| `POST` | `/api/meetings/ask` | Grounded question answering with sources (RAG) |
| `GET` | `/api/knowledge/status` | Search configuration and index coverage |
| `POST` | `/api/knowledge/index` | Index historical meetings into the vector database |
| `POST` | `/api/knowledge/meetings/{id}` | Index or refresh one meeting |
| `DELETE` | `/api/knowledge/meetings/{id}` | Remove one meeting's vectors |
| `GET` | `/api/health/vector` | Check the local embedding model and Pinecone connectivity |
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

## 18. Testing

```bash
# Backend  (373 tests)
cd backend
source venv/bin/activate     # Windows: venv\Scripts\activate
pytest

# Frontend  (41 tests)
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
| `test_groq_client.py` | Groq is the only provider (no Grok/Gemini code remains), request shape, JSON mode, auth/quota/timeout handling, retry limits, one request per analysis, no key leakage |
| `test_settings.py` | Configuration parsing, including blank `.env` values that carry a comment |
| `test_knowledge.py` | Knowledge documents for every source type, meeting linkage, deterministic ids, model-aware fingerprints, local embedding generation (plus an opt-in real-model test) |
| `test_vector_search.py` | Pinecone insert/update/delete, similarity search, metadata filtering, meeting-to-vector mapping, indexing resilience |
| `test_rag.py` | Grounded answering with Groq, no-hallucination rules, multi-meeting context, controlled failures, answer schema validation |
| `test_milestone3_e2e.py` | The whole Milestone 3 scenario: index historical meetings -> semantic search -> grounded answer |
| `test_accuracy.py` | WER maths, substitutions/deletions/insertions, target pass/fail, edge cases |
| `test_chunking.py` | Chunk sizes, overlap, ordering, the max-chunks ceiling |
| `test_api.py` | Response envelope, status codes, upload rejection paths |

Tests that need FFmpeg generate their media fixtures at runtime and skip themselves cleanly if
FFmpeg is not installed. No test requires Supabase, Pinecone or a Groq key, and **no test makes a
real API call**: Groq and Pinecone are mocked at the HTTP layer with `respx` (so the real request
bodies are exercised), and the embedding model is swapped for a deterministic stand-in — a test run
never spends quota or downloads a model. To also run the real embedding model:
`RUN_REAL_EMBEDDING_TESTS=1 pytest tests/test_knowledge.py`.

---

## 19. Deployment

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

Give the service at least 2 GB of RAM for the `base` model, more for larger ones. The local
embedding model adds roughly 150–250 MB of RAM and a one-time ~67 MB download; set
`EMBEDDING_CACHE_DIR` to a persistent disk path so restarts do not download it again (the default,
the OS temp directory, is wiped on many hosts). The model is loaded in the background at startup,
so the first search does not wait for it.

---

## 20. Troubleshooting

| Symptom | Cause and fix |
| --- | --- |
| Badge reads "Backend unreachable" | The API is not running, or `VITE_API_BASE_URL` is wrong. Check <http://localhost:8000/api/health>. |
| `DATABASE_NOT_CONFIGURED` | `SUPABASE_URL` or `SUPABASE_SERVICE_ROLE_KEY` is missing from `backend/.env`. |
| `DATABASE_TABLE_MISSING` | `backend/database/schema.sql` has not been run in the Supabase SQL editor. |
| `FFMPEG_NOT_AVAILABLE` | FFmpeg is not installed or not on the PATH. Run `ffmpeg -version`, or set `FFMPEG_PATH`. |
| `LLM_NOT_CONFIGURED` | `GROQ_API_KEY` is empty or rejected. Check it at <http://localhost:8000/api/health/llm>. |
| `LLM_RATE_LIMITED` | Groq's per-minute or daily limit was reached. Wait, or use a smaller `GROQ_MODEL`. |
| First search after install is slow | The embedding model (~67 MB) is downloading. This happens once per cache directory. |
| `VECTOR_DIMENSION_MISMATCH` | `EMBEDDING_DIMENSIONS` does not match the model or the Pinecone index. Use 384 with the default model. |
| `EMBEDDING_NOT_CONFIGURED` | `pip install -r requirements.txt` was not run, or `EMBEDDING_PROVIDER` is not `fastembed`. |
| First transcription hangs for minutes | The Whisper weights are downloading. This only happens once per model. |
| `EMPTY_TRANSCRIPT` | The recording is silent, or the audio track has no speech. |
| Transcription is very slow | Use a smaller `WHISPER_MODEL`, or set `WHISPER_LANGUAGE=en` to skip language detection. |
| Accuracy below the target | Try `WHISPER_MODEL=small`, set the language explicitly, and use a cleaner recording. |
| CORS errors in the browser console | Add the frontend origin to `CORS_ORIGINS` in `backend/.env` and restart the backend. |
| `MEDIA_UNAVAILABLE` on transcribe | The recording is deleted after transcription. Upload it again to re-run. |
| `ModuleNotFoundError: requests` | Reinstall dependencies: `pip install -r requirements.txt`. |

---

## 21. Scope notes

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
