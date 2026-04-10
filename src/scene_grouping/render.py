"""
Render pipeline: takes a timeline file and produces a final video with narration.

Steps:
1. Parse timeline → get VOICEOVER text
2. Piper TTS → generate .wav narration
3. DSL compile → .scene → .render.json
4. Manim render → silent .mp4
5. ffmpeg merge → final .mp4 with audio

Can render a single scene or all scenes for a section.
"""
import os
import re
import subprocess
from pathlib import Path

from piper import PiperVoice
import wave

from src.config.constants import (
    GROUPING_TIMELINES_DIR,
    GROUPING_SCENE_FILES_DIR,
    GROUPING_NARRATION_DIR,
    GROUPING_OUTPUT_DIR,
    GROUPING_PIPER_MODEL,
)


TIMELINES_DIR = GROUPING_TIMELINES_DIR
SCENE_FILES_DIR = GROUPING_SCENE_FILES_DIR
NARRATION_DIR = GROUPING_NARRATION_DIR
OUTPUT_DIR = GROUPING_OUTPUT_DIR
RENDER_DIR = Path("pipeline/render")

# cache the piper voice model
_voice = None


def _get_voice() -> PiperVoice:
    global _voice
    if _voice is None:
        _voice = PiperVoice.load(GROUPING_PIPER_MODEL)
    return _voice


# --- Step 1: Parse voiceover from timeline ---

def _parse_voiceover(timeline_file: Path) -> str:
    content = timeline_file.read_text(encoding="utf-8", errors="replace")
    match = re.search(r'VOICEOVER:\s*"?(.+?)"?\s*$', content, re.MULTILINE)
    return match.group(1).strip().strip('"') if match else ""


# --- Step 2: Generate narration audio ---

def generate_narration(timeline_file: Path) -> Path:
    """Generate .wav narration from timeline's VOICEOVER text."""
    NARRATION_DIR.mkdir(parents=True, exist_ok=True)
    output_file = NARRATION_DIR / f"{timeline_file.stem}.wav"

    if output_file.exists():
        return output_file

    voiceover = _parse_voiceover(timeline_file)
    if not voiceover:
        return output_file

    voice = _get_voice()

    # collect all audio data first, then write a complete WAV
    all_audio = bytearray()
    for audio_chunk in voice.synthesize(voiceover):
        all_audio.extend(audio_chunk.audio_int16_bytes)

    with wave.open(str(output_file), "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(voice.config.sample_rate)
        wav_file.writeframes(bytes(all_audio))

    print(f"  NARRATION: {output_file.name}")
    return output_file


# --- Step 3: Compile DSL to render JSON ---

def compile_scene(timeline_file: Path) -> Path:
    """Compile timeline → .scene → .render.json"""
    from src.scene_grouping.dsl_compiler import compile_timeline
    from src.dsl.parser import parse_scene
    from src.dsl.transformer import SceneModelTransformer

    SCENE_FILES_DIR.mkdir(parents=True, exist_ok=True)
    RENDER_DIR.mkdir(parents=True, exist_ok=True)

    scene_file = SCENE_FILES_DIR / f"{timeline_file.stem}.scene"
    render_file = RENDER_DIR / f"{timeline_file.stem}.render.json"

    if not scene_file.exists():
        dsl = compile_timeline(timeline_file)
        scene_file.write_text(dsl, encoding="utf-8")

    if not render_file.exists():
        ast = parse_scene(
            scene_path=scene_file,
            grammar_path=Path("src/dsl/renderer_dsl.lark"),
        )
        model = SceneModelTransformer().transform(ast)
        model.write_json(render_file)

    print(f"  COMPILED: {render_file.name}")
    return render_file


# --- Step 4: Render with manim ---

def render_manim(render_file: Path, scene_name: str) -> Path:
    """Render .render.json with manim to produce silent .mp4"""
    env = os.environ.copy()
    env["RENDERER_INSTRUCTIONS_FILE"] = str(render_file)

    subprocess.run(
        [
            "python", "-m", "manim", "-ql",
            "src/renderer/manim/manim_runner.py", "ManimScene",
            "-o", scene_name,
        ],
        check=True,
        env=env,
        capture_output=True,
    )

    # manim outputs to media/videos/
    video_path = Path(f"media/videos/manim_runner/480p15/{scene_name}.mp4")
    if video_path.exists():
        print(f"  MANIM: {video_path.name}")
        return video_path

    # fallback: search for it
    for p in Path("media").rglob(f"{scene_name}.mp4"):
        print(f"  MANIM: {p.name}")
        return p

    raise FileNotFoundError(f"Manim output not found for {scene_name}")


# --- Step 5: Merge audio + video ---

def merge_audio_video(video_path: Path, audio_path: Path, output_name: str) -> Path:
    """Merge silent video with narration audio using ffmpeg."""
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    output_file = OUTPUT_DIR / f"{output_name}.mp4"

    if output_file.exists():
        return output_file

    subprocess.run(
        [
            "ffmpeg", "-y",
            "-i", str(video_path),
            "-i", str(audio_path),
            "-c:v", "copy",
            "-c:a", "aac",
            "-shortest",
            output_file,
        ],
        check=True,
        capture_output=True,
    )

    print(f"  OUTPUT: {output_file.name}")
    return output_file


# --- Full pipeline ---

def render_scene(timeline_file: Path) -> Path:
    """Full pipeline: timeline → narration + manim → merged video."""
    scene_name = timeline_file.stem

    print(f"\nRendering {scene_name}...")

    # generate narration
    audio_path = generate_narration(timeline_file)

    # compile and render
    render_file = compile_scene(timeline_file)
    video_path = render_manim(render_file, scene_name)

    # merge
    output = merge_audio_video(video_path, audio_path, scene_name)
    print(f"  DONE: {output}")
    return output


def render_section(section_name: str) -> list[Path]:
    """Render all scenes for a section."""
    timeline_files = sorted(TIMELINES_DIR.glob(f"timeline_{section_name}_scene_*.txt"))
    if not timeline_files:
        print(f"No timeline files found for {section_name}")
        return []

    outputs = []
    for tf in timeline_files:
        try:
            output = render_scene(tf)
            outputs.append(output)
        except Exception as e:
            print(f"  FAILED: {tf.name} — {e}")

    return outputs


if __name__ == "__main__":
    import sys
    import argparse

    parser = argparse.ArgumentParser(description="Render scenes with narration")
    parser.add_argument("target", type=str, help="Timeline file path or section name (e.g. section_2)")
    args = parser.parse_args()

    target = args.target
    if target.endswith(".txt") and Path(target).exists():
        render_scene(Path(target))
    else:
        render_section(target)
