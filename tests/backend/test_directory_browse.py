import pytest
from empornium_megapack.config import Settings
from empornium_megapack.gql import StashClient, StashError


def test_list_directory_success(monkeypatch):
    captured_post = []

    def mock_post(self, query, variables):
        captured_post.append({"query": query, "variables": variables})
        return {
            "directory": {
                "path": "C:\\Packs",
                "parent": "C:\\",
                "directories": ["C:\\Packs\\Action", "C:\\Packs\\Drama"],
            }
        }

    monkeypatch.setattr(StashClient, "_post", mock_post)

    client = StashClient(Settings())
    res = client.list_directory("C:\\Packs")

    assert len(captured_post) == 1
    assert "query Directory($path: String)" in captured_post[0]["query"]
    assert captured_post[0]["variables"] == {"path": "C:\\Packs"}
    assert res["path"] == "C:\\Packs"
    assert res["parent"] == "C:\\"
    assert res["directories"] == ["C:\\Packs\\Action", "C:\\Packs\\Drama"]


def test_list_directory_root(monkeypatch):
    captured_post = []

    def mock_post(self, query, variables):
        captured_post.append({"query": query, "variables": variables})
        return {
            "directory": {
                "path": "",
                "parent": None,
                "directories": ["C:\\", "D:\\", "E:\\"],
            }
        }

    monkeypatch.setattr(StashClient, "_post", mock_post)

    client = StashClient(Settings())
    res = client.list_directory(None)

    assert len(captured_post) == 1
    assert captured_post[0]["variables"] == {"path": None}
    assert res["directories"] == ["C:\\", "D:\\", "E:\\"]


def test_list_directory_error(monkeypatch):
    def mock_post(self, query, variables):
        raise StashError("Directory not found")

    monkeypatch.setattr(StashClient, "_post", mock_post)

    client = StashClient(Settings())
    with pytest.raises(StashError, match="Directory not found"):
        client.list_directory("Z:\\Invalid")
