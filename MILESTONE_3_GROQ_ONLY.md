# Groq-Only LLM Architecture — Milestones 1, 2 and 3

> Beginner-friendly explanation of how the AI Career Intelligence Platform works
> now that **Groq is the only LLM** and embeddings come from a **free local
> model**. Timings, test counts and results were measured on the real system;
> the only estimate is the embedding model's memory use (marked as such).

---

## Table of contents

1. [What was changed](#1-what-was-changed)
2. [Why Grok (xAI) was removed](#2-why-grok-xai-was-removed)
3. [Why Google Gemini was removed](#3-why-google-gemini-was-removed)
4. [Why Groq is now the only LLM](#4-why-groq-is-now-the-only-llm)
5. [How embeddings are generated](#5-how-embeddings-are-generated)
6. [Why the embedding model is separate from Groq](#6-why-the-embedding-model-is-separate-from-groq)
7. [How Pinecone works](#7-how-pinecone-works)
8. [How semantic search works](#8-how-semantic-search-works)
9. [How RAG works](#9-how-rag-works)
10. [How the database is connected](#10-how-the-database-is-connected)
11. [Milestone 1 after the change](#11-milestone-1-after-the-change)
12. [Milestone 2 after the change](#12-milestone-2-after-the-change)
13. [Milestone 3 after the change](#13-milestone-3-after-the-change)
14. [The complete pipeline](#14-the-complete-pipeline)
15. [Environment variables](#15-environment-variables)
16. [API flow](#16-api-flow)
17. [Error handling](#17-error-handling)
18. [Testing performed](#18-testing-performed)
19. [Deployment considerations](#19-deployment-considerations)
20. [Things found along the way](#20-things-found-along-the-way)

---

## First, three names that are easy to mix up

| Name | What it is | Status in this project |
| --- | --- | --- |
| **Grok** | xAI's language model | **Removed** |
| **Gemini** | Google's language + embedding models | **Removed** |
| **Groq** | an inference platform that runs open models very fast | **The only LLM provider** |

"Grok" and "Groq" differ by one letter and are completely different companies.
Everywhere this document says **Groq**, it means the platform at `groq.com`.

---

## 1. What was changed

**In one sentence:** the three-provider fallback chain (Grok → Gemini → Groq)
was replaced by a single Groq client, and Gemini embeddings were replaced by a
free local embedding model.

```
BEFORE                                   AFTER
──────                                   ─────
LLM:        Grok ─► Gemini ─► Groq       LLM:        Groq (only)
            (fallback chain)
Embeddings: Gemini API (768-d, key)      Embeddings: local model (384-d, no key)
```

### Removed

| Removed | Why it is safe to remove |
| --- | --- |
| `app/ai/providers/` (Grok, Gemini, Groq provider classes + base) | replaced by one `groq_client.py` |
| `app/ai/llm_orchestrator.py` (the fallback loop) | there is nothing to fall back to |
| `app/ai/llm_client.py` (Grok alias) | nothing imported it any more |
| `app/knowledge/embeddings/gemini.py` | replaced by the local model |
| Settings: `XAI_*`, `GEMINI_*`, `LLM_PROVIDER_ORDER`, `EMBEDDING_API_KEY`, … | the app no longer reads them |
| Error: `LLM_ALL_PROVIDERS_FAILED` | only meaningful with a chain |

Every importer of those modules was found and updated first (the intelligence
service, the RAG service, the health controller, the package exports and the
tests), so nothing was left pointing at a deleted file.

### Added

| Added | Purpose |
| --- | --- |
| `app/ai/groq_client.py` | **the only code in the project that talks to an LLM** |
| `app/knowledge/embeddings/fastembed_provider.py` | local embedding model |
| `app/utils/http.py` | shared HTTPS connection pool (big latency win, see §8) |
| `tests/test_groq_client.py`, `tests/test_settings.py`, `tests/helpers.py` | new tests |
| `fastembed==0.8.0` in `requirements.txt` | the one new dependency |

### Changed (and why)

| File | Change |
| --- | --- |
| `app/ai/llm_service.py` | now the single entry point for *all* LLM work; gained `generate_validated()` shared by analysis and RAG; token budgets are configurable |
| `app/services/intelligence_service.py`, `rag_service.py` | use `LLMService` instead of the orchestrator |
| `app/services/embedding_service.py` | picks the local provider; a bad setting can no longer crash unrelated features |
| `app/knowledge/documents.py`, `knowledge_index_service.py`, `knowledge_repository.py`, `semantic_search_service.py` | every vector and fingerprint records which embedding model made it |
| `app/repositories/vector_repository.py` | waits for a new index to be ready; uses the connection pool |
| `app/config/settings.py` | Groq-only settings; fixed a `.env` parsing bug (see §20) |
| health controller / schemas / routes / `main.py` | report Groq instead of a provider chain |
| Frontend (text only) | error messages and setup hints say Groq; **no layout or design change** |

**Unchanged:** Whisper, FFmpeg, upload, file validation, transcript views,
downloads, accuracy testing, participant mapping, the database schema, every
API URL and the UI design.

---

## 2. Why Grok (xAI) was removed

Grok was the original primary provider, but in this environment its API
returned authorization failures. A provider that cannot authenticate adds a
failed network request (and delay) to every single analysis before the chain
reaches something that works. Keeping it would also mean keeping its key, its
settings and its error handling alive for no benefit.

## 3. Why Google Gemini was removed

Gemini was fallback #1 **and** the embedding provider. It also failed
authorization here. Because embeddings depended on it, a Gemini outage would
have taken down *search* as well as analysis. Replacing its embeddings with a
local model removes that dependency entirely.

## 4. Why Groq is now the only LLM

* **It works** with the available account (verified live: authentication and
  structured output both succeed).
* **It is fast** — a full meeting analysis took **1.2 seconds**.
* **It speaks the standard OpenAI-compatible API** with JSON mode, so the
  existing prompts and validation work unchanged.
* **One provider is simpler** to reason about, to test, and to explain: every
  LLM request has exactly one destination.

There is deliberately **no fallback**. If Groq is unavailable, the application
says so clearly (see §17) instead of quietly trying somewhere else.

---

## 5. How embeddings are generated

An **embedding** is a list of numbers that represents what a piece of text
*means*. Texts with similar meanings get similar numbers.

```
"We need to move the Postgres schema over"   ─►  [0.03, -0.41, 0.77, … 384 numbers]
"Which meeting discussed the database migration?" ─►  [0.05, -0.38, 0.71, … 384 numbers]
                                                          ▲ close together = related
```

This project uses **`BAAI/bge-small-en-v1.5`**, run **on the backend itself**
with the `fastembed` library:

| Property | Value |
| --- | --- |
| Output size | 384 numbers per text |
| Download | ~67 MB, once (then cached) |
| Speed | ~3 ms per query after loading (measured) |
| Cost | free — no API key, no quota, no network call |
| Runtime | ONNX Runtime (already installed for Whisper); **no PyTorch** |

`sentence-transformers` was considered and rejected: it pulls in PyTorch,
which is about 2 GB on a Linux server — impractical for this deployment.

```
KnowledgeDocument text
        │
        ▼
EmbeddingService ── chooses the provider from EMBEDDING_PROVIDER
        │
        ▼
FastEmbedProvider ── runs in a worker thread (never blocks the web server)
        │             loaded once per process, shared by every request
        ▼
384-number vector ── size checked against EMBEDDING_DIMENSIONS before use
```

**Measured proof that it matches meaning, not keywords** (real model):

```
query: "Which meeting discussed the database migration?"
  0.669  "We need to move the Postgres schema over before the release."   ← no shared keywords
  0.533  "The mobile application must be released by Friday."
  0.395  "The office coffee machine is broken again."
```

---

## 6. Why the embedding model is separate from Groq

Groq and the embedding model do **different jobs**:

```
Groq (LLM)          text ─► text      "Write a summary", "Answer this question"
Embedding model     text ─► numbers   "Where does this text sit on the map of meaning?"
```

Groq does not provide embeddings, and a chat model must never be used as one.
So the architecture keeps them apart:

```
                ┌──────────────── meeting analysis ────────────────┐
                │                                                   │
Transcript ─────┤                                                   ├─► Groq
                │                                                   │
Question ───────┼─► Embedding model ─► Pinecone ─► context ─────────┘
                └──────── semantic search / retrieval ──────────────
```

**The same embedding model must be used for passages and for questions.** Two
models place text on two different "maps"; comparing across them is
meaningless. Three safeguards guarantee this:

1. Passages and queries both go through `EmbeddingService`, which uses one model.
2. Every vector stores `embedding_model` in its metadata, and **every search is
   filtered to the current model** — an old model's vectors are never compared.
3. The index fingerprint includes the model, so changing `EMBEDDING_MODEL`
   re-embeds every meeting instead of silently keeping old vectors.

---

## 7. How Pinecone works

Pinecone is a database built for one question: *"which stored vectors are
closest to this one?"* Supabase stays the **source of truth**; Pinecone only
holds a searchable copy that can always be rebuilt from Supabase.

```
Supabase (the truth)                  Pinecone (the search index)
────────────────────                  ───────────────────────────
meetings, transcripts,   ─ index ─►   vector + metadata per passage
summaries, decisions,                 {meeting_id, source_type, source_id,
action items, participants             chunk_index, content excerpt,
                         ◄─ ids ───    meeting_title, date, embedding_model}
```

| Setting | Value |
| --- | --- |
| Index | `meeting-knowledge` (created automatically on first use) |
| Dimension | 384, metric cosine, serverless (aws / us-east-1) |
| Namespace | `meetings` |

Every vector has a **deterministic id**:

```
{meeting_id}#{source_type}#{source_id}#{chunk_index}
e.g. 2222…#decision#dec-b#0
```

| Operation | How | Safety |
| --- | --- | --- |
| Insert | upsert with the deterministic id | — |
| Update | the *same* upsert → overwrites, never duplicates | verified live: 5 ids before and after an update |
| Delete | list ids by `{meeting_id}#` prefix, delete those ids | can only ever touch one meeting; there is **no delete-everything** code |
| Search | query with a vector, `topK`, optional metadata filter | read-only |
| Filter | `meeting_id`, `source_type`, date range, assignee — applied *inside* the query | verified live |

> Your Pinecone project also contains an unrelated index, **`medicalbot`**
> (384-d, 5,860 vectors). This application never reads, writes or deletes it —
> confirmed after all live tests (still 5,860 vectors).

---

## 8. How semantic search works

```
USER QUERY  "Which meeting discussed the database migration?"
     │
     ▼
QUERY VALIDATION            empty or too long → 422, no work done
     │
     ▼
QUERY EMBEDDING             local model, ~3 ms, no API call
     │
     ▼
PINECONE SIMILARITY SEARCH  1 request, filtered to the current embedding model
     │
     ▼
RELEVANT VECTORS            passages + scores + meeting ids
     │
     ▼
DATABASE CONTEXT            1 batched Supabase read for all matched meetings
     │                      (matches whose meeting was deleted are dropped)
     ▼
RELEVANT MEETINGS           grouped by meeting, best passage as the excerpt,
                            ranked by score
```

**Search never calls Groq.** Finding *which* meeting discussed something is a
vector problem, not a writing problem — and it keeps search free of LLM quota.

### Measured speed

| Measurement (real Pinecone, from this development machine) | Result |
| --- | --- |
| Before the connection-pool fix | median **1,272 ms** |
| After the fix, 20 searches | median **353 ms**, p90 453 ms, max **535 ms** |
| One earlier run of 10 | one outlier of 3.2 s (network variance), the rest ~0.6 s |

The fix: every request used to open a brand-new HTTPS connection (a full TLS
handshake to Pinecone's US server). Reusing one pooled connection cut a
Pinecone query from **1,229 ms to 332 ms**. These numbers exclude the one
Supabase read that production adds, which could not be measured because the
Supabase project was unreachable (§20). Well inside three seconds in normal
conditions, but not a guarantee on every network.

---

## 9. How RAG works

**RAG** (Retrieval-Augmented Generation) means: *find the facts first, then ask
the LLM to write an answer using only those facts.*

```
USER QUESTION   "What deadline was decided for the mobile application?"
     │
     ▼
QUERY EMBEDDING
     │
     ▼
PINECONE SEARCH
     │
     ├── nothing relevant? ──► "I couldn't find enough information in the
     │                          meeting records to answer that question."
     │                          (NO Groq request is made)
     ▼
RELEVANT MEETING CONTEXT   labelled blocks, grouped per meeting, size-capped
     │
     ▼
GROQ LLM                   ONE request, strict grounding prompt
     │
     ▼
VALIDATION                 schema check; invented citations discarded
     │
     ▼
GROUNDED ANSWER + SOURCES ─► USER
```

Each context block names its source, so separate meetings never blur together:

```
MEETING: Mobile Application Planning
MEETING_ID: a111…
DATE: 2026-09-22
SOURCE TYPE: decision
CONTENT: Decision made in the meeting: Launch the mobile application next Monday
```

### How hallucination is prevented

1. **Only retrieved context is sent.** Never the whole database.
2. **The prompt forbids outside knowledge**, requires exact dates and names,
   asks for concise answers, and requires `answer_found: false` when the
   records do not contain the answer.
3. **The answer is validated** against a schema (`answer`, `answer_found`,
   `used_meeting_ids`, `confidence`); malformed output gets one correction and is
   otherwise rejected.
4. **Citations are checked** against what was actually retrieved; an invented
   meeting id is dropped.
5. **No retrieval → no Groq request** and an honest "not found".

### Measured, live

| Question | Groq's answer |
| --- | --- |
| "What deadline was decided for the mobile application?" | *"The mobile application will launch next Monday."* — cited the Mobile Application Planning meeting |
| "What budget did the finance team approve for the marketing campaign?" (not in any meeting) | *"The meeting records do not contain information about the budget…"* — `answer_found: false` |

RAG took **2.3 s** before the connection-pool fix and **1.0 s** after it.

---

## 10. How the database is connected

Supabase (PostgreSQL) is unchanged and remains the source of truth:

```
meetings ─┬─ transcript_segments        (Milestone 1)
          ├─ meeting_summaries          (Milestone 2; provider = "groq")
          ├─ key_points
          ├─ decisions
          ├─ action_items ── participants
          └─ index_status, knowledge_fingerprint   (Milestone 3 bookkeeping,
                                                   migration 002, optional)
```

* No table was created or altered by this change.
* `meeting_summaries.provider` now always records `groq` for new analyses;
  older rows keep whatever provider produced them (the UI still labels those
  correctly).
* Milestone 3 reads meetings through the existing repositories — it never
  writes to Milestone 1 or 2 tables.

---

## 11. Milestone 1 after the change

**Unchanged.** Upload → validation → FFmpeg → Whisper → transcript → paragraph
and timeline views → TXT / timeline / JSON / CSV download → accuracy (WER).

Verified live: FFmpeg converted a generated speech recording to 16 kHz mono,
and Whisper transcribed it correctly (60 words in 3.8 s):

> *"Good morning everyone. This is the mobile application planning meeting.
> Ravi will finish the API integration by Friday. Priya will prepare the user
> interface testing report…"*

One Milestone 1 bug was found and fixed along the way: uploads were being
staged in a folder literally named `# blank = OS temp dir…` (§20).

## 12. Milestone 2 after the change

Same pipeline, same prompt, same schema, same participant mapping, same
database writes — only the LLM changed to Groq:

```
TRANSCRIPT ─► LLMService ─► GroqClient ─► Groq (openai/gpt-oss-20b)
                                              │ one request, JSON mode
                                              ▼
            Pydantic validation ◄── summary, key points, decisions,
                    │                action items (owner, deadline,
                    ▼                priority, status), participants
       participant mapping ─► Supabase ─► UI
```

**One request per normal meeting** returns every field at once. Measured live
on the Whisper transcript above (1.2 s):

| Field | Groq output |
| --- | --- |
| Decision | "Launch the mobile application next Monday" |
| Action item | "Finish the API integration" — Ravi — **Friday** — high — pending |
| Action item | "Prepare the user interface testing report" — Priya — **Wednesday** — medium — pending |
| Participants | Ravi, Priya |

### Staying inside a limited Groq plan

| Measure | Effect |
| --- | --- |
| One structured request per transcript | no field-by-field requests |
| `reasoning_effort=low` for reasoning models | fewer hidden reasoning tokens |
| Output caps (3,000 / 2,000 per chunk) | predictable token use |
| Long transcripts analysed one chunk at a time | respects tokens-per-minute limits |
| Oversized chunk-merge done locally | no request that would only fail |
| Bad key / quota gone stops a long meeting after **one** request | no burning 40 chunk requests |
| Groq's own failed JSON is repaired locally first | saves a re-request |
| 429 retried once, only if Groq asks for ≤ 20 s | exhausted daily quota fails fast |
| Already-analysed meetings are served from Supabase | re-opening costs nothing |

## 13. Milestone 3 after the change

Same knowledge repository, Pinecone index, search and RAG — with the local
embedding model and Groq:

| Task | Verified live |
| --- | --- |
| 1. Knowledge repository | 2 meetings → 15 traceable vectors (ids prefixed by meeting) |
| 2. Embedding generation | 384-d, from the text, same model for queries |
| 3. Vector database | insert, update (no duplicates), filter, delete (0 left) |
| 4. Semantic search | correct meeting, including a paraphrase with **no shared keywords** |
| 5. RAG | Groq answer grounded in the retrieved meeting, with sources; unknown questions answered honestly |

---

## 14. The complete pipeline

```
╔══════════════════════════ MILESTONE 1 ══════════════════════════╗
║  MEETING (audio/video)                                          ║
║     │                                                           ║
║     ▼                                                           ║
║  FILE VALIDATION ─► FFMPEG (16 kHz mono) ─► WHISPER             ║
║                                                │                ║
║                                                ▼                ║
║                                           TRANSCRIPT ─► SUPABASE║
╚════════════════════════════════════════════════│════════════════╝
                                                 ▼
╔══════════════════════════ MILESTONE 2 ══════════════════════════╗
║  TRANSCRIPT ─► LLMService ─► GROQ (one request, JSON mode)      ║
║                                   │                             ║
║                                   ▼                             ║
║  SUMMARY / KEY POINTS / DECISIONS / ACTION ITEMS / PARTICIPANTS ║
║         (deadlines, priorities, statuses) ─► validation         ║
║                                   │                             ║
║                                   ▼                             ║
║                     PARTICIPANT MAPPING ─► SUPABASE             ║
╚═══════════════════════════════════│═════════════════════════════╝
                                    ▼  (automatic, best-effort)
╔══════════════════════════ MILESTONE 3 ══════════════════════════╗
║  SUPABASE RECORDS ─► KNOWLEDGE DOCUMENTS ─► EMBEDDING MODEL     ║
║                                              (local, 384-d)     ║
║                                                   │             ║
║                                                   ▼             ║
║                                    PINECONE (vectors + metadata)║
║                                                   ▲             ║
║  SEMANTIC SEARCH:                                 │             ║
║    USER QUERY ─► QUERY EMBEDDING ─► SIMILARITY SEARCH           ║
║                                     ─► RELEVANT MEETINGS        ║
║                                     ─► DATABASE CONTEXT ─► UI   ║
║                                                                 ║
║  RAG:                                                           ║
║    USER QUESTION ─► QUERY EMBEDDING ─► PINECONE SEARCH          ║
║      ─► RELEVANT MEETING CONTEXT ─► GROQ LLM                    ║
║      ─► GROUNDED ANSWER + SOURCES ─► USER                       ║
╚═════════════════════════════════════════════════════════════════╝
```

---

## 15. Environment variables

All backend-only — **none reaches the React app**. Full list with comments:
`backend/.env.example`.

### Required

| Variable | Purpose |
| --- | --- |
| `GROQ_API_KEY` | Groq — the only LLM (analysis + answers) |
| `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY` | the database |
| `PINECONE_API_KEY` | meeting search (Milestone 3 only) |

### Groq tuning (defaults are fine)

| Variable | Default | Meaning |
| --- | --- | --- |
| `GROQ_MODEL` | `openai/gpt-oss-20b` | model on Groq |
| `GROQ_MAX_ATTEMPTS` | `2` | requests per prompt on transient errors |
| `GROQ_REASONING_EFFORT` | `low` | sent only to reasoning models |
| `GROQ_MAX_RATE_LIMIT_WAIT_SECONDS` | `20` | longest 429 wait worth retrying once |
| `LLM_SCHEMA_RETRY_ATTEMPTS` | `2` | corrective re-prompts (1 = none) |
| `LLM_CHUNK_CONCURRENCY` | `1` | chunks analysed at once |
| `LLM_MAX_OUTPUT_TOKENS` / `LLM_CHUNK_MAX_OUTPUT_TOKENS` | `3000` / `2000` | output caps |
| `LLM_MAX_REQUEST_TOKENS` | `7000` | above this, a chunk merge is done locally |

### Embeddings (no key)

| Variable | Default | Meaning |
| --- | --- | --- |
| `EMBEDDING_PROVIDER` | `fastembed` | local model |
| `EMBEDDING_MODEL` | `BAAI/bge-small-en-v1.5` | the model |
| `EMBEDDING_DIMENSIONS` | `384` | must match model **and** index |
| `EMBEDDING_CACHE_DIR` | blank (OS temp) | set a persistent path in production |
| `EMBEDDING_PRELOAD` | `true` | load the model in the background at startup |

### Removed (the app ignores them if still present)

`XAI_API_KEY`, `XAI_MODEL`, `XAI_*`, `GEMINI_API_KEY`, `GEMINI_MODEL`,
`LLM_PROVIDER_ORDER`, `LLM_PROVIDER_MAX_ATTEMPTS`, `EMBEDDING_API_KEY`,
`EMBEDDING_TIMEOUT_SECONDS`, `EMBEDDING_MAX_ATTEMPTS`.

In your local `backend/.env` these lines were **commented out, not deleted**, so
no key value was lost.

---

## 16. API flow

Every endpoint follows the same layers — no Groq, Pinecone or Supabase call
happens in a route or controller:

```
Frontend (services/api/*.js)
   │  HTTP + JSON
   ▼
Route        paths, status codes, request validation (Pydantic)
   ▼
Controller   thin: calls one service
   ▼
Service      business logic
   ▼
Repository / client      Supabase │ Pinecone │ GroqClient │ embedding model
   ▼
{ "success": true, "data": …, "message": … }   or   { "success": false, "error": {code, message} }
```

| Frontend call | Endpoint | Reaches |
| --- | --- | --- |
| `intelligenceApi.analyze` | `POST /api/meetings/{id}/analyze` | Supabase → **Groq** → Supabase |
| `knowledgeApi.search` | `POST /api/meetings/search` | embedding → Pinecone → Supabase (no Groq) |
| `knowledgeApi.ask` | `POST /api/meetings/ask` | embedding → Pinecone → Supabase → **Groq** |
| `knowledgeApi.index` | `POST /api/knowledge/index` | Supabase → embedding → Pinecone |
| `knowledgeApi.status` | `GET /api/knowledge/status` | configuration + Supabase (no external API) |
| `systemApi.health` | `GET /api/health` | configuration only |
| — | `GET /api/health/llm` | one tiny Groq call (operator use) |
| — | `GET /api/health/vector` | embedding model + Pinecone stats |

All **22** API calls made by the frontend were checked programmatically against
the backend's real routes and HTTP methods: **0 mismatches**. CORS from
`http://localhost:5173` was verified.

---

## 17. Error handling

Every failure returns a stable code and a sentence a person can act on. **API
keys, headers and stack traces never reach the browser**; provider error bodies
are logged at DEBUG only.

| Situation | Code | HTTP |
| --- | --- | --- |
| Missing or rejected Groq key | `LLM_NOT_CONFIGURED` (names `GROQ_API_KEY`) | 503 |
| Groq rate limit / quota | `LLM_RATE_LIMITED` | 429 |
| Request too large for the Groq plan | `LLM_REQUEST_TOO_LARGE` | 429 |
| Groq timeout / outage | `LLM_TIMEOUT` / `LLM_UNAVAILABLE` | 502 / 503 |
| Unknown Groq model | `LLM_MODEL_NOT_FOUND` (names `GROQ_MODEL`) | 502 |
| Malformed LLM output (after one correction) | `LLM_INVALID_RESPONSE` | 502 |
| Embedding model missing / cannot load | `EMBEDDING_NOT_CONFIGURED` / `EMBEDDING_MODEL_UNAVAILABLE` | 503 |
| Malformed vector | `EMBEDDING_MALFORMED` | 502 |
| Vector size ≠ index size | `VECTOR_DIMENSION_MISMATCH` (names both sizes) | 500 |
| Pinecone key missing / rejected | `VECTOR_STORE_NOT_CONFIGURED` | 503 |
| Pinecone unreachable during search | `SEARCH_UNAVAILABLE` ("Unable to search meeting knowledge…") | 503 |
| Supabase unreachable | `DATABASE_ERROR` | 503 |
| Unknown meeting | `MEETING_NOT_FOUND` | 404 |
| Empty query / bad filter | `VALIDATION_ERROR` | 422 |
| No search results | success, message "No relevant meetings were found…" | 200 |
| No RAG context | success, honest "couldn't find" answer, **no Groq call** | 200 |
| Groq fails after retrieval | success, the relevant meetings + a clear message, `answer_found: false` | 200 |

Two failure rules protect the rest of the app:

* **Indexing can never fail a meeting.** If Pinecone is down after analysis,
  the analysis is still saved; the meeting is simply marked unindexed.
* **A bad embedding setting cannot crash anything else.** An unknown
  `EMBEDDING_PROVIDER` (e.g. a leftover `gemini`) disables search with a clear
  message; upload, transcription and analysis keep working.

---

## 18. Testing performed

### Automated (no keys, no network, no model download)

| Suite | Result |
| --- | --- |
| Backend `pytest` | **373 passed**, 2 skipped (the opt-in real-model tests) |
| Real embedding model (`RUN_REAL_EMBEDDING_TESTS=1`) | **2 passed** |
| Frontend `npm run test` | **41 passed** |
| Frontend `npm run build` | **succeeds** |

Groq and Pinecone are mocked at the HTTP layer (`respx`), so real request bodies
are checked; the embedding model is replaced by a deterministic stand-in.
Selected guarantees the tests enforce:

* no Grok/Gemini module, URL, key name or class exists anywhere in `app/`;
* a normal analysis costs **exactly one** Groq request;
* a bad key stops a long transcript after **one** request;
* 401/403/404/413 are never retried; 429 is retried at most once;
* the API key never appears in any error message, detail or URL;
* search never imports or calls the LLM layer;
* every query is filtered to the current embedding model;
* no hardcoded answers exist in the RAG service.

### Live (real Groq, real Pinecone, real Whisper) — 23/23 checks passed

| # | Check | Result |
| --- | --- | --- |
| 1 | Backend starts; all routes register | ✅ |
| 2 | FFmpeg + Whisper transcription | ✅ 60 words, correct |
| 3 | Groq authentication | ✅ |
| 4 | Groq structured meeting intelligence | ✅ decision, owners, deadlines, priorities |
| 5 | Embeddings generated (384-d) | ✅ |
| 6 | Pinecone receives vectors | ✅ 15 vectors |
| 7 | Vector ids traceable to meetings | ✅ |
| 8 | Semantic search → correct meeting | ✅ score 0.85 |
| 9 | Meaning-based (paraphrase) match | ✅ |
| 10 | Metadata filters (source type, meeting) | ✅ |
| 11 | Search under 3 s | ✅ median 353 ms (20 runs) |
| 12 | Update without duplicates | ✅ 5 → 5 ids |
| 13 | Unchanged content skipped | ✅ |
| 14 | RAG answered by Groq from context | ✅ "launch next Monday" |
| 15 | RAG cites the right meeting | ✅ |
| 16 | Unknown question → no hallucination | ✅ `answer_found: false` |
| 17 | Delete removes vectors | ✅ 0 left |
| 18 | No Grok/Gemini requests possible | ✅ no such code remains |

Live testing wrote only to a separate `verification` namespace and deleted
everything afterwards. It used about **7 small Groq requests** in total (health
checks, two meeting analyses and three RAG questions across the runs).

### Not verified live

**Supabase-backed flows** (saving meetings, the dashboard, indexing real stored
meetings) could not be run live, because the configured Supabase project's
address does not resolve (§20). They are covered by the automated tests, and the
live API check confirmed they fail cleanly with `DATABASE_ERROR` rather than
crashing.

---

## 19. Deployment considerations

| Concern | Status |
| --- | --- |
| Frontend | unchanged — static build on Vercel |
| Backend host | unchanged — a long-running container (Render / Railway / Fly.io / VM), as Whisper and FFmpeg already require |
| New dependency | `fastembed` only; no PyTorch; reuses ONNX Runtime, tokenizers, huggingface_hub already present for Whisper; `pip check` clean |
| Memory | the embedding model adds an estimated 150–250 MB (not measured) to the existing ≥ 2 GB guidance |
| Model download | ~67 MB once. **Set `EMBEDDING_CACHE_DIR` to a persistent disk path**, or it is re-downloaded after restarts on hosts that wipe `/tmp` |
| Cold start | the model loads in a background thread at startup; the first search does not wait for it |
| Serverless | not suitable for the backend (Whisper/FFmpeg), exactly as before |
| Pinecone | index auto-created (384-d, cosine, serverless, aws/us-east-1); creation now waits until the index is ready |
| Groq | outbound HTTPS to `api.groq.com`; the pooled connection is closed cleanly on shutdown |
| Secrets | backend `.env` only; `.env` is git-ignored; nothing is exposed to React |
| CORS | unchanged; set `CORS_ORIGINS` to the deployed frontend URL |

---

## 20. Things found along the way

### Supabase project unreachable — action needed

The host in `SUPABASE_URL` does not resolve in DNS, while `supabase.com`,
Pinecone and Groq all do. That usually means the Supabase project is **paused**
(free projects pause after inactivity) or was deleted. **Restore it from the
Supabase dashboard**, then everything that stores data will work again. This is
outside the code.

### A `.env` parsing bug (fixed)

Lines such as

```env
TEMP_DIR=                           # blank = OS temp dir
```

were read by `python-dotenv` with the **comment as the value**, because it only
strips `# …` when whitespace comes *directly* before it and the spaces after `=`
are consumed first. Consequences found:

* uploads were staged in a folder literally named
  `backend/# blank = OS temp dir. Uploads live here only/` (pre-existing, Milestone 1);
* the Pinecone host became an invalid URL (this is what the first live run caught).

Fixed twice over: the settings loader now treats a value that starts with `#`
as blank, and those comments were moved onto their own lines in `.env.example`
and `.env`. A regression test covers it. The stray folders were removed.

### First-run index creation (fixed)

A newly created Pinecone index reports its address before it can accept
writes. Index creation now waits until Pinecone reports it ready, so the very
first indexing run cannot fail on a half-created index.

---

## How I would explain this to my mentor

> "The platform now uses **one** LLM provider, **Groq**, for everything that
> needs a language model: summarising meetings, extracting decisions and action
> items, and answering questions in Milestone 3. The old chain of Grok, then
> Gemini, then Groq was removed because the first two kept failing
> authorisation, and every failed attempt cost time before reaching the one that
> worked. All LLM calls now go through a single client file, so there is exactly
> one place that talks to Groq.
>
> Embeddings were a separate problem, because they came from Gemini too. Groq
> doesn't make embeddings — it generates text — so I switched to a small free
> model that runs on our own server, `bge-small`. It turns every passage and
> every question into 384 numbers that capture meaning, with no API key and no
> quota. The same model embeds both sides, and every vector records which model
> made it, so we never compare vectors from different models.
>
> I verified it live end to end: Whisper transcribed a spoken meeting, Groq
> extracted the right decision and deadlines in about a second, Pinecone found
> the right meeting even for a question that shared no keywords with it, and
> Groq answered 'the mobile application will launch next Monday' with the
> source meeting — and admitted it didn't know when asked about something that
> was never discussed. Search now takes about a third of a second."
