"""
Audio Processing and Whisper Transcription
===========================================
A Streamlit application that allows users to upload audio/video recordings
and generate transcripts using OpenAI's Whisper speech recognition model.

Milestone 1 – Audio Processing & Transcription
Infosys Springboard Virtual Internship
"""

import os
import re
import difflib
import pandas as pd
import streamlit as st
import whisper

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

# Supported file formats
AUDIO_EXTENSIONS = [".mp3", ".wav", ".m4a", ".flac", ".aac", ".ogg"]
VIDEO_EXTENSIONS = [".mp4", ".mpeg", ".mpg", ".mov", ".webm", ".avi"]
SUPPORTED_EXTENSIONS = AUDIO_EXTENSIONS + VIDEO_EXTENSIONS

# Maximum upload size in bytes (200 MB)
MAX_FILE_SIZE_BYTES = 200 * 1024 * 1024
MAX_FILE_SIZE_LABEL = "200 MB"

# Whisper model options
MODEL_OPTIONS = ["tiny", "base", "small"]
DEFAULT_MODEL = "base"

# Accuracy target percentage
ACCURACY_TARGET = 90

# Directory paths (relative to this script)
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
UPLOADS_DIR = os.path.join(BASE_DIR, "uploads")
TRANSCRIPTS_DIR = os.path.join(BASE_DIR, "transcripts")

# Ensure directories exist
os.makedirs(UPLOADS_DIR, exist_ok=True)
os.makedirs(TRANSCRIPTS_DIR, exist_ok=True)


# ---------------------------------------------------------------------------
# Helper Functions
# ---------------------------------------------------------------------------

def sanitize_filename(filename: str) -> str:
    """Remove unsafe characters from a filename, keeping the extension."""
    name, ext = os.path.splitext(filename)
    # Keep only alphanumeric, hyphens, underscores, and spaces
    safe_name = re.sub(r"[^\w\s-]", "", name).strip()
    # Replace whitespace runs with a single underscore
    safe_name = re.sub(r"\s+", "_", safe_name)
    if not safe_name:
        safe_name = "uploaded_file"
    return safe_name + ext.lower()


def validate_uploaded_file(uploaded_file) -> tuple:
    """
    Validate the uploaded file.

    Returns:
        (is_valid, error_message) – error_message is empty when valid.
    """
    if uploaded_file is None:
        return False, "Please upload a valid audio or video file."

    # Check file size (uploaded_file.size is in bytes)
    if uploaded_file.size == 0:
        return False, "❌ The uploaded file is empty."

    if uploaded_file.size > MAX_FILE_SIZE_BYTES:
        return False, (
            f"❌ File is too large. Maximum allowed size is {MAX_FILE_SIZE_LABEL}. "
            f"Your file is {uploaded_file.size / (1024 * 1024):.1f} MB."
        )

    # Check file extension
    _, ext = os.path.splitext(uploaded_file.name)
    if ext.lower() not in SUPPORTED_EXTENSIONS:
        return False, (
            "❌ Unsupported file format. Please upload a supported audio or video file. "
            f"Received: **{ext}**"
        )

    return True, ""


def save_uploaded_file(uploaded_file) -> str:
    """
    Save the uploaded file to the uploads/ directory.

    Returns:
        The full path to the saved file.
    """
    safe_name = sanitize_filename(uploaded_file.name)
    save_path = os.path.join(UPLOADS_DIR, safe_name)

    # Avoid overwriting existing files by appending a number
    if os.path.exists(save_path):
        name, ext = os.path.splitext(safe_name)
        counter = 1
        while os.path.exists(save_path):
            save_path = os.path.join(UPLOADS_DIR, f"{name}_{counter}{ext}")
            counter += 1

    with open(save_path, "wb") as f:
        f.write(uploaded_file.getbuffer())

    return save_path


@st.cache_resource
def load_whisper_model(model_name: str):
    """
    Load and cache a Whisper model so it is not reloaded on every interaction.
    """
    model = whisper.load_model(model_name)
    return model


def transcribe_audio(file_path: str, model_name: str) -> str:
    """
    Run Whisper transcription on the given audio/video file.

    Returns:
        The raw transcript text.
    """
    model = load_whisper_model(model_name)
    result = model.transcribe(file_path, fp16=False)
    return result.get("text", "")


def clean_transcript(raw_text: str) -> str:
    """
    Clean up the transcript text:
    - Strip leading/trailing whitespace
    - Collapse multiple spaces into one
    """
    text = raw_text.strip()
    text = " ".join(text.split())
    return text


def save_transcript(transcript: str, original_filename: str) -> str:
    """
    Save the transcript to a .txt file in the transcripts/ directory.

    Returns:
        The full path to the saved transcript file.
    """
    name, _ = os.path.splitext(sanitize_filename(original_filename))
    transcript_filename = f"{name}_transcript.txt"
    save_path = os.path.join(TRANSCRIPTS_DIR, transcript_filename)

    # Avoid overwriting existing transcripts
    if os.path.exists(save_path):
        counter = 1
        while os.path.exists(save_path):
            save_path = os.path.join(
                TRANSCRIPTS_DIR, f"{name}_transcript_{counter}.txt"
            )
            counter += 1

    with open(save_path, "w", encoding="utf-8") as f:
        f.write(transcript)

    return save_path


def validate_saved_transcript(saved_path: str, expected_text: str) -> tuple:
    """
    Verify the transcript was saved correctly.

    Returns:
        (is_valid, message)
    """
    if not os.path.exists(saved_path):
        return False, "Transcript file was not created."

    if os.path.getsize(saved_path) == 0:
        return False, "Saved transcript file is empty."

    try:
        with open(saved_path, "r", encoding="utf-8") as f:
            saved_content = f.read()
    except Exception:
        return False, "Unable to read the saved transcript file."

    if saved_content.strip() != expected_text.strip():
        return False, "Saved transcript does not match the generated transcript."

    return True, "Transcript saved and verified successfully."


def normalize_text(text: str) -> str:
    """
    Normalize text for comparison:
    - Convert to lowercase
    - Remove punctuation
    - Collapse extra whitespace
    """
    text = text.lower()
    text = re.sub(r"[^\w\s]", "", text)
    text = " ".join(text.split())
    return text


def calculate_accuracy(reference_text: str, generated_text: str) -> tuple:
    """
    Compare the reference transcript with the generated transcript using
    word-level accuracy via difflib.SequenceMatcher.get_opcodes().

    Returns:
        (accuracy_percentage, list_of_incorrect_or_missing_words)
        Returns (None, []) if reference is empty.
        Returns (0.0, reference_words) if generated is empty.
    """
    reference = normalize_text(reference_text)
    generated = normalize_text(generated_text)

    if not reference:
        return None, []

    if not generated:
        return 0.0, reference.split()

    reference_words = reference.split()
    generated_words = generated.split()

    matcher = difflib.SequenceMatcher(
        None,
        reference_words,
        generated_words
    )

    correct_words = 0
    incorrect_or_missing = []

    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            correct_words += (i2 - i1)
        elif tag == "delete":
            incorrect_or_missing.extend(reference_words[i1:i2])
        elif tag == "replace":
            incorrect_or_missing.extend(reference_words[i1:i2])
        elif tag == "insert":
            pass  # extra words in generated – not counted against accuracy

    accuracy = (correct_words / len(reference_words)) * 100

    return accuracy, incorrect_or_missing


def format_file_size(size_bytes: int) -> str:
    """Return a human-readable file size string."""
    if size_bytes < 1024:
        return f"{size_bytes} B"
    elif size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} KB"
    else:
        return f"{size_bytes / (1024 * 1024):.2f} MB"


def get_file_category(filename: str) -> str:
    """Return 'Audio' or 'Video' based on the file extension."""
    _, ext = os.path.splitext(filename)
    if ext.lower() in AUDIO_EXTENSIONS:
        return "Audio"
    elif ext.lower() in VIDEO_EXTENSIONS:
        return "Video"
    return "Unknown"


# ---------------------------------------------------------------------------
# Streamlit Application
# ---------------------------------------------------------------------------

def main():
    """Entry point for the Streamlit application."""

    # ---- Page configuration ----
    st.set_page_config(
        page_title="Audio Processing & Transcription",
        page_icon="🎤",
        layout="centered",
    )

    # ---- Title and description ----
    st.title("🎤 Audio Processing & Transcription")
    st.markdown(
        "Upload an **audio** or **video** recording and generate a transcript "
        "using [OpenAI Whisper](https://github.com/openai/whisper). "
        "Supports multiple formats and includes accuracy testing."
    )
    st.divider()

    # ---- Sidebar – model selection & info ----
    st.sidebar.header("⚙️ Settings")
    model_name = st.sidebar.selectbox(
        "Whisper Model",
        options=MODEL_OPTIONS,
        index=MODEL_OPTIONS.index(DEFAULT_MODEL),
        help=(
            "**tiny** – Fastest, lowest accuracy\n\n"
            "**base** – Good balance (recommended)\n\n"
            "**small** – Better accuracy, slower"
        ),
    )
    st.sidebar.info(
        "Larger models may provide better accuracy but require more "
        "processing time and memory."
    )
    st.sidebar.divider()
    st.sidebar.markdown(
        "**Supported formats**\n\n"
        f"Audio: {', '.join(AUDIO_EXTENSIONS)}\n\n"
        f"Video: {', '.join(VIDEO_EXTENSIONS)}\n\n"
        f"Max size: {MAX_FILE_SIZE_LABEL}"
    )

    # ---- Initialize session state ----
    if "generated_transcript" not in st.session_state:
        st.session_state["generated_transcript"] = ""
    if "saved_transcript_path" not in st.session_state:
        st.session_state["saved_transcript_path"] = ""
    if "current_file_name" not in st.session_state:
        st.session_state["current_file_name"] = ""
    if "accuracy_history" not in st.session_state:
        st.session_state["accuracy_history"] = []

    # ---- File uploader ----
    st.subheader("📁 Upload Meeting Recording")
    uploaded_file = st.file_uploader(
        "Choose an audio or video file",
        type=[ext.lstrip(".") for ext in SUPPORTED_EXTENSIONS],
        help=f"Maximum file size: {MAX_FILE_SIZE_LABEL}",
    )

    # ---- Reset transcript when a new file is uploaded ----
    if uploaded_file is not None:
        if uploaded_file.name != st.session_state.get("current_file_name", ""):
            st.session_state["generated_transcript"] = ""
            st.session_state["saved_transcript_path"] = ""
            st.session_state["current_file_name"] = uploaded_file.name

    # ---- File details & validation ----
    if uploaded_file is not None:
        is_valid, error_msg = validate_uploaded_file(uploaded_file)

        if not is_valid:
            st.error(error_msg)
            return

        # --- File Information ---
        st.markdown("---")
        st.subheader("📋 File Information")

        file_category = get_file_category(uploaded_file.name)
        _, file_ext = os.path.splitext(uploaded_file.name)

        col1, col2, col3 = st.columns(3)
        col1.metric("File Name", uploaded_file.name)
        col2.metric("Type", f"{file_category} ({file_ext.upper()})")
        col3.metric("Size", format_file_size(uploaded_file.size))

        st.success("✅ File validation successful")

        st.divider()

        # ---- Whisper model display ----
        st.markdown(f"**Whisper Model:** `{model_name}`")

        # ---- Transcribe button ----
        transcribe_clicked = st.button(
            "🎙️ Transcribe Recording", type="primary", use_container_width=True
        )

        if transcribe_clicked:
            try:
                # Processing Status
                with st.status("Processing your recording...", expanded=True) as status:
                    # Step 1 – Upload
                    st.write("✅ File uploaded")

                    # Step 2 – Validation
                    st.write("✅ File validated")

                    # Step 3 – Save file
                    st.write("⏳ Processing audio...")
                    file_path = save_uploaded_file(uploaded_file)
                    st.write("✅ Audio processed")

                    # Step 4 – Load Whisper model
                    st.write(f"⏳ Loading Whisper model ({model_name})...")
                    load_whisper_model(model_name)
                    st.write("✅ Whisper model loaded")

                    # Step 5 – Transcribe
                    st.write("⏳ Transcribing recording...")
                    raw_transcript = transcribe_audio(file_path, model_name)
                    st.write("✅ Transcription complete")

                    # Step 6 – Clean & validate transcript
                    st.write("⏳ Generating transcript...")
                    transcript = clean_transcript(raw_transcript)

                    if not transcript:
                        st.write("❌ Transcription completed but no speech was detected.")
                        status.update(
                            label="Transcription produced empty result.",
                            state="error"
                        )
                        st.session_state["generated_transcript"] = ""
                        st.session_state["saved_transcript_path"] = ""
                    else:
                        st.write(
                            f"✅ Transcript generated from the uploaded recording "
                            f"({len(transcript.split())} words)"
                        )

                        # Step 7 – Save transcript
                        st.write("⏳ Saving transcript...")
                        try:
                            saved_path = save_transcript(
                                transcript, uploaded_file.name
                            )
                            st.write(
                                f"✅ Transcript saved to "
                                f"`{os.path.basename(saved_path)}`"
                            )
                        except Exception as save_err:
                            st.write(f"❌ Failed to save transcript: {save_err}")
                            saved_path = ""

                        # Step 8 – Verify saved transcript
                        if saved_path:
                            is_ok, verify_msg = validate_saved_transcript(
                                saved_path, transcript
                            )
                            if is_ok:
                                st.write("✅ Transcript saved successfully")
                                st.write("✅ Saved transcript verified")
                            else:
                                st.write(
                                    f"❌ Transcript could not be verified "
                                    f"after saving. {verify_msg}"
                                )

                        status.update(
                            label="Transcription complete!", state="complete"
                        )

                        # Store transcript in session state
                        st.session_state["generated_transcript"] = transcript
                        st.session_state["saved_transcript_path"] = (
                            saved_path if saved_path else ""
                        )

            except FileNotFoundError:
                st.error(
                    "❌ **FFmpeg not found.** Whisper requires FFmpeg to process "
                    "audio/video files. Please install FFmpeg and add it to your "
                    "system PATH.\n\n"
                    "Verify installation with: `ffmpeg -version`\n\n"
                    "See the README for instructions."
                )
                return
            except Exception as e:
                st.error(
                    "❌ Unable to process this recording. Please check that "
                    "the file is valid and not corrupted.\n\n"
                    f"**Error details:** {e}"
                )
                return

    # ------------------------------------------------------------------
    # Display transcript (persisted via session state)
    # ------------------------------------------------------------------
    generated_transcript = st.session_state.get("generated_transcript", "")
    saved_transcript_path = st.session_state.get("saved_transcript_path", "")

    if generated_transcript:
        st.divider()
        st.subheader("📝 Generated Transcript")

        st.info(
            f"✅ Transcript generated from the uploaded recording. "
            f"Word count: **{len(generated_transcript.split())}**"
        )

        st.text_area(
            "Transcript",
            value=generated_transcript,
            height=250,
            disabled=True,
            label_visibility="collapsed",
        )

        # Transcript validation status
        if saved_transcript_path:
            st.success("✅ Transcript saved successfully")
            st.success("✅ Saved transcript verified")

        # Download button
        st.download_button(
            label="⬇️ Download Transcript",
            data=generated_transcript,
            file_name=(
                os.path.basename(saved_transcript_path)
                if saved_transcript_path
                else "transcript.txt"
            ),
            mime="text/plain",
            use_container_width=True,
        )

    # ------------------------------------------------------------------
    # Accuracy Testing Section
    # ------------------------------------------------------------------
    st.divider()
    st.subheader("📊 Accuracy Testing")
    st.markdown(
        "Paste the **actual / reference transcript** (the real spoken words) "
        "below and click **Calculate Accuracy** to evaluate transcription quality.\n\n"
        "_This is used to calculate transcription accuracy by comparing "
        "the Whisper-generated transcript with the known actual words._"
    )

    if generated_transcript:
        st.write(
            f"Generated transcript contains "
            f"**{len(generated_transcript.split())}** words."
        )

    reference_transcript = st.text_area(
        "Reference / Actual Transcript",
        height=200,
        placeholder=(
            "Paste the actual/reference transcript of the recording here. "
            "This is used to calculate transcription accuracy."
        ),
        key="reference_transcript",
    )

    if st.button(
        "🔍 Calculate Accuracy",
        type="primary",
        key="calculate_accuracy_button",
    ):
        # Validation
        if not generated_transcript.strip():
            st.warning(
                "⚠️ Please generate a transcript before calculating accuracy."
            )
        elif not reference_transcript.strip():
            st.warning(
                "⚠️ Please paste the actual/reference transcript."
            )
        else:
            try:
                accuracy, incorrect_words = calculate_accuracy(
                    reference_transcript,
                    generated_transcript,
                )

                if accuracy is None:
                    st.error(
                        "Reference transcript is empty after normalization."
                    )
                else:
                    # Display accuracy metric
                    st.metric(
                        "Estimated Transcription Accuracy",
                        f"{accuracy:.2f}%",
                    )

                    # PASS or NEEDS IMPROVEMENT
                    if accuracy >= ACCURACY_TARGET:
                        st.success(
                            f"✅ PASS – Transcription accuracy target of "
                            f"≥{ACCURACY_TARGET}% achieved."
                        )
                        result_label = "PASS"
                    else:
                        st.warning(
                            f"⚠️ NEEDS IMPROVEMENT – Transcription accuracy "
                            f"is below the {ACCURACY_TARGET}% target."
                        )
                        result_label = "NEEDS IMPROVEMENT"

                    # Missing or incorrect words
                    st.markdown("**Missing / Incorrect Words**")
                    if incorrect_words:
                        unique_words = list(dict.fromkeys(incorrect_words))
                        for word in unique_words[:50]:
                            st.write(f"- {word}")
                        if len(unique_words) > 50:
                            st.write(
                                f"_...and {len(unique_words) - 50} more_"
                            )
                    else:
                        st.success(
                            "✅ No missing or incorrect reference words "
                            "detected."
                        )

                    # Word comparison details
                    reference_word_count = len(
                        normalize_text(reference_transcript).split()
                    )
                    generated_word_count = len(
                        normalize_text(generated_transcript).split()
                    )
                    st.info(
                        f"Reference words: {reference_word_count} | "
                        f"Generated words: {generated_word_count}"
                    )

                    # --- Record to accuracy history ---
                    recording_name = st.session_state.get(
                        "current_file_name", "Unknown"
                    )
                    st.session_state["accuracy_history"].append({
                        "Recording": recording_name,
                        "Accuracy": f"{accuracy:.2f}%",
                        "Result": result_label,
                    })

            except Exception as acc_err:
                st.error(
                    f"❌ Error calculating accuracy: {acc_err}"
                )

    st.caption(
        "**Note:** Actual accuracy depends on audio quality, background "
        "noise, speaker clarity, accent, language, and the Whisper model used."
    )

    # ------------------------------------------------------------------
    # Accuracy Testing History
    # ------------------------------------------------------------------
    accuracy_history = st.session_state.get("accuracy_history", [])
    if accuracy_history:
        st.divider()
        st.subheader("📋 Accuracy Testing History")
        st.markdown(
            "Results from all accuracy tests performed during this session."
        )
        # Build a table
        history_df = pd.DataFrame(accuracy_history)
        history_df.index = history_df.index + 1
        history_df.index.name = "#"
        st.table(history_df)


if __name__ == "__main__":
    main()
