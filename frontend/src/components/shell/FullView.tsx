import { useChat } from '../../hooks/useChat';
import { useAppStore } from '../../stores/appStore';
import { ConversationSidebar } from '../chat/ConversationSidebar';
import { GroupSidebar } from '../chat/GroupSidebar';
import { ChatPanel } from '../chat/ChatPanel';

/**
 * Full chat mode: sidebar conversation list + chat panel.
 * 在群里时左栏换成群侧栏（成员 + 该群的对话列表），聊天区共用同一套。
 */
export function FullView() {
  const { newConversation, switchConversation } = useChat();
  const inGroup = useAppStore((s) => s.activeGroup !== null);

  return (
    <div className="flex flex-1 overflow-hidden">
      {inGroup ? (
        <GroupSidebar
          onSwitchConversation={switchConversation}
          onNewConversation={newConversation}
        />
      ) : (
        <ConversationSidebar
          onSwitchConversation={switchConversation}
          onNewConversation={newConversation}
        />
      )}
      <ChatPanel />
    </div>
  );
}
