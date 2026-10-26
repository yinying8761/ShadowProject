# 03 — 搜索：原生搜索进链首 + 本地抓取降级

**What to build:** 搜索优先走提供商原生能力（OpenAI / Anthropic 两种响应形状先落地），拿到带来源的结果；原生不可用或失败时自动降级到现有本地抓取链。`research` 工具的对外契约与「主 LLM 决定要不要搜」的决策权都不变。

**Blocked by:** 01（本目录内的编号）

**Status:** ready-for-agent

**参考:** docs/specs/native-web-search.md

- [ ] 能力支持时答案基于真实网页并带来源 URL，日志显示 backend=native 与实际 api
- [ ] 原生失败自动落到 ddgs，再落到 bing 抓取；全链失败返回明确的空结果而不是抛错
- [ ] OpenAI 与 Anthropic 两种响应都解析成统一的搜索结果结构（各有一份固定响应体快照测试）
- [ ] 未知或缺失 search_api 时不尝试原生、直接走本地链，且不报错
- [ ] research 工具签名与返回结构不变；已有关键词提示注入路径不变
