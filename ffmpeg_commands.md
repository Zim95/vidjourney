# ffmpeg / ffprobe commands used in VidJourney

Reference for every ffmpeg and ffprobe invocation the scroll pipeline uses or
that came up while diagnosing it. Front half is *workflow & intuition* — when
do I reach for which command, what am I looking for in the output, what each
signal means. Back half is the command tables for quick reference once you
know what you're after.

---

# ffmpeg / ffprobe workflow & intuition

## The mental model

Three layers, every video question lives in one of them:

| Layer | What it is | Where it lives in ffmpeg |
|---|---|---|
| **Container** | The outer file format: `mp4`, `mkv`, `webm`, `mov`. Holds metadata, indexes, and one or more streams. | `-f` flag, file extension, `format=` in ffprobe |
| **Codec** | How each stream's samples are compressed: `h264`, `aac`, `hevc`, `vp9`. Has its own knobs (CRF, preset, profile, level). | `-c:v` / `-c:a` flags, `stream=codec_name` |
| **Filter graph** | What happens to the streams *between* decode and encode: `crop`, `scale`, `xfade`, `apad`, `tpad`. Time-varying expressions go here. | `-vf` (single video filter chain), `-af` (single audio chain), `-filter_complex` (arbitrary graph) |

Almost every bug I hit in this session was at exactly *one* of those layers:
- **"moov atom not found"** → container layer (mp4 header missing because encode was killed mid-write)
- **yuv444p stuttering** → codec/encoder layer (libx264 chose wrong pix_fmt because nothing pinned it)
- **"Missing ')' or too many args"** → filter graph layer (crop expression too deeply nested)

Knowing which layer the error lives in tells me which tool to reach for.

## The diagnostic loop — always ffprobe first

When something's wrong with a video, **never** run ffmpeg blind on it. Probe
first. The order I follow:

```
1. ffprobe -v error -show_format FILE   →  Is the container readable at all?
2. ffprobe -v error -show_streams FILE  →  What's the codec / pix_fmt / fps / sample rate?
3. (if streams are weird) -show_packets →  Are timestamps continuous? Any frame drops?
4. (if 1 fails) ffmpeg -v error -i FILE -t 5 -f null -  →  What does the decoder actually say?
```

Each step costs nothing and rules out a whole class of explanations.

## Reading `-show_format` output

```
[FORMAT]
duration=621.794401
bit_rate=2188802
TAG:major_brand=isom
[/FORMAT]
```

| Field | Why I look at it |
|---|---|
| `duration` | First sanity check — does it match what you expect? Mismatch means truncation, padding, or a wrong `-t`. |
| `bit_rate` | Way too low (under ~500 kbps for 1080p) suggests heavy compression or short content. Way too high suggests lossless / uncompressed. |
| `TAG:major_brand` | Confirms the container variant: `isom`, `mp42`, `qt`. Mostly relevant if a player rejects the file. |
| **Absence of `[FORMAT]` block + a "moov atom not found" stderr** | The container is unreadable — file is mid-write or truncated. Do **not** ignore: ffmpeg will produce confusing errors downstream. |

## Reading `-show_streams` output

```
codec_name=h264
pix_fmt=yuv444p   ← THIS is what made section_11 stutter
width=1920
height=1080
r_frame_rate=60/1
duration=411.100000
nb_frames=24666
```

This is the densest diagnostic in the whole pipeline. What I check for, in order:

1. **`codec_name`** — should be `h264` for our video, `aac` for audio. Anything else (`mpeg4`, `prores`, `flac`) means a step misconfigured the encoder.
2. **`pix_fmt`** — must be `yuv420p` for universal playback. Anything else (`yuv422p`, `yuv444p`, `yuvj420p`, `rgb24`) → certain QuickTime/browser stutter. **This was the single most impactful bug in this session.**
3. **`width × height`** — must match what the encoder was told. A 1920×1080 expected but 3840×2160 actual means a `crop` or `scale` filter wasn't applied or got dropped.
4. **`r_frame_rate` vs `avg_frame_rate`** — `r_frame_rate=60/1` is the *nominal* (target) framerate. `avg_frame_rate` is the actual measured one. If they diverge by more than ~0.1%, there's frame timing drift somewhere — usually a concat issue or a `-vf fps=N` filter doing something weird.
5. **`duration` vs `nb_frames / r_frame_rate`** — these should agree to within one frame. If `nb_frames=37304` and `r_frame_rate=60/1` but `duration=600s`, that's `nb_frames=36000` expected → 1304 extra frames means a stuck/repeated frame somewhere.
6. **Audio: `sample_rate`** — must match across all clips you plan to concat. If section A is 22050Hz and section B is 48000Hz, concat-demuxer stream-copy will silently produce garbage; you need to re-encode.

If any field is missing entirely (e.g. you get audio stream but no `pix_fmt`),
the file is corrupted and the probe gave up mid-parse.

## When to use which ffprobe form

| What you need | Command pattern | Why this one |
|---|---|---|
| Quick sanity check | `ffprobe -v error -show_format FILE` | Tiny output, just "is it readable, what's the duration." First probe I run. |
| Codec & format details | `ffprobe -v error -show_streams FILE` | Gives the pix_fmt, fps, codec, sample rate — all the structural facts. |
| **Just one field, cheaply** | `ffprobe -v error -select_streams v:0 -show_entries stream=pix_fmt -of csv=p=0 FILE` | When validating *many* files in a loop, the `-show_entries … -of csv=p=0` form is the fastest. Returns just `yuv420p` (one line, no JSON, no headers). I used this for the 224-section validity sweep. |
| Frame-by-frame timing | `ffprobe -v error -select_streams v:0 -show_packets -print_format compact FILE` | Each packet shows its pts_time, dts_time, duration, size, flags. Use to confirm timestamps are continuous across a concat boundary. |
| Keyframe / GOP analysis | `ffprobe -v error -select_streams v:0 -show_frames -show_entries frame=pict_type` | When the *seek behavior* is bad (player jumps weirdly when scrubbing). Wide I-frame spacing means slow seeking. |

The `-v error` flag is something I always add: it silences ffprobe's chatty
banner (version + build config) and shows only error messages and the data
you asked for. Without it, every probe produces 30+ lines of garbage.

`-of csv=p=0` is the trick for compact output. `csv` = comma-separated;
`p=0` = print no headers, just values. Without this you get JSON-like
sections wrapped around your one field.

## ffmpeg invocation anatomy

Every ffmpeg call has the same structural shape:

```
ffmpeg [global-options]
       [input 1 options]      -i input1.foo
       [input 2 options]      -i input2.foo
       [filter graph]         -vf "..." OR -filter_complex "..."
       [output options]       -c:v ... -c:a ... -pix_fmt ... -t ...
                              output.bar
```

When ffmpeg behaves weirdly, the first thing I check is **whether each option
attached to the right argument**. An option appearing *before* `-i input.mp4`
applies to that input; appearing *after* the last `-i` (but before the
output filename) applies to the output. Common confusion:

- `-r 60 -i in.mp4 ...` → tells the *decoder* "interpret input as 60fps" (probably not what you want)
- `... -i in.mp4 -r 60 out.mp4` → tells the *encoder* "produce 60fps output" (usually what you want)

### Input options I use most

| Option | Purpose | When |
|---|---|---|
| `-loop 1` | Loop a single image forever as a video stream | When feeding a PNG to be panned across — the canvas-PNG render |
| `-i file.png` | Static image as a stream | Same |
| `-i file.wav` | Audio source | Per-section narration |
| `-f lavfi -i 'anullsrc=r=22050:cl=mono'` | Synthesized silence | Image-only blocks that need an audio track to stay in sync |
| `-f lavfi -i 'color=black:s=1920x1080:r=60'` | Synthesized black video | Black-pad clips between sections (if needed) |

`lavfi` ("libavfilter input") is the trick for *generating* streams without a
file. Any filter that doesn't need an input can be a source: `anullsrc`
(silence), `color` (solid color), `sine` (test tone), `testsrc` (test pattern).

### Output options I always think about

| Option | Why I pin it |
|---|---|
| `-c:v libx264 -crf 18 -preset medium` | Visually-lossless H.264, balanced speed/quality. CRF 18 = "no one notices the difference." Preset slides quality↔speed. |
| `-c:a aac` | Universally-supported audio codec. |
| `-pix_fmt yuv420p` | **Mandatory** if input is RGB (PNG, color, testsrc). Otherwise libx264 will pick yuv444p and your video will stutter in QuickTime / browsers. |
| `-shortest` | When mixing infinite (`-loop 1` PNG) and finite (audio) inputs — stop at the finite one's length. Without this, the PNG keeps producing video forever. |
| `-t SECONDS` | Hard duration cap. Useful for short test renders, and as a safety net with `-loop 1`. |
| `-y` | Overwrite output without prompting. Always on in scripts. |

### Filter graphs: `-vf` vs `-af` vs `-filter_complex`

- **`-vf "crop=...,scale=..."`** — a *chain* on the single video stream. Comma-separated filters, output of one feeds the next.
- **`-af "afade=...,apad=..."`** — same idea, single audio stream.
- **`-filter_complex "..."`** — full graph notation. Multiple inputs and outputs, labels like `[v0]`. Required when you have more than one input or want to split/merge streams.

`-filter_complex` syntax has a specific shape:

```
[input_label]filter=arg1=val1:arg2=val2[output_label] ;
[input_label]filter=arg1=val1[output_label] ;
[v0][v1]concat=n=2:v=1:a=0[vout]
```

Each filter clause takes `[labels]` in, produces `[labels]` out. Separator is
`;` between clauses, `,` within a chain. Inputs from the command line get
default labels `[0:v]`, `[0:a]`, `[1:v]`, `[1:a]`, etc.

The two cases where I always reach for `-filter_complex` instead of `-vf`:

1. **Multiple inputs** — `xfade`, `acrossfade`, `concat` filter all need 2+ inputs.
2. **Conditional / time-varying expressions** — the camera-pan `crop=...:y=EXPRESSION` was simple enough to fit in `-vf`, but anything more elaborate (e.g. apply a different filter to each input then concat) needs `-filter_complex`.

## Reading ffmpeg errors

ffmpeg error output is famously noisy. The key is to **filter for the
relevant line**, which is usually one of these:

```bash
# After running ffmpeg with `2>&1`, grep for the signal:
ffmpeg ... 2>&1 | grep -iE "error|invalid|missing|parse|fail|cannot|traceback" | head -10
```

The patterns I've personally hit in this repo and what they mean:

| Error fragment | Layer | Cause | Fix |
|---|---|---|---|
| `moov atom not found` | Container | mp4 wasn't finalized — encode was killed mid-write. | Re-encode. Don't trust the partial file. |
| `Missing ')' or too many args` | Filter graph | Expression too deep/long for ffmpeg's parser. | Simplify the expression (Douglas-Peucker, flat-sum, fewer waypoints). |
| `Invalid data found when processing input` | Container | File header is malformed or the file is empty. | Same as moov-not-found, usually. |
| `Cairo error: surface size too large` | Underlying library (manim/cairo) | PNG dimensions exceed 32767px. | Reduce supersample multiplier. |
| `pix_fmt yuv444p is not supported by encoder X` | Codec | Encoder doesn't accept that chroma format. | Pin `-pix_fmt yuv420p`. |
| `Failed to configure input pad on Parsed_X` | Filter graph | The previous filter's output doesn't match the next filter's expected input. | Usually means a chain has missing or extra `[labels]`. |
| `error reinitializing filters` | Filter graph | Same root cause — graph wiring failure. | Same fix. |
| `Non-monotonic DTS` (warning) | Container/timing | Timestamps went backward between packets — usually a concat with mismatched timebases. | Reset timestamps with `-c copy -avoid_negative_ts make_zero` or re-encode. |

The trick is `2>&1 | grep | head`: ffmpeg's progress output is huge, but the
*first* error line is almost always the root cause. Subsequent errors are
downstream noise.

If `grep` returns nothing, the run was successful — exit code is the
authoritative signal.

## Filter graph intuition for the patterns we used

### Pan a tall PNG across a viewport — `crop` with time-varying `y`

```
crop=W:H:0:Y_EXPR,scale=1920:1080:flags=lanczos,fps=60
```

The chain: take the full PNG → crop a `W × H` window at offset `(0, Y_EXPR)` →
downscale to 1080p with Lanczos → enforce 60fps timing. `Y_EXPR` is an
expression in `t` (seconds). The expression's nesting limit was the bug we
hit with very long sections — fixed by switching from nested `if()` to a
flat sum of gated terms.

`scale=1920:1080:flags=lanczos` — `flags` picks the resampling algorithm.
Lanczos is the gold standard for downscaling; bilinear is faster but soft;
bicubic is in between. For a pre-rasterized canvas we always want Lanczos
because the *whole point of supersampling* is the resampler doing sub-pixel
anti-aliasing.

`fps=60` — without this, the crop filter's frame timing comes from the
input's "framerate" (a static PNG technically has none → defaults to 25fps
which is wrong). Pinning the output framerate ensures smooth motion.

### Crossfade between two clips — `xfade` (which we then dropped)

```
[0:v][1:v]xfade=transition=fade:duration=0.5:offset=D0-0.5[v_out]
```

`xfade` overlaps the *last 0.5s of input 0* with the *first 0.5s of input 1*.
`offset` is when the transition begins, measured from input 0's start.
Chaining: `[v_out][2:v]xfade=...:offset=D0+D1-1.0[v_out2]` — offset for the
next stage is cumulative duration minus accumulated transition time.

For narrated content this turned out to be the wrong choice because audio
crossfades blend two narrators speaking simultaneously. We switched to
hard-concat instead.

### Hard concat — `-f concat` demuxer with stream copy

```
ffmpeg -y -f concat -safe 0 -i list.txt -c copy out.mp4
```

`-f concat` reads a text file with `file 'path'` entries and stitches them
end-to-end. `-c copy` (stream copy) is the magic: no re-encoding, just
splicing the encoded packets. This requires **identical codec parameters**
across all inputs (same codec, resolution, fps, sample rate, channel layout)
— otherwise the output is malformed or refuses to play. Since all our
section mp4s come from the same pipeline they match, so stream-copy works
and is essentially instant.

The `-safe 0` flag is required when the paths in `list.txt` contain
slashes/absolute paths; the default `-safe 1` rejects them for security.

### Pad audio with silence — `apad`

```
ffmpeg -i in.wav -af "apad=pad_dur=1.5" out.wav
```

Appends 1.5s of silence to the end of the audio. There's also
`pad_len=SAMPLES` (sample count) and `whole_dur=DURATION` (total target
length including the original).

### Freeze the last video frame — `tpad`

```
ffmpeg -i in.mp4 -vf "tpad=stop_mode=clone:stop_duration=1.5" out.mp4
```

`stop_mode=clone` repeats the last frame; `stop_mode=add:stop_pad=black`
fills with black. Often paired with `apad` so audio gets the same extension.

### Synthesize silence — `anullsrc`

```
ffmpeg -f lavfi -i "anullsrc=r=22050:cl=mono" -t 1.5 silence.wav
```

`r=22050` = sample rate, `cl=mono` = channel layout. Must match the rate of
whatever you'll concatenate it with — otherwise the silence wav and the
narration wav are incompatible.

## Testing without committing

When debugging a filter graph or encoder setting, **always test with a
2-second render before doing the full encode.** A 10-min video at preset=medium
takes 10+ minutes; a 2-second test takes <5 seconds and gives the same
error messages if the filter is broken.

```bash
ffmpeg -y \
  -loop 1 -i canvas.png \
  -t 2 \
  -vf "crop=1920:1080:0:500,scale=1920:1080:flags=lanczos" \
  -c:v libx264 -preset ultrafast \
  -pix_fmt yuv420p \
  /tmp/test.mp4
```

`-preset ultrafast` for test renders — encode quality is irrelevant, you're
just validating the pipeline runs.

I used this pattern twice in this session: once to confirm a *simple* crop
worked on the canvas PNG (ruling out "the PNG itself is broken"), then again
to validate that the *flat-sum* expression parses where the *nested-if*
expression didn't. Both ran in ~5 seconds and immediately confirmed the
right hypothesis.

## When ffmpeg succeeds but the output is wrong

ffmpeg returns exit 0 even when the *content* is bad — as long as the pipeline
ran without error. Common ways this happens:

1. **Stream copy with mismatched codecs** — output plays but audio/video desync, or one track is missing.
2. **`-shortest` with the wrong input order** — output truncates to whichever input you didn't expect.
3. **Filter expression that's syntactically valid but always returns the same value** — output looks like the source PNG with no panning.

The defense: **always ffprobe your output** before declaring success. The
duration, frame count, and pix_fmt should match what you expected. If duration
is way off, something silently truncated. If pix_fmt is unexpected, you
forgot the chroma pin.

---

# ffmpeg / ffprobe command reference

(Quick-reference tables — once you know what you're after, the syntax.)

## Diagnosing videos (ffprobe)

| Command | Purpose | Where |
|---|---|---|
| `ffprobe -v error -show_format -show_streams FILE` | Verbose stream/codec/container dump (codec_name, pix_fmt, width/height, duration, bit_rate, sample_rate, channels, has_b_frames). First-pass diagnostic. | section_11 freeze diagnosis, Part 1 "moov not found" |
| `ffprobe -v error -select_streams v:0 -show_entries stream=pix_fmt -of csv=p=0 FILE` | Cheap pix_fmt check (yuv420p vs yuv444p). Used to identify which mp4s needed the chroma fix and to validate parts. | survey across 224 mp4s, `_is_valid_mp4()` in build_video.py |
| `ffprobe -v error -show_entries format=duration -of default=noprint_wrappers=1:nokey=1 FILE` | Get clip duration in seconds. Drives the bin-pack. | `_video_duration_seconds()` in build_video.py |
| `ffprobe -v error -select_streams v:0 -show_frames -show_entries frame=pict_type -of csv FILE` | Frame-type analysis (find I-frame spacing). Used to check if "freezing" was from a missing-keyframe issue (it wasn't — pix_fmt was the cause). | section_11 freeze diagnosis |
| `ffprobe -v error -show_entries format=duration:stream=pix_fmt -of default=nw=1 FILE` | Two-shot probe (duration + pix_fmt) with stderr-visible to confirm "moov atom not found" on partial files. | partial-write detection |

## Decoding-only sanity check (ffmpeg)

| Command | Purpose | Where |
|---|---|---|
| `ffmpeg -v error -i FILE -t 5 -f null -` | Try to decode the first 5s; print decoder errors only. Used to confirm Part 1 mp4 was unreadable. | Part 1 "moov not found" diagnosis |

## Generating audio (ffmpeg)

| Command | Purpose | Where |
|---|---|---|
| `ffmpeg -y -f lavfi -i anullsrc=r=22050:cl=mono -t DURATION OUT.wav` | Synthesize silent wav for image-only blocks (no narration text). Keeps audio cursor synced with video pan duration. | `_make_silence_wav()` in build_raster.py |

## Building per-section mp4 (ffmpeg, the main pan)

```
ffmpeg -y \
  -loop 1 -i CANVAS.png \
  -i SECTION.wav \
  -t TOTAL_DURATION \
  -vf "crop=W:H:0:Y_EXPR,scale=1920:1080:flags=lanczos,fps=60" \
  -pix_fmt yuv420p \
  -c:v libx264 -crf 18 -preset medium \
  -c:a aac \
  -shortest \
  section_N_raster.mp4
```

| Element | Purpose |
|---|---|
| `-loop 1 -i CANVAS.png` | Treat the tall canvas PNG as an infinite-duration single-frame video. `-shortest` later truncates to audio length. |
| `-i SECTION.wav` | Section narration audio. |
| `crop=W:H:0:Y_EXPR` | The pan — `Y_EXPR` is a time-varying flat-sum of gated linear segments that places the crop window at the camera's current y. Supersampled W/H (3840×2160 at 2×, 1920×1080 at 1×). |
| `scale=1920:1080:flags=lanczos` | Downsample to 1080p with Lanczos (the sub-pixel motion smoother — the whole point of supersampling). |
| `fps=60` | Lock to 60 fps output. |
| `-pix_fmt yuv420p` | **Critical fix** — without it libx264 picks yuv444p from the RGB PNG input, which QuickTime/browsers stutter or refuse. |
| `-c:v libx264 -crf 18 -preset medium` | Visually-lossless H.264. |
| `-c:a aac -shortest` | Encode audio; stop at end of audio (PNG would loop forever otherwise). |

## Re-encoding a stale mp4 in place (ffmpeg)

```
ffmpeg -y -i IN.mp4 -c:v libx264 -crf 18 -preset medium -pix_fmt yuv420p -c:a copy OUT.tmp.mp4
mv OUT.tmp.mp4 IN.mp4
```

Purpose: convert existing yuv444p mp4s to yuv420p without re-rendering the
canvas PNG or re-running the pan math. Audio copies as-is. Much cheaper than
a full rebuild.

## Building parts with crossfade (ffmpeg)

```
ffmpeg -y \
  -i s1.mp4 -i s2.mp4 ... -i sN.mp4 \
  -filter_complex "
    [0:v][1:v]xfade=transition=fade:duration=0.5:offset=D0-0.5[v1];
    [v1][2:v]xfade=transition=fade:duration=0.5:offset=D0+D1-1.0[v2];
    ...
    [0:a][1:a]acrossfade=d=0.5[a1];
    [a1][2:a]acrossfade=d=0.5[a2];
    ...
  " \
  -map "[v_last]" -map "[a_last]" \
  -c:v libx264 -crf 18 -preset medium \
  -c:a aac \
  Part.mp4
```

| Element | Purpose |
|---|---|
| `xfade=transition=fade:duration=0.5:offset=...` | Crossfade between consecutive section videos. Offset = cumulative duration so far minus the transition duration, so the fade overlaps the end of input N with the start of input N+1. |
| `acrossfade=d=0.5` | Audio counterpart — overlaps the last 0.5s of one section's audio with the first 0.5s of the next. |
| Chained labels `[v1][v2]…` | Each xfade output feeds the next, building one continuous output. |

## Single-section "part" passthrough (ffmpeg, no re-encode)

```
ffmpeg -y -f concat -safe 0 -i list.txt -c copy out.mp4
```

Purpose: when a part contains exactly one section (no transitions needed),
skip the expensive re-encode and stream-copy the section mp4 into the part
filename.

## Quick test commands used for debugging

| Command | Purpose |
|---|---|
| `ffmpeg -y -loop 1 -i CANVAS.png -t 2 -vf "crop=...,scale=..." -c:v libx264 -preset ultrafast /tmp/test.mp4` | 2-second sanity render to test a crop expression's parsability/correctness without waiting for a full encode. Used twice — once to prove a simple crop works on the canvas (confirming the PNG is fine), once to prove the flat-sum gated expression parses where the nested-if didn't. |
| `ffmpeg ... 2>&1 \| grep -iE "error\|invalid\|missing\|parse"` | Filter stderr for parser-level errors — that's how the "Missing ')' or too many args" pointing at the nested-if depth limit got caught. |

## Key insights

- **`-pix_fmt yuv420p`** is mandatory whenever the input is a PNG/RGB source.
  libx264 won't normalize it for you.
- **ffmpeg's expression parser has both a nesting limit AND an operand-count
  limit.** Deep nested-if (~100 deep) and flat-sum chains (~200 terms) both
  fail. The robust solution is to *reduce the curve* (Douglas-Peucker
  simplification) before generating the expression.
- **Cairo's 32767px surface limit** is what kills manim's `-s` PNG renders
  for very tall content. The fallback is to reduce supersample multiplier
  per-section.
- **"moov atom not found"** = ffmpeg's container header was never written,
  almost always because the encode was killed mid-write. The fix isn't
  ffmpeg-side — it's making downstream tooling treat such files as invalid
  and rebuild.

---

# Process & file-debugging workflow

The shell-side commands I lean on around ffmpeg — for finding, monitoring,
and killing jobs, sampling logs, and counting progress. Grouped by intent.

## Inspecting running processes (`ps`)

```bash
# All processes for the user, with full args:
ps aux

# Find one specific Python module's workers (most-used pattern in this repo):
ps aux | grep "src.scroll.build_raster" | grep -v grep

# Why two greps?  The first matches; the second filters out the grep
# process itself, which would otherwise appear in its own match.
```

The columns of `ps aux` are: `USER PID %CPU %MEM VSZ RSS TT STAT STARTED TIME COMMAND`.
Most useful for me are **PID** (column 2), **STARTED** (column 9), **TIME** (column 10, total CPU),
and **COMMAND** (column 11+).

For one specific PID with the actual elapsed wall time:

```bash
ps -p 14896 -o pid,stat,etime,command
# Shows  PID STAT ELAPSED COMMAND
```

`etime` is *wall clock time since the process started* — much more useful than the
`TIME` column (which is cumulative CPU). A process showing `etime=10:00:00` but
`TIME=0:02` has been sleeping for 10 hours and only consumed 2 seconds of CPU →
**it's stuck**.

`STAT` field decoder (one letter you'll see most often):

| Letter | Meaning |
|---|---|
| `R` | Running on CPU right now |
| `S` | Sleeping (interruptible — waiting on I/O, lock, semaphore) |
| `D` | Uninterruptible disk wait — *can't* be killed normally |
| `Z` | Zombie — child exited but parent hasn't reaped |
| `T` | Stopped (signaled with SIGSTOP) |

## Killing processes (`kill`, `pkill`, `xargs kill`)

```bash
# Graceful: send SIGTERM, lets the process clean up. Default.
kill 14896

# Force: send SIGKILL, immediate. Use after SIGTERM didn't work,
# or for stuck processes you know aren't going to clean up.
kill -9 14896

# Kill many PIDs at once via a pipeline:
ps aux | grep "src.scroll.build_raster" | grep -v grep | awk '{print $2}' | xargs -n1 kill -9
```

The `xargs -n1` is important: without `-n1`, xargs would try to pass *all* PIDs
as one argument list to a single `kill`, which works on most shells but fails if
the list is too long or if any PID is invalid (the whole call errors out). With
`-n1`, each PID gets its own `kill` invocation — failures are isolated.

`pkill` is the shortcut version when you don't need to inspect first:

```bash
pkill -9 -f "src.scroll.build_raster"
# -9 = SIGKILL; -f matches the full command line, not just the program name.
```

I usually use the `ps | grep | awk | xargs` chain instead because it lets me
*see* the matching processes before pulling the trigger.

## Parsing process output with `awk`

`awk '{print $2}'` is the workhorse — print column 2, separators are runs of
whitespace.

```bash
# Just PIDs:
ps aux | grep PATTERN | grep -v grep | awk '{print $2}'

# Multiple columns, formatted:
ps aux | grep build_raster | grep -v grep | awk '{print "PID="$2, "CPU="$10, "start="$9}'

# With a custom delimiter (ffprobe's pipe-separated output):
ffprobe -show_packets -print_format compact FILE | awk -F'|' '{ ... }'

# Filter rows numerically:
awk -F'|' '{
  for (i=1; i<=NF; i++) if ($i ~ /pts_time=/) { gsub("pts_time=", "", $i); pts=$i+0 }
  if (pts >= 71.5 && pts <= 72.5) print "pts=" pts
}'
```

For one-line text munging it beats writing a Python script every time.

## Watching files and directories

```bash
# Most-recently-modified file in a dir:
ls -lt DIR/ | head -2

# Watch a directory's growth (which sections finished, when):
ls -lt pipeline/scroll/output/section_*_raster.mp4 | head -5 | awk '{print $6, $7, $8, $9}'

# Files modified in the last N minutes:
find pipeline/scroll/output -name "*.mp4" -mmin -2 -ls

# Files modified after a specific time (newer GNU find / bsd find):
find pipeline/scroll/output -name "*.mp4" -newermt "2026-05-29 00:00"
```

`-mmin -N` reads as "modified less than N minutes ago" → recent files.
`-mmin +N` reads as "more than N minutes ago" → old files.

When an mp4 is currently being written by ffmpeg, its mtime is *now* and ffprobe
returns `moov atom not found` (the file has packet data but no container index
because that gets written at the end). The combination of `recent mtime + ffprobe
failure = still writing` is a very common diagnostic in this repo.

## Tailing logs (background-job output)

Background tasks (the harness's `run_in_background` mechanism) write their
stdout+stderr to a per-task file:

```bash
cat /private/tmp/claude-501/.../tasks/<task-id>.output
tail -20 /private/tmp/claude-501/.../tasks/<task-id>.output

# Live-follow a log as it grows (Ctrl-C to stop):
tail -f /private/tmp/.../tasks/<task-id>.output
```

For Python jobs that buffer stdout, the log file stays empty until the process
exits or flushes — that's not a bug, just buffering. The filesystem (mp4
appearance, ffprobe-readable state) is often a more reliable progress signal
than the log.

## Counting / progress checks

```bash
# How many of pattern X exist on disk?
ls pipeline/scroll/output/section_*_raster.mp4 | wc -l

# Same with a process count:
ps aux | grep src.scroll.build_raster | grep -v grep | wc -l | xargs echo "workers:"

# Frequency table:
some-output | sort | uniq -c
# e.g. "yuv420p: 184, yuv444p: 2, invalid: 38"
```

Inline-Python is the right escape hatch when shell arithmetic gets clumsy:

```bash
.venv/bin/python -c "
import pathlib
done = {int(p.stem.split('_')[1]) for p in pathlib.Path('pipeline/scroll/output').glob('section_*_raster.mp4')}
target = set(range(1, 225))
print(f'done {len(done)}, remaining: {sorted(target - done)}')"
```

I use this any time I need to compare two *sets* (which sections are done vs.
which should be), where pure shell would force me into awk/sort/comm gymnastics.

## Running parallel jobs (`xargs -P`)

```bash
# Process a list of section IDs across 3 worker subprocesses:
echo "11 13 16 17 18 19 20" | tr ' ' '\n' | \
  xargs -P 3 -I {} sh -c '.venv/bin/python -m src.scroll.build_raster "$1" 2>&1 | tail -2 | sed "s/^/[s$1] /"' sh {}
```

Breaking this down:

| Piece | Why |
|---|---|
| `echo "..." \| tr ' ' '\n'` | One item per line — `xargs` reads line-by-line by default |
| `xargs -P 3` | Run up to 3 parallel children |
| `-I {}` | Use `{}` as the placeholder for each input item |
| `sh -c '...' sh {}` | Spawn a shell so I can use `"$1"` and pipe within each child. `sh` is `$0`, `{}` becomes `$1`. |
| `2>&1 \| tail -2` | Capture last few lines of stderr+stdout per child — keeps the log compact instead of dumping every child's full output |
| `sed "s/^/[s$1] /"` | Tag each line with which section produced it, so interleaved parallel output is still readable |

This pattern handles fan-out without needing a job-queue library.

## End-to-end debugging recipes

A few full chains I used repeatedly in this session.

### "Is X still running, and what is it doing?"

```bash
ps aux | grep "src.scroll.build_raster" | grep -v grep | awk '{print $2, $9, $10, $11, $12, $13}'
ls -lt pipeline/scroll/output/section_*_raster.mp4 | head -3 | awk '{print $6, $7, $8, $9}'
```

The first command shows live workers; the second shows what they're producing.
Together they answer "alive AND making progress?" in one screen.

### "Identify and kill stuck processes"

```bash
# 1. See candidates:
ps aux | grep build_raster | grep -v grep | awk '{print $2, $9, $10}'

# 2. Inspect one's state to confirm it's stuck (long elapsed, tiny CPU):
ps -p 14896 -o pid,stat,etime,command

# 3. Kill them all in one shot once confirmed:
ps aux | grep build_raster | grep -v grep | awk '{print $2}' | xargs -n1 kill -9
```

### "How many of 224 are done, what's left, what's failing?"

```bash
.venv/bin/python -c "
import pathlib, subprocess
done, bad = [], []
for p in sorted(pathlib.Path('pipeline/scroll/output').glob('section_*_raster.mp4')):
    n = int(p.stem.split('_')[1])
    r = subprocess.run(['ffprobe','-v','error','-select_streams','v:0','-show_entries','stream=pix_fmt','-of','csv=p=0',str(p)], capture_output=True, text=True)
    if r.stdout.strip() == 'yuv420p':
        done.append(n)
    else:
        bad.append(n)
print(f'valid: {len(done)}/224, invalid/missing: {sorted(set(range(1,225)) - set(done))}')"
```

This is the bread-and-butter diagnostic: distinguishes "file exists but is
broken" from "file doesn't exist at all" — the difference matters a lot when
debugging.

### "Find ffmpeg errors in a long log"

```bash
grep -iE "error|invalid|missing|parse|fail|cannot|traceback" /tmp/output.log | head -10
```

`-i` = case-insensitive, `-E` = extended regex (lets `|` mean OR). Output is
piped to `head` because the first hit is almost always the root cause and
the rest are downstream noise.

### "Is this mp4 broken or just mid-write?"

```bash
# Two-second decode test — fails with a real error message:
ffmpeg -v error -i FILE -t 5 -f null -

# vs. ffprobe which gives the most informative "moov atom not found" diagnosis:
ffprobe -v error -show_entries format=duration -of csv=p=0 FILE
```

`moov atom not found` + recent mtime + active ffmpeg child writing to that
path = the file is mid-encode, not actually broken. If the mtime is hours old
and no ffmpeg child exists, it's a *partial file* from a killed encode and
needs to be deleted/rebuilt.

## When to reach for what

| Question | Tool |
|---|---|
| "Is X process running?" | `ps aux \| grep X \| grep -v grep` |
| "What process is writing this file?" | `lsof FILE` (not used much here, but exists) |
| "Did this file get touched recently?" | `ls -lt FILE` or `find -mmin -N` |
| "Are these many files all the same in property Y?" | `for f in ...; do ffprobe ... ; done \| sort \| uniq -c` |
| "Is this mp4 valid?" | `ffprobe -v error … -of csv=p=0 FILE` (empty = valid; error = broken) |
| "How long has this been running?" | `ps -p PID -o etime` |
| "Kill many at once" | `ps … \| awk '{print $2}' \| xargs -n1 kill -9` |
| "Run N things in parallel" | `... \| xargs -P N -I {} sh -c '...' sh {}` |
| "Set arithmetic on filenames" | inline `.venv/bin/python -c "..."` |
