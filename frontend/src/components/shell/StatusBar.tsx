import { useChatStore } from '../../stores/chatStore';
import { useAppStore } from '../../stores/appStore';

export function StatusBar() {
  const isStreaming = useChatStore((s) => s.isStreaming);
  const isConnected = useAppStore((s) => s.isConnected);
  const config = useAppStore((s) => s.config);

  return (
    <div className="flex items-center justify-between h-7 px-3 text-[10px] text-companion-text-muted border-t border-companion-border/30 bg-[#0d0d12]">
      <div className="flex items-center gap-3">
        <span className="flex items-center gap-1">
          <div className={`w-1.5 h-1.5 rounded-full ${isConnected ? 'bg-emerald-500' : 'bg-red-400'}`} />
          {isConnected ? 'Connected' : 'Disconnected'}
        </span>
        {isStreaming && <span className="animate-pulse-soft text-companion-accent">Responding...</span>}
      </div>
      <span>{config.llmProvider} / {config.llmModel}</span>
    </div>
  );
}
