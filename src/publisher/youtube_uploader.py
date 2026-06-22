"""Upload the assembled part videos to YouTube, scheduled and paced.

This module reuses the paste-ready ``pipeline/descriptions/part_NN.md`` files
produced by ``scripts/generate_descriptions.py`` — it does NOT regenerate any
title/description/tags. It:

  * parses each ``part_NN.md`` for its Title / Description / Tags / mp4 path,
  * computes a *scheduled* publish time per part (start_date + i * interval),
    so the channel drip-feeds instead of dumping every video at once,
  * uploads parts **sequentially** (one at a time) with a configurable delay
    between finished uploads, so it looks like a person and doesn't hammer the
    API, and
  * records each uploaded part in a ledger so reruns never double-upload.

All knobs live in the ``[youtube]`` section of ``configuration.cfg``. The
Google API client libraries are imported lazily so the rest of the pipeline
does not need them installed.

Auth: the first run opens a browser for OAuth consent (Desktop-app client),
then writes a refresh token to ``token_file`` and reuses it silently after.
"""
from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable
from zoneinfo import ZoneInfo

from src.config.constants import (
    YOUTUBE_CATEGORY_ID,
    YOUTUBE_CLIENT_SECRETS_FILE,
    YOUTUBE_DECLARE_ALTERED_CONTENT,
    YOUTUBE_DEFAULT_LANGUAGE,
    YOUTUBE_DESCRIPTIONS_DIR,
    YOUTUBE_MADE_FOR_KIDS,
    YOUTUBE_PLAYLIST_ID,
    YOUTUBE_PLAYLIST_PRIVACY,
    YOUTUBE_PLAYLIST_TITLE,
    YOUTUBE_THUMBNAIL_FILE,
    YOUTUBE_TITLE_SOURCE,
    YOUTUBE_PARTS_DIR,
    YOUTUBE_PUBLISH_INTERVAL_DAYS,
    YOUTUBE_PUBLISH_PRIVACY_STATUS,
    YOUTUBE_PUBLISH_START_DATE,
    YOUTUBE_PUBLISH_TIME,
    YOUTUBE_PUBLISH_TIMEZONE,
    YOUTUBE_TOKEN_FILE,
    YOUTUBE_UPLOAD_CHUNK_SIZE,
    YOUTUBE_UPLOAD_DELAY_SECONDS,
    YOUTUBE_UPLOAD_MAX_RETRIES,
    YOUTUBE_UPLOAD_STATE_FILE,
)

# YouTube tag list is capped at ~500 chars total by the API; trim defensively.
_MAX_TAGS_CHARS = 480
# YouTube title hard cap.
_MAX_TITLE_CHARS = 100
# Scopes: upload videos + read/manage our own channel. The broad ``youtube``
# scope is needed to create playlists and add videos to them (and also covers
# the read used by channel_info). If you previously authorized with a narrower
# scope, delete the token_file to re-consent.
_SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube",
]


# --------------------------------------------------------------------------- #
# Description parsing
# --------------------------------------------------------------------------- #
@dataclass
class PartUpload:
    """Everything needed to upload one part."""

    part_num: int
    md_path: Path
    title: str
    description: str
    tags: list[str]
    video_path: Path

    @property
    def key(self) -> str:
        return f"part_{self.part_num:02d}"


def _split_sections(md: str) -> dict[str, str]:
    """Split a part markdown into ``{h2_title_lower: body}``."""
    sections: dict[str, str] = {}
    current: str | None = None
    buf: list[str] = []
    for line in md.splitlines():
        if line.startswith("## "):
            if current is not None:
                sections[current] = "\n".join(buf).strip()
            current = line[3:].strip().lower()
            buf = []
        elif current is not None:
            buf.append(line)
    if current is not None:
        sections[current] = "\n".join(buf).strip()
    return sections


def _parse_tags(raw: str) -> list[str]:
    tags: list[str] = []
    total = 0
    for tag in (t.strip() for t in raw.split(",")):
        if not tag:
            continue
        # +1 accounts for the joining comma YouTube counts toward the limit.
        if total + len(tag) + 1 > _MAX_TAGS_CHARS:
            break
        tags.append(tag)
        total += len(tag) + 1
    return tags


def _resolve_video_path(file_section: str, parts_dir: Path) -> Path | None:
    """The ``## File`` body is a backticked path; fall back to parts_dir."""
    m = re.search(r"`([^`]+\.mp4)`", file_section)
    if not m:
        return None
    p = Path(m.group(1))
    if p.exists():
        return p
    # Path in the md may be stale; try resolving the basename in parts_dir.
    candidate = parts_dir / p.name
    return candidate if candidate.exists() else None


def parse_part_md(md_path: Path, parts_dir: Path) -> PartUpload | None:
    """Parse a ``part_NN.md`` into a PartUpload, or None if it can't upload."""
    num_match = re.search(r"part_(\d+)\.md$", md_path.name)
    if not num_match:
        return None
    part_num = int(num_match.group(1))

    sections = _split_sections(md_path.read_text(encoding="utf-8"))
    md_title = sections.get("youtube title", "").strip()
    description = sections.get("youtube description", "").strip()
    tags = _parse_tags(sections.get("youtube tags", ""))
    video_path = _resolve_video_path(sections.get("file", ""), parts_dir)

    if not description or video_path is None:
        return None

    # Title: the hyphenated mp4 filename stem
    # (e.g. "Designing Data-Intensive Applications - Part 9 - Graph databases
    # and querying"), or the md "## YouTube Title" if title_source says so.
    if YOUTUBE_TITLE_SOURCE == "description" and md_title:
        title = md_title
    else:
        title = video_path.stem
    if len(title) > _MAX_TITLE_CHARS:
        title = title[: _MAX_TITLE_CHARS - 1].rstrip() + "…"
    if not title:
        return None
    return PartUpload(
        part_num=part_num,
        md_path=md_path,
        title=title,
        description=description,
        tags=tags,
        video_path=video_path,
    )


def discover_parts(
    descriptions_dir: Path = YOUTUBE_DESCRIPTIONS_DIR,
    parts_dir: Path = YOUTUBE_PARTS_DIR,
) -> list[PartUpload]:
    """All uploadable parts found under ``descriptions_dir``, ordered by part."""
    out: list[PartUpload] = []
    for md_path in sorted(descriptions_dir.glob("part_*.md")):
        parsed = parse_part_md(md_path, parts_dir)
        if parsed is not None:
            out.append(parsed)
    out.sort(key=lambda p: p.part_num)
    return out


# --------------------------------------------------------------------------- #
# Scheduling
# --------------------------------------------------------------------------- #
def _tzinfo(name: str):
    try:
        return ZoneInfo(name)
    except Exception:
        return timezone.utc


def publish_at_for(index: int) -> str:
    """RFC3339 UTC timestamp at which the ``index``-th part should go live.

    ``index`` is zero-based over the *parts being uploaded this run*, so the
    first uploaded part lands on ``publish_start_date`` and each subsequent
    part is pushed ``publish_interval_days`` later at ``publish_time``.
    """
    if not YOUTUBE_PUBLISH_START_DATE:
        raise ValueError(
            "youtube.publish_start_date is empty — set it in configuration.cfg "
            "(YYYY-MM-DD) so videos can be scheduled."
        )
    hh, mm = (int(x) for x in YOUTUBE_PUBLISH_TIME.split(":"))
    base_date = datetime.strptime(YOUTUBE_PUBLISH_START_DATE, "%Y-%m-%d").date()
    tz = _tzinfo(YOUTUBE_PUBLISH_TIMEZONE)
    local_dt = datetime(
        base_date.year, base_date.month, base_date.day, hh, mm, tzinfo=tz
    ) + timedelta(days=YOUTUBE_PUBLISH_INTERVAL_DAYS * index)
    return local_dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# --------------------------------------------------------------------------- #
# Upload ledger
# --------------------------------------------------------------------------- #
def load_ledger(path: Path = YOUTUBE_UPLOAD_STATE_FILE) -> dict[str, dict]:
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {}
    return {}


def save_ledger(ledger: dict[str, dict], path: Path = YOUTUBE_UPLOAD_STATE_FILE) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(ledger, indent=2, sort_keys=True), encoding="utf-8")


# --------------------------------------------------------------------------- #
# YouTube client
# --------------------------------------------------------------------------- #
def _build_service():
    """Authenticate (browser on first run) and return a YouTube API client."""
    try:
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
        from google_auth_oauthlib.flow import InstalledAppFlow
        from googleapiclient.discovery import build
    except ImportError as exc:  # pragma: no cover - depends on optional deps
        raise RuntimeError(
            "Google API client libraries are not installed. Run:\n"
            "  uv add google-api-python-client google-auth-oauthlib "
            "google-auth-httplib2"
        ) from exc

    if not YOUTUBE_CLIENT_SECRETS_FILE:
        raise ValueError(
            "youtube.client_secrets_file is empty — download an OAuth "
            "'Desktop app' client JSON from Google Cloud Console and point "
            "configuration.cfg at it."
        )

    token_path = YOUTUBE_TOKEN_FILE
    creds = None
    if token_path.exists():
        creds = Credentials.from_authorized_user_file(str(token_path), _SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(
                YOUTUBE_CLIENT_SECRETS_FILE, _SCOPES
            )
            creds = flow.run_local_server(port=0)
        token_path.parent.mkdir(parents=True, exist_ok=True)
        token_path.write_text(creds.to_json(), encoding="utf-8")

    return build("youtube", "v3", credentials=creds)


def channel_info(service) -> dict:
    """Return ``{id, title, customUrl}`` for the channel the token uploads to.

    This is the authoritative answer to "which channel are we uploading to?" —
    it's whichever channel the Google account signed in during OAuth owns.
    """
    resp = (
        service.channels()
        .list(part="snippet", mine=True)
        .execute()
    )
    items = resp.get("items", [])
    if not items:
        return {}
    ch = items[0]
    snip = ch.get("snippet", {})
    return {
        "id": ch.get("id", ""),
        "title": snip.get("title", ""),
        "customUrl": snip.get("customUrl", ""),
    }


_PLAYLIST_LEDGER_KEY = "_playlist"


def _normalize_playlist_id(raw: str) -> str:
    """Accept a bare id (PL…), a watch/``?list=`` URL, or a Studio
    ``/playlist/<id>/edit`` URL; return the bare playlist id."""
    raw = raw.strip()
    for pat in (r"[?&]list=([A-Za-z0-9_-]+)", r"/playlist/([A-Za-z0-9_-]+)"):
        m = re.search(pat, raw)
        if m:
            return m.group(1)
    return raw


def resolve_playlist(service, ledger: dict) -> str | None:
    """Return the playlist id uploads should be added to.

    Precedence: configured ``playlist_id`` → a previously-created id cached in
    the ledger → create a new playlist (titled ``playlist_title``) and cache it.
    Caching is what keeps the daily ``--limit`` batches adding to one playlist
    instead of creating a fresh one each run. Returns None on creation failure.
    """
    if YOUTUBE_PLAYLIST_ID.strip():
        return _normalize_playlist_id(YOUTUBE_PLAYLIST_ID)

    cached = ledger.get(_PLAYLIST_LEDGER_KEY)
    if isinstance(cached, dict) and cached.get("id"):
        return cached["id"]

    from googleapiclient.errors import HttpError

    title = YOUTUBE_PLAYLIST_TITLE.strip() or "Uploads"
    try:
        resp = service.playlists().insert(
            part="snippet,status",
            body={
                "snippet": {"title": title},
                "status": {"privacyStatus": YOUTUBE_PLAYLIST_PRIVACY},
            },
        ).execute()
    except HttpError as exc:
        print(f"Warning: could not create playlist {title!r}: {exc}")
        return None

    pid = resp["id"]
    ledger[_PLAYLIST_LEDGER_KEY] = {
        "id": pid,
        "title": title,
        "url": f"https://www.youtube.com/playlist?list={pid}",
    }
    save_ledger(ledger)
    print(f"Created playlist: {title} → https://www.youtube.com/playlist?list={pid}")
    return pid


def _add_to_playlist(service, playlist_id: str, video_id: str) -> bool:
    """Append a video to the playlist. Non-fatal: a failure here doesn't undo
    the upload (the video is already up and recorded in the ledger)."""
    from googleapiclient.errors import HttpError

    try:
        service.playlistItems().insert(
            part="snippet",
            body={
                "snippet": {
                    "playlistId": playlist_id,
                    "resourceId": {"kind": "youtube#video", "videoId": video_id},
                }
            },
        ).execute()
        return True
    except HttpError as exc:
        print(f"      ! could not add to playlist: {exc}")
        return False


def _set_thumbnail(service, video_id: str, path: Path) -> bool:
    """Set a custom thumbnail on a video. Non-fatal; requires a verified
    channel (otherwise YouTube returns 403)."""
    from googleapiclient.errors import HttpError
    from googleapiclient.http import MediaFileUpload

    try:
        service.thumbnails().set(
            videoId=video_id, media_body=MediaFileUpload(str(path))
        ).execute()
        return True
    except HttpError as exc:
        print(f"      ! could not set thumbnail: {exc}")
        return False


def _is_quota_error(exc) -> bool:
    """True if an HttpError is a daily-quota exhaustion (403 quotaExceeded)."""
    status = getattr(getattr(exc, "resp", None), "status", None)
    return status == 403 and "quota" in str(exc).lower()


def _upload_one(service, part: PartUpload, publish_at: str) -> str:
    """Resumable-upload a single part; return its YouTube video id."""
    import http.client
    import httplib2
    from googleapiclient.errors import HttpError
    from googleapiclient.http import MediaFileUpload

    # Transient network failures (flaky wifi, SSL EOF, dropped sockets). The
    # resumable upload resumes from the last byte the server received on retry,
    # so the in-progress upload isn't restarted. IOError (== OSError) covers
    # ssl.SSLError/SSLEOFError, socket.error, and ConnectionError.
    retriable_net = (IOError, http.client.HTTPException, httplib2.HttpLib2Error)

    body = {
        "snippet": {
            "title": part.title,
            "description": part.description,
            "tags": part.tags,
            "categoryId": YOUTUBE_CATEGORY_ID,
            "defaultLanguage": YOUTUBE_DEFAULT_LANGUAGE,
            "defaultAudioLanguage": YOUTUBE_DEFAULT_LANGUAGE,
        },
        "status": {
            # A future publishAt + "private" makes the video "scheduled";
            # YouTube flips it to publish_privacy_status at that instant.
            "privacyStatus": "private",
            "publishAt": publish_at,
            "selfDeclaredMadeForKids": YOUTUBE_MADE_FOR_KIDS,
        },
    }

    # Altered/AI-content disclosure. The Data API supports this via
    # status.containsSyntheticMedia (added 2024-10-30), so we send it
    # EXPLICITLY — sending nothing leaves the question unanswered and Studio
    # flags it for a manual toggle. Defaults to False ("not altered/synthetic").
    body["status"]["containsSyntheticMedia"] = YOUTUBE_DECLARE_ALTERED_CONTENT

    media = MediaFileUpload(
        str(part.video_path),
        chunksize=YOUTUBE_UPLOAD_CHUNK_SIZE,
        resumable=True,
        mimetype="video/mp4",
    )
    request = service.videos().insert(
        part="snippet,status", body=body, media_body=media
    )

    response = None
    error_retries = 0
    last_pct = -10
    while response is None:
        try:
            status, response = request.next_chunk()
            if status:
                pct = int(status.progress() * 100)
                if pct - last_pct >= 10:
                    print(f"      … {pct}%")
                    last_pct = pct
        except HttpError as exc:
            if exc.resp.status in (500, 502, 503, 504) and error_retries < YOUTUBE_UPLOAD_MAX_RETRIES:
                error_retries += 1
                sleep_s = min(2 ** error_retries, 60)
                print(f"      transient {exc.resp.status}; retry {error_retries} in {sleep_s}s")
                time.sleep(sleep_s)
                continue
            raise
        except retriable_net as exc:
            if error_retries < YOUTUBE_UPLOAD_MAX_RETRIES:
                error_retries += 1
                sleep_s = min(2 ** error_retries, 60)
                print(f"      network error ({type(exc).__name__}); resuming, retry {error_retries} in {sleep_s}s")
                time.sleep(sleep_s)
                continue
            raise
    return response["id"]


# --------------------------------------------------------------------------- #
# Orchestration
# --------------------------------------------------------------------------- #
def upload_parts(
    parts: Iterable[PartUpload] | None = None,
    *,
    dry_run: bool = False,
    limit: int | None = None,
    delay_seconds: float | None = None,
) -> list[dict]:
    """Upload parts sequentially with scheduling, pacing, and a ledger.

    Returns a list of result dicts (one per part acted on).
    """
    if parts is None:
        parts = discover_parts()
    parts = list(parts)

    ledger = load_ledger()
    pending = [p for p in parts if p.key not in ledger]
    if limit is not None:
        pending = pending[:limit]

    already = len(parts) - len([p for p in parts if p.key not in ledger])
    print(
        f"Found {len(parts)} parts · {already} already uploaded · "
        f"{len(pending)} to upload this run"
        + (" (dry run)" if dry_run else "")
    )

    delay = YOUTUBE_UPLOAD_DELAY_SECONDS if delay_seconds is None else delay_seconds
    service = None if dry_run else _build_service()
    playlist_id: str | None = None
    thumbnail_path = Path(YOUTUBE_THUMBNAIL_FILE) if YOUTUBE_THUMBNAIL_FILE.strip() else None
    if service is not None:
        ch = channel_info(service)
        if ch:
            handle = f" ({ch['customUrl']})" if ch.get("customUrl") else ""
            print(f"Uploading to channel: {ch['title']}{handle} [{ch['id']}]")
        else:
            print("Warning: could not resolve the channel for this token.")
        playlist_id = resolve_playlist(service, ledger)
        if playlist_id:
            print(f"Adding uploads to playlist: {playlist_id}")
        if thumbnail_path and not thumbnail_path.exists():
            print(f"Warning: thumbnail_file not found ({thumbnail_path}); skipping thumbnails.")
            thumbnail_path = None
        elif thumbnail_path:
            print(f"Setting thumbnail on each upload: {thumbnail_path}")
    elif dry_run:
        if YOUTUBE_PLAYLIST_ID.strip():
            print(f"Would add uploads to playlist: {_normalize_playlist_id(YOUTUBE_PLAYLIST_ID)}")
        else:
            cached = load_ledger().get(_PLAYLIST_LEDGER_KEY)
            if isinstance(cached, dict) and cached.get("id"):
                print(f"Would add uploads to existing playlist: {cached['id']}")
            else:
                title = YOUTUBE_PLAYLIST_TITLE.strip() or "Uploads"
                print(f"Would create + use a new playlist: {title!r}")
    results: list[dict] = []

    for i, part in enumerate(pending):
        # Schedule index is over ALL parts so the calendar stays stable across
        # runs — part N always targets start_date + N*interval.
        publish_at = publish_at_for(part.part_num - 1)
        print(
            f"[{i + 1}/{len(pending)}] {part.key}: {part.title!r}\n"
            f"      file: {part.video_path.name}\n"
            f"      publishAt: {publish_at}"
        )

        if dry_run:
            results.append({"part": part.key, "publishAt": publish_at, "dryRun": True})
            continue

        from googleapiclient.errors import HttpError

        try:
            video_id = _upload_one(service, part, publish_at)
        except HttpError as exc:
            if _is_quota_error(exc):
                print(
                    f"\nYouTube daily upload quota reached after "
                    f"{len(results)} upload(s) this run.\n"
                    f"Re-run tomorrow with --limit N; the ledger resumes where "
                    f"you left off."
                )
                break
            raise

        url = f"https://youtu.be/{video_id}"
        in_playlist = _add_to_playlist(service, playlist_id, video_id) if playlist_id else False
        thumb_set = _set_thumbnail(service, video_id, thumbnail_path) if thumbnail_path else False
        ledger[part.key] = {
            "videoId": video_id,
            "url": url,
            "title": part.title,
            "publishAt": publish_at,
            "file": part.video_path.as_posix(),
            "playlistId": playlist_id if in_playlist else None,
            "thumbnail": thumb_set,
        }
        save_ledger(ledger)
        added = " · added to playlist" if in_playlist else ""
        thumbed = " · thumbnail set" if thumb_set else ""
        print(f"      ✓ uploaded → {url} (scheduled for {publish_at}){added}{thumbed}")
        results.append(ledger[part.key])

        # Human-like pacing between uploads (skip after the last one).
        if delay and i < len(pending) - 1:
            print(f"      sleeping {delay:.0f}s before next upload …")
            time.sleep(delay)

    print(f"\nDone. {len([r for r in results if not r.get('dryRun')])} uploaded.")
    return results
