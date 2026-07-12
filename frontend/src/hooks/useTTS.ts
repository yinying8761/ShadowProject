import { useEffect, useRef } from 'react';
import { useChatStore } from '../stores/chatStore';
import { useAppStore } from '../stores/appStore';
import { speak, stop } from '../services/tts';

/**
 * Auto-speak assistant messages when they arrive (if TTS is enabled).
 * Also exposes manual replay via chatStore.speakMessage.
 */
export function useTTS() {
  const messages = useChatStore((s) => s.messages);
  const ttsEnabled = useAppStore((s) => s.config.ttsEnabled !== false);
  const activeChar = useAppStore((s) => s.activeCharacter);
  const spokenIds = useRef<Set<string>>(new Set());

  // Auto-speak new assistant messages
  useEffect(() => {
    if (!ttsEnabled || !activeChar) return;
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
  }, [messages, ttsEnabled, activeChar]);

  // Register manual speak trigger on chatStore
  useEffect(() => {
    useChatStore.setState({
      speakMessage: (content: string) => {
        stop();
        if (activeChar) speak(content, activeChar.id);
      },
      stopSpeaking: () => stop(),
    });
    return () => {
      useChatStore.setState({ speakMessage: null, stopSpeaking: null });
    };
  }, [activeChar]);
}
