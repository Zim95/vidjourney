#!/usr/bin/env bash
# Fresh-Mac bootstrap for VidJourney.
# Idempotent — safe to re-run. Each step checks before installing.
#
# What this installs / verifies:
#   - Homebrew (must be pre-installed)
#   - ffmpeg with libass (for burned-in subtitles)
#   - uv (Python package manager)
#   - Python dependencies (uv sync)
#   - Ollama + gemma4:e2b model
#   - Piper TTS voice model (en_US-lessac-medium)
#   - faster-whisper alignment model (warmed up by triggering one alignment call)
#
# Does NOT install:
#   - The Random Forest code/text classifier (models/code_rf.joblib).
#     That requires manual labeling — see README.md "ML model training".
#
# Usage:
#   ./install.sh           # interactive, prompts before each external install
#   ./install.sh --yes     # non-interactive, install everything missing

set -e

YES=${1:-}
prompt_yes() {
    if [[ "$YES" == "--yes" ]]; then
        return 0
    fi
    read -r -p "$1 [Y/n] " response
    case "$response" in
        [nN][oO]|[nN]) return 1 ;;
        *) return 0 ;;
    esac
}

echo "==> VidJourney install — preflight"
echo

# --- Homebrew check (Mac-only assumption) ----------------------------------

if ! command -v brew >/dev/null 2>&1; then
    echo "Homebrew not found. Install from https://brew.sh first, then re-run."
    exit 1
fi
echo "✓ Homebrew present ($(brew --version | head -1))"

# --- ffmpeg with libass ----------------------------------------------------

echo
echo "==> ffmpeg (must have libass for burned-in subtitles)"
if command -v ffmpeg >/dev/null 2>&1 && ffmpeg -version 2>&1 | grep -q libass; then
    echo "✓ ffmpeg with libass already installed"
else
    echo "ffmpeg with libass not found."
    if prompt_yes "Install homebrew-ffmpeg/ffmpeg/ffmpeg (full build with libass)?"; then
        brew tap homebrew-ffmpeg/ffmpeg 2>/dev/null || true
        brew install homebrew-ffmpeg/ffmpeg/ffmpeg
    else
        echo "Skipping. The pipeline will still run, but subtitles won't burn in."
    fi
fi

# --- uv --------------------------------------------------------------------

echo
echo "==> uv (Python package manager)"
if command -v uv >/dev/null 2>&1; then
    echo "✓ uv present ($(uv --version))"
else
    if prompt_yes "Install uv?"; then
        curl -LsSf https://astral.sh/uv/install.sh | sh
        # Add to PATH for the rest of this script
        export PATH="$HOME/.local/bin:$PATH"
    else
        echo "uv is required. Aborting."
        exit 1
    fi
fi

# --- Python dependencies ---------------------------------------------------

echo
echo "==> Python dependencies (uv sync)"
uv sync
echo "✓ Python deps installed in .venv"

# --- Ollama + gemma4:e2b ---------------------------------------------------

echo
echo "==> Ollama"
if ! command -v ollama >/dev/null 2>&1; then
    if prompt_yes "Install Ollama via Homebrew?"; then
        brew install ollama
    else
        echo "Ollama is required for the LLM stages. Aborting."
        exit 1
    fi
fi
echo "✓ Ollama binary present"

# Start the ollama daemon if not running
if ! pgrep -x ollama >/dev/null 2>&1; then
    echo "Starting ollama daemon in the background..."
    nohup ollama serve > /tmp/ollama.log 2>&1 &
    sleep 3
fi

if ollama list 2>/dev/null | grep -q "gemma4:e2b"; then
    echo "✓ gemma4:e2b model already pulled"
else
    if prompt_yes "Pull gemma4:e2b (~3 GB)?"; then
        ollama pull gemma4:e2b
    else
        echo "Skipping — pipeline will fail until this model is available."
    fi
fi

# --- Piper TTS voice -------------------------------------------------------

echo
echo "==> Piper TTS voice (en_US-lessac-medium)"
PIPER_DIR="$HOME/.local/share/piper-voices"
PIPER_MODEL="$PIPER_DIR/en_US-lessac-medium.onnx"
PIPER_JSON="$PIPER_DIR/en_US-lessac-medium.onnx.json"

if [[ -f "$PIPER_MODEL" && -f "$PIPER_JSON" ]]; then
    echo "✓ Piper voice already at $PIPER_MODEL"
else
    if prompt_yes "Download Piper voice model (~60 MB)?"; then
        mkdir -p "$PIPER_DIR"
        BASE_URL="https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/lessac/medium"
        curl -L -o "$PIPER_MODEL" "$BASE_URL/en_US-lessac-medium.onnx"
        curl -L -o "$PIPER_JSON"  "$BASE_URL/en_US-lessac-medium.onnx.json"
        echo "✓ Piper voice downloaded"
    else
        echo "Skipping. The narrate stage will fail until this exists."
    fi
fi

# --- faster-whisper model warm-up ------------------------------------------

echo
echo "==> faster-whisper alignment model (base.en, ~150 MB)"
WHISPER_CACHE="$HOME/.cache/huggingface/hub/models--Systran--faster-whisper-base.en"
if [[ -d "$WHISPER_CACHE" ]]; then
    echo "✓ faster-whisper base.en already cached"
else
    if prompt_yes "Pre-download the alignment model now (saves time on first run)?"; then
        uv run python -c "
from faster_whisper import WhisperModel
print('Downloading base.en...')
WhisperModel('base.en', device='cpu', compute_type='int8')
print('Done.')
"
    else
        echo "Skipping — model will download on first alignment call."
    fi
fi

# --- ML model check (informational only) -----------------------------------

echo
echo "==> Code-detection ML model"
if [[ -f models/code_rf.joblib ]]; then
    echo "✓ models/code_rf.joblib present"
else
    echo "⚠ models/code_rf.joblib not found."
    echo "  Code detection falls back to heuristics until this is trained."
    echo "  See README.md 'ML model training' to train it (one-time setup)."
fi

# --- Sanity check ----------------------------------------------------------

echo
echo "==> Final sanity check"
uv run python -c "
import sys
import importlib

required = ['fitz', 'lark', 'manim', 'nltk', 'piper', 'faster_whisper', 'requests', 'watchdog']
missing = []
for mod in required:
    try:
        importlib.import_module(mod)
    except ImportError:
        missing.append(mod)
if missing:
    print('Missing modules:', missing)
    sys.exit(1)
print('All required Python modules import cleanly.')
"

echo
echo "✓ VidJourney install complete."
echo
echo "Next steps:"
echo "  1. python main.py /path/to/your.pdf"
echo "  2. python -m src.assembler.build_video  # bundle into 10-min parts"
echo
echo "Output lands in pipeline/output/parts/"
