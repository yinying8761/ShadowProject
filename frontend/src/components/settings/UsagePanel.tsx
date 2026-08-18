import { useEffect, useState } from 'react';
import { useChatStore } from '../../stores/chatStore';
import { useTranslation } from '../../i18n/useTranslation';
import { api } from '../../services/api';
import type { TokenUsageResponse } from '../../types';

/**
 * Settings tab showing the current conversation's token consumption.
 *
 * Refreshes when the conversation changes and when a round finishes
 * (chatStore.isStreaming flips true → false on `done`). Pure REST — no
 * new WebSocket events.
 */
export function UsagePanel() {
  const conversationId = useChatStore((s) => s.currentConversationId);
  const isStreaming = useChatStore((s) => s.isStreaming);
  const { t } = useTranslation();
  const [data, setData] = useState<TokenUsageResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(false);

  useEffect(() => {
    // Refetch on conversation switch and on every streaming transition;
    // the true→false flip after `done` picks up the just-persisted round.
    if (!conversationId) {
      setData(null);
      setError(false);
      setLoading(false);
      return;
    }
    let cancelled = false;
    setLoading(true);
    api
      .fetchTokenUsage(conversationId)
      .then((d) => {
        if (!cancelled) {
          setData(d);
          setError(false);
        }
      })
      .catch((err) => {
        // Keep whatever was already shown; a failed refresh should not be
        // indistinguishable from "no usage yet". Matches MemoryPanel.
        if (!cancelled) {
          console.error(err);
          if (!data) setError(true);
        }
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [conversationId, isStreaming]); // eslint-disable-line react-hooks/exhaustive-deps

  if (!conversationId) {
    return (
      <div className="py-6 text-center text-xs text-companion-text/40">
        {t('Select a conversation to see token usage')}
      </div>
    );
  }

  if (loading && !data) {
    return <p className="py-4 text-xs text-companion-text/40">{t('Checking')}</p>;
  }

  if (error && !data) {
    return (
      <div className="py-6 text-center text-xs text-companion-text/40">
        {t('Failed to load usage')}
      </div>
    );
  }

  if (!data || data.summary.rounds === 0) {
    return (
      <div className="py-6 text-center text-xs text-companion-text/40">
        {t('No usage yet')}
      </div>
    );
  }

  const s = data.summary;

  return (
    <div className="space-y-3">
      {/* Summary */}
      <div className="rounded-lg border border-white/10 bg-black/30 p-3">
        <div className="mb-2 text-[10px] font-medium text-companion-accent/70">
          {t('Summary')}
        </div>
        <div className="grid grid-cols-3 gap-2 text-center">
          <div>
            <div className="text-base font-semibold text-white">
              {s.prompt_tokens.toLocaleString()}
            </div>
            <div className="text-[10px] text-companion-text/40">{t('Prompt Tokens')}</div>
          </div>
          <div>
            <div className="text-base font-semibold text-white">
              {s.completion_tokens.toLocaleString()}
            </div>
            <div className="text-[10px] text-companion-text/40">{t('Completion Tokens')}</div>
          </div>
          <div>
            <div className="text-base font-semibold text-white">
              {s.total_tokens.toLocaleString()}
            </div>
            <div className="text-[10px] text-companion-text/40">{t('Total Tokens')}</div>
          </div>
        </div>
        <div className="mt-2 text-center text-[10px] text-companion-text/40">
          {t('Rounds')}: {s.rounds}
        </div>
      </div>

      {/* Per-round detail — API returns newest-first; render oldest→newest. */}
      <div className="space-y-2">
        {[...data.usage]
          .sort((a, b) => a.round_num - b.round_num)
          .map((u) => (
            <div
              key={u.id}
              className="rounded-lg border border-white/10 bg-black/30 p-3"
            >
              <div className="flex items-center justify-between text-[10px] text-companion-text/40">
                <span>
                  {/* backend round_num is 0-based — show 1-based for humans */}
                  {t('Round')} {u.round_num + 1}
                </span>
                <span className="truncate pl-2">{u.model || '-'}</span>
              </div>
              <div className="mt-1.5 grid grid-cols-3 gap-2 text-center text-xs">
                <div>
                  <div className="text-white">{u.prompt_tokens.toLocaleString()}</div>
                  <div className="text-[10px] text-companion-text/40">{t('Prompt')}</div>
                </div>
                <div>
                  <div className="text-white">{u.completion_tokens.toLocaleString()}</div>
                  <div className="text-[10px] text-companion-text/40">{t('Completion')}</div>
                </div>
                <div>
                  <div className="text-white">{u.total_tokens.toLocaleString()}</div>
                  <div className="text-[10px] text-companion-text/40">{t('Total')}</div>
                </div>
              </div>
              <div className="mt-1.5 text-[10px] text-companion-text/40">
                {t('Estimated')} {u.estimated_prompt_tokens.toLocaleString()} ·{' '}
                {t('Actual')} {u.prompt_tokens.toLocaleString()}
              </div>
            </div>
          ))}
      </div>
    </div>
  );
}
