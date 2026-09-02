# Audio Processing & Transcription

A simple Streamlit application that allows users to upload audio or video recordings and generate transcripts using **OpenAI Whisper**.

This project was developed as part of **Milestone 1 – Audio Processing & Transcription** for the Infosys Springboard Virtual Internship.

---

## Features

- **Audio/Video upload** – Upload audio or video files for transcription
- **Multiple format support** – MP3, WAV, M4A, FLAC, AAC, OGG, MP4, MPEG, MPG, MOV, WEBM, AVI
- **File validation** – Extension, size, empty file, and corrupted file checks
- **File size validation** – Maximum 200 MB upload limit
- **Whisper transcription** – Powered by OpenAI Whisper (tiny / base / small models)
- **CPU support** – Works on CPU machines without requiring a GPU
- **Processing status** – Real-time progress updates during transcription
- **Transcript validation** – Checks for empty or invalid transcripts
- **Transcript saving** – Saves transcripts as `.txt` files with verification
- **Transcript download** – One-click download of the generated transcript
- **Accuracy testing** – Compare generated transcript with a reference transcript
- **≥90% accuracy target evaluation** – PASS / NEEDS IMPROVEMENT result
- **Multiple recording testing** – Test several recordings and track results in a session history table
- **Missing/incorrect word detection** – Shows which reference words were missed or incorrectly transcribed

---

## Technologies Used

| Technology | Purpose |
|---|---|
| Python 3.8+ | Programming language |
| Streamlit | Web application framework |
| OpenAI Whisper | Speech recognition / transcription |
| PyTorch | Deep learning backend for Whisper |
| FFmpeg | Audio/video processing (system dependency) |

---

## Installation

### 1. Clone or Open the Project

```bash
cd audio_transcription_project
```

### 2. Create Virtual Environment

**Windows:**

```bash
python -m venv venv
```

### 3. Activate Virtual Environment

**Windows PowerShell:**

```powershell
.\venv\Scripts\Activate.ps1
```

> If you get an execution policy error, run this first:
>
> ```powershell
> Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
> ```
>
> Then activate again.

**Windows Command Prompt:**

```cmd
venv\Scripts\activate
```

### 4. Install Dependencies

```bash
python -m pip install --upgrade pip
pip install -r requirements.txt
```

---

## FFmpeg Installation

Whisper requires **FFmpeg** to be installed on your system and available in the system PATH.

### Windows

1. Download FFmpeg from [https://ffmpeg.org/download.html](https://ffmpeg.org/download.html) or use a builds page such as [https://www.gyan.dev/ffmpeg/builds/](https://www.gyan.dev/ffmpeg/builds/).
2. Download the **release full** or **release essentials** build (zip file).
3. Extract the zip file to a folder, for example `C:\ffmpeg`.
4. Add the `bin` folder to your system PATH:
   - Open **System Properties** → **Advanced** → **Environment Variables**.
   - Under **System variables**, find `Path` and click **Edit**.
   - Click **New** and add: `C:\ffmpeg\bin`
   - Click **OK** to save.
5. Open a **new** terminal and verify:

```bash
ffmpeg -version
```

You should see FFmpeg version information. If you get "command not found", double-check the PATH.

> **Note:** FFmpeg cannot be installed via pip. It must be installed as a system-level dependency.

---

## Run the Application

Make sure your virtual environment is activated, then run:

```bash
streamlit run app.py
```

Streamlit will start a local web server. Open the URL displayed in the terminal (typically):

```
http://localhost:8501
```

---

## How to Use

### Upload and Transcribe a Recording

1. **Upload** an audio or video recording using the file uploader on the main page.
2. **Verify** the file details (name, type, size) and the validation status shown on screen.
3. **Select** a Whisper model from the sidebar (tiny / base / small). Default is `base`.
4. Click **🎙️ Transcribe Recording**.
5. **Wait** for processing – status updates are shown in real time for each step:
   - File uploaded
   - File validated
   - Audio processed
   - Whisper model loaded
   - Transcription complete
   - Transcript generated
   - Transcript saved and verified
6. **View** the generated transcript in the text area.
7. **Download** the transcript using the **⬇️ Download Transcript** button.

### Perform Accuracy Testing

1. After generating a transcript, scroll down to the **Accuracy Testing** section.
2. **Paste** the actual / reference transcript (the real spoken words) into the text area.
3. Click **🔍 Calculate Accuracy**.
4. **View** the results:
   - **Estimated Transcription Accuracy** – a percentage value
   - **PASS** (≥90%) or **NEEDS IMPROVEMENT** (<90%)
   - **Missing / Incorrect Words** – words from the reference that were missed or incorrect

### Test Multiple Recordings

You can test multiple recordings during a single application session:

1. Upload **Recording 1** → Transcribe → Paste reference → Calculate Accuracy → See result.
2. Upload **Recording 2** → Transcribe → Paste reference → Calculate Accuracy → See result.
3. Repeat for as many recordings as needed.

Each accuracy test result is recorded in the **Accuracy Testing History** table at the bottom of the page. This table shows:

| # | Recording | Accuracy | Result |
|---|---|---|---|
| 1 | meeting.mp3 | 94.20% | PASS |
| 2 | interview.wav | 87.50% | NEEDS IMPROVEMENT |

The history is maintained for the current session.

### How the ≥90% Target is Evaluated

- The application calculates **word-level accuracy** by comparing the generated transcript against the reference transcript you provide.
- Both texts are **normalized** (lowercased, punctuation removed, whitespace collapsed) before comparison.
- `difflib.SequenceMatcher` is used to align the word sequences and count correct matches.
- **Accuracy = (correct words / total reference words) × 100**
- If accuracy ≥ 90%: result is **PASS**
- If accuracy < 90%: result is **NEEDS IMPROVEMENT**
- The accuracy is calculated from the **actual comparison** — it is never hard-coded or artificially adjusted.

---

## Supported File Formats

### Audio

| Format | Extension |
|---|---|
| MP3 | `.mp3` |
| WAV | `.wav` |
| M4A | `.m4a` |
| FLAC | `.flac` |
| AAC | `.aac` |
| OGG | `.ogg` |

### Video

| Format | Extension |
|---|---|
| MP4 | `.mp4` |
| MPEG | `.mpeg` |
| MPG | `.mpg` |
| MOV | `.mov` |
| WebM | `.webm` |
| AVI | `.avi` |

---

## Project Structure

```
audio_transcription_project/
│
├── app.py                          # Main Streamlit application
├── requirements.txt                # Python dependencies
├── README.md                       # This file
├── IMPLEMENTATION_EXPLANATION.md   # Detailed implementation explanation
│
├── uploads/                        # Temporary storage for uploaded files
│   └── .gitkeep
│
├── transcripts/                    # Saved transcript files
│   └── .gitkeep
│
└── venv/                           # Python virtual environment
```

---

## Troubleshooting

### FFmpeg not found

Whisper requires FFmpeg as a system-level dependency. Install FFmpeg and add it to your system PATH. See the [FFmpeg Installation](#ffmpeg-installation) section above.

Verify FFmpeg is installed:

```bash
ffmpeg -version
```

### Virtual environment not activating

**PowerShell:**

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\venv\Scripts\Activate.ps1
```

**Command Prompt:**

```cmd
venv\Scripts\activate
```

### Module not found

Make sure your virtual environment is activated and install dependencies:

```bash
pip install -r requirements.txt
```

### Transcription is slow

- Use the **tiny** or **base** model (selected in the sidebar).
- Use shorter recordings for testing.
- Larger models (`small`) provide better accuracy but are significantly slower on CPU.

### CUDA / GPU issues

This project works using **CPU** by default (`fp16=False` is set automatically). If you have an NVIDIA GPU with CUDA installed, Whisper will automatically use it for faster processing. No additional configuration is required.

### Empty transcript

If Whisper returns an empty transcript, check:
- The recording contains audible speech.
- The audio is not corrupted.
- The volume is sufficient.
- Try a different Whisper model.

### Accuracy below 90%

Transcription accuracy depends on:
- Audio quality and background noise
- Speaker clarity and accent
- Language
- Whisper model size (larger = more accurate)

Use clear, high-quality recordings with minimal background noise for best results.

---

## License

This project is for educational purposes as part of the Infosys Springboard Virtual Internship.
