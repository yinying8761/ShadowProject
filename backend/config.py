from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parent

# Convenience presets — auto-fill base_url and suggest default model.
# Users can set LLM_PROVIDER=custom with any LLM_BASE_URL for arbitrary endpoints.
#
# 这里同时是 ADR-0011 的**模型能力表**：`supports_vision` / `image_api`（视觉）与
# `native_search` / `native_fetch` / `search_api`（原生搜索）。字段含义：
#   - 布尔项三态：True / False / None（Unknown —— 预设没把握，消费者按 ADR-0011 的
#     不对称规则处理：视觉未知也试一次，原生搜索未知就不走）。
#   - 形状项只能是 `services.capabilities.IMAGE_APIS` / `SEARCH_APIS` 里的值
#     （`tests/test_capabilities.py` 会遍历整张表锁住这一点，写歪了会红）。
# 新增一家厂商 = 填这几个字段；只有形状不在已知三种/四种里时才动 formatter/parser 缝。
PROVIDER_PRESETS = {
    "anthropic": {
        "base_url": None,
        "default_model": "claude-sonnet-4-20250514",
        "sdk_type": "anthropic",
        "description": "Anthropic Claude",
        "supports_vision": True,
        "image_api": "anthropic",
        "native_search": True,
        "native_fetch": True,
        "search_api": "anthropic",
    },
    "openai": {
        "base_url": None,
        "default_model": "gpt-4o",
        "sdk_type": "openai",
        "description": "OpenAI ChatGPT",
        "supports_vision": True,
        "image_api": "openai",
        "native_search": True,
        "native_fetch": True,
        "search_api": "openai",
    },
    "deepseek": {
        "base_url": "https://api.deepseek.com/v1",
        "default_model": "deepseek-v4-pro",
        "sdk_type": "openai",
        "description": "DeepSeek",
        "supports_vision": True,
        "image_api": "openai",
        "native_search": True,
        "native_fetch": True,
        "search_api": "deepseek",
    },
    "gemini": {
        # 走 Google 的 OpenAI 兼容端点，所以形状就是 openai（ADR-0011）。
        "base_url": "https://generativelanguage.googleapis.com/v1beta/openai/",
        "default_model": "gemini-2.5-flash",
        "sdk_type": "openai",
        "description": "Google Gemini",
        "supports_vision": True,
        "image_api": "openai",
        "native_search": True,
        "native_fetch": True,
        "search_api": "openai",
    },
    "qwen": {
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "default_model": "qwen-plus",
        "sdk_type": "openai",
        "description": "Qwen / 通义千问",
        # 同一家厂商里 qwen-vl 能看、qwen-plus 不能 → 不替用户猜
        "supports_vision": None,
        "image_api": "openai",
        "native_search": None,
        "native_fetch": None,
        "search_api": None,
    },
    "zhipu": {
        "base_url": "https://open.bigmodel.cn/api/paas/v4",
        "default_model": "glm-4-flash",
        "sdk_type": "openai",
        "description": "ZhipuAI / 智谱",
        # glm-4v 能看、glm-4 不能
        "supports_vision": None,
        "image_api": "openai",
        "native_search": None,
        "native_fetch": None,
        "search_api": None,
    },
    "moonshot": {
        "base_url": "https://api.moonshot.cn/v1",
        "default_model": "moonshot-v1-8k",
        "sdk_type": "openai",
        "description": "Moonshot / Kimi",
        "supports_vision": False,
        "image_api": "openai",
        "native_search": None,
        "native_fetch": None,
        "search_api": None,
    },
    "custom": {
        "base_url": None,
        "default_model": "",
        "sdk_type": "openai",
        "description": "Custom (any OpenAI-compatible API)",
        # 任意 OpenAI 兼容端点：形状默认 openai，能不能看由用户覆盖
        "supports_vision": None,
        "image_api": "openai",
        "native_search": None,
        "native_fetch": None,
        "search_api": None,
    },
}


# 专家视觉模型的预设（ticket 05 之后**只**在主模型看不了时兜底，见 services/vision_tool.py）。
# 删掉了已停用的 `qwen` / `qwen-vl-max`：那正是"第二个互不相干的预设表让一个退役模型
# 在产品里留了好几周"的来源（ADR-0011）。base_url / sdk_type 不在这里重复 —— 直接取
# 同一个供应商在 PROVIDER_PRESETS 里的值（见下方 get_vision_* 的兜底）。
VISION_PRESETS = {
    "openai": {
        "default_model": "gpt-4o-mini",
        "description": "GPT-4o-mini",
    },
    "anthropic": {
        "default_model": "claude-haiku-4-5-20251001",
        "description": "Claude Haiku 4.5",
    },
    "gemini": {
        "default_model": "gemini-2.5-flash",
        "description": "Google Gemini (OpenAI 兼容端点)",
    },
    "zhipu": {
        "default_model": "glm-4v-flash",
        "description": "ZhipuAI GLM-4V",
    },
    "custom": {
        "default_model": "",
        "description": "Custom vision endpoint",
    },
}


#: searxng 的默认地址（占位符：没显式选中/改成别的地址时不进搜索链，见 search_tools）。
SEARXNG_DEFAULT_URL = "http://127.0.0.1:8080"


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

    # ---- Vision (specialist fallback for screen understanding, ticket #05) ----
    # 主模型能看图时这些**都不用配**（截图直接进当轮请求）。只有主模型不能看时才用
    # 这里的专家视觉模型；两者都没有 → see_screen 返回明确的"看不了"错误。
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
    search_backend: str = "duckduckgo"  # duckduckgo | bing_web | searxng | bing
    searxng_url: str = SEARXNG_DEFAULT_URL
    bing_api_key: str = ""
    search_proxy: str = ""  # e.g. http://127.0.0.1:7890 for VPN/proxy
    # 原生搜索进链首（ticket 03）；关掉即回到纯本地链，用于对照与排障。
    search_prefer_native: bool = True
    # 原生搜索的整体超时：超时/失败都只降级到本地链，不抛错。
    search_timeout: float = 20.0

    # ---- Location & Weather ----
    amap_api_key: str = ""  # 高德地图 API Key (Web服务)
    daily_greeting_enabled: bool = True
    weather_enabled: bool = True
    user_city: str = ""  # 手动指定城市，留空则 IP 自动定位
    user_lat: float = 0.0
    user_lon: float = 0.0

    # ---- TTS (GPT-SoVITS) ----
    tts_api_url: str = "http://127.0.0.1:9880/tts"
    tts_ref_base: str = ""  # set in .env, e.g. ./tools/GPT-SoVITS/GPT-SoVITS

    # ---- MCP (Model Context Protocol) ----
    # Path to mcp_servers.json.  Leave empty to use {data_dir}/mcp_servers.json.
    mcp_config_path: str = ""
    # Health-check interval (seconds) between MCP connectivity probes.
    mcp_health_check_interval: float = 30.0

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

    def _vision_provider_row(self) -> dict:
        """同一个供应商在 PROVIDER_PRESETS 里的那一行（base_url / sdk_type 的唯一来源）。"""
        return PROVIDER_PRESETS.get(self.vision_provider, PROVIDER_PRESETS.get("custom")) or {}

    def get_vision_base_url(self) -> str | None:
        if self.vision_base_url:
            return self.vision_base_url
        return self._vision_provider_row().get("base_url")

    def get_vision_sdk_type(self) -> str:
        return self._vision_provider_row().get("sdk_type", "openai")

    def get_vision_model(self) -> str:
        if self.vision_model:
            return self.vision_model
        preset = self.get_vision_preset()
        return preset.get("default_model", "") if preset else ""


settings = Settings()
