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


# --- presentation banner ------------------------------------------------------------

# Stash's own Blueprint interface colors, so the header reads as Stash rather
# than as a generic uploader ad slot. Every banner paints its own background
# and text colors: Empornium skins vary and none of them are safe to inherit.
BANNER_BG = "#202b33"
BANNER_STRIP_BG = "#30404d"
BANNER_TEXT = "#f5f8fa"
BANNER_MUTED = "#8a9ba8"
BANNER_DIM = "#5c7080"
BANNER_LINK = "#48aff0"
BANNER_SIG = "#7b8894"

STASH_URL = "https://stashapp.cc"
UPLOADER_URL = "https://github.com/barelyboundaries/empstashuploader"
UPLOADER_NAME = "Empornium Stash Uploader"

# Blueprint's dark ladder continued downward: the page ground sits one step
# below the banner chrome (DARK_GRAY2 under DARK_GRAY3) so the header reads as
# a raised surface instead of a flat block on an identical field.
PAGE_BG = "#182026"
PAGE_FONT = "Helvetica"

BANNER_STYLES = ("plate", "rail", "signature", "off")
DEFAULT_BANNER_STYLE = "plate"

# Only the two styles that already paint dark chrome carry that surface across
# the whole post; "signature" exists precisely to add no background, and "off"
# adds nothing at all.
PAGE_WRAPPED_STYLES = ("plate", "rail")

# A long megapack title at [size=6] wraps to three lines and swamps the strip
# below it, so the display size steps down as the title grows.
_BANNER_TITLE_SIZES = ((60, 6), (100, 5))
_BANNER_TITLE_MIN_SIZE = 4


def normalize_banner_style(value: str | None) -> str:
    """Coerce a configured banner style to one of BANNER_STYLES."""
    cleaned = str(value or "").strip().lower()
    if cleaned in ("", "none", "false", "no", "0"):
        return "off" if cleaned else DEFAULT_BANNER_STYLE
    if cleaned in ("default", "true", "yes", "on", "1"):
        return DEFAULT_BANNER_STYLE
    return cleaned if cleaned in BANNER_STYLES else DEFAULT_BANNER_STYLE


def _banner_title_size(title: str) -> int:
    for limit, size in _BANNER_TITLE_SIZES:
        if len(title) <= limit:
            return size
    return _BANNER_TITLE_MIN_SIZE


def _banner_link(url: str, label: str, bold: bool = False, color: str = BANNER_LINK) -> str:
    text = f"[b]{label}[/b]" if bold else label
    return f"[url={url}][color={color}]{text}[/color][/url]"


def render_banner(
    style: str = DEFAULT_BANNER_STYLE,
    title: str = "",
    kind: str = "MEGAPACK",
    subtitle: str = "",
    stats: list[tuple[str, str]] | None = None,
) -> str:
    """Render the header that opens every generated presentation.

    Pure BBCode by design: a hosted logo would rot and would count against
    ``presentation_max_bytes``. Each style is emitted on a single physical
    line because the tracker runs descriptions through ``nl2br`` -- a newline
    between two table rows becomes a stray ``<br>`` and a blank band.

    Every argument is raw text; escaping happens here, so callers must not
    pre-escape (``bbcode_escape`` is idempotent, so passing escaped text is
    harmless, but the banner's own markup must never be escaped).
    """
    resolved = normalize_banner_style(style)
    if resolved == "off":
        return ""

    stash = _banner_link(STASH_URL, "Stash", bold=True)
    uploader = _banner_link(UPLOADER_URL, UPLOADER_NAME, bold=True)

    if resolved == "signature":
        parts = [
            f"[align=center][size=1][color={BANNER_SIG}]built from a [/color]",
            _banner_link(STASH_URL, "Stash", bold=True, color=BANNER_SIG),
            f"[color={BANNER_SIG}] library with the [/color]",
            _banner_link(UPLOADER_URL, UPLOADER_NAME, bold=True, color=BANNER_SIG),
            "[/size][/align][hr]",
        ]
        return "".join(parts)

    clean_kind = bbcode_escape(kind).upper() or "RELEASE"

    if resolved == "rail":
        return "".join([
            f"[bg={BANNER_BG}][table=100%,nball,nopad][tr]",
            f"[td=5px,{BANNER_LINK}][/td][td=12px][/td]",
            f"[td=vam][size=3][color={BANNER_TEXT}][b]STASH[/b][/color]",
            f"[color={BANNER_DIM}] ▸ [/color]",
            f"[color={BANNER_TEXT}][b]{clean_kind}[/b][/color][/size][/td]",
            f"[td=vam][align=right][size=1][color={BANNER_MUTED}]catalogued in [/color]{stash}",
            f"[color={BANNER_MUTED}] · posted with the [/color]{uploader}[/size][/align][/td]",
            "[td=12px][/td][/tr][/table][/bg]",
        ])

    # plate: the banner absorbs the title and carries a spec strip, so the
    # attribution rides along with figures a downloader actually wants above
    # the fold instead of standing on its own as a credit.
    clean_title = bbcode_escape(title)
    clean_subtitle = bbcode_escape(subtitle)
    eyebrow = f"STASH {clean_kind}"
    if clean_subtitle:
        eyebrow += f" · {clean_subtitle}"

    lines = [
        f"[bg={BANNER_BG}][table=100%,nball,nopad][tr][td=16px][/td]",
        f"[td=vab][size=1][color={BANNER_MUTED}]{eyebrow}[/color][/size]",
    ]
    if clean_title:
        size = _banner_title_size(clean_title)
        lines.append(
            f"[br][size={size}][font=Trebuchet MS][b][color={BANNER_TEXT}]"
            f"{clean_title}[/color][/b][/font][/size]"
        )
    lines.extend([
        "[/td]",
        f"[td=vab][align=right][size=1]{_banner_link(STASH_URL, 'stashapp.cc')}",
        f"[color={BANNER_DIM}] · [/color]{_banner_link(UPLOADER_URL, UPLOADER_NAME)}",
        "[/size][/align][/td][td=16px][/td][/tr][/table][/bg]",
    ])

    cells = []
    for label, value in stats or []:
        if label is None or value is None:
            continue
        clean_label = bbcode_escape(str(label)).upper()
        clean_value = bbcode_escape(str(value))
        if clean_label and clean_value:
            cells.append((clean_label, clean_value))
    if cells:
        width = max(1, 100 // len(cells))
        lines.append(f"[bg={BANNER_STRIP_BG}][table=100%,nball][tr]")
        for label, value in cells:
            lines.append(
                f"[td=vam,{width}%][align=center][size=1][color={BANNER_MUTED}]{label}[/color][/size]"
                f"[br][size=3][color={BANNER_TEXT}][b]{value}[/b][/color][/size][/align][/td]"
            )
        lines.append("[/tr][/table][/bg]")

    return "".join(lines)


def render_meta_panel(
    studio: str | None = None,
    performers: str | None = None,
    tags: str | None = None,
    notes: str | None = None,
) -> str:
    """Render the metadata block following the banner as a surface panel.

    Cohesive with the banner palette: uses BANNER_BG for the panel surface,
    BANNER_MUTED for field labels, BANNER_DIM for separators, and BANNER_TEXT
    for values. Emitted on a single physical line to comply with the tracker's
    nl2br processing (no stray blank bands between table rows).

    Callers may pass raw text or pre-escaped display values; escaping is handled
    via bbcode_escape (which is idempotent), while the panel's own markup is
    never escaped. Returns an empty string if there is nothing to render.
    """
    clean_studio = bbcode_escape(str(studio)).strip() if studio else ""
    clean_performers = bbcode_escape(str(performers)).strip() if performers else ""
    clean_tags = bbcode_escape(str(tags)).strip() if tags else ""
    clean_notes = bbcode_escape(str(notes), keep_newlines=True).strip() if notes else ""

    rows: list[str] = []
    if clean_studio:
        rows.append(
            f"[b][color={BANNER_MUTED}]Studio[/color][/b]"
            f"[color={BANNER_DIM}]: [/color]"
            f"[color={BANNER_TEXT}]{clean_studio}[/color]"
        )
    if clean_performers:
        rows.append(
            f"[b][color={BANNER_MUTED}]Performers[/color][/b]"
            f"[color={BANNER_DIM}]: [/color]"
            f"[color={BANNER_TEXT}]{clean_performers}[/color]"
        )
    if clean_tags:
        rows.append(
            f"[b][color={BANNER_MUTED}]Tags[/color][/b]"
            f"[color={BANNER_DIM}]: [/color]"
            f"[color={BANNER_TEXT}]{clean_tags}[/color]"
        )
    if clean_notes:
        # Not [quote]: inside the page wrapper the skin's own quote box keeps
        # its light background while the inherited text colour is now near
        # white. This paints its own surface, so it reads on every skin.
        rows.append(
            f"[bg={BANNER_STRIP_BG}][table=100%,nball,nopad][tr]"
            f"[td=3px,{BANNER_LINK}][/td][td=12px][/td]"
            f"[td][color={BANNER_TEXT}]{clean_notes}[/color][/td]"
            f"[td=12px][/td][/tr][/table][/bg]"
        )

    if not rows:
        return ""

    body = "[br]".join(rows)
    return f"[bg={BANNER_BG}][table=100%,nball,nopad][tr][td=16px][/td][td]{body}[/td][td=16px][/td][/tr][/table][/bg]"


def wrap_presentation(header: str, body: str, style: str = DEFAULT_BANNER_STYLE) -> str:
    """Extend the banner's surface across the whole post.

    The header (banner + metadata panel) stays full-bleed so it reads as a
    masthead; the body gets a 16px gutter so headings and thumbnails line up
    with the header's own inner padding instead of touching the edge.

    Returns the parts unwrapped for any style that paints no chrome, so an
    "off" or "signature" post is left exactly as the tracker's skin renders it.
    """
    resolved = normalize_banner_style(style)
    parts = [p for p in (header, body) if p and p.strip()]
    if resolved not in PAGE_WRAPPED_STYLES or not parts:
        return "\n".join(parts)

    guttered = body
    if body and body.strip():
        guttered = (
            "[table=100%,nball,nopad][tr][td=16px][/td][td]\n"
            f"{body}\n"
            "[/td][td=16px][/td][/tr][/table]"
        )

    inner = "\n".join(p for p in (header, guttered) if p and p.strip())
    return (
        f"[bg={PAGE_BG}][color={BANNER_TEXT}][font={PAGE_FONT}]\n"
        f"{inner}\n"
        "[/font][/color][/bg]"
    )


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
