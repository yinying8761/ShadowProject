import { useTranslation } from '../../i18n/useTranslation';
import { CharacterAvatar } from '../character/CharacterAvatar';
import type { SpeakerMark } from './MessageBubble';

/**
 * 群聊里某个成员"正在输入"（ticket #11）。
 *
 * 并发之后**同时会有好几个**：一个成员一个指示器，各自出现、各自消失（后端每轮思考
 * 报一次 `group_thinking{thinking}`）。视觉与 `MessageBubble` 一致 —— 说话人标识由
 * 调用方按同一套规则算好传进来（`SpeakerMark`），这里只负责画。
 */
export function GroupTypingIndicator({ speaker }: { speaker: SpeakerMark }) {
  const { t } = useTranslation();

  return (
    <div className="flex justify-start animate-fade-in">
      <div className="flex-shrink-0 mr-2 self-start">
        <CharacterAvatar
          name={speaker.name || 'AI'}
          avatarUrl={speaker.avatarUrl}
          size="sm"
        />
      </div>

      <div className="max-w-[80%] min-w-0">
        <div className="mb-0.5 px-0.5">
          <span className="text-[10px] font-medium" style={{ color: speaker.color }}>
            {speaker.name || 'AI'}
          </span>
        </div>

        <div
          className="px-3.5 py-2 rounded-2xl rounded-bl-md bg-companion-card/60 border border-companion-border/30"
          style={{ borderLeftWidth: 2, borderLeftColor: speaker.color }}
          role="status"
          aria-label={t('Typing…')}
        >
          <span className="inline-flex items-center gap-1 h-4">
            {[0, 1, 2].map((dot) => (
              <span
                key={dot}
                className="w-1.5 h-1.5 rounded-full bg-current animate-cursor-blink"
                style={{ color: speaker.color, animationDelay: `${dot * 160}ms` }}
              />
            ))}
          </span>
        </div>
      </div>
    </div>
  );
}
