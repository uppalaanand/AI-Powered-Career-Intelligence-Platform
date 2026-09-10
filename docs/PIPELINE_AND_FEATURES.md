# Pipeline and features

A plain-language walkthrough of how this project works, written to be read start to finish. No
prior knowledge of Whisper, FFmpeg or language models is assumed.

---

## Contents

1. [Project overview](#1-project-overview)
2. [The big picture](#2-the-big-picture)
3. [Milestone 1 explained step by step](#3-milestone-1-explained-step-by-step)
4. [Milestone 2 explained step by step](#4-milestone-2-explained-step-by-step)
5. [How the database is organised](#5-how-the-database-is-organised)
6. [How the frontend is organised](#6-how-the-frontend-is-organised)
7. [How errors are handled](#7-how-errors-are-handled)
8. [Design decisions and why](#8-design-decisions-and-why)
9. [How to explain this project to the mentor](#9-how-to-explain-this-project-to-the-mentor)
10. [Two-minute demo script](#10-two-minute-demo-script)

---

## 1. Project overview

**The problem.** In most meetings somebody says "I'll have that done by Friday", everyone nods, and
two weeks later nobody remembers who said it or what "that" was. Meetings generate commitments
faster than they generate records of them.

**What this does.** You upload a recording of the meeting. The application:

1. writes down everything that was said, with timestamps
2. summarises what the meeting was about
3. lists the decisions that were actually made
4. lists every task somebody committed to, with the owner, the deadline and how urgent it is
5. lets you check how accurate the transcription was

**The rule that shapes everything.** When the recording does not say something, the application
says "not stated" rather than making a sensible guess. A tool that invents a deadline is worse than
no tool at all, because you would trust it.

---

## 2. The big picture

```
        You
         |
         |  upload a recording
         v
  React frontend  (what you see in the browser)
         |
         |  HTTP request
         v
  FastAPI backend  (the brain, on the server)
         |
         +--> FFmpeg    turns any video/audio into clean audio
         +--> Whisper   turns audio into text
         +--> Grok      turns text into structured facts
         +--> Supabase  stores everything
         |
         v
  React frontend displays the results
```

Five pieces, each doing one job:

| Piece | One-line job |
| --- | --- |
| **React** | Shows the interface and talks to the backend |
| **FastAPI** | Receives requests, runs the pipeline, returns JSON |
| **FFmpeg** | Converts any media file into audio Whisper can read |
| **Whisper** | Converts speech into text |
| **Grok** | Reads the text and extracts structured facts |
| **Supabase** | Stores the results so they survive a page refresh |

---

## 3. Milestone 1 explained step by step

```
You upload a meeting recording
             |
             v
    Backend validates the file          <-- reject bad files early
             |
             v
    FFmpeg extracts and normalises audio
             |
             v
    Whisper converts speech to text
             |
             v
    Transcript validation                <-- is this actually usable?
             |
             v
    Saved to the database
             |
             v
    React displays it two ways
```

### Step 1 — Upload

You drag a file onto the page. Before sending it, the browser does a quick check: is the extension
one we support, is the file non-empty, is it under the size limit? This is only a courtesy to save
you a pointless upload. The backend does not trust it and re-checks everything properly.

The file is sent in chunks and the progress bar shows real bytes transferred, not an animation.

### Step 2 — Validation

This is where bad files are stopped, using four checks in order from cheapest to most expensive:

**1. Extension.** Is `.mp3` on our list? If you upload `.pdf`, it stops here.

**2. Size.** Empty files and files over the limit (200 MB by default) are rejected.

**3. Content sniff.** The first few bytes of a file identify what it really is. Every MP3 starts
with a recognisable marker; every PDF starts with `%PDF`. So if you rename `report.pdf` to
`song.mp3`, this check catches the lie.

**4. ffprobe.** FFmpeg's inspection tool opens the file and reports what is inside. This catches a
half-downloaded video, a corrupted file, and a video with no audio track at all (valid media, but
nothing to transcribe, so we say exactly that).

Why four checks instead of just looking at the extension? Because the extension is just the last
few characters of a filename. Anyone can type anything there. Every one of these checks catches a
real failure the others miss.

### Step 3 — FFmpeg

Media files come in many containers (MP4, MKV, MOV) with many codecs inside them (AAC, MP3, Opus).
Whisper wants one specific thing: 16,000 samples per second, single channel, uncompressed.

FFmpeg does that conversion:

```
Any video or audio file
        |
        |  drop the video track (we only need sound)
        |  keep the first audio track
        |  mix stereo down to mono
        |  resample to 16 kHz
        |  write as uncompressed WAV
        v
audio-<meeting-id>.wav
```

Whisper would resample internally anyway, but doing it once up front with FFmpeg is faster and
removes every difference between an MKV screen recording and an M4A phone memo. After this step,
every file looks identical to Whisper.

### Step 4 — Whisper

Whisper is OpenAI's speech recognition model. It runs locally on your machine, so no audio is sent
to a third party and there is no per-minute cost.

It returns the text plus a list of **segments**, each with a start time, an end time and the words
spoken in that window. The segments are what make the timeline view possible.

The project uses `faster-whisper`, which is the same Whisper models rebuilt to run about four times
faster on a CPU without needing a 2 GB PyTorch install. The code supports the original
`openai-whisper` too; changing `WHISPER_BACKEND` in the configuration switches between them, and
nothing else in the application changes.

### Step 5 — Transcript validation

Whisper always returns *something*, even for a silent recording. This step decides whether that
something is usable:

- Is there any text at all? Silence produces an empty transcript, which is an error, not a result.
- Do the segments make sense? A segment that ends before it starts means broken timestamps.
- Does this transcript belong to this meeting? A safety check against mixing up records.
- Does the speech cover a reasonable portion of the recording? If not, we warn (but do not block),
  because it usually means long silent stretches.

Only after passing does anything get saved. A failed transcript produces a clear error rather than
an empty row in the database.

### Step 6 — Saving

Two things are stored:

- The full transcript text goes on the `meetings` row, because it is always needed with the meeting.
- The segments go in their own `transcript_segments` table, because there can be hundreds of them
  and they are only needed for the timeline view.

Re-running transcription **replaces** that meeting's segments rather than adding to them, so you
can never end up with the transcript twice.

The uploaded recording is then deleted from the server. Only the transcript is kept.

### Step 7 — Displaying

**Paragraph view** — for reading. Segments are joined into paragraphs, with a new paragraph
starting after a pause of two seconds or more, so it reads like a document rather than a wall
of text.

**Timeline view** — for finding a moment. Every segment with its start and end time in a fixed-width
column, so the timestamps line up and you can scan them. If speaker labels are available they
appear here.

### Step 8 — Downloads

Four formats, all generated from the real stored transcript:

| Format | File | Good for |
| --- | --- | --- |
| Paragraph | `meeting_transcript.txt` | Reading, pasting into a document |
| Timeline | `meeting_transcript_timeline.txt` | Reviewing with timestamps |
| JSON | `meeting_transcript.json` | Feeding into another program |
| CSV | `meeting_transcript.csv` | Opening in Excel |

### Step 9 — Accuracy testing

How do you know the transcription is any good? You measure it.

Paste a correct, human-written transcript of the same recording. The backend lines the two
transcripts up word by word and counts three kinds of mistake:

| Mistake | Meaning | Example |
| --- | --- | --- |
| **Substitution** | Heard the wrong word | "Ravi" became "Robbie" |
| **Deletion** | Missed a word entirely | "the API integration" became "integration" |
| **Insertion** | Added a word never said | "Friday" became "Friday afternoon" |

```
Word Error Rate = (substitutions + deletions + insertions) / words in the reference
Accuracy = 100 - Word Error Rate
```

Worked example. Reference: *"ravi will finish the api integration by friday"* (8 words).
Transcript: *"robbie will finish integration by friday today"*.

- `ravi` → `robbie` = 1 substitution
- `the` and `api` missing = 2 deletions
- `today` added = 1 insertion
- Total 4 errors ÷ 8 reference words = **50% WER**, so **50% accuracy**

The interface shows the score against the 90% target with a PASSED or NEEDS IMPROVEMENT verdict,
plus the actual lists of missing, incorrect and extra words, so a bad score can be diagnosed
instead of just observed.

**Important honesty note.** The project does not claim to achieve 90% accuracy. It provides the
measuring tool that lets you demonstrate whether a particular recording reaches it. Accuracy
depends on audio quality, accents, background noise and which Whisper model you chose.

---

## 4. Milestone 2 explained step by step

```
The stored transcript
        |
        v
Split into chunks if it is long
        |
        v
Send to the AI provider chain with a strict prompt
Grok -> Gemini -> Groq, stopping at the first valid answer
        |
        v
A provider returns JSON
        |
        v
Parse and validate it against a schema     <-- next provider if wrong
        |
        v
Map and de-duplicate participants
        |
        v
Save to Supabase
        |
        v
React displays the intelligence
```

### Step 1 — Chunking long transcripts

A language model can only read so much at once. A two-hour meeting can be 20,000 words, and sending
all of it in one request risks hitting that limit. It also produces a vague summary, because the
important details get diluted.

So long transcripts are split into chunks of about 9,000 characters, cut at sentence boundaries so
no sentence is ever broken in half. Consecutive chunks **overlap** by about 600 characters.

Why overlap? Because a task assigned at the end of one chunk often has its deadline stated at the
start of the next. Repeating a little context keeps those pairs together.

Each chunk is analysed separately, then the results are merged. Short transcripts skip all this and
go through in a single request.

### Step 2 — The prompt

The prompt is the instruction sent to Grok. It lives in
`backend/app/ai/prompts/meeting_intelligence_prompt.py` and does three things:

1. **Describes the job.** Read this transcript, extract these specific things.
2. **Defines the exact output shape.** A JSON template with every field named.
3. **Sets the rules**, which are mostly about *not* inventing things:
   - Use only what the transcript says
   - If a deadline is not stated, return `null`. Never invent a date.
   - If nobody is clearly responsible, return `null`. Never assign work to someone who did not
     accept it.
   - If there were no decisions, return an empty list. An empty list is a correct answer.
   - Return only JSON, no explanation

### Step 3 — Calling the AI provider chain

The request goes to **Grok (xAI) first**. If Grok cannot answer, the same request goes to
**Google Gemini**, and if that fails, to **Groq**. The chain stops the moment one of them returns a
valid answer, so a working Grok means Gemini and Groq are never contacted.

Things go wrong on networks and on free tiers, so each failure is classified rather than blindly
retried:

| Problem | Response |
| --- | --- |
| Network timeout | One controlled retry, then the next provider |
| Server error (500-504) | One controlled retry, then the next provider |
| Rate limited (429) / quota exhausted | **Do not retry** — go straight to the next provider, so no more of that quota is spent |
| Bad API key (401/403) | **Do not retry** — retrying a wrong key just wastes time |
| Model not found (404) | **Do not retry** — this is a configuration mistake |
| No API key set for a provider | The provider is **skipped without being called** |

Nothing loops forever: one provider spends at most `LLM_PROVIDER_MAX_ATTEMPTS` requests on one
prompt (two by default, one if you set it to `1`), and there are at most three providers.

This matters because the API accounts are on free tiers. A normal analysis costs **one** request.
The providers are never called in parallel, never all called to compare answers, and an
already-analysed meeting is read back from Supabase without calling anything at all.

### Step 4 — Parsing and validating

Models sometimes wrap JSON in explanation text or markdown fences even when told not to. The parser
handles that: it finds the JSON inside surrounding prose, strips code fences, and fixes trailing
commas and curly quotes.

Then the parsed JSON is checked against a **Pydantic schema**, which defines exactly what a valid
response looks like. If `summary` is missing, or `priority` says "extremely urgent" instead of
`high`/`medium`/`low`, validation fails.

When it fails, the model is asked again with a message explaining what was wrong. If it fails
again, that provider is treated as failed and the **next provider in the chain** is tried with the
same prompt. Only when every configured provider has failed does the request return a clear error.
**Malformed AI output is never saved**, whichever provider produced it.

The schema is also forgiving where it safely can be: `urgent` is accepted as `high`, `done` as
`completed`, and `[{"text": "..."}]` is accepted where `["..."]` was requested. These are
formatting variations, not content changes.

### Step 5 — Merging chunks

When a transcript was split, each chunk produces its own analysis and they have to become one.

The merge is attempted by the model first, because it writes a better connected summary than
gluing chunk summaries together. But the merged action item lists are then combined with the
originals and de-duplicated, because a merge model sometimes drops an item, and **losing a real
action item is worse than showing a near-duplicate**.

If the merge call fails entirely, a deterministic local merge takes over: concatenate the
summaries, combine the lists, remove duplicates. Not as elegant, but it always works and it never
invents anything.

### Step 6 — Participant mapping

The model returns names inconsistently: `Ravi`, `ravi`, `RAVI`, `Dr. Ravi`, `Ravi Kumar`. These are
one person and must become one record.

First each name is **normalised** into a comparison key: lowercase, punctuation removed, titles
like "Dr." stripped. So all five of those become `ravi` or `ravi kumar`.

Then names are grouped, from safest rule to loosest:

| Rule | Example | Result |
| --- | --- | --- |
| Identical keys | `ravi`, `Ravi`, `RAVI` | one person |
| First name inside a full name | `Ravi` + `Ravi Kumar` | one person, shown as `Ravi Kumar` |
| Nearly identical spelling (90%+) | `Priya` + `Priyaa` | one person |
| Anything else | `Ravi` + `Rahul` | **two** people |

That last row is deliberate. `Ravi` and `Rahul` are 50% similar, well below the threshold, so they
stay separate. **Merging two real people is a worse mistake than listing one person twice**, so the
rules are strict and very short names are never fuzzy-matched at all.

Placeholder names (`Unknown`, `Speaker 1`, `someone`, `team`) never become participants. If a task
was assigned to "someone", the owner is stored as `null` and the interface shows "Not stated".

Finally, every action item's owner is rewritten to the canonical name, so a task assigned to `ravi`
links to the same person as one assigned to `Ravi Kumar`.

### Step 7 — Saving

Re-analysing a meeting **replaces** its previous analysis instead of adding a second copy. The
database also enforces this: a constraint on `(meeting_id, normalized_name)` makes duplicate
participants impossible even if the code had a bug.

### Step 8 — Displaying

The meeting intelligence page shows the summary, action items in a table, key points, decisions,
participants, and deadlines. Deadlines are **derived** from action items rather than extracted
separately, so a date can never appear without the task it belongs to.

---

## 5. How the database is organised

Seven tables rather than one big blob of JSON:

```
meetings  (one row per recording)
    |
    +---< transcript_segments   the timed pieces of the transcript
    +---< participants          the people, de-duplicated
    +---< action_items          tasks (each may link to a participant)
    +---< decisions             what was decided
    +---< key_points            what was discussed
    +---< meeting_summaries     the summary
```

**Why not one JSON column?** Because you would not be able to ask "show me every open action item
across all meetings" without loading and unpacking every record. Real tables mean real queries,
real indexes, and real constraints.

The one place JSON is used is `media_metadata`, which stores FFmpeg's technical report about the
file. That genuinely is schemaless and is only ever read as a whole.

`ON DELETE CASCADE` means deleting a meeting automatically removes its transcript, participants,
action items and everything else. No orphaned rows.

---

## 6. How the frontend is organised

```
src/
  pages/        One file per screen (Dashboard, Upload, Meetings, MeetingDetail)
  components/   Reusable pieces, grouped by what they are for
  hooks/        Reusable behaviour (loading data, running the pipeline)
  services/api/ Every network call in the application
  utils/        Formatting and shared constants
  types/        Descriptions of the data shapes
```

**Every network call goes through `services/api/`.** No component ever calls `fetch` directly. That
means timeouts, error translation and the response envelope are handled in exactly one place. If
the API changes, one folder changes.

**The processing screen tells the truth.** Each stage turns green only when the backend call that
performs it has actually returned. Nothing is ticked on a timer, and there is no animation
pretending work is happening.

---

## 7. How errors are handled

Every error carries three things:

| Part | Example | Who reads it |
| --- | --- | --- |
| HTTP status | `415` | The browser |
| Error code | `UNSUPPORTED_FILE_TYPE` | The frontend code |
| Message | "That file type is not supported." | The person |

The code never changes, so the frontend can branch on it reliably. The message can be reworded
without breaking anything.

Stack traces and database driver messages are written to the server log and **never** sent to the
browser. Internal details are exactly what an attacker wants.

---

## 8. Design decisions and why

| Decision | Reason |
| --- | --- |
| `faster-whisper` as the default | Same models, ~4x faster on CPU, no 2 GB PyTorch download. The original is still selectable. |
| Convert audio with FFmpeg first | One predictable format reaching Whisper, instead of every codec variation. |
| Upload and transcribe as separate API calls | The interface can show genuine per-stage progress. One giant endpoint could only show a spinner. |
| Delete the recording after transcription | Storage cost and privacy. Only the transcript has lasting value. |
| Word Error Rate implemented by hand | The interface needs the alignment (which words were missed), not just a score. |
| Temperature 0.1 for Grok | Low temperature makes the model literal, which is what fact extraction needs. |
| Nullable `assigned_to` and `deadline` | A schema that required a string would force the model to invent one. |
| Strict participant merging | Merging two real people is a worse failure than a duplicate row. |
| Replace-on-rerun | Re-running transcription or analysis cannot produce duplicate records. |
| Seven tables, not one JSON blob | Real queries, real constraints, real indexes. |
| JavaScript with JSDoc, not TypeScript | Editor type hints without adding a compile step to the build. |

---

## 9. How to explain this project to the mentor

Short, technically correct answers to the questions most likely to be asked.

### What is the project?

A web application that turns meeting recordings into useful records. You upload an audio or video
file; it produces a transcript, a summary, the decisions taken, and a list of action items with
owners and deadlines. It has two halves: Milestone 1 is speech to text, Milestone 2 is turning that
text into structured facts with a language model.

### Why did you choose React?

The brief asked for React instead of Streamlit. It suits this application anyway: the processing
screen updates through several stages, the transcript has switchable views, and results load
independently. React re-renders just the part that changed. Vite is the build tool because it gives
near-instant reloads in development and a small optimised bundle for production.

### Why Python?

The whole speech and AI ecosystem is Python-first. Whisper, the FFmpeg bindings and the Supabase
client are all natively Python, so there is no bridging layer.

### Why FastAPI?

Three reasons. It is asynchronous, so it can wait on the Grok API without blocking other requests.
It uses Pydantic, so the same type definitions that validate incoming requests also generate the
API documentation. And that documentation is generated automatically at `/docs`, which means it
cannot drift out of date.

### Why Whisper?

It is accurate across accents and languages, and it runs locally. That means no audio leaves the
machine and there is no per-minute cost. It also returns timestamped segments, which is what makes
the timeline view possible. I used `faster-whisper`, which is the same models rebuilt to run about
four times faster on CPU.

### Why FFmpeg?

Recordings arrive in many containers and codecs. Whisper wants one specific format: 16 kHz, mono,
uncompressed. FFmpeg handles every format there is, so it does that conversion and strips the video
track. After FFmpeg, every file looks identical to Whisper regardless of what was uploaded.

### Why a chain of providers?

Grok was the specified provider, and it is still the primary one. The problem is that free/limited
API access can fail for reasons that have nothing to do with the meeting: an expired key, a `401`,
an exhausted daily quota, a model that was retired. Losing the whole analysis to that would be a
poor trade when the transcript has already been produced.

So each vendor is isolated behind one small `LLMProvider` class, and an orchestrator tries them in
order — Grok, then Gemini, then Groq — stopping at the first valid answer. The rest of the
application cannot tell which one responded: all three are given the same prompt and validated
against the same schema. Adding a fourth provider is one new file and one registry entry.

Details: [`MULTI_MODEL_AI_FALLBACK.md`](MULTI_MODEL_AI_FALLBACK.md).

### Why Supabase?

It is managed PostgreSQL, so I get a real relational database — foreign keys, constraints, indexes
— without running a database server. It has a straightforward Python client, and Row Level Security
is there for when authentication is added.

### What happens when a user uploads a video?

Seven steps. It is validated (extension, size, content bytes, and ffprobe confirming a real audio
stream). FFmpeg extracts the audio and converts it to 16 kHz mono. Whisper transcribes it. The
transcript is validated for emptiness and timestamp sanity. It is saved to Supabase along with its
segments. The video file is deleted from the server. Then Grok analyses the transcript, and the
results are saved and displayed.

### How does transcription work?

Whisper is a neural network trained on a very large amount of audio paired with text. It breaks the
audio into short overlapping windows, converts each into a spectrogram — a picture of which
frequencies are present over time — and predicts the most likely sequence of words. It outputs the
text plus segments with start and end times.

### How do you validate the transcript?

Four checks before anything is stored. Is there any text at all, because a silent recording
produces an empty result. Do the timestamps make sense, meaning no segment ends before it starts
and none is unrealistically long. Does the transcript belong to the meeting it is being attached
to. And does the transcribed speech cover a reasonable portion of the recording, which is a warning
rather than a hard failure. If any hard check fails, the user gets a clear error instead of an
empty record.

### How do you calculate accuracy?

Word Error Rate. The user pastes a correct reference transcript, and I align it with the generated
one using a Levenshtein edit-distance algorithm over words. That gives the minimum number of edits
to turn one into the other, classified as substitutions, deletions and insertions. WER is the total
errors divided by the number of reference words, and accuracy is 100 minus WER. I implemented it
rather than using a library because the interface needs the actual alignment — which specific words
were missed or misheard — not just the final number.

### How does the LLM extract action items?

The prompt tells it exactly what an action item is — a task somebody committed to — and gives it a
JSON template with fields for the task, owner, deadline, priority and status. The response is
parsed and validated against a Pydantic schema. If it does not match, the model is asked again with
a correction. If it fails twice, the request errors out rather than storing bad data.

### How do you prevent hallucinated information?

Four layers, because prompting alone is not reliable. The prompt states the rule explicitly and
repeatedly. Temperature is set to 0.1, which makes the model literal. The schema allows `null` for
owner and deadline, so "not stated" is representable — a schema that required a string would force
the model to invent one. And post-validation converts every way a model says "nothing here", like
"unknown" or "N/A" or "TBD", into a real `null`. The interface then displays "Not stated" rather
than a blank that might look like an oversight.

### How are participants mapped?

Names are normalised into a comparison key: lowercase, punctuation removed, titles stripped. Then
they are grouped by three rules — exact key match, a first name matching the first name of a fuller
name, and 90%-plus string similarity for typos. Anything below that stays separate, so "Ravi" and
"Rahul" remain two people. The strictness is deliberate: merging two real people is a worse failure
than listing one twice. There is also a database constraint on meeting id plus normalised name, so
duplicates are impossible at the storage layer too.

### How is the data stored?

Seven normalised tables in Supabase, with the meeting as the parent and transcript segments,
participants, action items, decisions, key points and the summary as children, all cascading on
delete. Normalised rather than one JSON blob so that queries like "all open action items" are
possible. Re-running transcription or analysis replaces that meeting's rows rather than adding
more, so retries cannot create duplicates.

### How does the frontend communicate with the backend?

Plain HTTP with JSON. Every call goes through one API service layer, so timeouts and error handling
live in a single place. The backend always responds with the same envelope: a `success` flag, a
`data` object, and on failure an `error` object with a stable code the frontend switches on to show
the right message. In development, Vite proxies `/api` to the backend so there is no cross-origin
request; in production the backend allows the deployed frontend's domain through CORS.

### How is the application deployed?

Three separate pieces. The React frontend builds to static files and goes on Vercel. The backend
goes on a normal Python host such as Render or Railway. The database is Supabase, already hosted.

The backend cannot go on Vercel, and that is a deliberate architectural point: Whisper is
CPU-heavy and long-running, FFmpeg is a system binary, and the model weights are hundreds of
megabytes. Serverless functions have short time limits and a read-only filesystem, so any recording
longer than about a minute would fail. Splitting them means each piece runs where it fits.

---

## 10. Two-minute demo script

1. **Open the dashboard.** "This is the overview: total meetings, how many are processed, open
   action items, and total audio transcribed. The badge in the corner shows all services are
   configured and reachable."

2. **Go to Upload and drop in a recording.** "Real upload progress here. Then these stages — each
   one turns green only when the backend actually finishes that step, so this is genuine progress,
   not an animation."

3. **When it finishes, open the transcript.** "Paragraph view for reading. Timeline view for
   finding a specific moment, with timestamps on every segment. And four download formats, all
   generated from the stored transcript."

4. **Scroll to accuracy testing and paste a reference transcript.** "This measures Word Error Rate
   against the 90% target. It lists the actual missing, incorrect and extra words, so a low score
   can be diagnosed. I want to be clear that the project doesn't promise 90% — it gives you the
   tool to measure whether a given recording achieves it."

5. **Switch to Meeting intelligence.** "Grok's analysis: the summary, action items with owner,
   deadline and priority, key points, decisions and participants."

6. **Point at a row with an empty owner.** "This is the part I'd most like to highlight. This task
   says 'Not stated' because nobody in the recording said who would do it. The model is explicitly
   instructed to return null rather than guess, the schema allows null, and the UI displays that
   honestly. A tool that invented a name here would be worse than no tool, because you'd trust it."

7. **Open `/docs`.** "The API documentation generates itself from the same type definitions that
   validate the requests, so it can't go out of date."
