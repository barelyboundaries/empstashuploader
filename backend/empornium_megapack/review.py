import ctypes
import json
import os
import re
import shutil
import sys
import tempfile
from ctypes import wintypes
from datetime import datetime

import httpx

from .build import (
    BuildError,
    CONTACT_SHEETS_DIR,
    PACK_ROOT_NAME,
    build_created_utc,
    make_bundle,
    new_pack_id,
    payload_size,
    sanitize_name,
    stage_payload,
    write_manifest,
)
from .config import get_settings
from .gql import StashClient
from .images import ContactSheetError, ImageService
from .metadata import (
    bbcode_escape,
    finalize_description,
    merge_tags,
    normalize_meta_input,
    pack_title_default,
    render_description,
    resolution_for,
    scene_title_default,
    format_duration,
)
from .models import (
    ApplyRequest,
    ApplyResponse,
    BuildRequest,
    BuildResponse,
    BuiltScene,
    CleanupResponse,
    FileReview,
    ImagesRequest,
    ImagesResponse,
    Issue,
    MetaRequest,
    MetaResponse,
    MoveFilesRequest,
    MoveFilesResponse,
    MovedFileItem,
    PackMeta,
    PolicyInfo,
    ResolvedScene,
    ReviewRequest,
    ReviewResponse,
    SceneImage,
    SceneMeta,
    SceneReview,
    WarningItem,
)
from .torrents import (
    create_torrent,
    piece_size_for,
    sanitize_announce_url,
    source_for_announce,
    validate_announce_url,
)
from .paths import PathMapper, verify_same_file

POLICIES = ("creation", "mod_time")


def _epoch(dt_iso: str) -> float:
    return datetime.fromisoformat(dt_iso).timestamp()


def _scene_id_key(scene_id: str):
    try:
        return (0, int(scene_id))
    except ValueError:
        return (1, scene_id)


def _normname(name: str) -> str:
    return name.casefold()


def _needs_copy(source_dev, staging_dev) -> bool:
    if source_dev is None or staging_dev is None:
        return True
    return source_dev != staging_dev


class Win32FileTime(ctypes.Structure):
    _fields_ = [("dwLowDateTime", wintypes.DWORD), ("dwHighDateTime", wintypes.DWORD)]


class ByHandleFileInfo(ctypes.Structure):
    _fields_ = [
        ("dwFileAttributes", wintypes.DWORD),
        ("ftCreationTime", Win32FileTime),
        ("ftLastAccessTime", Win32FileTime),
        ("ftLastWriteTime", Win32FileTime),
        ("dwVolumeSerialNumber", wintypes.DWORD),
        ("nFileSizeHigh", wintypes.DWORD),
        ("nFileSizeLow", wintypes.DWORD),
        ("nNumberOfLinks", wintypes.DWORD),
        ("nFileIndexHigh", wintypes.DWORD),
        ("nFileIndexLow", wintypes.DWORD),
    ]


def _os_creation_time(path: str) -> float | None:
    if sys.platform != "win32":
        return None
    handle = None
    try:
        handle = _create_file_w(
            path,
            wintypes.DWORD(0x80000000),
            wintypes.DWORD(0x1 | 0x2),
            None,
            wintypes.DWORD(3),
            wintypes.DWORD(0),
            None,
        )
        if handle in (ctypes.c_void_p(-1).value, 0):
            return None
        info = ByHandleFileInfo()
        if not _get_file_information_by_handle(handle, ctypes.byref(info)):
            return None
        ft = info.ftCreationTime
        h = (ft.dwHighDateTime << 32) | ft.dwLowDateTime
        return (h - 116444736000000000) / 10_000_000
    except (AttributeError, OSError, ValueError, TypeError):
        return None
    finally:
        if handle:
            _close_handle(handle)


if sys.platform == "win32":
    _kernel32 = ctypes.windll.kernel32

    _create_file_w = _kernel32.CreateFileW
    _create_file_w.restype = wintypes.HANDLE
    _create_file_w.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    ]

    _get_file_information_by_handle = _kernel32.GetFileInformationByHandle
    _get_file_information_by_handle.restype = wintypes.BOOL
    _get_file_information_by_handle.argtypes = [wintypes.HANDLE, ctypes.POINTER(ByHandleFileInfo)]

    _close_handle = _kernel32.CloseHandle
    _close_handle.restype = wintypes.BOOL
    _close_handle.argtypes = [wintypes.HANDLE]
else:
    _create_file_w = None
    _get_file_information_by_handle = None
    _close_handle = None


class PackService:
    INDEX_FILENAME = "packs.json"

    def __init__(self, settings=None, stash=None, http=None):
        self.settings = settings or get_settings()
        self.stash = stash or StashClient(self.settings)
        self.images_service = ImageService(self.settings)
        self.pathmapper = PathMapper(self.settings)
        self.http = http if http is not None else httpx.Client(
            timeout=httpx.Timeout(self.settings.pack_download_timeout, connect=10.0)
        )
        self.packs: dict[str, dict] = self._load_index()

    def _index_path(self) -> Path:
        return self.settings.output_dir / self.INDEX_FILENAME

    def _load_index(self) -> dict[str, dict]:
        """Restore the completed-build registry across backend restarts."""
        index = self._index_path()
        try:
            data = json.loads(index.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        registry: dict[str, dict] = {}
        for pack_id, entry in data.items():
            if not re.fullmatch(r"[0-9a-f]{10}", pack_id or ""):
                continue
            if not isinstance(entry, dict):
                continue
            registry[pack_id] = {
                key: entry[key]
                for key in ("title", "torrent", "manifest", "description", "bundle")
                if isinstance(entry.get(key), str) and entry.get(key)
            }
        return registry

    def _persist_index(self) -> None:
        index = self._index_path()
        index.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(self.packs, indent=2, sort_keys=True, ensure_ascii=True)
        fd, tmp = tempfile.mkstemp(dir=str(index.parent), suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(payload)
            os.replace(tmp, index)
        finally:
            if os.path.exists(tmp):
                try:
                    os.unlink(tmp)
                except OSError:
                    pass

    def _resolve_policy(self, policy_name: str | None, ascending: bool | None):
        name = policy_name or self.settings.file_time_policy
        if name not in POLICIES:
            name = "creation"
        ascending = self.settings.file_time_ascending if ascending is None else ascending
        if name == "creation" and sys.platform != "win32":
            note = "OS creation time is not reliably available on this platform; using mod_time fallback."
        else:
            note = ""
        return PolicyInfo(name=name, ascending=ascending, note=note)

    def _staging_volume(self) -> int | None:
        staging = self.settings.staging_dir
        try:
            staging.mkdir(parents=True, exist_ok=True)
            return os.stat(staging).st_dev
        except OSError:
            parent = staging.parent
            while parent != staging and not os.path.exists(parent):
                parent = parent.parent
            try:
                return os.stat(parent).st_dev
            except OSError:
                return None

    def _probe(self, raw: dict) -> FileReview:
        mapped = self.pathmapper.apply(raw["path"])
        exists = os.path.isfile(mapped)
        accessible = exists and os.access(mapped, os.R_OK)
        path = mapped
        mapping_applied = self.pathmapper.is_mapped(raw["path"])
        if exists and mapping_applied and not verify_same_file(mapped, raw.get("size"), True, raw.get("oshash")):
            accessible = False
        creation_time = None
        time_source = "mod_time"
        if exists:
            creation_time = _os_creation_time(path) if sys.platform == "win32" else None
        if exists and accessible and creation_time is not None:
            time_source = "creation"
        elif exists:
            time_source = "mod_time" if sys.platform == "win32" else "creation_unavailable"
        return FileReview(
            file_id=raw["id"],
            basename=raw["basename"],
            path=path,
            size=raw["size"] or 0,
            width=raw.get("width"),
            height=raw.get("height"),
            duration=raw.get("duration"),
            video_codec=raw.get("video_codec"),
            mod_time=raw["mod_time"],
            created_at=raw["created_at"],
            creation_time=(
                datetime.fromtimestamp(creation_time).isoformat() if creation_time else None
            ),
            time_source=time_source,
            exists=exists,
            accessible=accessible,
            will_copy=False,
        )

    def _file_sort_key(self, file_review: FileReview, policy: str):
        if policy == "creation" and file_review.creation_time:
            t = _epoch(file_review.creation_time)
        else:
            t = _epoch(file_review.mod_time)
        return (t, _epoch(file_review.created_at), _scene_id_key(file_review.file_id))

    def _scene_sort_key(self, scene_raw: dict, primary: FileReview | None, policy: str):
        if primary is None:
            return (0.0, 0.0, _scene_id_key(scene_raw["id"]))
        t, fc, fid = self._file_sort_key(primary, policy)
        return (t, fc, _epoch(scene_raw.get("created_at") or primary.created_at), _scene_id_key(scene_raw["id"]))

    def _provisional_primary(self, files: list[FileReview], policy: str) -> FileReview | None:
        if not files:
            return None
        return min(files, key=lambda f: self._file_sort_key(f, policy))

    def _build_scenes(self, raw_scenes: dict[str, dict], policy_name: str) -> list[SceneReview]:
        scenes = []
        for scene_id, raw in raw_scenes.items():
            if raw is None:
                continue
            files = [self._probe(f) for f in raw.get("files", [])]
            needs_choice = len(files) > 1
            provisional = self._provisional_primary(files, policy_name)
            issues = []
            if not files:
                issues.append(Issue(code="no_files", message="Scene has no video files.", scene_id=scene_id))
            for f in files:
                if not f.exists:
                    issues.append(
                        Issue(code="file_missing", message=f"File not found on disk: {f.path}", scene_id=scene_id, path=f.path)
                    )
                elif not f.accessible:
                    issues.append(
                        Issue(code="file_inaccessible", message=f"File exists but is not readable: {f.path}", scene_id=scene_id, path=f.path)
                    )
            scenes.append(
                SceneReview(
                    scene_id=scene_id,
                    title=raw.get("title") or "",
                    date=raw.get("date"),
                    studio=(raw.get("studio") or {}).get("name"),
                    performers=[p["name"] for p in raw.get("performers", [])],
                    tags=[t["name"] for t in raw.get("tags", [])],
                    created_at=raw.get("created_at") or "",
                    needs_choice=needs_choice,
                    provisional_file_id=provisional.file_id if provisional else None,
                    files=files,
                    issues=issues,
                )
            )
        return scenes

    def _primary_duplicate_issues(self, primaries: dict[str, FileReview]) -> list[Issue]:
        by_name: dict[str, list[tuple[str, FileReview]]] = {}
        for scene_id, f in primaries.items():
            if f is None:
                continue
            by_name.setdefault(_normname(f.basename), []).append((scene_id, f))
        issues = []
        for name, entries in by_name.items():
            if len(entries) > 1:
                for scene_id, f in entries:
                    others = ", ".join(f"{s} ({o.basename})" for s, o in entries if s != scene_id)
                    issues.append(
                        Issue(
                            code="duplicate_name",
                            message=f"Primary basename '{f.basename}' collides with {others}; torrent layout requires unique names.",
                            scene_id=scene_id,
                            path=f.path,
                        )
                    )
        return issues

    def _copy_warnings(self, primaries: dict[str, FileReview], staging_dev) -> list[WarningItem]:
        warnings = []
        total_bytes = 0
        copied = 0
        for scene_id, primary in primaries.items():
            if primary is None:
                continue
            dev = None
            if primary.exists:
                dev = os.stat(primary.path).st_dev
            primary.will_copy = _needs_copy(dev, staging_dev)
            if primary.will_copy:
                copied += 1
                total_bytes += primary.size
        if copied:
            warnings.append(
                WarningItem(
                    code="cross_volume_copy",
                    message=(
                        f"{copied} file(s) ({total_bytes / 1e6:.1f} MB) will be copied to staging "
                        f"({self.settings.staging_dir}) because they are on a different volume than staging. "
                        "Configure staging_dir on the media volume to use hardlinks instead."
                    ),
                )
            )
        if self._policy_creation_fallback:
            warnings.append(
                WarningItem(
                    code="creation_time_fallback",
                    message="OS creation time is unavailable on this platform; file ordering uses mod_time as a visible fallback.",
                )
            )
        return warnings

    @property
    def _policy_creation_fallback(self) -> bool:
        return self.settings.file_time_policy == "creation" and sys.platform != "win32"

    def review(self, request: ReviewRequest) -> ReviewResponse:
        scene_ids = list(dict.fromkeys(request.scene_ids))
        policy = self._resolve_policy(request.file_time_policy, request.file_time_ascending)
        raw_scenes = self.stash.fetch_scenes(scene_ids)
        missing = [sid for sid in scene_ids if raw_scenes.get(sid) is None]
        errors = [
            Issue(code="unknown_scene", message=f"Scene {sid} was not found in Stash.", scene_id=sid)
            for sid in missing
        ]
        scenes = self._build_scenes(raw_scenes, policy.name)
        scenes.sort(
            key=lambda s: self._scene_sort_key(raw_scenes[s.scene_id], self._provisional_primary(s.files, policy.name), policy.name),
            reverse=not policy.ascending,
        )
        staging_dev = self._staging_volume()
        primaries = {
            s.scene_id: (
                next((f for f in s.files if f.file_id == s.provisional_file_id), None)
                if not s.needs_choice
                else self._provisional_primary(s.files, policy.name)
            )
            for s in scenes
        }
        dupes = self._primary_duplicate_issues(primaries)
        for scene in scenes:
            scene.issues.extend(i for i in dupes if i.scene_id == scene.scene_id)
        warnings = self._copy_warnings(primaries, staging_dev)
        return ReviewResponse(policy=policy, scenes=scenes, warnings=warnings, errors=errors)

    def _chosen_primaries(
        self, scenes: list[SceneReview], file_choices: dict[str, str]
    ) -> tuple[dict[str, FileReview], list[Issue]]:
        """Resolve the chosen primary file per scene. Never mutates Stash."""
        by_id = {s.scene_id: s for s in scenes}
        primaries: dict[str, FileReview] = {}
        errors: list[Issue] = []
        for scene_id in by_id:
            scene = by_id[scene_id]
            chosen_id = file_choices.get(scene_id)
            if chosen_id is None and not scene.needs_choice:
                chosen_id = scene.provisional_file_id
            chosen = next((f for f in scene.files if f.file_id == chosen_id), None)
            if scene.needs_choice and chosen is None:
                errors.append(
                    Issue(
                        code="needs_choice",
                        message=f"Scene {scene_id} has multiple files; select one primary file.",
                        scene_id=scene_id,
                    )
                )
                continue
            if chosen is None:
                errors.append(
                    Issue(code="no_files", message=f"Scene {scene_id} has no usable file.", scene_id=scene_id)
                )
                continue
            primaries[scene_id] = chosen
        return primaries, errors

    def apply(self, request: ApplyRequest) -> ApplyResponse:
        policy = self._resolve_policy(request.file_time_policy, request.file_time_ascending)
        raw_scenes = self.stash.fetch_scenes(request.scene_ids)
        errors: list[Issue] = []
        for sid in request.scene_ids:
            if raw_scenes.get(sid) is None:
                errors.append(Issue(code="unknown_scene", message=f"Scene {sid} was not found in Stash.", scene_id=sid))
        scenes = self._build_scenes(raw_scenes, policy.name)
        by_id = {s.scene_id: s for s in scenes}
        primaries, chosen_errors = self._chosen_primaries(scenes, request.file_choices)
        errors.extend(chosen_errors)
        request_modes = {m.scene_id: m.fetch_mode for m in (request.meta.scenes if request.meta else [])}
        for scene_id in request.scene_ids:
            chosen = primaries.get(scene_id)
            if chosen is None:
                continue
            if request.meta is None and request_modes.get(scene_id, "copy") == "copy":
                if not chosen.exists:
                    errors.append(Issue(code="file_missing", message=f"File not found on disk: {chosen.path}", scene_id=scene_id, path=chosen.path))
                elif not chosen.accessible:
                    errors.append(Issue(code="file_inaccessible", message=f"File is not readable: {chosen.path}", scene_id=scene_id, path=chosen.path))
        pack = [
            ResolvedScene(scene_id=scene_id, title=by_id[scene_id].title, primary_file=primaries[scene_id])
            for scene_id in request.scene_ids
            if scene_id in primaries
        ]
        dupes = self._primary_duplicate_issues({r.scene_id: r.primary_file for r in pack})
        errors.extend(dupes)
        meta, meta_errors = self._build_meta(scenes, {r.scene_id: r.primary_file for r in pack}, request.meta)
        errors.extend(meta_errors)
        if errors:
            return ApplyResponse(policy=policy, pack=[], meta=None, warnings=[], errors=errors)
        staging_dev = self._staging_volume()
        primaries = {r.scene_id: r.primary_file for r in pack}
        warnings = self._copy_warnings(primaries, staging_dev)
        self._apply_fetch_mode_warnings(meta, primaries, warnings)
        return ApplyResponse(policy=policy, pack=pack, meta=meta, warnings=warnings, errors=[])

    def meta(self, request: MetaRequest) -> MetaResponse:
        policy = self._resolve_policy(request.file_time_policy, request.file_time_ascending)
        raw_scenes = self.stash.fetch_scenes(request.scene_ids)
        errors: list[Issue] = []
        for sid in request.scene_ids:
            if raw_scenes.get(sid) is None:
                errors.append(Issue(code="unknown_scene", message=f"Scene {sid} was not found in Stash.", scene_id=sid))
        scenes = self._build_scenes(raw_scenes, policy.name)
        by_id = {s.scene_id: s for s in scenes}
        primaries: dict[str, FileReview] = {}
        for scene_id in request.scene_ids:
            scene = by_id.get(scene_id)
            if scene is None:
                continue
            chosen_id = request.file_choices.get(scene_id)
            if chosen_id is None and not scene.needs_choice:
                chosen_id = scene.provisional_file_id
            chosen = next((f for f in scene.files if f.file_id == chosen_id), None)
            if chosen is None:
                chosen = next((f for f in scene.files if f.exists and f.accessible), None)
            if chosen is not None:
                primaries[scene_id] = chosen
        meta, meta_errors = self._build_meta(scenes, primaries, None)
        errors.extend(meta_errors)
        if errors:
            return MetaResponse(meta=meta, warnings=[], errors=errors)
        warnings = self._copy_warnings(primaries, self._staging_volume())
        return MetaResponse(meta=meta, warnings=warnings, errors=[])

    def images(self, request: ImagesRequest) -> ImagesResponse:
        """Generate and upload one contact sheet per resolved primary file.

        Contact sheets are required: any generation or upload failure raises
        ContactSheetError and the caller fails pack generation. Completed
        scenes stay cached on the service (digest + URL + local sheet), so a
        rerun of a partially failed pack resumes without re-uploading.
        Local sheet paths are never exposed in the response; Stage 5 reads
        them from ``images_service.sheets``.
        """
        policy = self._resolve_policy(request.file_time_policy, request.file_time_ascending)
        raw_scenes = self.stash.fetch_scenes(request.scene_ids)
        errors: list[Issue] = []
        for sid in request.scene_ids:
            if raw_scenes.get(sid) is None:
                errors.append(Issue(code="unknown_scene", message=f"Scene {sid} was not found in Stash.", scene_id=sid))
        scenes = self._build_scenes(raw_scenes, policy.name)
        primaries, chosen_errors = self._chosen_primaries(scenes, request.file_choices)
        errors.extend(chosen_errors)
        for scene_id in list(primaries):
            chosen = primaries[scene_id]
            if not chosen.exists:
                errors.append(Issue(code="file_missing", message=f"File not found on disk: {chosen.path}", scene_id=scene_id, path=chosen.path))
                del primaries[scene_id]
            elif not chosen.accessible:
                errors.append(Issue(code="file_inaccessible", message=f"File is not readable: {chosen.path}", scene_id=scene_id, path=chosen.path))
                del primaries[scene_id]
        if errors:
            return ImagesResponse(images=[], warnings=[], errors=errors)
        images: list[SceneImage] = []
        for scene_id in request.scene_ids:
            chosen = primaries.get(scene_id)
            if chosen is None:
                continue
            url, digest, _path = self.images_service.contact_sheet(scene_id, chosen.path, request.layout)
            images.append(SceneImage(scene_id=scene_id, url=url, digest=digest))
        return ImagesResponse(images=images, warnings=[], errors=[])

    def _build_meta(self, scenes: list[SceneReview], primaries: dict[str, FileReview], user_meta) -> tuple[PackMeta, list[Issue]]:
        errors: list[Issue] = []
        resolved = {s.scene_id: s for s in scenes}
        order = [s.scene_id for s in scenes]
        default_scenes: list[SceneMeta] = []
        for scene_id in order:
            scene = resolved.get(scene_id)
            if scene is None:
                continue
            primary = primaries.get(scene_id)
            default_scenes.append(
                SceneMeta(
                    scene_id=scene_id,
                    title=scene_title_default(scene, primary),
                    date=scene.date,
                    studio=scene.studio,
                    performers=scene.performers,
                    resolution=resolution_for(primary.height) if primary else "",
                    duration=format_duration(primary.duration) if primary else "",
                    codec=(primary.video_codec or "") if primary else "",
                    size=primary.size if primary else 0,
                    basename=primary.basename if primary else "",
                    fetch_mode="copy",
                    will_copy=primary.will_copy if primary else False,
                )
            )
        default_title = pack_title_default(scenes)
        default_tags = merge_tags(scenes, primaries)
        if user_meta is None:
            title = default_title
            tags = default_tags
            notes = ""
            titles_by_scene: dict[str, str] = {s.scene_id: s.title for s in default_scenes}
            modes_by_scene: dict[str, str] = {s.scene_id: "copy" for s in default_scenes}
        else:
            normalized = normalize_meta_input(user_meta.title, user_meta.tags, user_meta.notes, default_title, default_tags)
            title, tags, notes = normalized["title"], normalized["tags"], normalized["notes"]
            titles_by_scene = {}
            modes_by_scene = {}
            for item in user_meta.scenes:
                if item.scene_id not in resolved:
                    continue
                titles_by_scene[item.scene_id] = bbcode_escape(item.title) or scene_title_default(resolved[item.scene_id], primaries.get(item.scene_id))
                if item.fetch_mode not in ("copy", "download"):
                    errors.append(
                        Issue(code="bad_fetch_mode", message=f"fetch_mode must be 'copy' or 'download' (got '{item.fetch_mode}').", scene_id=item.scene_id)
                    )
                else:
                    modes_by_scene[item.scene_id] = item.fetch_mode
            for scene_id in order:
                if scene_id not in titles_by_scene:
                    titles_by_scene[scene_id] = scene_title_default(resolved[scene_id], primaries.get(scene_id))
                if scene_id not in modes_by_scene:
                    modes_by_scene[scene_id] = "copy"
        scenes_meta: list[SceneMeta] = []
        for scene_meta in default_scenes:
            primary = primaries.get(scene_meta.scene_id)
            scene_meta.title = titles_by_scene.get(scene_meta.scene_id, scene_meta.title)
            scene_meta.fetch_mode = modes_by_scene.get(scene_meta.scene_id, "copy")
            if scene_meta.fetch_mode == "copy" and primary is not None and not primary.exists:
                errors.append(
                    Issue(
                        code="copy_needs_local_file",
                        message=f"fetch_mode 'copy' requires the file to exist on disk; choose 'download' for missing files.",
                        scene_id=scene_meta.scene_id,
                        path=primary.path,
                    )
                )
            elif scene_meta.fetch_mode == "copy" and primary is not None and not primary.accessible:
                errors.append(
                    Issue(
                        code="copy_needs_local_file",
                        message=f"fetch_mode 'copy' requires a readable local file; choose 'download' for inaccessible files.",
                        scene_id=scene_meta.scene_id,
                        path=primary.path,
                    )
                )
            scenes_meta.append(scene_meta)
        for scene_meta in scenes_meta:
            if scene_meta.fetch_mode != "download":
                continue
            scene = resolved.get(scene_meta.scene_id)
            if scene is not None and len(scene.files) > 1:
                errors.append(
                    Issue(
                        code="download_multi_file",
                        message=(
                            "download mode is not supported for scenes with multiple files: "
                            "the /scene/{id}/stream endpoint may serve a different file than "
                            "the selected one. Choose 'copy' or make the file locally available."
                        ),
                        scene_id=scene_meta.scene_id,
                    )
                )
        image_urls = [f"{{scene-image-{i + 1}}}" for i in range(len(scenes_meta))]
        description = render_description(
            title,
            tags,
            notes,
            [{"image_url": image_urls[i]} for i in range(len(scenes_meta))],
        )
        return PackMeta(title=title, tags=tags, notes=notes, description=description, scenes=scenes_meta), errors

    def _apply_fetch_mode_warnings(self, meta: PackMeta, primaries: dict[str, FileReview], warnings: list[WarningItem]):
        downloads = [s for s in meta.scenes if s.fetch_mode == "download"]
        if downloads:
            total = sum(s.size for s in downloads)
            warnings.append(
                WarningItem(
                    code="http_download",
                    message=f"{len(downloads)} scene(s) ({total / (1024 ** 2):.1f} MB) are set to be downloaded over HTTP from Stash at apply time.",
                )
            )

    def _http_download(self, scene_id: str, dest: Path, expected: int) -> None:
        """Fetch a missing scene file from Stash's stream endpoint."""
        url = f"{self.settings.stash_url}/scene/{scene_id}/stream"
        headers = {"X-API-Key": self.settings.stash_api_key} if self.settings.stash_api_key else {}
        with self.http.stream("GET", url, headers=headers) as response:
            response.raise_for_status()
            written = 0
            with open(dest, "wb") as fh:
                for chunk in response.iter_bytes():
                    fh.write(chunk)
                    written += len(chunk)
        if expected is not None and written != expected:
            dest.unlink(missing_ok=True)
            raise BuildError(f"Stream size mismatch for scene {scene_id}: got {written}, expected {expected} bytes.")

    def build(self, request: BuildRequest) -> BuildResponse:
        """Build the complete pack: images, BBCode, staged payload, torrent.

        Every ``{scene-image-N}`` placeholder in the description is replaced
        with its uploaded URL (reusing the images service cache) or
        generation fails — the no-placeholder guarantee. The announce URL
        exists only inside the private torrent: never in responses, logs,
        manifests, or the description.
        """
        announce = (self.settings.empornium_announce_url or "").strip()
        if not announce:
            raise BuildError(
                "No Empornium announce URL configured; set DEEPSEEK_EMPORNIUM_ANNOUNCE_URL."
            )
        validate_announce_url(announce)
        policy = self._resolve_policy(request.file_time_policy, request.file_time_ascending)
        raw_scenes = self.stash.fetch_scenes(request.scene_ids)
        errors: list[Issue] = []
        for sid in request.scene_ids:
            if raw_scenes.get(sid) is None:
                errors.append(Issue(code="unknown_scene", message=f"Scene {sid} was not found in Stash.", scene_id=sid))
        scenes = self._build_scenes(raw_scenes, policy.name)
        primaries, chosen_errors = self._chosen_primaries(scenes, request.file_choices)
        errors.extend(chosen_errors)
        meta, meta_errors = self._build_meta(scenes, primaries, request.meta)
        errors.extend(meta_errors)
        if errors:
            return BuildResponse(
                pack_id="", title="", description="", torrent_file="", manifest_file="",
                total_bytes=0, piece_size=0, piece_count=0, infohash="", scenes=[],
                errors=errors, warnings=[],
            )
        urls: dict[str, str] = {}
        for scene_meta in meta.scenes:
            primary = primaries.get(scene_meta.scene_id)
            if primary is None:
                raise BuildError(f"Scene {scene_meta.scene_id} has no primary file at build time.")
            urls[scene_meta.scene_id] = self.images_service.url_for(
                scene_meta.scene_id, primary.path, request.layout
            )
        description = finalize_description(
            meta.description, [urls[s.scene_id] for s in meta.scenes]
        )
        pack_id = new_pack_id()
        pack_root = self.settings.staging_dir / PACK_ROOT_NAME / pack_id
        torrent_path = self.settings.output_dir / f"{pack_id}.torrent"
        manifest_path = self.settings.output_dir / f"{pack_id}.manifest.json"
        description_path = self.settings.output_dir / f"{pack_id}.description.txt"
        bundle_path = self.settings.output_dir / f"{pack_id}.bundle.zip"
        try:
            return self._finish_build(
                request, pack_id, pack_root, torrent_path, manifest_path, description_path, bundle_path,
                urls, meta, primaries, announce, description,
            )
        except Exception:
            shutil.rmtree(pack_root, ignore_errors=True)
            for artifact in (torrent_path, manifest_path, description_path, bundle_path):
                artifact.unlink(missing_ok=True)
            raise

    def _finish_build(
        self, request, pack_id, pack_root, torrent_path, manifest_path, description_path, bundle_path,
        urls, meta, primaries, announce, description,
    ) -> BuildResponse:
        scenes_for_stage = [
            {
                "scene_id": s.scene_id,
                "title": s.title,
                "source_path": primaries[s.scene_id].path,
                "sheet_path": str(self.images_service.sheets[s.scene_id]),
                "fetch_mode": s.fetch_mode,
                "expected_size": primaries[s.scene_id].size,
            }
            for s in meta.scenes
        ]
        staged = stage_payload(pack_root, meta.title, scenes_for_stage, self.settings, http_download=self._http_download)
        total_bytes = payload_size(pack_root)
        piece_size = piece_size_for(total_bytes)
        payload_dir = pack_root / sanitize_name(meta.title)
        torrent_meta = create_torrent(
            payload_dir,
            announce,
            torrent_path,
            source=self.settings.torrent_source or source_for_announce(announce),
            piece_size=piece_size,
            expected_bytes=total_bytes,
        )
        manifest = {
            "pack_id": pack_id,
            "title": meta.title,
            "created_utc": build_created_utc(),
            "total_bytes": total_bytes,
            "piece_size": piece_size,
            "piece_count": torrent_meta["piece_count"],
            "infohash": torrent_meta["infohash"],
            "torrent_file": torrent_path.name,
            "scenes": [
                {
                    "scene_id": st.scene_id,
                    "title": st.title,
                    "video_name": st.video_name,
                    "sheet_name": st.sheet_name,
                    "linked": st.linked,
                    "sheet_url": urls[st.scene_id],
                    "sheet_digest": self.images_service.digest_for(st.scene_id),
                }
                for st in staged
            ],
        }
        write_manifest(manifest_path, manifest)
        description_path.write_text(description, encoding="utf-8")
        bundle_file = ""
        bundle = request.bundle if request.bundle is not None else self.settings.bundle_after_build
        if bundle:
            make_bundle(
                pack_root,
                bundle_path,
                extras=[
                    (torrent_path, torrent_path.name),
                    (manifest_path, manifest_path.name),
                    (description_path, description_path.name),
                ],
            )
            bundle_file = bundle_path.name
        warnings: list[WarningItem] = []
        copied = [st for st in staged if not st.linked]
        if copied:
            warnings.append(
                WarningItem(
                    code="payload_copy",
                    message=f"{len(copied)} scene(s) copied instead of hardlinked (payload {total_bytes / 1e6:.1f} MB).",
                )
            )
        self._apply_fetch_mode_warnings(meta, primaries, warnings)
        self.packs[pack_id] = {
            "title": meta.title,
            "torrent": torrent_path.name,
            "manifest": manifest_path.name,
            "description": description_path.name,
            "bundle": bundle_file,
        }
        self._persist_index()
        return BuildResponse(
            pack_id=pack_id,
            title=meta.title,
            description=description,
            torrent_file=torrent_path.name,
            manifest_file=manifest_path.name,
            bundle_file=bundle_file,
            total_bytes=total_bytes,
            piece_size=piece_size,
            piece_count=torrent_meta["piece_count"],
            infohash=torrent_meta["infohash"],
            scenes=[
                BuiltScene(
                    scene_id=st.scene_id,
                    title=st.title,
                    video_name=st.video_name,
                    sheet_name=st.sheet_name,
                    linked=st.linked,
                )
                for st in staged
            ],
            warnings=warnings,
            errors=[],
        )

    def _validate_pack_id(self, pack_id: str) -> Path:
        """Return the contained staging root for a well-formed pack id."""
        if not re.fullmatch(r"[0-9a-f]{10}", pack_id or ""):
            raise ValueError(f"invalid pack id: {pack_id!r}")
        base = (self.settings.staging_dir / PACK_ROOT_NAME).resolve()
        root = self.settings.staging_dir / PACK_ROOT_NAME / pack_id
        try:
            resolved = root.resolve()
        except OSError:
            resolved = root.absolute()
        if resolved != base and not str(resolved).startswith(str(base) + os.sep):
            raise ValueError(f"pack id escapes the staging directory: {pack_id!r}")
        return root

    def cleanup(self, pack_id: str) -> CleanupResponse:
        """Remove the staged payload after seeding; output artifacts remain."""
        pack_root = self._validate_pack_id(pack_id)
        removed = False
        if pack_root.exists():
            shutil.rmtree(pack_root)
            removed = True
        return CleanupResponse(pack_id=pack_id, staging_removed=removed)

    def pack_artifact(self, pack_id: str, kind: str) -> Path:
        """Resolve an output artifact for a completed build (registry-gated)."""
        root = self._validate_pack_id(pack_id)
        entry = self.packs.get(pack_id)
        if entry is None:
            raise FileNotFoundError(pack_id)
        name = entry.get(kind)
        if not name:
            raise FileNotFoundError(kind)
        path = self.settings.output_dir / name
        if not path.is_file():
            raise FileNotFoundError(name)
        return path

    def move_files(self, request: MoveFilesRequest) -> MoveFilesResponse:
        """Consolidate scene files into a single destination folder using Stash moveFiles mutation."""
        dest_str = request.destination_folder.strip()
        if not dest_str:
            return MoveFilesResponse(
                destination_folder="",
                total=len(request.scene_ids),
                moved_count=0,
                already_in_place_count=0,
                error_count=len(request.scene_ids),
                items=[],
                errors=[Issue(code="missing_destination", message="Destination folder must not be empty.")],
            )

        dest_norm = os.path.normcase(os.path.normpath(dest_str))

        raw_scenes = self.stash.fetch_scenes(request.scene_ids)
        errors: list[Issue] = []
        warnings: list[WarningItem] = []
        items: list[MovedFileItem] = []

        files_to_move: list[str] = []

        for sid in request.scene_ids:
            raw = raw_scenes.get(sid)
            if raw is None:
                errors.append(Issue(code="unknown_scene", message=f"Scene {sid} was not found in Stash.", scene_id=sid))
                items.append(
                    MovedFileItem(
                        scene_id=sid,
                        file_id="",
                        title="",
                        status="error",
                        error=f"Scene {sid} not found in Stash.",
                    )
                )
                continue

            raw_files = raw.get("files") or []
            if not raw_files:
                errors.append(Issue(code="no_files", message=f"Scene {sid} has no files attached.", scene_id=sid))
                items.append(
                    MovedFileItem(
                        scene_id=sid,
                        file_id="",
                        title=raw.get("title") or f"Scene {sid}",
                        status="error",
                        error=f"Scene {sid} has no files.",
                    )
                )
                continue

            chosen_file_id = request.file_choices.get(sid)
            chosen_file = next((f for f in raw_files if f.get("id") == chosen_file_id), None)
            if chosen_file is None:
                chosen_file = raw_files[0]

            file_id = chosen_file.get("id")
            source_path = chosen_file.get("path") or ""
            basename = chosen_file.get("basename") or os.path.basename(source_path)
            title = raw.get("title") or basename

            if not source_path:
                items.append(
                    MovedFileItem(
                        scene_id=sid,
                        file_id=str(file_id),
                        title=title,
                        basename=basename,
                        source_path="",
                        status="error",
                        error="File path is empty.",
                    )
                )
                continue

            source_dir = os.path.normcase(os.path.normpath(os.path.dirname(source_path)))
            dest_file_path = os.path.join(dest_str, basename)

            if source_dir == dest_norm:
                items.append(
                    MovedFileItem(
                        scene_id=sid,
                        file_id=str(file_id),
                        title=title,
                        basename=basename,
                        source_path=source_path,
                        destination_path=dest_file_path,
                        status="already_in_place",
                    )
                )
            else:
                files_to_move.append(str(file_id))
                items.append(
                    MovedFileItem(
                        scene_id=sid,
                        file_id=str(file_id),
                        title=title,
                        basename=basename,
                        source_path=source_path,
                        destination_path=dest_file_path,
                        status="pending_move",
                    )
                )

        if files_to_move:
            try:
                success = self.stash.move_files(file_ids=files_to_move, destination_folder=dest_str)
                if success:
                    for item in items:
                        if item.status == "pending_move":
                            item.status = "moved"
                else:
                    for item in items:
                        if item.status == "pending_move":
                            item.status = "error"
                            item.error = "Stash moveFiles returned false."
                    errors.append(Issue(code="stash_move_failed", message="Stash was unable to move files."))
            except Exception as exc:
                for item in items:
                    if item.status == "pending_move":
                        item.status = "error"
                        item.error = str(exc)
                errors.append(Issue(code="stash_move_error", message=f"Error executing Stash move: {exc}"))

        moved_count = sum(1 for i in items if i.status == "moved")
        already_in_place_count = sum(1 for i in items if i.status == "already_in_place")
        error_count = sum(1 for i in items if i.status == "error")

        return MoveFilesResponse(
            destination_folder=dest_str,
            total=len(items),
            moved_count=moved_count,
            already_in_place_count=already_in_place_count,
            error_count=error_count,
            items=items,
            warnings=warnings,
            errors=errors,
        )

