import { MessageList } from './MessageList';
import { ToolStatusStrip } from './ToolStatusStrip';
import { InputBar } from './InputBar';
import { ErrorBubble } from './ErrorBubble';
import { RetryIndicator } from './RetryIndicator';

export function ChatPanel() {
  return (
    <div className="flex-1 flex flex-col min-w-0">
      <MessageList />
      <div className="flex-shrink-0 pb-1">
        {/* Persistent error + transient retry progress, above the input. */}
        <ErrorBubble />
        <RetryIndicator />
        <ToolStatusStrip />
        <InputBar />
      </div>
    </div>
  );
}
