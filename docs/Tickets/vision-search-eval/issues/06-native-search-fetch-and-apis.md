# 06 — 搜索：原生抓取 + Gemini/DeepSeek 分支

**What to build:** 补完原生能力的其余分支：fetch_url 优先走原生抓取，失败回落现有正文抽取；Gemini（grounding）与 DeepSeek 的原生搜索也能用，分派表完整。

**Blocked by:** 03（本目录内的编号）

**Status:** ready-for-agent

**参考:** docs/specs/native-web-search.md

- [ ] fetch_url 优先 native_fetch，失败回落正文抽取，两者都失败返回错误信封
- [ ] Gemini 与 DeepSeek 的搜索分支可用，各自有固定响应体测试
- [ ] 未知 search_api 仍直接降级，不报错
- [ ] 日志能区分后端与 api
