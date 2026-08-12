import { useChat } from '../../hooks/useChat';
import { ConversationSidebar } from '../chat/ConversationSidebar';
import { ChatPanel } from '../chat/ChatPanel';

/**
 * Full chat mode: sidebar conversation list + chat panel.
 */
export function FullView() {
  const { newConversation, switchConversation } = useChat();

  return (
    <div className="flex flex-1 overflow-hidden">
      <ConversationSidebar
        onSwitchConversation={switchConversation}
        onNewConversation={newConversation}
      />
      <ChatPanel />
    </div>
  );
}
