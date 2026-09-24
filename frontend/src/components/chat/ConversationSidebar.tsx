import { useAppStore } from '../../stores/appStore';
import { useChatStore } from '../../stores/chatStore';
import { useTranslation } from '../../i18n/useTranslation';
import { api } from '../../services/api';
import { characterAvatarUrl } from '../../utils/avatarUrl';
import { CharacterAvatar } from '../character/CharacterAvatar';
import { ConversationList, conversationReloadKey } from './ConversationList';

interface ConversationSidebarProps {
  onSwitchConversation: (convId: string) => void;
  onNewConversation: () => void;
}

/** 1:1 模式的左侧栏：角色头像 + 该角色的对话列表。 */
export function ConversationSidebar({ onSwitchConversation, onNewConversation }: ConversationSidebarProps) {
  const activeCharacter = useAppStore((s) => s.activeCharacter);
  const currentConversationId = useChatStore((s) => s.currentConversationId);
  const conversationListVersion = useChatStore((s) => s.conversationListVersion);
  const { t } = useTranslation();

  const avatarUrl = characterAvatarUrl(activeCharacter?.avatar_path);

  return (
    <aside className="w-[180px] flex-shrink-0 flex flex-col border-r border-companion-border/30 bg-companion-sidebar/60" role="navigation" aria-label={t('Conversations')}>
      {/* Character avatar section */}
      <div className="flex flex-col items-center gap-2 px-3 py-4 border-b border-companion-border/20">
        <CharacterAvatar name={activeCharacter?.name || 'AI'} avatarUrl={avatarUrl} />
        <span className="text-[11px] text-companion-text/70 truncate max-w-full">
          {activeCharacter?.name || 'AI'}
        </span>
      </div>

      <ConversationList
        // 没有当前角色就不拉：不带 character_id 的列表接口会把群对话也列出来
        load={() => (activeCharacter ? api.fetchConversations(activeCharacter.id) : Promise.resolve([]))}
        reloadKey={conversationReloadKey(activeCharacter?.id, currentConversationId, String(conversationListVersion))}
        currentId={currentConversationId}
        onSwitch={onSwitchConversation}
        onNew={onNewConversation}
      />
    </aside>
  );
}
