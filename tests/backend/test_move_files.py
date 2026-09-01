from unittest.mock import MagicMock

from empornium_megapack.models import MoveFilesRequest, MoveFilesResponse
from empornium_megapack.review import PackService



def test_move_files_empty_destination():
    service = PackService(stash=MagicMock())
    req = MoveFilesRequest(scene_ids=["101"], destination_folder="   ")
    resp = service.move_files(req)
    assert resp.error_count == 1
    assert len(resp.errors) == 1
    assert resp.errors[0].code == "missing_destination"
    assert resp.moved_count == 0
    assert resp.already_in_place_count == 0


def test_move_files_already_in_place():
    mock_stash = MagicMock()
    mock_stash.fetch_scenes.return_value = {
        "101": {
            "id": "101",
            "title": "Scene 101",
            "files": [
                {
                    "id": "f-101",
                    "path": r"C:\Media\Performer\Scene101.mp4",
                    "basename": "Scene101.mp4",
                }
            ],
        },
        "102": {
            "id": "102",
            "title": "Scene 102",
            "files": [
                {
                    "id": "f-102",
                    "path": r"c:/media/performer/Scene102.mp4",
                    "basename": "Scene102.mp4",
                }
            ],
        },
    }
    service = PackService(stash=mock_stash)
    req = MoveFilesRequest(scene_ids=["101", "102"], destination_folder=r"C:\Media\Performer")
    resp = service.move_files(req)

    assert resp.total == 2
    assert resp.already_in_place_count == 2
    assert resp.moved_count == 0
    assert resp.error_count == 0
    # Stash move_files should not have been called
    mock_stash.move_files.assert_not_called()
    assert all(item.status == "already_in_place" for item in resp.items)


def test_move_files_mixed_and_move_success():
    mock_stash = MagicMock()
    mock_stash.fetch_scenes.return_value = {
        "101": {
            "id": "101",
            "title": "Scene 101",
            "files": [
                {
                    "id": "f-101",
                    "path": r"C:\Media\Performer\Scene101.mp4",
                    "basename": "Scene101.mp4",
                }
            ],
        },
        "102": {
            "id": "102",
            "title": "Scene 102",
            "files": [
                {
                    "id": "f-102",
                    "path": r"D:\Downloads\Scene102.mp4",
                    "basename": "Scene102.mp4",
                }
            ],
        },
    }
    mock_stash.move_files.return_value = True

    service = PackService(stash=mock_stash)
    req = MoveFilesRequest(scene_ids=["101", "102"], destination_folder=r"C:\Media\Performer")
    resp = service.move_files(req)

    assert resp.total == 2
    assert resp.already_in_place_count == 1
    assert resp.moved_count == 1
    assert resp.error_count == 0

    mock_stash.move_files.assert_called_once_with(file_ids=["f-102"], destination_folder=r"C:\Media\Performer")

    item101 = next(i for i in resp.items if i.scene_id == "101")
    item102 = next(i for i in resp.items if i.scene_id == "102")
    assert item101.status == "already_in_place"
    assert item102.status == "moved"
