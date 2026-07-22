from pydantic import BaseModel


class AgentManifest(BaseModel):
    did: str
    capabilities: list[str]
    languages: list[str]
    rate_limit: dict[str, int]
    model_disclosure: str


class ManifestRegistry:
    def __init__(self) -> None:
        self._manifests: dict[str, AgentManifest] = {}

    def register(self, manifest: AgentManifest) -> None:
        self._manifests[manifest.did] = manifest

    def resolve(self, did: str) -> AgentManifest | None:
        return self._manifests.get(did)
