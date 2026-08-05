# AI Companion

桌面 AI 伙伴 — 一个可以常驻桌面的角色扮演 AI Agent。支持多角色切换、流式聊天、屏幕感知、记忆系统、语音合成。

## 特性

- **角色扮演**：自定义 AI 角色的名字、性别、性格、说话风格、声线
- **用户画像**：AI 自动学习并持久化用户的个人信息（姓名、关系、偏好等），跨会话保留
- **流式聊天**：WebSocket 实时流式对话，支持工具调用（搜索、文件读写、屏幕查看）
- **主动陪伴**：AI 会在空闲时主动发起话题，支持定时问候和空闲检测
- **记忆系统**：SQLite FTS5 全文索引 + 向量嵌入混合检索，按角色隔离；自动记忆提取与裁剪
- **语音合成**：支持 GPT-SoVITS 零样本声线克隆，每个角色独立语音
- **每日问候**：首次打开时根据时间、天气、位置自动发起问候
- **位置感知**：浏览器 Geolocation + 高德逆地理编码，周边推荐
- **内容保护**：截屏时窗口自动排除自身（Windows WDA_EXCLUDEDFROMCAPTURE）
- **浮动图标**：最小化后显示 64x64 圆形头像，有未读消息时呼吸灯提示
- **工具运行时**：带追踪、超时沙箱的工具执行层，支持工具调用日志查询
- **多智能体**：SearchAgent 独立搜索引擎子智能体；MCP Vision 免费视觉感知（智谱 GLM-4V Flash）
- **相对日期**：对话摘要和记忆中的日期自动格式化为人类可读的相对时间

## 技术栈

| 层 | 技术 |
|---|---|
| 前端 | Electron + React + TypeScript + Tailwind CSS + Zustand |
| 后端 | Python FastAPI + WebSocket + SQLAlchemy + aiosqlite |
| AI | Anthropic / OpenAI / DeepSeek 多 provider 支持 |
| 语音 | GPT-SoVITS 神经网络声线克隆 |

## 项目结构

```
├── backend/
│   ├── main.py              # FastAPI 入口、工具注册、MCP 初始化
│   ├── config.py            # pydantic-settings 配置
│   ├── database.py          # SQLite 异步引擎 + 迁移
│   ├── api/                 # REST + WebSocket 路由（含 tool_logs）
│   ├── core/                # Agent 引擎、SubAgent、ToolRuntime、Prompt 管理
│   ├── services/            # LLM、Vision、Memory、TTS、Location、Weather、MCP
│   ├── tools/               # 文件操作、搜索、记忆、屏幕捕捉（MCP/旧版）
│   ├── models/              # SQLAlchemy ORM（含 UserProfile、ToolRun）
│   └── eval/                # 手工标注测试集 + 自动跑分
├── frontend/
│   ├── electron/            # Electron 主进程、preload、浮动窗口
│   └── src/
│       ├── components/      # React 组件
│       ├── hooks/           # 自定义 Hooks
│       ├── stores/          # Zustand 状态管理
│       ├── services/        # API 封装、TTS
│       └── i18n/            # 中英文翻译
├── scripts/                 # dev.bat 一键启动
├── mcp_servers.example.json # MCP 视觉服务器配置模板
├── .env.example             # 环境变量模板
└── README.md
```

## 快速开始

### 环境要求

- Windows 10/11
- Python 3.10+（推荐 3.10.6）
- Node.js 18+
- [可选] NVIDIA GPU + GPT-SoVITS（语音功能）

### 安装

**一键安装**（推荐）：

```bash
scripts\install.bat
```

安装 Python 和 Node.js 所有依赖。

**手动安装**：

```bash
cd backend && pip install -r requirements.txt
cd frontend && npm install
```

### 语音功能（可选）

**一键安装 GPT-SoVITS**：

```bash
scripts\setup-tts.bat
```

自动完成：克隆仓库 → 虚拟环境 → PyTorch → 依赖 → 预训练模型下载。如果模型下载失败（网络原因），去 B 站搜 "GPT-SoVITS 整合包" 手动下载。

**为角色克隆声线**：

1. 准备一段 3-10 秒的干净单人音频（无 BGM、无噪音），放到 GPT-SoVITS 目录下
2. 记下音频说的文本内容
3. 在 GPT-SoVITS 目录启动 API：`runtime\python.exe api_v2.py`
4. 测试声音效果：

```bash
curl -X POST "http://127.0.0.1:9880/tts" \
  -H "Content-Type: application/json" \
  -d '{"text":"你好呀","text_lang":"zh","ref_audio_path":"你的音频.wav","prompt_lang":"zh","prompt_text":"音频对应的文本","temperature":0.6,"top_k":30}'
```

5. 满意后配置角色语音：

```bash
curl -X PUT "http://127.0.0.1:8722/api/tts/{角色ID}/voice-config?ref_audio=你的音频.wav&prompt_text=音频对应的文本"
```

**推荐参数**：`temperature=0.6` `top_k=30` `text_split_method=cut5` — 声音更稳定，减少吸气声。

**最佳实践**：
- 参考音频选中性/温柔语调，单一情绪
- 5-8 秒最佳，不超过 10 秒
- 日语音源也能克隆出中文声音（`prompt_lang` 设为 `ja`）
- 每个角色独立配置，切换角色自动换声线

### 配置

```bash
# 从模板创建配置文件
cp .env.example .env
```

编辑 `.env`，至少填入 `LLM_API_KEY`。其他配置可选。

### 启动

```bash
# 方式一：一键启动
scripts/dev.bat

# 方式二：分别启动
cd backend && python main.py        # 后端 :8722
cd frontend && npm run electron:dev  # 前端 :16173
```

## 配置说明

完整配置项见 `.env.example`：

| 配置 | 说明 |
|---|---|
| `LLM_*` | 对话 LLM 的 provider、model、base_url、api_key |
| `VISION_*` | 屏幕感知视觉模型（可选，推荐使用 MCP 免费方案替代） |
| `SEARCH_BACKEND` | 搜索引擎：duckduckgo / bing_web |
| `AMAP_API_KEY` | 高德地图 Key（位置 + 天气，免费） |
| `TTS_REF_BASE` | GPT-SoVITS 安装路径（语音功能） |
| `MAX_CONTEXT_TOKENS` | 对话上下文窗口大小（默认 16000） |

### MCP 视觉（推荐免费方案）

复制 `mcp_servers.example.json` 为 `data/mcp_servers.json`，填入智谱 API Key：

```json
{
  "servers": [
    {
      "name": "vision",
      "transport": "stdio",
      "command": "python",
      "args": ["-m", "deepseek_vision_mcp"],
      "env": {
        "ZHIPU_API_KEY": "your-zhipu-api-key-here"
      }
    }
  ]
}
```

使用智谱 GLM-4V Flash 免费模型，`see_screen` 会优先走 MCP，不可用时自动降级到旧版 VisionService。

## License

MIT
