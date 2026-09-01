from typing import Any
from pydantic import BaseModel, Field, field_validator


class Issue(BaseModel):
    code: str
    message: str
    scene_id: str | None = None
    path: str | None = None


class WarningItem(BaseModel):
    code: str
    message: str


class PolicyInfo(BaseModel):
    name: str
    ascending: bool
    note: str


class FileReview(BaseModel):
    file_id: str
    basename: str
    path: str
    size: int
    width: int | None = None
    height: int | None = None
    duration: float | None = None
    video_codec: str | None = None
    mod_time: str
    created_at: str
    creation_time: str | None = None
    time_source: str
    exists: bool
    accessible: bool
    will_copy: bool


class SceneReview(BaseModel):
    scene_id: str
    title: str = ""
    date: str | None = None
    studio: str | None = None
    performers: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    created_at: str = ""
    needs_choice: bool = False
    provisional_file_id: str | None = None
    files: list[FileReview] = Field(default_factory=list)
    issues: list[Issue] = Field(default_factory=list)


class ReviewRequest(BaseModel):
    scene_ids: list[str] = Field(min_length=1)
    file_time_policy: str | None = None
    file_time_ascending: bool | None = None


class ReviewResponse(BaseModel):
    policy: PolicyInfo
    scenes: list[SceneReview]
    warnings: list[WarningItem]
    errors: list[Issue]


class MetaRequest(BaseModel):
    scene_ids: list[str] = Field(min_length=1)
    file_choices: dict[str, str] = Field(default_factory=dict)
    file_time_policy: str | None = None
    file_time_ascending: bool | None = None


class SceneMeta(BaseModel):
    scene_id: str
    title: str
    date: str | None = None
    studio: str | None = None
    performers: list[str] = Field(default_factory=list)
    resolution: str = ""
    duration: str = ""
    codec: str = ""
    size: int = 0
    basename: str = ""
    fetch_mode: str = "copy"
    will_copy: bool = False


class PackMeta(BaseModel):
    title: str
    tags: list[str] = Field(default_factory=list)
    notes: str = ""
    description: str = ""
    scenes: list[SceneMeta] = Field(default_factory=list)


class MetaResponse(BaseModel):
    meta: PackMeta
    warnings: list[WarningItem]
    errors: list[Issue]


class SceneMetaInput(BaseModel):
    scene_id: str
    title: str = ""
    fetch_mode: str = "copy"


class PackMetaInput(BaseModel):
    title: str = ""
    tags: list[str] | None = None
    notes: str = ""
    scenes: list[SceneMetaInput] = Field(default_factory=list)


class ApplyRequest(BaseModel):
    scene_ids: list[str] = Field(min_length=1)
    file_choices: dict[str, str] = Field(default_factory=dict)
    file_time_policy: str | None = None
    file_time_ascending: bool | None = None
    meta: PackMetaInput | None = None


class ResolvedScene(BaseModel):
    scene_id: str
    title: str = ""
    primary_file: FileReview


class ApplyResponse(BaseModel):
    policy: PolicyInfo
    pack: list[ResolvedScene]
    meta: PackMeta | None = None
    warnings: list[WarningItem]
    errors: list[Issue]


class ImagesRequest(BaseModel):
    scene_ids: list[str] = Field(min_length=1)
    file_choices: dict[str, str] = Field(default_factory=dict)
    file_time_policy: str | None = None
    file_time_ascending: bool | None = None
    layout: str | None = None


class SceneImage(BaseModel):
    scene_id: str
    url: str
    digest: str


class ImagesResponse(BaseModel):
    images: list[SceneImage]
    warnings: list[WarningItem]
    errors: list[Issue]


class BuildRequest(BaseModel):
    scene_ids: list[str] = Field(min_length=1)
    file_choices: dict[str, str] = Field(default_factory=dict)
    file_time_policy: str | None = None
    file_time_ascending: bool | None = None
    meta: PackMetaInput | None = None
    layout: str | None = None
    bundle: bool | None = None


class BuiltScene(BaseModel):
    scene_id: str
    title: str
    video_name: str
    sheet_name: str
    linked: bool


class BuildResponse(BaseModel):
    pack_id: str
    title: str
    description: str
    torrent_file: str
    manifest_file: str
    bundle_file: str = ""
    total_bytes: int
    piece_size: int
    piece_count: int
    infohash: str
    scenes: list[BuiltScene]
    warnings: list[WarningItem]
    errors: list[Issue]


class CleanupResponse(BaseModel):
    pack_id: str
    staging_removed: bool


class MoveFilesRequest(BaseModel):
    scene_ids: list[str] = Field(min_length=1)
    destination_folder: str
    file_choices: dict[str, str] = Field(default_factory=dict)


class MovedFileItem(BaseModel):
    scene_id: str
    file_id: str
    title: str = ""
    basename: str = ""
    source_path: str = ""
    destination_path: str = ""
    status: str  # 'moved' | 'already_in_place' | 'error'
    error: str | None = None


class MoveFilesResponse(BaseModel):
    destination_folder: str
    total: int
    moved_count: int
    already_in_place_count: int
    error_count: int
    items: list[MovedFileItem]
    warnings: list[WarningItem] = Field(default_factory=list)
    errors: list[Issue] = Field(default_factory=list)


class TokenCreateRequest(BaseModel):
    sceneIds: list[int] = Field(min_length=1, max_length=200)

    @field_validator("sceneIds", mode="before")
    @classmethod
    def validate_scene_ids(cls, v: Any) -> Any:
        if not isinstance(v, (list, tuple)):
            raise ValueError("sceneIds must be a list")
        for item in v:
            if isinstance(item, bool):
                raise ValueError("bool not allowed")
            if not isinstance(item, int) or item <= 0:
                raise ValueError("scene ID must be positive integer")
        return v



class TokenCreateResponse(BaseModel):
    token: str


class TokenGetResponse(BaseModel):
    sceneIds: list[int]


