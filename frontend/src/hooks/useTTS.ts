import { useEffect, useRef } from 'react';
import { useChatStore } from '../stores/chatStore';
import { useAppStore } from '../stores/appStore';
import { speak, stop } from '../services/tts';

/**
 * Auto-speak assistant messages when they arrive (if TTS is enabled).
 * Also exposes manual replay via chatStore.speakMessage.
 */
/**
 * 群聊 TTS 预留开关（spec Out of Scope）：现在的 TTS 有延迟、多角色会语音重叠，
 * 所以群聊闭嘴；将来有了串行语音队列，把这里打开即可恢复。
 */
const GROUP_TTS_ENABLED = false;

export function useTTS() {
  const messages = useChatStore((s) => s.messages);
  const ttsEnabled = useAppStore((s) => s.config.ttsEnabled !== false);
  const activeChar = useAppStore((s) => s.activeCharacter);
  const inGroup = useAppStore((s) => s.activeGroup !== null);
  const spokenIds = useRef<Set<string>>(new Set());

  // Auto-speak new assistant messages
  useEffect(() => {
    if (!ttsEnabled || !activeChar) return;
    if (inGroup && !GROUP_TTS_ENABLED) return;  // 群聊不自动朗读（预留开关）
    const last = messages[messages.length - 1];
    if (!last || last.role !== 'assistant' || last.isProactive) return;
    if (!last.content.trim() || spokenIds.current.has(last.id)) return;

    spokenIds.current.add(last.id);
    // Keep set small
    if (spokenIds.current.size > 20) {
      const arr = [...spokenIds.current];
      spokenIds.current = new Set(arr.slice(-10));
    }

    // Small delay so the UI finishes rendering the message
    const timer = setTimeout(() => {
      speak(last.content, activeChar.id);
    }, 200);
    return () => clearTimeout(timer);
  }, [messages, ttsEnabled, activeChar, inGroup]);

  // Register manual speak trigger on chatStore
  useEffect(() => {
    useChatStore.setState({
      speakMessage: (content: string) => {
        stop();
        if (inGroup && !GROUP_TTS_ENABLED) return;  // 群聊也不支持手动重播
        if (activeChar) speak(content, activeChar.id);
      },
      stopSpeaking: () => stop(),
    });
    return () => {
      useChatStore.setState({ speakMessage: null, stopSpeaking: null });
    };
  }, [activeChar, inGroup]);
}
