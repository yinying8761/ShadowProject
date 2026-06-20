import { useChat } from '../../hooks/useChat';
import { useTranslation } from '../../i18n/useTranslation';

export function ApprovalDialog() {
  const { pendingApproval, sendApprovalResponse } = useChat();
  const { t } = useTranslation();

  if (!pendingApproval) return null;

  const { requestId, toolName, arguments: args } = pendingApproval;
  const label = t(toolName === 'write_file' ? 'Write File'
    : toolName === 'read_file' ? 'Read File'
    : toolName === 'list_directory' ? 'List Directory'
    : toolName === 'search_files' ? 'Search Files'
    : toolName === 'see_screen' ? 'See Screen'
    : toolName);
  const hasPath = Boolean(args.path);
  const focusText = typeof args.focus === 'string' && args.focus ? args.focus : t('No specific focus.');

  const renderArgValue = (value: unknown): string => {
    if (typeof value === 'string') {
      return value.length > 200 ? value.slice(0, 200) + '...' : value;
    }
    return JSON.stringify(value) ?? String(value);
  };

  return (
    <div className="no-drag fixed inset-0 z-[60] flex items-center justify-center bg-black/70 backdrop-blur-sm animate-fade-in">
      <div className="flex max-h-[70vh] w-[400px] flex-col overflow-hidden rounded-2xl border border-white/15 bg-[rgba(15,15,22,0.95)] shadow-2xl">
        <div className="flex items-center gap-3 border-b border-white/10 px-5 py-3">
          <div className="flex h-8 w-8 flex-shrink-0 items-center justify-center rounded-lg bg-amber-500/20">
            <svg
              width="16"
              height="16"
              viewBox="0 0 24 24"
              fill="none"
              stroke="#f59e0b"
              strokeWidth="2"
              strokeLinecap="round"
              strokeLinejoin="round"
            >
              <path d="M10.29 3.86 1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0Z" />
              <line x1="12" y1="9" x2="12" y2="13" />
              <line x1="12" y1="17" x2="12.01" y2="17" />
            </svg>
          </div>
          <div>
            <h3 className="text-sm font-semibold text-white">{t('Approval Required')}</h3>
            <p className="text-[11px] text-white/50">{t('AI wants to run this action')}</p>
          </div>
        </div>

        <div className="flex-1 space-y-3 overflow-y-auto px-5 py-4">
          <div className="flex items-center gap-2">
            <span className="text-xs text-white/50">{t('Tool:')}</span>
            <span className="rounded bg-amber-500/10 px-2 py-0.5 font-mono text-xs text-amber-400">
              {label}
            </span>
          </div>

          <div className="space-y-2">
            <span className="text-xs text-white/50">{t('Arguments:')}</span>
            <div className="overflow-x-auto rounded-lg border border-white/8 bg-black/40 p-3">
              {Object.entries(args).map(([key, value]) => (
                <div key={key} className="mb-2 last:mb-0">
                  <span className="font-mono text-[11px] text-white/60">{key}: </span>
                  <span className="break-all font-mono text-[11px] text-white/90">
                    {renderArgValue(value)}
                  </span>
                </div>
              ))}
            </div>
          </div>

          {toolName === 'write_file' && hasPath ? (
            <div className="rounded-lg border border-amber-500/20 bg-amber-500/8 px-3 py-2 text-[11px] text-amber-400/80">
              {t('Target path:')} {String(args.path)}
            </div>
          ) : null}

          {toolName === 'see_screen' ? (
            <div className="space-y-1 rounded-lg border border-amber-500/20 bg-amber-500/8 px-3 py-2 text-[11px] text-amber-400/80">
              <div>{t('AI will capture your current screen and send it to the vision model.')}</div>
              <div className="text-white/60">{t('Focus')}: {focusText}</div>
            </div>
          ) : null}
        </div>

        <div className="flex items-center justify-end gap-3 border-t border-white/10 px-5 py-3">
          <button
            onClick={() => sendApprovalResponse(requestId, false)}
            className="rounded-lg border border-white/20 px-4 py-2 text-xs text-white/70 transition-colors hover:border-white/40 hover:text-white"
          >
            {t('Deny')}
          </button>
          <button
            onClick={() => sendApprovalResponse(requestId, true)}
            className="rounded-lg bg-companion-accent px-4 py-2 text-xs font-medium text-white transition-colors hover:bg-companion-accent-hover"
          >
            {t('Allow')}
          </button>
        </div>
      </div>
    </div>
  );
}
