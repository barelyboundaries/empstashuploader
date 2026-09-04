import re
from datetime import date

from .tags import (
    MAX_TAGS,
    ResolvedTags,
    TagSource,
    empify,
    load_vocabulary,
    resolve_tags,
)

MAX_TITLE_LEN = 200
MAX_NOTES_LEN = 2000

PLACEHOLDER_RE = re.compile(r"\{scene-image-(\d+)\}")

RESOLUTION_LADDER = [
    (144, "144p"),
    (240, "240p"),
    (360, "360p"),
    (480, "480p"),
    (540, "540p"),
    (720, "720p"),
    (1080, "1080p"),
    (1440, "1440p"),
    (1920, "2160p"),
    (2560, "5K"),
    (3000, "6K"),
    (3584, "7K"),
    (3840, "8K"),
    (6143, "8K+"),
]


def resolution_for(height: int | None) -> str:
    if not height:
        return ""
    best = ""
    for low, label in RESOLUTION_LADDER:
        if height >= low:
            best = label
    return best


def format_duration(seconds: float | None) -> str:
    if not seconds or seconds <= 0:
        return ""
    total = int(seconds)
    hours, rem = divmod(total, 3600)
    minutes, secs = divmod(rem, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"


def join_names(names: list[str]) -> str:
    count = len(names)
    if count == 0:
        return ""
    if count == 1:
        return names[0]
    if count == 2:
        return f"{names[0]} & {names[1]}"
    return ", ".join(names[:-1]) + f" & {names[-1]}"


def bbcode_escape(text: str, keep_newlines: bool = False) -> str:
    cleaned = "".join(ch for ch in text if ch >= " " or ch in "\t\n\r")
    if keep_newlines:
        cleaned = re.sub(r"[ \t]+", " ", cleaned).strip()
    else:
        cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned.replace("[", "&#91;").replace("]", "&#93;")


def tag_sources_for_scene(scene, primary=None) -> list[TagSource]:
    sources: list[TagSource] = []
    raw_tags = getattr(scene, "tags", None) if hasattr(scene, "tags") else (scene.get("tags", []) if isinstance(scene, dict) else [])
    for t in raw_tags or []:
        if isinstance(t, TagSource):
            sources.append(t)
        elif isinstance(t, str) and t.strip():
            sources.append(TagSource(t.strip(), "scene_tag"))
        elif isinstance(t, dict) and "name" in t and str(t["name"]).strip():
            sources.append(TagSource(str(t["name"]).strip(), "scene_tag"))

    raw_perfs = getattr(scene, "performers", None) if hasattr(scene, "performers") else (scene.get("performers", []) if isinstance(scene, dict) else [])
    for p in raw_perfs or []:
        if isinstance(p, TagSource):
            sources.append(p)
        elif isinstance(p, str) and p.strip():
            sources.append(TagSource(p.strip(), "performer"))
        elif isinstance(p, dict) and "name" in p and str(p["name"]).strip():
            sources.append(TagSource(str(p["name"]).strip(), "performer"))

    raw_studio = getattr(scene, "studio", None) if hasattr(scene, "studio") else (scene.get("studio") if isinstance(scene, dict) else None)
    if raw_studio:
        if isinstance(raw_studio, TagSource):
            sources.append(raw_studio)
        elif isinstance(raw_studio, str) and raw_studio.strip():
            sources.append(TagSource(raw_studio.strip(), "studio"))
        elif isinstance(raw_studio, dict) and "name" in raw_studio and str(raw_studio["name"]).strip():
            sources.append(TagSource(str(raw_studio["name"]).strip(), "studio"))

    height = getattr(primary, "height", None) if primary else (scene.get("height") if isinstance(scene, dict) else None)
    video_codec = getattr(primary, "video_codec", None) if primary else (scene.get("video_codec") if isinstance(scene, dict) else None)
    duration = getattr(primary, "duration", None) if primary else (scene.get("duration") if isinstance(scene, dict) else None)

    resolution = resolution_for(height)
    if resolution:
        sources.append(TagSource(resolution, "derived"))
    if video_codec:
        sources.append(TagSource(str(video_codec).strip(), "derived"))
    if duration:
        sources.append(TagSource(f"{max(1, round(float(duration) / 60))}.min", "derived"))

    raw_date = getattr(scene, "date", None) if hasattr(scene, "date") else (scene.get("date") if isinstance(scene, dict) else None)
    if raw_date:
        try:
            parsed = date.fromisoformat(str(raw_date)[:10])
            sources.append(TagSource(str(parsed.year), "derived"))
            sources.append(TagSource(f"{parsed.year}.{parsed.month:02d}", "derived"))
            sources.append(TagSource(f"{parsed.year}.{parsed.month:02d}.{parsed.day:02d}", "derived"))
        except ValueError:
            pass
    return sources


def merge_tags_detailed(scenes, primaries: dict[str, object] | None = None) -> ResolvedTags:
    primaries = primaries or {}
    collected: list[TagSource] = []
    for scene in scenes:
        scene_id = getattr(scene, "scene_id", None) if hasattr(scene, "scene_id") else (scene.get("id") or scene.get("scene_id") if isinstance(scene, dict) else None)
        primary = primaries.get(scene_id) if scene_id is not None else None
        for item in tag_sources_for_scene(scene, primary):
            if isinstance(item, TagSource):
                collected.append(item)
            elif isinstance(item, str):
                collected.append(TagSource(item, "scene_tag"))
    return resolve_tags(collected)


def merge_tags(scenes, primaries: dict[str, object] | None = None) -> list[str]:
    return merge_tags_detailed(scenes, primaries).tags


def pack_performer_union(scenes, limit: int = 4) -> list[str]:
    names: set[str] = set()
    for scene in scenes:
        perfs = getattr(scene, "performers", None) if hasattr(scene, "performers") else (scene.get("performers", []) if isinstance(scene, dict) else [])
        for name in perfs or []:
            if isinstance(name, str):
                cleaned = name.strip()
                if cleaned:
                    names.add(cleaned)
            elif isinstance(name, dict) and "name" in name:
                cleaned = str(name["name"]).strip()
                if cleaned:
                    names.add(cleaned)
    ordered = sorted(names)
    return ordered[:limit]


def pack_studio(scenes) -> str:
    studios = set()
    for s in scenes:
        val = getattr(s, "studio", None) if hasattr(s, "studio") else (s.get("studio") if isinstance(s, dict) else None)
        if val:
            if isinstance(val, str) and val.strip():
                studios.add(val.strip())
            elif isinstance(val, dict) and "name" in val and str(val["name"]).strip():
                studios.add(str(val["name"]).strip())
    if len(studios) == 1:
        return next(iter(studios))
    return ""


def pack_title_default(scenes) -> str:
    parts: list[str] = []
    studio = pack_studio(scenes)
    if studio:
        parts.append(f"[{studio}]")
    performers = pack_performer_union(scenes)
    if performers:
        names = join_names(performers)
        union = {p.strip() for s in scenes for p in s.performers}
        extra = len(union) - len(performers)
        if extra > 0:
            names += f" +{extra} more"
        parts.append(names)
    parts.append(f"Megapack ({len(scenes)} scenes)")
    dates: list[str] = []
    for scene in scenes:
        if scene.date:
            try:
                dates.append(date.fromisoformat(scene.date).isoformat())
            except ValueError:
                pass
    if dates and len(dates) == len(scenes):
        parts.append(f"({min(dates)} to {max(dates)})")
    return " - ".join(parts)


def scene_title_default(scene, primary) -> str:
    title = (scene.title or "").strip()
    if title:
        return title
    if primary:
        return primary.basename
    return scene.scene_id


THUMB_WIDTH = 200
# Thumbnails are generated at 2x the display width so they stay crisp on
# high-DPI screens while costing a fraction of the full-size image.
THUMB_RENDER_WIDTH = THUMB_WIDTH * 2          # 400


def render_description(title: str, tags: list[str], notes: str, scenes, thumb_urls: list[str] | None = None) -> str:
    lines: list[str] = []
    lines.append(f"[size=4][b]{bbcode_escape(title)}[/b][/size]")
    lines.append("")
    for i, item in enumerate(scenes):
        image = item.get("image_url")
        if not image:
            continue
        escaped_full = bbcode_escape(image)
        thumb = thumb_urls[i] if thumb_urls and i < len(thumb_urls) and thumb_urls[i] else image
        escaped_thumb = bbcode_escape(thumb)
        lines.append(f"[url={escaped_full}][img={THUMB_WIDTH}]{escaped_thumb}[/img][/url]")
        lines.append("")
    if tags:
        lines.append(f"[b]Tags:[/b] {bbcode_escape(' '.join(tags))}")
        lines.append("")
    if notes:
        escaped = bbcode_escape(notes, keep_newlines=True).replace("\n", "[br]")
        lines.append(f"[b]Notes:[/b] {escaped}")
        lines.append("")
    while lines and lines[-1] == "":
        lines.pop()
    return "\n".join(lines)


class ImagePlaceholderError(ValueError):
    """A {scene-image-N} placeholder could not be resolved; generation fails."""


def finalize_description(description: str, urls: list[str]) -> str:
    """Replace every ``{scene-image-N}`` placeholder (1-based pack order).

    Raises ImagePlaceholderError if any placeholder is missing, empty, or
    left over. The Stage 5 no-placeholder guarantee is enforced here.
    """
    def _sub(match: re.Match) -> str:
        index = int(match.group(1)) - 1
        if 0 <= index < len(urls) and urls[index]:
            return urls[index]
        raise ImagePlaceholderError(f"unresolved placeholder {match.group(0)}")

    if not PLACEHOLDER_RE.search(description):
        raise ImagePlaceholderError("description contains no image placeholders")
    try:
        result = PLACEHOLDER_RE.sub(_sub, description)
    except ImagePlaceholderError:
        raise
    if PLACEHOLDER_RE.search(result):
        raise ImagePlaceholderError("placeholders remained after substitution")
    return result


def normalize_meta_input(title: str, tags: list[str] | None, notes: str, default_title: str, default_tags: list[str]) -> dict:
    clean_title = bbcode_escape(title)[:MAX_TITLE_LEN] or default_title
    clean_tags: list[str] = []
    seen: set[str] = set()
    for tag in default_tags if tags is None else tags:
        cleaned = empify(tag)
        if cleaned and cleaned not in seen:
            seen.add(cleaned)
            clean_tags.append(cleaned)
    clean_notes = bbcode_escape(notes, keep_newlines=True)[:MAX_NOTES_LEN]
    return {"title": clean_title, "tags": clean_tags, "notes": clean_notes}
