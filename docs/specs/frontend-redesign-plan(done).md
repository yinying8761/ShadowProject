# 前端重设计方案：紧凑 / 完整 双模式

> 制定日期：2026-08-10
> 状态：规划中

## 一、概述

将当前的单一竖屏布局重构为 **双模式架构**：

- **紧凑模式（Compact）**：440×720 → 立绘 C 位 + 最新对话 + 快捷输入，适合桌面角落常驻
- **完整模式（Full）**：700×580 → 左侧对话列表 + 右侧聊天区，适合深入交流

用户通过 TitleBar 中的切换按钮一键切换，窗口尺寸由框架自动处理。

---

## 二、核心理念

```
紧凑模式 = 陪伴感（glanceable）
完整模式 = 效率感（operable）
```

两种模式共享同一份数据（chatStore + appStore），只切换视图层的排列方式，切换时**不丢失当前对话状态**。

---

## 三、双模式布局

### 3.1 紧凑模式（Compact Mode）

```
┌─────────────────────────┐  ← W: 440px
│  TitleBar (drag)         │  H: 36px
│  [角色名] [展开] [···]    │
├─────────────────────────┤
│                         │
│   CharacterDisplay      │  ← flex-1 撑满
│   (立绘/头像 + 呼吸动画)  │
│                         │
├─────────────────────────┤
│  ┌───────────────────┐  │
│  │ 角色名            │  │  ← DialogueBox
│  │ 最新一条回复...    │  │  max-h: 120px
│  └───────────────────┘  │
│  ToolStatusStrip         │
│  ┌───────────────────┐  │
│  │ 📷 输入框...   ➤  │  │  ← InputBar
│  └───────────────────┘  │
└─────────────────────────┘  ← H: ~720px
```

**特点：**
- 立绘占据最大视觉面积
- 对话区只显示最新消息
- 所有操作单手可达
- 角标提示记忆/主动消息

### 3.2 完整模式（Full Chat Mode）

```
┌───────────────────────────────────────┐  ← W: 700px
│  TitleBar (drag)                      │  H: 36px
│  [角色名] [收起] [···]                 │
├──────────┬────────────────────────────┤
│          │                            │
│ Conv     │  Chat Area                 │
│ Sidebar  │  ┌────────────────────┐   │
│          │  │ 完整的对话历史       │   │
│ [对话1]  │  │ (user + assistant)  │   │
│ [对话2]  │  │                    │   │
│ [对话3]  │  └────────────────────┘   │
│ [+新对话]│                            │
│          │  ┌────────────────────┐   │
│ 角色头像  │  │ 📷 输入框...    ➤  │   │
│          │  └────────────────────┘   │
└──────────┴────────────────────────────┘  ← H: ~580px
```

**特点：**
- 左侧：对话列表（可滚动）+ 角色头像 + 新建对话
- 右侧：完整聊天历史 + 流式响应 + 输入区
- 立绘缩小为头像，放在侧边栏顶部
- 支持消息搜索（后续迭代）

---

## 四、组件树重构

```
App
├─ LayoutProvider          ← 模式状态 + 平滑切换 (NEW)
│
├─ Shell
│   ├─ TitleBar            ← 重构：加模式切换按钮
│   └─ FloatingWidget      ← 悬浮球（Electron 端，不变）
│
├─ CompactView             ← 紧凑模式容器 (NEW)
│   ├─ CharacterDisplay    ← 保持现有，立绘 C 位
│   ├─ DialogueBox         ← 保持现有
│   ├─ ToolStatusStrip     ← 保持现有
│   └─ InputBar            ← 保持现有
│
├─ FullView                ← 完整模式容器 (NEW)
│   ├─ ConversationSidebar ← 对话列表 (NEW)
│   │   ├─ ConvListItem    ← 单个对话条目 (NEW)
│   │   └─ CharacterAvatar ← 角色头像 (NEW)
│   ├─ ChatPanel           ← 聊天面板 (NEW)
│   │   ├─ MessageList     ← 消息列表 (NEW)
│   │   │   └─ MessageBubble ← 消息气泡 (NEW)
│   │   ├─ ToolStatusStrip
│   │   └─ InputBar
│   └─ (CharacterDisplay 不出现)
│
├─ Overlays
│   ├─ SettingsPanel       ← 浮层外壳保留，内部重构为侧边导航
│   ├─ MemoryViewer
│   ├─ CharacterEditor
│   ├─ HistoryOverlay      ← 完整模式下可弱化/移除
│   └─ ApprovalDialog
│
└─ DesignTokens            ← 全局 CSS 变量 — 全息主题 (NEW)
```

---

## 五、视觉方向：全息主题（Holographic Theme）

### 5.1 设计目标

- 「全息投影」般的半透明漂浮感
- 科幻但不冰冷 — 保留 AI 伴侣的温度
- 暗色基底 + 发光强调 — 与透明无框窗口天然契合
- 减少色彩种类 — 用明暗和透明度区分层级

### 5.2 配色方案（已选定）

**方案 A：冰蓝全息**

```css
/* —— 基底 —— */
--color-bg-primary:    #080c14;
--color-bg-secondary:  #0f1420;
--color-bg-card:       #141a28;
--color-border:        rgba(255, 255, 255, 0.06);
--color-border-glow:   rgba(0, 198, 255, 0.2);

/* —— 主色调：冰蓝 —— */
--color-accent:        #00c6ff;
--color-accent-hover:  #00b0e6;
--color-accent-muted:  rgba(0, 198, 255, 0.12);
--color-accent-glow:   rgba(0, 198, 255, 0.25);

/* —— 功能色 —— */
--color-nametag:       #ff7eb3;
--color-nametag-bg:    rgba(255, 126, 179, 0.15);
--color-success:       #00e5bf;
--color-warning:       #f59e0b;
--color-error:         #ef4444;

/* —— 文字 —— */
--color-text-primary:   #e8ecf4;
--color-text-secondary: #7a8498;
--color-text-muted:     #5a6478;
```

### 5.3 全息视觉特征（配色无关）

| 特征 | 实现方式 |
|------|---------|
| 玻璃拟态 | 已有 `backdrop-filter: blur(12px)`，加深透明度 |
| 发光边框 | `box-shadow: 0 0 12px rgba(主色, 0.2)` + `border-color: rgba(主色, 0.3)` |
| 扫描线 | 可选 — CSS `repeating-linear-gradient` 背景叠加 |
| 文字发光 | `text-shadow: 0 0 8px rgba(主色, 0.4)` 用于标题 |
| 焦点光晕 | `box-shadow: 0 0 0 2px rgba(主色, 0.5)` 替代纯色边框 |
| 标签 | 半透明底 + 发光边框，替代实色填充 |

### 5.4 立绘与全息

角色立绘是投影的「实体化」：
- 立绘底部淡入淡出光柱（可选）
- 头像框用发光圆环包裹
- 紧凑模式的对话气泡从立绘「投射」出来

---

## 六、设计系统（Design Tokens）

> 具体色值待 5.2 配色确定后填入。

```css
/* —— 间距 —— */
--space-xs:   4px;
--space-sm:   8px;
--space-md:   12px;
--space-lg:   16px;
--space-xl:   24px;

/* —— 圆角 —— */
--radius-sm:   6px;
--radius-md:   10px;
--radius-lg:   14px;
--radius-xl:   20px;
--radius-full: 9999px;

/* —— 全息阴影 —— */
--glow-sm:  0 0 8px rgba(主色, 0.15);
--glow-md:  0 0 16px rgba(主色, 0.25);
--glow-lg:  0 0 32px rgba(主色, 0.3);
--glow-focus: 0 0 0 2px rgba(主色, 0.5);
--shadow-card: 0 8px 32px rgba(0, 0, 0, 0.4);
--shadow-popup: 0 16px 48px rgba(0, 0, 0, 0.6);

/* —— 过渡 —— */
--transition-fast:   150ms ease;
--transition-normal: 250ms ease;
--transition-slow:   400ms cubic-bezier(0.4, 0, 0.2, 1);
--transition-layout: 350ms cubic-bezier(0.4, 0, 0.2, 1);  /* 模式切换 */
```

---

## 七、模式切换机制

```typescript
// stores/appStore.ts — 新增字段
interface AppState {
  // ... existing ...
  layoutMode: 'compact' | 'full';
  setLayoutMode: (mode: 'compact' | 'full') => void;
}
```

**切换流程：**

1. 用户点击 TitleBar 中的展开/收起按钮
2. `setLayoutMode` 更新状态
3. `LayoutProvider` 触发 `window.electronAPI?.resizeWindow(targetW, targetH)`
4. CSS transition 平滑过渡窗口尺寸
5. React 条件渲染 `CompactView` 或 `FullView`
6. 两个视图共享 store，状态自动保持

**窗口尺寸：**

| 模式 | 宽度 | 高度 | 最小宽 | 最小高 |
|------|------|------|--------|--------|
| compact | 440px | 720px | 360px | 560px |
| full | 700px | 580px | 560px | 420px |

---

## 八、SettingsPanel 拆分方案

**保留模态浮层外壳，内部改为侧边导航 + 内容区。**

```
┌──────┬──────────────────────────┐
│  🌐  │  语言切换 / 外观设置      │
│  😊  │                          │
│  👤  │  ← 选中项高亮             │
│  🤖  │                          │
│  🎨  │                          │
│  🔔  │                          │
│  🎵  │                          │
│  💾  │                          │
└──────┴──────────────────────────┘
```

组件树：
```
SettingsPanel (入口，外壳 + 导航)
├─ SettingsNav          ← 左侧图标导航 (NEW)
├─ GeneralSettings      ← 语言、字体、置顶、悬浮球
├─ CharacterSettings    ← 角色列表、切换、新建、编辑
├─ UserProfileSettings  ← 用户画像编辑
├─ ModelSettings        ← Provider / Model / API Key
├─ AppearanceSettings   ← 主题色等新选项 (NEW)
├─ ProactiveSettings    ← 主动陪伴所有开关
├─ VoiceSettings        ← TTS / 声音克隆
└─ MemorySettings       ← 记忆查看（复用 MemoryViewer）
```

每个子面板独立文件，功能零丢失。

---

## 九、动画与过渡增强

| 场景 | 实现方式 | 时长 |
|------|---------|------|
| 模式切换 | CSS `view-transition` / FLIP + setBounds | 300-400ms |
| 消息出现 | `fade-in` + `slide-up`（已有，保持） | 250ms |
| 模态框打开 | `scale(0.95)→1.0` + `fade-in` | 200ms |
| 开关切换 | `transition-colors`（已有） | 150ms |
| 流式光标 | `cursor-blink`（已有） | 1s step |
| 立绘呼吸 | `breathe`（已有） | 4s |
| 未读标记 | `pulse` 脉动 | 2s infinite |
| 全息光晕 | `box-shadow` transition | 250ms |

---

## 十、实施路线图

### Phase 1：基础重构
- [ ] 确定配色方案（选项 A/B/C 择一）
- [ ] 建立全息主题 CSS 变量
- [ ] 新增 `LayoutProvider` + `layoutMode` 状态
- [ ] 创建 `CompactView` / `FullView` 容器
- [ ] 验证模式切换无数据丢失

### Phase 2：完整模式
- [ ] `ConversationSidebar`（对话列表）
- [ ] `ChatPanel` + `MessageList` + `MessageBubble`
- [ ] `CharacterAvatar` 组件
- [ ] 模式切换的窗口 resize 联动

### Phase 3：设置面板拆分
- [ ] 拆分子面板组件
- [ ] 侧边导航布局
- [ ] 保持所有配置功能不变

### Phase 4：全息视觉打磨
- [ ] 发光边框、光晕替换实色
- [ ] 立绘光柱/粒子效果（可选）
- [ ] 过渡动画 + 键盘快捷键
- [ ] ARIA 无障碍

### Phase 5：高级特性（后续迭代）
- [ ] 消息搜索
- [ ] 对话分组/标签
- [ ] 自定义主题色
- [ ] 插件/扩展系统

---

## 十一、风险与注意事项

1. **窗口 resize**：Electron `setBounds` 可能在动画期间闪烁，需要先 `hide` → `setBounds` → `show`
2. **状态保持**：模式切换时 `messages` / `streamingContent` 必须保留，注意 WebSocket Hook 不被卸载
3. **全息效果性能**：`backdrop-filter` + `box-shadow` 叠加注意 GPU 合成层数量
4. **向后兼容**：悬浮球和系统托盘逻辑不受影响
