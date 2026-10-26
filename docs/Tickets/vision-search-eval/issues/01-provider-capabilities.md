# 01 — 提供商能力表：模型级能力 + 用户覆盖 + 设置面板

**What to build:** 把「模型能做什么」收成一张模型级能力表：视觉（是否支持 + 图片载荷形状）与原生搜索（是否支持 + 响应形状），并在设置里能看到生效值、能手动覆盖。做完之后，后续的视觉与搜索改造都只是「填表 + 用既有的缝」，业务代码不再出现按厂商名分支。

**Blocked by:** None — 可立即开始。

**Status:** ready-for-agent

**参考:** docs/adr/0011-provider-capability-matrix.md, docs/specs/multimodal-vision.md

- [ ] 能力表覆盖视觉（supports_vision / image_api）与原生搜索（native_search / native_fetch / search_api），DeepSeek、OpenAI、Anthropic、Gemini 默认可用，未列出的保持未知
- [ ] 解析顺序为「用户覆盖 → 预设默认 → 未知」，三态（默认支持 / 手动开启 / 不支持）可分辨
- [ ] 设置面板显示每项能力的生效值与来源，并能手动覆盖未知模型
- [ ] 业务代码只通过一个运行时访问器判断能力，不新增按厂商名的分支
- [ ] 测试：预设默认、用户覆盖优先、未知兜底；UI 手工验收
