import sys

import httpx
import pytest

from empornium_megapack.config import Settings
from empornium_megapack.images import (
    HAMSTER_UPLOAD_URL,
    ContactSheetError,
    ImageService,
    _retry_after_seconds,
    _retry_delay,
    _vcsi_command,
    enforce_size_limit,
    generate_contact_sheet,
    resolve_ffmpeg,
    resolve_ffprobe,
    sha256_file,
    upload_hamster,
)
from empornium_megapack.models import ImagesRequest
from empornium_megapack.review import PackService
from test_review import FakeStash, make_file, make_scene

VCSTUB = r"""
import sys
import shutil
if "--fail" in sys.argv:
    sys.exit(7)
out = None
payload = None
for i, arg in enumerate(sys.argv):
    if arg == "-o":
        out = sys.argv[i + 1]
    if arg == "--payload":
        payload = sys.argv[i + 1]
if out is None:
    sys.exit(3)
if "--noout" in sys.argv:
    sys.exit(0)
if payload:
    shutil.copyfile(payload, out)
else:
    with open(out, "wb") as fh:
        fh.write(b"\xff\xd8" + "|".join(sys.argv[1:]).encode())
sys.exit(0)
"""


@pytest.fixture
def vcstub(tmp_path):
    stub = tmp_path / "vcstub.py"
    stub.write_text(VCSTUB, encoding="utf-8")
    return stub


@pytest.fixture
def stub_vcsi(monkeypatch, vcstub):
    def set_vcsi(extra=()):
        monkeypatch.setattr(
            "empornium_megapack.images._vcsi_command",
            lambda settings: [sys.executable, str(vcstub)] + list(extra),
        )

    set_vcsi()
    return set_vcsi


class FakeResponse:
    def __init__(self, status_code=200, payload=None, json_error=False, headers=None):
        self.status_code = status_code
        self._payload = payload if payload is not None else {"image": {"url": "https://img.example/x.jpg"}}
        self._json_error = json_error
        self.headers = headers or {}

    def json(self):
        if self._json_error:
            raise ValueError("not json")
        return self._payload


class FakeClient:
    def __init__(self, responses=()):
        self.calls = []
        self._responses = list(responses)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def post(self, url, **kwargs):
        self.calls.append((url, kwargs))
        if self._responses:
            status, payload, json_error, *rest = self._responses.pop(0)
            return FakeResponse(status, payload, json_error, rest[0] if rest else None)
        return FakeResponse(200)


@pytest.fixture
def no_backoff(monkeypatch):
    monkeypatch.setattr("empornium_megapack.images.time.sleep", lambda seconds: None)
    monkeypatch.setattr("empornium_megapack.images.random.uniform", lambda a, b: 0.0)


@pytest.fixture
def record_sleep(monkeypatch):
    calls = []
    monkeypatch.setattr("empornium_megapack.images.time.sleep", lambda seconds: calls.append(seconds))
    monkeypatch.setattr("empornium_megapack.images.random.uniform", lambda a, b: 0.0)
    return calls


@pytest.fixture
def fake_upload(monkeypatch):
    def install(responses=()):
        client = FakeClient(responses)
        monkeypatch.setattr("empornium_megapack.images.httpx.Client", lambda timeout: client)
        return client

    return install


def settings_with(tmp_path, **kwargs):
    base = dict(
        stash_url="http://unused",
        staging_dir=tmp_path / "staging",
        hamster_api_key="test-key",
        contact_sheet_upload_retries=3,
    )
    base.update(kwargs)
    return Settings(**base)


def test_sha256_deterministic(tmp_path):
    p = tmp_path / "a.bin"
    p.write_bytes(b"payload")
    assert sha256_file(p) == sha256_file(p)
    assert len(sha256_file(p)) == 64


def test_resolve_ffmpeg_explicit(tmp_path):
    assert resolve_ffmpeg(settings_with(tmp_path, ffmpeg_binary="C:\\tools\\ffmpeg.exe")) == "C:\\tools\\ffmpeg.exe"


def test_resolve_ffmpeg_which(monkeypatch, tmp_path):
    monkeypatch.setattr("empornium_megapack.images.shutil.which", lambda name: "C:\\ffmpeg.exe")
    assert resolve_ffmpeg(settings_with(tmp_path)) == "C:\\ffmpeg.exe"


def test_resolve_ffmpeg_cove_fallback(monkeypatch, tmp_path):
    cove = tmp_path / "cove" / "ffmpeg" / "ffmpeg.exe"
    cove.parent.mkdir(parents=True)
    cove.touch()
    monkeypatch.setattr("empornium_megapack.images._COVE_FFMPEG", cove)
    monkeypatch.setattr("empornium_megapack.images.shutil.which", lambda name: None)
    assert resolve_ffmpeg(settings_with(tmp_path)) == str(cove)


def test_resolve_ffmpeg_none(monkeypatch, tmp_path):
    monkeypatch.setattr("empornium_megapack.images.shutil.which", lambda name: None)
    monkeypatch.setattr("empornium_megapack.images._COVE_FFMPEG", tmp_path / "missing" / "ffmpeg.exe")
    monkeypatch.setattr("empornium_megapack.images.Path.home", classmethod(lambda cls: tmp_path))
    assert resolve_ffmpeg(settings_with(tmp_path)) is None


def test_generate_contact_sheet_success(tmp_path, vcstub, stub_vcsi):
    settings = settings_with(tmp_path)
    out = tmp_path / "cs.jpg"
    assert generate_contact_sheet(tmp_path / "video.mp4", out, settings)
    assert out.read_bytes()[:2] == b"\xff\xd8"


def test_generate_contact_sheet_passes_layout(tmp_path, vcstub, stub_vcsi):
    out = tmp_path / "o.jpg"
    assert generate_contact_sheet(tmp_path / "v.mp4", out, settings_with(tmp_path), layout="2x2")
    assert b"-g|2x2" in out.read_bytes()


def test_generate_contact_sheet_failure_exit(tmp_path, vcstub):
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(
        "empornium_megapack.images._vcsi_command",
        lambda settings: [sys.executable, str(vcstub), "--fail"],
    )
    try:
        assert not generate_contact_sheet(tmp_path / "v.mp4", tmp_path / "o.jpg", settings_with(tmp_path))
    finally:
        monkeypatch.undo()


def test_generate_contact_sheet_missing_output(tmp_path, vcstub):
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(
        "empornium_megapack.images._vcsi_command",
        lambda settings: [sys.executable, str(vcstub), "--noout"],
    )
    try:
        assert not generate_contact_sheet(tmp_path / "v.mp4", tmp_path / "o.jpg", settings_with(tmp_path))
    finally:
        monkeypatch.undo()


def test_generate_contact_sheet_vcsi_unavailable(monkeypatch, tmp_path):
    monkeypatch.setattr("empornium_megapack.images.shutil.which", lambda name: None)
    monkeypatch.setattr("empornium_megapack.images.sys.executable", str(tmp_path / "nopython.exe"))
    with pytest.raises(ContactSheetError):
        generate_contact_sheet(tmp_path / "v.mp4", tmp_path / "o.jpg", settings_with(tmp_path, vcsi_binary=""))


def test_vcsi_found_next_to_python(monkeypatch, tmp_path):
    venv = tmp_path / "venv" / "Scripts"
    venv.mkdir(parents=True)
    (venv / "vcsi.exe").write_bytes(b"")
    monkeypatch.setattr("empornium_megapack.images.shutil.which", lambda name: None)
    monkeypatch.setattr("empornium_megapack.images.sys.executable", str(venv / "python.exe"))
    settings = settings_with(tmp_path, vcsi_binary="")
    assert _vcsi_command(settings) == [str(venv / "vcsi.exe")]


def test_upload_success_shape(tmp_path, fake_upload):
    client = fake_upload()
    img = tmp_path / "cs.jpg"
    img.write_bytes(b"\xff\xd8data")
    url = upload_hamster(img, settings_with(tmp_path))
    assert url == "https://img.example/x.jpg"
    (call_url, kwargs) = client.calls[0]
    assert call_url == HAMSTER_UPLOAD_URL
    assert kwargs["headers"]["X-API-Key"] == "test-key"
    assert kwargs["data"]["type"] == "file"
    assert kwargs["data"]["action"] == "upload"
    assert kwargs["data"]["nsfw"] == "1"
    assert kwargs["data"]["format"] == "json"
    assert "source" in kwargs["files"]


def test_upload_missing_key_raises(tmp_path, fake_upload):
    client = fake_upload()
    img = tmp_path / "cs.jpg"
    img.write_bytes(b"x")
    with pytest.raises(ContactSheetError):
        upload_hamster(img, settings_with(tmp_path, hamster_api_key="  "))
    assert client.calls == []


def test_upload_api_error_raises_no_retry(tmp_path, fake_upload):
    client = fake_upload([(200, {"error": {"message": "banned"}}, False)])
    img = tmp_path / "cs.jpg"
    img.write_bytes(b"x")
    with pytest.raises(ContactSheetError, match="banned"):
        upload_hamster(img, settings_with(tmp_path))
    assert len(client.calls) == 1


def test_upload_retries_transient_then_success(tmp_path, fake_upload, no_backoff):
    client = fake_upload(
        [
            (500, None, False),
            (502, None, False),
            (200, {"image": {"url": "https://img.example/final.jpg"}}, False),
        ]
    )
    img = tmp_path / "cs.jpg"
    img.write_bytes(b"x")
    assert upload_hamster(img, settings_with(tmp_path)) == "https://img.example/final.jpg"
    assert len(client.calls) == 3


def test_upload_retries_invalid_json(tmp_path, fake_upload, no_backoff):
    client = fake_upload([(200, None, True), (200, {"image": {"url": "https://img.example/y.jpg"}}, False)])
    img = tmp_path / "cs.jpg"
    img.write_bytes(b"x")
    assert upload_hamster(img, settings_with(tmp_path)) == "https://img.example/y.jpg"
    assert len(client.calls) == 2


def test_upload_fails_after_retries(tmp_path, fake_upload, no_backoff):
    client = fake_upload([(500, None, False), (500, None, False), (500, None, False)])
    img = tmp_path / "cs.jpg"
    img.write_bytes(b"x")
    with pytest.raises(ContactSheetError, match="after retries"):
        upload_hamster(img, settings_with(tmp_path, contact_sheet_upload_retries=3))
    assert len(client.calls) == 3


def test_upload_network_error_retried(tmp_path, no_backoff):
    class BoomClient(FakeClient):
        def post(self, url, **kwargs):
            self.calls.append((url, kwargs))
            if len(self.calls) < 2:
                raise httpx.ConnectError("connection reset")
            return FakeResponse(200, {"image": {"url": "https://img.example/z.jpg"}})

    monkeypatch = pytest.MonkeyPatch()
    client = BoomClient()
    monkeypatch.setattr("empornium_megapack.images.httpx.Client", lambda timeout: client)
    try:
        img = tmp_path / "cs.jpg"
        img.write_bytes(b"x")
        assert upload_hamster(img, settings_with(tmp_path)) == "https://img.example/z.jpg"
    finally:
        monkeypatch.undo()


def test_retry_delay_exponential(monkeypatch):
    monkeypatch.setattr("empornium_megapack.images.random.uniform", lambda a, b: 0.0)
    settings = Settings(contact_sheet_upload_backoff_base=0.5, contact_sheet_upload_backoff_max=15.0)
    assert _retry_delay(0, settings) == 0.5
    assert _retry_delay(1, settings) == 1.0
    assert _retry_delay(2, settings) == 2.0
    assert _retry_delay(3, settings) == 4.0


def test_retry_delay_capped(monkeypatch):
    monkeypatch.setattr("empornium_megapack.images.random.uniform", lambda a, b: 0.0)
    settings = Settings(contact_sheet_upload_backoff_base=10.0, contact_sheet_upload_backoff_max=15.0)
    assert _retry_delay(1, settings) == 15.0


def test_retry_delay_capped_after_jitter(monkeypatch):
    monkeypatch.setattr("empornium_megapack.images.random.uniform", lambda a, b: 0.25)
    settings = Settings(contact_sheet_upload_backoff_base=10.0, contact_sheet_upload_backoff_max=15.0)
    assert _retry_delay(0, settings) == 12.5
    assert _retry_delay(1, settings) == 15.0


def test_retry_delay_never_exceeds_max(monkeypatch):
    calls = []
    monkeypatch.setattr("empornium_megapack.images.random.uniform", lambda a, b: calls.append((a, b)) or 0.25)
    settings = Settings(contact_sheet_upload_backoff_base=8.0, contact_sheet_upload_backoff_max=10.0)
    for attempt in range(8):
        assert _retry_delay(attempt, settings) <= 10.0


def test_retry_backoff_sleeps_between_attempts(tmp_path, fake_upload, record_sleep):
    client = fake_upload(
        [
            (500, None, False),
            (500, None, False),
            (200, {"image": {"url": "https://img.example/final.jpg"}}, False),
        ]
    )
    img = tmp_path / "cs.jpg"
    img.write_bytes(b"x")
    settings = settings_with(tmp_path, contact_sheet_upload_backoff_base=0.5)
    assert upload_hamster(img, settings) == "https://img.example/final.jpg"
    assert record_sleep == [0.5, 1.0]


def test_retry_no_sleep_after_final_attempt(tmp_path, fake_upload, record_sleep):
    client = fake_upload([(500, None, False), (500, None, False)])
    img = tmp_path / "cs.jpg"
    img.write_bytes(b"x")
    with pytest.raises(ContactSheetError, match="after retries"):
        upload_hamster(img, settings_with(tmp_path, contact_sheet_upload_retries=2))
    assert record_sleep == [0.5]


def test_429_retried_and_retry_after_honored(tmp_path, fake_upload, record_sleep):
    client = fake_upload(
        [
            (429, {"error": {"message": "rate limited"}}, False, {"Retry-After": "2"}),
            (200, {"image": {"url": "https://img.example/final.jpg"}}, False),
        ]
    )
    img = tmp_path / "cs.jpg"
    img.write_bytes(b"x")
    assert upload_hamster(img, settings_with(tmp_path)) == "https://img.example/final.jpg"
    assert len(client.calls) == 2
    assert record_sleep == [2.0]


def test_429_retry_after_date_form(tmp_path, fake_upload, record_sleep):
    from datetime import datetime, timedelta, timezone
    import email.utils

    when = email.utils.format_datetime(datetime.now(timezone.utc) + timedelta(seconds=10))
    client = fake_upload(
        [
            (429, None, False, {"Retry-After": when}),
            (200, {"image": {"url": "https://img.example/final.jpg"}}, False),
        ]
    )
    img = tmp_path / "cs.jpg"
    img.write_bytes(b"x")
    assert upload_hamster(img, settings_with(tmp_path)) == "https://img.example/final.jpg"
    assert len(record_sleep) == 1
    assert 9.0 <= record_sleep[0] <= 11.0


def test_retry_after_bounded(tmp_path, fake_upload, record_sleep):
    client = fake_upload(
        [
            (429, None, False, {"Retry-After": "999999"}),
            (200, {"image": {"url": "https://img.example/final.jpg"}}, False),
        ]
    )
    img = tmp_path / "cs.jpg"
    img.write_bytes(b"x")
    assert upload_hamster(img, settings_with(tmp_path)) == "https://img.example/final.jpg"
    assert record_sleep == [60.0]


def test_retry_after_seconds_helpers():
    response = FakeResponse(429, None, False, {"Retry-After": "5"})
    assert _retry_after_seconds(response) == 5.0
    assert _retry_after_seconds(FakeResponse(429, None, False, {"Retry-After": "garbage"})) is None
    assert _retry_after_seconds(FakeResponse(429)) is None
    assert _retry_after_seconds(FakeResponse(429, None, False, {"Retry-After": "-1"})) == 0.0
    assert _retry_after_seconds(FakeResponse(429, None, False, {"Retry-After": "-999"})) == 0.0


def test_retry_after_negative_delta_no_sleep_negative(tmp_path, fake_upload, record_sleep):
    client = fake_upload(
        [
            (429, None, False, {"Retry-After": "-1"}),
            (200, {"image": {"url": "https://img.example/final.jpg"}}, False),
        ]
    )
    img = tmp_path / "cs.jpg"
    img.write_bytes(b"x")
    assert upload_hamster(img, settings_with(tmp_path)) == "https://img.example/final.jpg"
    assert record_sleep == [0.0]


def test_retry_after_naive_datetime_rejected(monkeypatch):
    from datetime import datetime

    monkeypatch.setattr("empornium_megapack.images.email.utils.parsedate_to_datetime", lambda raw: datetime(2026, 1, 1, 12, 0, 0))
    response = FakeResponse(429, None, False, {"Retry-After": "Thu, 01 Jan 2026 12:00:00 GMT"})
    assert _retry_after_seconds(response) is None


def test_429_with_error_json_still_retried(tmp_path, fake_upload, no_backoff):
    client = fake_upload(
        [
            (429, {"error": {"message": "rate limited"}}, False),
            (200, {"image": {"url": "https://img.example/final.jpg"}}, False),
        ]
    )
    img = tmp_path / "cs.jpg"
    img.write_bytes(b"x")
    assert upload_hamster(img, settings_with(tmp_path)) == "https://img.example/final.jpg"
    assert len(client.calls) == 2


def test_4xx_rejected_no_retry(tmp_path, fake_upload, no_backoff):
    client = fake_upload([(403, {"error": {"message": "forbidden"}}, False)])
    img = tmp_path / "cs.jpg"
    img.write_bytes(b"x")
    with pytest.raises(ContactSheetError, match="forbidden"):
        upload_hamster(img, settings_with(tmp_path))
    assert len(client.calls) == 1


def test_4xx_rejected_generic_message(tmp_path, fake_upload, no_backoff):
    client = fake_upload([(401, None, False)])
    img = tmp_path / "cs.jpg"
    img.write_bytes(b"x")
    with pytest.raises(ContactSheetError, match="HTTP 401"):
        upload_hamster(img, settings_with(tmp_path))
    assert len(client.calls) == 1


def test_contact_sheet_end_to_end(tmp_path, vcstub, stub_vcsi, fake_upload):
    client = fake_upload()
    service = ImageService(settings_with(tmp_path))
    video = tmp_path / "video.mp4"
    video.write_bytes(b"video")
    url, digest, path = service.contact_sheet("s1", str(video))
    assert url == "https://img.example/x.jpg"
    assert len(digest) == 64
    assert path.name == f"s1.{digest[:8]}.jpg"
    assert path.is_file()
    assert len(client.calls) == 1


def test_digest_cache_reuses_url(tmp_path, vcstub, stub_vcsi, fake_upload):
    client = fake_upload()
    service = ImageService(settings_with(tmp_path))
    video = tmp_path / "video.mp4"
    video.write_bytes(b"video")
    url1, digest1, path1 = service.contact_sheet("s1", str(video))
    url2, digest2, path2 = service.contact_sheet("s1", str(video))
    assert url1 == url2
    assert digest1 == digest2
    assert path1 == path2
    assert len(client.calls) == 1


def test_digest_cache_misses_on_change(tmp_path, vcstub, stub_vcsi, fake_upload):
    client = fake_upload()
    service = ImageService(settings_with(tmp_path))
    video = tmp_path / "video.mp4"
    video.write_bytes(b"video")
    _, digest1, _ = service.contact_sheet("s1", str(video))

    alt = tmp_path / "vcstub_alt.py"
    alt.write_text(VCSTUB.replace('"|".join(sys.argv[1:]).encode()', 'b"ALT"'), encoding="utf-8")
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr("empornium_megapack.images._vcsi_command", lambda settings: [sys.executable, str(alt)])
    try:
        _, digest2, _ = service.contact_sheet("s1", str(video))
    finally:
        monkeypatch.undo()
    assert digest1 != digest2
    assert len(client.calls) == 2


def test_required_upload_failure_propagates(tmp_path, vcstub, stub_vcsi, fake_upload, no_backoff):
    fake_upload([(500, None, False), (500, None, False), (500, None, False)])
    service = ImageService(settings_with(tmp_path))
    video = tmp_path / "video.mp4"
    video.write_bytes(b"video")
    with pytest.raises(ContactSheetError, match="after retries"):
        service.contact_sheet("s1", str(video))


def _big_jpeg(path, size=1400):
    from PIL import Image

    image = Image.new("RGB", (size, size), (200, 120, 60))
    for x in range(0, size, 8):
        for y in range(0, size, 8):
            image.putpixel((x, y), (x % 256, y % 256, (x + y) % 256))
    image.save(path, format="JPEG", quality=100)


def test_enforce_size_limit_skips_small_files(tmp_path):
    path = tmp_path / "small.jpg"
    path.write_bytes(b"\xff\xd8small")
    enforce_size_limit(path, 10_000_000)
    assert path.read_bytes() == b"\xff\xd8small"


def test_enforce_size_limit_reenforces_deterministically(tmp_path):
    path = tmp_path / "big.jpg"
    _big_jpeg(path)
    assert path.stat().st_size > 200_000
    enforce_size_limit(path, 100_000)
    assert path.stat().st_size <= 100_000
    first = path.read_bytes()
    enforce_size_limit(path, 100_000)
    assert path.read_bytes() == first


def test_enforce_size_limit_impossible_raises(tmp_path):
    path = tmp_path / "big.jpg"
    _big_jpeg(path)
    with pytest.raises(ContactSheetError, match="under 200 bytes"):
        enforce_size_limit(path, 200)


def test_contact_sheet_size_enforced_before_upload(tmp_path, vcstub, stub_vcsi, fake_upload, no_backoff):
    client = fake_upload()
    big = tmp_path / "bigpayload.jpg"
    _big_jpeg(big)
    stub_vcsi(extra=["--payload", str(big)])
    service = ImageService(settings_with(tmp_path, contact_sheet_max_bytes=5_000))
    video = tmp_path / "video.mp4"
    video.write_bytes(b"video")
    url, digest, path = service.contact_sheet("s1", str(video))
    assert url == "https://img.example/x.jpg"
    assert path.stat().st_size <= 5_000
    assert len(client.calls) == 1
    with path.open("rb") as fh:
        assert fh.read(2) == b"\xff\xd8"


def test_pack_images_endpoint_success(tmp_path, vcstub, stub_vcsi, fake_upload, no_backoff):
    fake_upload()
    video = tmp_path / "scene.mp4"
    video.write_bytes(b"video")
    scenes = {"1": make_scene("1", [make_file("f1", str(video))])}
    svc = PackService(settings=settings_with(tmp_path), stash=FakeStash(scenes))
    response = svc.images(ImagesRequest(scene_ids=["1"]))
    assert response.errors == []
    assert len(response.images) == 1
    assert response.images[0].scene_id == "1"
    assert response.images[0].url == "https://img.example/x.jpg"
    assert "path" not in response.images[0].model_dump()
    assert svc.images_service.sheets["1"].is_file()


def test_pack_images_endpoint_respects_layout(tmp_path, vcstub, stub_vcsi, fake_upload, no_backoff):
    client = fake_upload()
    video = tmp_path / "scene.mp4"
    video.write_bytes(b"video")
    scenes = {"1": make_scene("1", [make_file("f1", str(video))])}
    svc = PackService(settings=settings_with(tmp_path), stash=FakeStash(scenes))
    response = svc.images(ImagesRequest(scene_ids=["1"], layout="4x4"))
    assert response.errors == []
    assert len(response.images) == 1
    sheet = svc.images_service.sheets["1"]
    assert b"-g|4x4" in sheet.read_bytes()
    assert len(client.calls) == 1


def test_pack_images_endpoint_unknown_scene(tmp_path, vcstub, stub_vcsi, fake_upload):
    svc = PackService(settings=settings_with(tmp_path), stash=FakeStash({}))
    response = svc.images(ImagesRequest(scene_ids=["nope"]))
    assert response.images == []
    assert any(i.code == "unknown_scene" for i in response.errors)


def test_pack_images_endpoint_missing_file(tmp_path, vcstub, stub_vcsi, fake_upload):
    scenes = {"1": make_scene("1", [make_file("f1", str(tmp_path / "gone.mp4"))])}
    svc = PackService(settings=settings_with(tmp_path), stash=FakeStash(scenes))
    response = svc.images(ImagesRequest(scene_ids=["1"]))
    assert response.images == []
    assert any(i.code == "file_missing" for i in response.errors)


def test_pack_images_endpoint_needs_choice(tmp_path, vcstub, stub_vcsi, fake_upload):
    scenes = {"1": make_scene("1", [make_file("f1", str(tmp_path / "a.mp4")), make_file("f2", str(tmp_path / "b.mp4"))])}
    svc = PackService(settings=settings_with(tmp_path), stash=FakeStash(scenes))
    response = svc.images(ImagesRequest(scene_ids=["1"]))
    assert response.images == []
    assert any(i.code == "needs_choice" for i in response.errors)


# --- ~/.stash ffmpeg/ffprobe fallback (mirrors the legacy launcher's lookup) ----------

def test_resolve_ffmpeg_stash_fallback(monkeypatch, tmp_path):
    """When ffmpeg is not on PATH and cove is absent, fall back to ~/.stash/ffmpeg(.exe)."""
    stash_dir = tmp_path / ".stash"
    stash_dir.mkdir()
    exe = stash_dir / "ffmpeg.exe"
    exe.touch()
    monkeypatch.setattr("empornium_megapack.images.shutil.which", lambda name: None)
    monkeypatch.setattr("empornium_megapack.images._COVE_FFMPEG", tmp_path / "missing.exe")
    monkeypatch.setattr("empornium_megapack.images.Path.home", classmethod(lambda cls: tmp_path))
    assert resolve_ffmpeg(settings_with(tmp_path)) == str(exe)


def test_resolve_ffprobe_stash_fallback(monkeypatch, tmp_path):
    """When ffprobe is not on PATH but ffmpeg was found in ~/.stash, derive ffprobe from there."""
    stash_dir = tmp_path / ".stash"
    stash_dir.mkdir()
    ffmpeg_exe = stash_dir / "ffmpeg.exe"
    ffmpeg_exe.touch()
    ffprobe_exe = stash_dir / "ffprobe.exe"
    ffprobe_exe.touch()
    monkeypatch.setattr("empornium_megapack.images.shutil.which", lambda name: None)
    monkeypatch.setattr("empornium_megapack.images._COVE_FFMPEG", tmp_path / "missing.exe")
    monkeypatch.setattr("empornium_megapack.images.Path.home", classmethod(lambda cls: tmp_path))
    assert resolve_ffprobe(settings_with(tmp_path)) == str(ffprobe_exe)


# ---------------------------------------------------------------------------
# Milestone B1: upload_hamster log_callback and retry line tests
# ---------------------------------------------------------------------------

def test_upload_hamster_log_callback_on_5xx_retry(tmp_path, fake_upload, no_backoff):
    """upload_hamster invokes log_callback with formatted attempt/retry details on 5xx errors."""
    client = fake_upload([
        (502, None, False),
        (200, {"image": {"url": "https://img.example/recovered.jpg"}}, False),
    ])
    logs = []
    img = tmp_path / "cs.jpg"
    img.write_bytes(b"\xff\xd8preview")
    settings = settings_with(tmp_path, contact_sheet_upload_backoff_base=1.0)

    url = upload_hamster(img, settings, log_callback=logs.append)
    assert url == "https://img.example/recovered.jpg"
    assert len(logs) == 1
    # Expected format: attempt 1/3 failed (HTTP 502), retrying in 1.0s
    assert "attempt 1/3 failed" in logs[0]
    assert "HTTP 502" in logs[0]
    assert "retrying in" in logs[0]


def test_upload_hamster_log_callback_on_429_retry_after(tmp_path, fake_upload, no_backoff):
    """upload_hamster logs retry after delay when HTTP 429 rate limit is encountered."""
    client = fake_upload([
        (429, {"error": {"message": "rate limited"}}, False, {"Retry-After": "4"}),
        (200, {"image": {"url": "https://img.example/success.jpg"}}, False),
    ])
    logs = []
    img = tmp_path / "cs.jpg"
    img.write_bytes(b"\xff\xd8preview")

    url = upload_hamster(img, settings_with(tmp_path), log_callback=logs.append)
    assert url == "https://img.example/success.jpg"
    assert len(logs) == 1
    assert "attempt 1/3 failed" in logs[0]
    assert "HTTP 429" in logs[0]
    assert "retrying in 4.0s" in logs[0]


def test_upload_hamster_log_callback_on_network_error(tmp_path, no_backoff):
    """upload_hamster logs network error details during retries."""
    class NetworkErrorClient(FakeClient):
        def post(self, url, **kwargs):
            self.calls.append((url, kwargs))
            if len(self.calls) == 1:
                raise httpx.ConnectError("Connection timed out")
            return FakeResponse(200, {"image": {"url": "https://img.example/ok.jpg"}})

    monkeypatch = pytest.MonkeyPatch()
    client = NetworkErrorClient()
    monkeypatch.setattr("empornium_megapack.images.httpx.Client", lambda timeout: client)
    try:
        logs = []
        img = tmp_path / "cs.jpg"
        img.write_bytes(b"\xff\xd8preview")
        url = upload_hamster(img, settings_with(tmp_path), log_callback=logs.append)
        assert url == "https://img.example/ok.jpg"
        assert len(logs) == 1
        assert "attempt 1/3 failed" in logs[0]
        assert "network error" in logs[0]
        assert "Connection timed out" in logs[0]
    finally:
        monkeypatch.undo()


def test_upload_hamster_log_callback_multiple_retries_before_exhaustion(tmp_path, fake_upload, no_backoff):
    """upload_hamster logs every retry attempt before raising ContactSheetError."""
    client = fake_upload([(500, None, False), (500, None, False), (500, None, False)])
    logs = []
    img = tmp_path / "cs.jpg"
    img.write_bytes(b"\xff\xd8data")
    settings = settings_with(tmp_path, contact_sheet_upload_retries=3)

    with pytest.raises(ContactSheetError, match="after retries"):
        upload_hamster(img, settings, log_callback=logs.append)

    assert len(logs) == 2
    assert "attempt 1/3 failed" in logs[0]
    assert "attempt 2/3 failed" in logs[1]


def test_upload_hamster_log_callback_optional_default_none(tmp_path, fake_upload):
    """Calling upload_hamster without log_callback maintains backward compatibility."""
    client = fake_upload()
    img = tmp_path / "cs.jpg"
    img.write_bytes(b"\xff\xd8data")
    # Must succeed without TypeError when log_callback is omitted
    url = upload_hamster(img, settings_with(tmp_path))
    assert url == "https://img.example/x.jpg"


def test_upload_hamster_log_callback_not_called_on_immediate_success(tmp_path, fake_upload):
    """log_callback is not called on clean single-attempt upload success."""
    client = fake_upload([(200, {"image": {"url": "https://img.example/fast.jpg"}}, False)])
    logs = []
    img = tmp_path / "cs.jpg"
    img.write_bytes(b"\xff\xd8data")
    url = upload_hamster(img, settings_with(tmp_path), log_callback=logs.append)
    assert url == "https://img.example/fast.jpg"
    assert logs == []


def test_upload_hamster_log_callback_not_called_on_fatal_4xx(tmp_path, fake_upload):
    """Non-retryable 4xx errors raise immediately without invoking retry log_callback."""
    client = fake_upload([(403, {"error": {"message": "Invalid API key"}}, False)])
    logs = []
    img = tmp_path / "cs.jpg"
    img.write_bytes(b"\xff\xd8data")
    with pytest.raises(ContactSheetError, match="Invalid API key"):
        upload_hamster(img, settings_with(tmp_path), log_callback=logs.append)
    assert logs == []

