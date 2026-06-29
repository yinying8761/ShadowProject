from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parent

# Convenience presets — auto-fill base_url and suggest default model.
# Users can set LLM_PROVIDER=custom with any LLM_BASE_URL for arbitrary endpoints.
PROVIDER_PRESETS = {
    "anthropic": {
        "base_url": None,
        "default_model": "claude-sonnet-4-20250514",
        "sdk_type": "anthropic",
        "description": "Anthropic Claude",
    },
    "openai": {
        "base_url": None,
        "default_model": "gpt-4o",
        "sdk_type": "openai",
        "description": "OpenAI ChatGPT",
    },
    "deepseek": {
        "base_url": "https://api.deepseek.com/v1",
        "default_model": "deepseek-chat",
        "sdk_type": "openai",
        "description": "DeepSeek",
    },
    "qwen": {
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "default_model": "qwen-plus",
        "sdk_type": "openai",
        "description": "Qwen / 通义千问",
    },
    "zhipu": {
        "base_url": "https://open.bigmodel.cn/api/paas/v4",
        "default_model": "glm-4-flash",
        "sdk_type": "openai",
        "description": "ZhipuAI / 智谱",
    },
    "moonshot": {
        "base_url": "https://api.moonshot.cn/v1",
        "default_model": "moonshot-v1-8k",
        "sdk_type": "openai",
        "description": "Moonshot / Kimi",
    },
    "custom": {
        "base_url": None,
        "default_model": "",
        "sdk_type": "openai",
        "description": "Custom (any OpenAI-compatible API)",
    },
}


# Vision-capable model presets (for the visual specialist agent).
VISION_PRESETS = {
    "qwen": {
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "default_model": "qwen-vl-max",
        "sdk_type": "openai",
        "description": "Qwen-VL (Alibaba)",
    },
    "openai": {
        "base_url": None,
        "default_model": "gpt-4o-mini",
        "sdk_type": "openai",
        "description": "GPT-4o-mini",
    },
    "anthropic": {
        "base_url": None,
        "default_model": "claude-haiku-4-5-20251001",
        "sdk_type": "anthropic",
        "description": "Claude Haiku 4.5",
    },
    "zhipu": {
        "base_url": "https://open.bigmodel.cn/api/paas/v4",
        "default_model": "glm-4v-flash",
        "sdk_type": "openai",
        "description": "ZhipuAI GLM-4V",
    },
    "custom": {
        "base_url": None,
        "default_model": "",
        "sdk_type": "openai",
        "description": "Custom vision endpoint",
    },
}


class Settings(BaseSettings):
    # Check backend/.env first, then project root .env
    _env_files = [str(BACKEND_DIR / ".env"), str(BACKEND_DIR.parent / ".env")]
    _existing = [f for f in _env_files if Path(f).exists()]

    model_config = SettingsConfigDict(
        env_file=_existing[0] if _existing else _env_files[0],
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ---- LLM (character dialogue) ----
    llm_provider: str = "custom"
    llm_model: str = ""
    llm_base_url: str = ""
    llm_api_key: str = ""

    # ---- Vision (specialist agent for screen understanding) ----
    # If vision_api_key is empty, the see_screen tool returns an error
    # so the user knows they need to configure a vision-capable model.
    vision_provider: str = ""
    vision_model: str = ""
    vision_base_url: str = ""
    vision_api_key: str = ""

    # ---- Server ----
    host: str = "127.0.0.1"
    port: int = 8722
    data_dir: str = "../data"

    max_context_tokens: int = 8000

    # ---- Embedding (for memory/RAG) ----
    # Leave blank to reuse the LLM provider/API key.
    embedding_provider: str = ""
    embedding_model: str = ""
    embedding_api_key: str = ""
    embedding_base_url: str = ""
    embedding_dim: int = 1536
    # If true, use fastembed (local ONNX model) instead of API.
    embedding_local: bool = False

    # ---- Search ----
    search_backend: str = "duckduckgo"  # duckduckgo | searxng | bing
    searxng_url: str = "http://127.0.0.1:8080"
    bing_api_key: str = ""
    search_proxy: str = ""  # e.g. http://127.0.0.1:7890 for VPN/proxy

    # ---- Location & Weather ----
    amap_api_key: str = ""  # 高德地图 API Key (Web服务)
    daily_greeting_enabled: bool = True
    weather_enabled: bool = True
    user_city: str = ""  # 手动指定城市，留空则 IP 自动定位
    user_lat: float = 0.0
    user_lon: float = 0.0

    def get_embedding_provider(self) -> str:
        return self.embedding_provider or self.llm_provider

    def get_embedding_model(self) -> str:
        if self.embedding_model:
            return self.embedding_model
        if self.embedding_local:
            return "BAAI/bge-small-zh-v1.5"
        # Default per provider
        if self.get_embedding_provider() in ("openai", "custom"):
            return "text-embedding-3-small"
        return ""

    def get_embedding_api_key(self) -> str:
        return self.embedding_api_key or self.llm_api_key

    def get_embedding_base_url(self) -> str | None:
        if self.embedding_base_url:
            return self.embedding_base_url
        return self.get_base_url()

    def get_embedding_dim(self) -> int:
        if self.embedding_local or self.embedding_model.startswith("BAAI/"):
            return 384
        return self.embedding_dim

    def resolve_data_dir(self) -> Path:
        p = Path(self.data_dir)
        if not p.is_absolute():
            p = Path(__file__).parent / p
        return p.resolve()

    # ---- LLM helpers ----
    def get_preset(self) -> dict | None:
        return PROVIDER_PRESETS.get(self.llm_provider, PROVIDER_PRESETS.get("custom"))

    def get_base_url(self) -> str | None:
        if self.llm_base_url:
            return self.llm_base_url
        preset = self.get_preset()
        return preset.get("base_url") if preset else None

    def get_sdk_type(self) -> str:
        preset = self.get_preset()
        return preset.get("sdk_type", "openai") if preset else "openai"

    def get_model(self) -> str:
        if self.llm_model:
            return self.llm_model
        preset = self.get_preset()
        return preset.get("default_model", "") if preset else ""

    # ---- Vision helpers ----
    def vision_enabled(self) -> bool:
        return bool(self.vision_api_key)

    def get_vision_preset(self) -> dict | None:
        return VISION_PRESETS.get(self.vision_provider, VISION_PRESETS.get("custom"))

    def get_vision_base_url(self) -> str | None:
        if self.vision_base_url:
            return self.vision_base_url
        preset = self.get_vision_preset()
        return preset.get("base_url") if preset else None

    def get_vision_sdk_type(self) -> str:
        preset = self.get_vision_preset()
        return preset.get("sdk_type", "openai") if preset else "openai"

    def get_vision_model(self) -> str:
        if self.vision_model:
            return self.vision_model
        preset = self.get_vision_preset()
        return preset.get("default_model", "") if preset else ""


settings = Settings()
