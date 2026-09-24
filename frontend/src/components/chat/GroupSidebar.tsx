import { useAppStore } from '../../stores/appStore';
import { useChatStore } from '../../stores/chatStore';
import { useTranslation } from '../../i18n/useTranslation';
import { api } from '../../services/api';
import { characterAvatarUrl } from '../../utils/avatarUrl';
import { speakerColor } from '../../utils/speakerStyle';
import { CharacterAvatar } from '../character/CharacterAvatar';
import { ConversationList, conversationReloadKey } from './ConversationList';

interface GroupSidebarProps {
  onSwitchConversation: (convId: string) => void;
  onNewConversation: () => void;
}

/**
 * 群聊的左侧栏：左上成员列表（含发言顺序）、中间该群的对话列表、左下「新对话」。
 * 退出群聊回到单聊时恢复进来之前的布局模式（store.leaveGroup）。
 */
export function GroupSidebar({ onSwitchConversation, onNewConversation }: GroupSidebarProps) {
  const activeGroup = useAppStore((s) => s.activeGroup);
  const characters = useAppStore((s) => s.characters);
  const leaveGroup = useAppStore((s) => s.leaveGroup);
  const currentConversationId = useChatStore((s) => s.currentConversationId);
  const conversationListVersion = useChatStore((s) => s.conversationListVersion);
  const { t } = useTranslation();

  if (!activeGroup) return null;

  const memberOf = (characterId: string) => characters.find((c) => c.id === characterId);

  return (
    <aside
      className="w-[180px] flex-shrink-0 flex flex-col border-r border-companion-border/30 bg-companion-sidebar/60"
      role="navigation"
      aria-label={t('Group Chat')}
    >
      {/* 左上：群名 + 成员（顺序即发言顺序）+ 退出 */}
      <div className="flex flex-col gap-1.5 px-3 py-3 border-b border-companion-border/20">
        <div className="flex items-center justify-between gap-1">
          <span className="text-[11px] font-medium text-companion-text/80 truncate" title={activeGroup.name}>
            {activeGroup.name}
          </span>
          <button
            onClick={leaveGroup}
            title={t('Leave Group')}
            className="flex-shrink-0 text-[10px] text-white/35 hover:text-companion-accent transition-colors"
          >
            {t('Leave')}
          </button>
        </div>

        <ul className="max-h-[38%] overflow-y-auto space-y-1">
          {activeGroup.members.map((member, index) => {
            const name =
              member.character_name || memberOf(member.character_id)?.name || t('AI Companion');
            return (
              <li key={member.character_id} className="flex items-center gap-1.5">
                <CharacterAvatar
                  name={name}
                  avatarUrl={characterAvatarUrl(memberOf(member.character_id)?.avatar_path)}
                  size="sm"
                />
                <span
                  className="flex-1 text-[11px] truncate"
                  style={{ color: speakerColor(member.character_id) }}
                >
                  {name}
                </span>
                <span className="text-[9px] text-white/25" title={t('Speaking order')}>
                  {index + 1}
                </span>
              </li>
            );
          })}
        </ul>
      </div>

      <ConversationList
        load={async () => (await api.fetchGroup(activeGroup.id)).conversations}
        reloadKey={conversationReloadKey(activeGroup.id, currentConversationId, String(conversationListVersion))}
        currentId={currentConversationId}
        onSwitch={onSwitchConversation}
        onNew={onNewConversation}
      />
    </aside>
  );
}
