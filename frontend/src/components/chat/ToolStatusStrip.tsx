import { useEffect, useState } from 'react';
import { useChatStore } from '../../stores/chatStore';
import type { ToolActivity } from '../../stores/chatStore';

const TOOL_NAMES: Record<string, string> = {
  see_screen: '查看屏幕',
  get_current_time: '获取时间',
  write_file: '写入文件',
  read_file: '读取文件',
  list_directory: '列出目录',
  search_files: '搜索文件',
};

const STATUS_STYLES: Record<ToolActivity['status'], string> = {
  running: 'bg-blue-500/20 border-blue-500/40 text-blue-200',
  success: 'bg-emerald-500/15 border-emerald-500/40 text-emerald-200',
  error: 'bg-red-500/15 border-red-500/40 text-red-200',
  denied: 'bg-white/10 border-white/20 text-white/60',
};

const STATUS_ICONS: Record<ToolActivity['status'], string> = {
  running: '...',
  success: 'OK',
  error: 'ERR',
  denied: 'NO',
};

export function ToolStatusStrip() {
  const recentTools = useChatStore((s) => s.recentTools);
  const clearOldTools = useChatStore((s) => s.clearOldTools);
  const [, setTick] = useState(0);

  useEffect(() => {
    const interval = setInterval(() => {
      clearOldTools();
      setTick((t) => t + 1);
    }, 1500);
    return () => clearInterval(interval);
  }, [clearOldTools]);

  if (recentTools.length === 0) return null;

  return (
    <div className="no-drag mx-3 mb-1 flex flex-wrap gap-1.5">
      {recentTools.slice(-3).map((tool) => {
        const label = TOOL_NAMES[tool.name] || tool.name;
        const errorMsg =
          tool.status === 'error' && tool.result
            ? tool.result.length > 80
              ? tool.result.slice(0, 80) + '...'
              : tool.result
            : undefined;

        return (
          <div
            key={tool.id}
            title={errorMsg || tool.result}
            className={`flex items-center gap-1.5 px-2 py-0.5 rounded-md border text-[10px] ${STATUS_STYLES[tool.status]}`}
          >
            <span className="font-mono">{STATUS_ICONS[tool.status]}</span>
            <span>{label}</span>
            {errorMsg && <span className="opacity-70 truncate max-w-[200px]">{errorMsg}</span>}
          </div>
        );
      })}
    </div>
  );
}
