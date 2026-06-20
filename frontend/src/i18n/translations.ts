export type Lang = 'zh' | 'en';
export type TranslationDict = Record<string, string>;

const zh: TranslationDict = {
  // Settings
  Settings: '设置',
  'Check connection': '检测连接',
  Connected: '已连接',
  Offline: '离线',
  Checking: '检测中...',
  Characters: '角色',
  '+ New': '+ 新建',
  'No characters yet': '还没有角色',
  Edit: '编辑',
  Model: '模型',
  Provider: '供应商',
  'API Key': 'API 密钥',
  Configured: '已配置',
  Missing: '未配置',
  'Edit `backend/.env` to change provider, model, and API key.':
    '编辑 `backend/.env` 来修改供应商、模型和 API 密钥。',
  Appearance: '外观',
  'Font Size': '字体大小',
  'Always On Top': '始终置顶',
  'Floating Desktop Button': '桌面悬浮按钮',
  'Show a floating avatar button after minimizing.': '最小化后显示桌面悬浮头像按钮。',
  'Proactive Chat': '主动聊天',
  'After a period of silence, {name} may start a conversation first.':
    '一段静默时间后，{name} 可能会主动发起对话。',
  Off: '关闭',
  Low: '低',
  Medium: '中',
  High: '高',
  'Never proactive': '不主动',
  '8-15 minutes': '8-15 分钟',
  '4-8 minutes': '4-8 分钟',
  '2-5 minutes': '2-5 分钟',
  'Daily Idle Trigger Limit': '每日空闲触发上限',
  'This limit only applies to silence-based proactive messages.':
    '此限制仅适用于基于静默的主动消息。',
  'Fixed Schedule Check-ins': '固定时间签到',
  'Trigger at 07:00, 12:00, and 18:00 without consuming the daily limit.':
    '在 07:00、12:00、18:00 触发，不消耗每日限额。',
  'Auto Check Screen Before Proactive Reply': '主动回复前自动查看屏幕',
  'Gather the latest screen context before composing a proactive message.':
    '在撰写主动消息前获取最新屏幕上下文。',
  'Skip Approval For Proactive Tools': '主动工具调用免确认',
  'Only affects proactive flows. Regular manual tool calls still ask for approval.':
    '仅影响主动流程。常规手动工具调用仍需要确认。',
  'System Notification For Proactive Reply': '主动回复系统通知',
  'When minimized or hidden, show a system notification for proactive replies.':
    '最小化或隐藏时，为主动回复显示系统通知。',
  Close: '关闭',
  Language: '语言',
  Chinese: '中文',
  English: 'English',

  // Character editor
  'Edit Character': '编辑角色',
  'Create Character': '创建角色',
  'Name *': '名称 *',
  Name: '名称',
  Gender: '性别',
  'Not set': '未设置',
  Female: '女',
  Male: '男',
  Role: '角色',
  Archetype: '原型',
  'Voice Style': '语音风格',
  'e.g. warm and friendly': '例如：温柔亲切',
  Personality: '性格',
  'Describe the character\'s personality in detail...': '详细描述角色的性格...',
  Delete: '删除',
  Save: '保存',
  'Saving...': '保存中...',
  Cancel: '取消',
  'Delete "{name}"? This cannot be undone.': '删除 "{name}"？此操作不可撤销。',
  'Failed to save': '保存失败',
  'Failed to delete': '删除失败',

  // Approval dialog
  'Approval Required': '需要确认',
  'AI wants to run this action': 'AI 想要执行此操作',
  'Tool:': '工具：',
  'Arguments:': '参数：',
  'Deny': '拒绝',
  'Allow': '允许执行',

  // Tool labels
  'Write File': '写入文件',
  'Read File': '读取文件',
  'List Directory': '列出目录',
  'Search Files': '搜索文件',
  'See Screen': '查看屏幕',
  'This will modify a file on your computer.': '此操作将修改您计算机上的文件。',
  'AI will capture your current screen and send it to the vision model.':
    'AI 将截取当前屏幕并发送给视觉模型分析。',
  'Target path:': '目标路径：',
  'No specific focus.': '无特定关注点。',
  Focus: '关注点',

  // DialogueBox
  'Say hi to {name}～': '和 {name} 打个招呼吧～',
  'Speaking…': '正在说话…',
  'Proactive': '💭 主动',

  // InputBar
  '{name} is thinking…': '{name} 正在思考…',
  'Say something to {name}…': '对 {name} 说点什么…',
  'Select a character first': '请先选择角色',
  '+ New Conversation': '+ 新对话',
  'Live': '在线',
  'Offline (input)': '离线',
  'Let her see your screen': '让她看看你屏幕',

  // HistoryOverlay
  'Chat with {name}': '与 {name} 的对话',
  'New Conversation': '新对话',
  'No messages yet': '还没有对话记录',

  // CharacterDisplay
  'Please select or create a character': '请选择或创建一个角色',

  // TitleBar
  'History': '历史消息',
  'Unpin': '取消置顶',
  'Pin': '始终置顶',
  'Minimize to tray': '最小化到托盘',

  // Memory viewer
  'View Memories': '查看记忆',
  'Memories': '记忆',
  'Memory saved': '记忆已保存',
  '{count} new memories saved': '{count} 条新记忆已保存',
  'View': '查看',
  'No memories yet': '还没有记忆',
  'Export JSON': '导出 JSON',
  'Export TXT': '导出 TXT',
  'User Fact': '用户事实',
  'User Preference': '用户偏好',
  'Important Event': '重要事件',
  'Importance': '重要性',
  'Memory Type': '类型',
  'Access Count': '访问次数',
  'Last Accessed': '最后访问',
  'Created': '创建时间',

  // General
  'AI Companion': 'AI 伴侣',
};

const en: TranslationDict = {};

// English is the source language; keys are already English so en dict is empty.
// The t() function returns the key itself when no translation exists for the current lang.

export function getTranslation(key: string, lang: Lang): string {
  if (lang === 'en') return key;
  return zh[key] ?? key;
}
