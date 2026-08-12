import { MessageList } from './MessageList';
import { ToolStatusStrip } from './ToolStatusStrip';
import { InputBar } from './InputBar';

export function ChatPanel() {
  return (
    <div className="flex-1 flex flex-col min-w-0">
      <MessageList />
      <div className="flex-shrink-0 pb-1">
        <ToolStatusStrip />
        <InputBar />
      </div>
    </div>
  );
}
