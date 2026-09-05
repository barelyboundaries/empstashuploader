from concurrent.futures import ThreadPoolExecutor

import httpx

from .config import get_settings

SCENE_QUERY = """
query ScenePack($id: ID!) {
  findScene(id: $id) {
    id
    title
    date
    rating100
    created_at
    urls
    studio { name }
    performers { name }
    tags { name }
    files {
      id
      path
      basename
      mod_time
      created_at
      size
      width
      height
      duration
      video_codec
      oshash: fingerprint(type: "oshash")
    }
  }
}
"""

MOVE_FILES_MUTATION = """
mutation MoveSceneFiles($input: MoveFilesInput!) {
  moveFiles(input: $input)
}
"""

DIRECTORY_QUERY = """
query Directory($path: String) {
  directory(path: $path) {
    path
    parent
    directories
  }
}
"""


class StashError(Exception):
    pass


PLUGIN_CONFIGURATION_QUERY = """
query PluginConfiguration {
  configuration {
    plugins
  }
}
"""


class StashClient:
    def __init__(self, settings=None):
        self.settings = settings or get_settings()

    def _post(self, query: str, variables: dict) -> dict:
        headers = {"Content-Type": "application/json"}
        if self.settings.stash_api_key:
            headers["ApiKey"] = self.settings.stash_api_key
        try:
            response = httpx.post(
                f"{self.settings.stash_url}/graphql",
                json={"query": query, "variables": variables},
                headers=headers,
                timeout=30,
            )
        except httpx.HTTPError as exc:
            raise StashError(f"Stash unreachable at {self.settings.stash_url}: {exc}") from exc
        if response.status_code != 200:
            raise StashError(f"Stash GraphQL HTTP {response.status_code}")
        data = response.json()
        if "errors" in data and data["errors"]:
            raise StashError(f"Stash GraphQL error: {data['errors'][0].get('message')}")
        return data.get("data", {})

    def plugin_configuration(self) -> dict:
        """Return the Stash `configuration { plugins }` map.

        Keyed by plugin id; each value is that plugin's settings object. Raises
        StashError like every other query here — callers that must not fail a
        build on a down Stash are responsible for catching it.
        """
        data = self._post(PLUGIN_CONFIGURATION_QUERY, {})
        configuration = data.get("configuration")
        if not isinstance(configuration, dict):
            return {}
        plugins = configuration.get("plugins")
        return plugins if isinstance(plugins, dict) else {}

    def find_scene(self, scene_id: str) -> dict | None:
        data = self._post(SCENE_QUERY, {"id": scene_id})
        return data.get("findScene")

    def fetch_scenes(self, scene_ids: list[str]) -> dict[str, dict | None]:
        with ThreadPoolExecutor(max_workers=self.settings.stash_fetch_workers) as pool:
            results = pool.map(self.find_scene, scene_ids)
        return dict(zip(scene_ids, results))

    def move_files(self, file_ids: list[str], destination_folder: str, destination_folder_id: str | None = None) -> bool:
        variables = {
            "input": {
                "ids": file_ids,
                "destination_folder": destination_folder,
            }
        }
        if destination_folder_id:
            variables["input"]["destination_folder_id"] = destination_folder_id
        data = self._post(MOVE_FILES_MUTATION, variables)
        return bool(data.get("moveFiles"))

    def list_directory(self, path: str | None = None) -> dict | None:
        data = self._post(DIRECTORY_QUERY, {"path": path})
        return data.get("directory")

