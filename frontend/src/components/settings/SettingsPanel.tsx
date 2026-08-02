import { useEffect, useState, useRef } from 'react';
import { useAppStore } from '../../stores/appStore';
import { useChatStore } from '../../stores/chatStore';
import { useCharacters } from '../../hooks/useCharacters';
import { useTranslation } from '../../i18n/useTranslation';
import { api } from '../../services/api';
import { VoiceClonePanel } from './VoiceClonePanel';
import type { UserProfile } from '../../types';

export function SettingsPanel() {
  const showSettings = useAppStore((s) => s.showSettings);
  const setShowSettings = useAppStore((s) => s.setShowSettings);
  const config = useAppStore((s) => s.config);
  const setConfig = useAppStore((s) => s.setConfig);
  const openCharacterEditor = useAppStore((s) => s.openCharacterEditor);
  const setShowMemoryViewer = useChatStore((s) => s.setShowMemoryViewer);
  const { characters, activeCharacter, switchCharacter } = useCharacters();
  const { t, language } = useTranslation();

  const [healthStatus, setHealthStatus] = useState<string>('');
  const [testing, setTesting] = useState(false);

  // UserProfile state
  const [profile, setProfile] = useState<Partial<UserProfile>>({
    user_name: '',
    user_gender: null,
    user_occupation: '',
    user_relationship: 'friend',
    user_bio: '',
  });
  const [profileLoaded, setProfileLoaded] = useState(false);
  const [profileSaving, setProfileSaving] = useState(false);
  const saveTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    if (showSettings && activeCharacter) {
      api.fetchUserProfile(activeCharacter.id)
        .then((p) => {
          setProfile({
            user_name: p.user_name,
            user_gender: p.user_gender,
            user_occupation: p.user_occupation,
            user_relationship: p.user_relationship,
            user_bio: p.user_bio,
          });
          setProfileLoaded(true);
        })
        .catch(() => setProfileLoaded(true));
    }
  }, [showSettings, activeCharacter]);

  // Debounced auto-save whenever profile changes after initial load
  useEffect(() => {
    if (!profileLoaded || !activeCharacter) return;
    if (saveTimer.current) clearTimeout(saveTimer.current);
    saveTimer.current = setTimeout(() => {
      setProfileSaving(true);
      api.updateUserProfile(activeCharacter.id, profile)
        .catch(console.error)
        .finally(() => setProfileSaving(false));
    }, 600);
    return () => { if (saveTimer.current) clearTimeout(saveTimer.current); };
  }, [profile, profileLoaded, activeCharacter]);

  const updateProfile = (patch: Partial<UserProfile>) => {
    setProfile((prev) => ({ ...prev, ...patch }));
  };

  useEffect(() => {
    if (showSettings) {
      api
        .healthCheck()
        .then(() => setHealthStatus(t('Connected')))
        .catch(() => setHealthStatus(t('Offline')));
      api.fetchConfig().then((c) => setConfig(c));
    }
    // Only re-run when settings panel opens/closes
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [showSettings]);

  const handleTestConnection = async () => {
    setTesting(true);
    try {
      await api.healthCheck();
      setHealthStatus(t('Connected'));
    } catch {
      setHealthStatus(t('Offline'));
    }
    setTesting(false);
  };

  const handleLanguageToggle = () => {
    const next = language === 'zh' ? 'en' : 'zh';
    setConfig({ language: next });
    api.updateConfig({ language: next });
  };

  if (!showSettings) return null;

  return (
    <div
      className="no-drag fixed inset-0 z-50 flex items-center justify-center bg-black/70 backdrop-blur-sm"
      onClick={() => setShowSettings(false)}
    >
      <div
        className="max-h-[88vh] w-[440px] space-y-5 overflow-y-auto rounded-2xl border border-white/10 bg-companion-overlay-strong p-6 shadow-2xl"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between">
          <h2 className="text-lg font-semibold text-white">{t('Settings')}</h2>
          <button
            onClick={handleTestConnection}
            className="text-xs text-white/50 transition-colors hover:text-white/90"
          >
            {testing ? t('Checking') : healthStatus || t('Check connection')}
          </button>
        </div>

        {/* ---- Language ---- */}
        <section className="space-y-2 rounded-lg border border-white/10 bg-black/30 p-3">
          <div className="flex items-center justify-between">
            <span className="text-xs text-white/60">{t('Language')}</span>
            <button
              onClick={handleLanguageToggle}
              className="flex items-center gap-1.5 rounded-md border border-white/15 bg-white/5 px-3 py-1 text-xs text-white/80 transition-colors hover:bg-white/10"
            >
              <span className={language === 'zh' ? 'text-companion-accent' : 'text-white/40'}>
                {t('Chinese')}
              </span>
              <span className="text-white/30">|</span>
              <span className={language === 'en' ? 'text-companion-accent' : 'text-white/40'}>
                {t('English')}
              </span>
            </button>
          </div>
        </section>

        {/* ---- Characters ---- */}
        <section className="space-y-2">
          <div className="flex items-center justify-between">
            <label className="text-xs font-medium text-white/60">{t('Characters')}</label>
            <button
              onClick={() => openCharacterEditor(null)}
              className="text-xs text-companion-accent transition-colors hover:text-companion-accent-hover"
            >
              {t('+ New')}
            </button>
          </div>
          <div className="space-y-1.5">
            {characters.map((char) => (
              <div
                key={char.id}
                className={`flex items-center justify-between rounded-lg border px-3 py-2 transition-colors ${
                  activeCharacter?.id === char.id
                    ? 'border-companion-accent/40 bg-companion-accent/15'
                    : 'border-white/10 bg-white/5 hover:bg-white/10'
                }`}
              >
                <button onClick={() => switchCharacter(char)} className="flex-1 text-left">
                  <div className="text-sm text-white">{char.name}</div>
                  <div className="mt-0.5 text-[10px] text-white/40">{char.archetype}</div>
                </button>
                <button
                  onClick={() => openCharacterEditor(char)}
                  className="rounded px-2 py-1 text-[10px] text-white/50 hover:text-white"
                >
                  {t('Edit')}
                </button>
              </div>
            ))}
            {characters.length === 0 && (
              <div className="py-3 text-center text-xs text-white/40">{t('No characters yet')}</div>
            )}
          </div>
        </section>

        {/* ---- User Profile ---- */}
        <section className="space-y-3 rounded-lg border border-white/10 bg-black/30 p-3">
          <div className="flex items-center justify-between">
            <span className="text-xs font-medium text-white/60">{t('Your Profile')}</span>
            {profileSaving && (
              <span className="text-[10px] text-companion-accent">{t('Saving...')}</span>
            )}
          </div>

          <div>
            <label className="mb-1 block text-[10px] text-white/50">{t('Name')}</label>
            <input
              type="text"
              value={profile.user_name || ''}
              onChange={(e) => updateProfile({ user_name: e.target.value })}
              placeholder={t('Your name')}
              className="w-full rounded-md border border-white/15 bg-white/5 px-3 py-1.5 text-xs text-white placeholder-white/30 focus:border-companion-accent/50 focus:outline-none"
            />
          </div>

          <div>
            <label className="mb-1 block text-[10px] text-white/50">{t('Gender')}</label>
            <select
              value={profile.user_gender || ''}
              onChange={(e) => updateProfile({ user_gender: e.target.value || null })}
              className="w-full rounded-md border border-white/15 bg-white/5 px-3 py-1.5 text-xs text-white focus:border-companion-accent/50 focus:outline-none"
            >
              <option value="">{t('Not set')}</option>
              <option value="男">{t('Male')}</option>
              <option value="女">{t('Female')}</option>
              <option value="其他">{t('Other')}</option>
            </select>
          </div>

          <div>
            <label className="mb-1 block text-[10px] text-white/50">{t('Identity')}</label>
            <input
              type="text"
              value={profile.user_occupation || ''}
              onChange={(e) => updateProfile({ user_occupation: e.target.value })}
              placeholder={t('e.g. 大学生, 打工人, 自由职业...')}
              className="w-full rounded-md border border-white/15 bg-white/5 px-3 py-1.5 text-xs text-white placeholder-white/30 focus:border-companion-accent/50 focus:outline-none"
            />
          </div>

          <div>
            <label className="mb-1 block text-[10px] text-white/50">{t('Relationship')}</label>
            <input
              type="text"
              value={profile.user_relationship || ''}
              onChange={(e) => updateProfile({ user_relationship: e.target.value })}
              placeholder={t('e.g. 朋友, 助手和用户, 伙伴...')}
              className="w-full rounded-md border border-white/15 bg-white/5 px-3 py-1.5 text-xs text-white placeholder-white/30 focus:border-companion-accent/50 focus:outline-none"
            />
          </div>

          <div>
            <label className="mb-1 block text-[10px] text-white/50">{t('Self-introduction')}</label>
            <textarea
              rows={4}
              value={profile.user_bio || ''}
              onChange={(e) => updateProfile({ user_bio: e.target.value })}
              placeholder={t('Tell the AI about yourself — interests, hobbies, what you do...')}
              className="w-full resize-none rounded-md border border-white/15 bg-white/5 px-3 py-1.5 text-xs text-white placeholder-white/30 focus:border-companion-accent/50 focus:outline-none"
            />
          </div>

          <p className="text-[10px] text-white/40">
            {t('Each character can have a different profile. Switch characters to edit theirs.')}
          </p>
        </section>

        {/* ---- Model ---- */}
        <section className="space-y-2 rounded-lg border border-white/10 bg-black/30 p-3">
          <div className="mb-1 text-xs font-medium text-white/60">{t('Model')}</div>
          <div className="flex justify-between text-xs">
            <span className="text-white/50">{t('Provider')}</span>
            <span className="text-white/90">{config.llmProvider}</span>
          </div>
          <div className="flex justify-between text-xs">
            <span className="text-white/50">{t('Model')}</span>
            <span className="text-white/90">{config.llmModel}</span>
          </div>
          <div className="flex justify-between text-xs">
            <span className="text-white/50">{t('API Key')}</span>
            <span className={config.hasApiKey ? 'text-emerald-400' : 'text-red-400'}>
              {config.hasApiKey ? t('Configured') : t('Missing')}
            </span>
          </div>
          <p className="border-t border-white/10 pt-1 text-[10px] text-white/40">
            {t('Edit `backend/.env` to change provider, model, and API key.')}
          </p>
        </section>

        {/* ---- Appearance ---- */}
        <section className="space-y-3">
          <div className="text-xs font-medium text-white/60">{t('Appearance')}</div>

          <div>
            <label className="mb-1 block text-xs text-white/50">
              {t('Font Size')} ({config.fontSize}px)
            </label>
            <input
              type="range"
              min="12"
              max="20"
              value={config.fontSize}
              onChange={(e) => {
                const v = parseInt(e.target.value, 10);
                setConfig({ fontSize: v });
                api.updateConfig({ font_size: v });
              }}
              className="w-full accent-companion-accent"
            />
          </div>

          <div className="flex items-center justify-between">
            <span className="text-xs text-white/60">{t('Always On Top')}</span>
            <button
              onClick={() => {
                const v = !config.alwaysOnTop;
                setConfig({ alwaysOnTop: v });
                api.updateConfig({ always_on_top: v });
                window.electronAPI?.setAlwaysOnTop(v);
              }}
              className={`h-5 w-10 rounded-full transition-colors ${
                config.alwaysOnTop ? 'bg-companion-accent' : 'bg-white/20'
              }`}
            >
              <div
                className={`mx-0.5 h-4 w-4 rounded-full bg-white transition-transform ${
                  config.alwaysOnTop ? 'translate-x-5' : ''
                }`}
              />
            </button>
          </div>

          <div className="flex items-center justify-between">
            <div>
              <div className="text-xs text-white/60">{t('Floating Desktop Button')}</div>
              <div className="mt-0.5 text-[10px] text-white/40">
                {t('Show a floating avatar button after minimizing.')}
              </div>
            </div>
            <button
              onClick={() => {
                const v = !config.showFloatingIcon;
                setConfig({ showFloatingIcon: v });
                api.updateConfig({ show_floating_icon: v });
                window.electronAPI?.setFloatingEnabled(v);
              }}
              className={`h-5 w-10 flex-shrink-0 rounded-full transition-colors ${
                config.showFloatingIcon ? 'bg-companion-accent' : 'bg-white/20'
              }`}
            >
              <div
                className={`mx-0.5 h-4 w-4 rounded-full bg-white transition-transform ${
                  config.showFloatingIcon ? 'translate-x-5' : ''
                }`}
              />
            </button>
          </div>
        </section>

        {/* ---- Proactive Chat ---- */}
        <section className="space-y-3">
          <div className="text-xs font-medium text-white/60">{t('Proactive Chat')}</div>

          <div>
            <div className="mb-2 text-[10px] text-white/40">
              {t('After a period of silence, {name} may start a conversation first.', {
                name: activeCharacter?.name || t('AI Companion'),
              })}
            </div>
            <div className="grid grid-cols-4 gap-1">
              {[
                { v: 'off', label: t('Off'), hint: t('Never proactive') },
                { v: 'low', label: t('Low'), hint: t('8-15 minutes') },
                { v: 'medium', label: t('Medium'), hint: t('4-8 minutes') },
                { v: 'high', label: t('High'), hint: t('2-5 minutes') },
              ].map((opt) => (
                <button
                  key={opt.v}
                  onClick={() => {
                    const v = opt.v as 'off' | 'low' | 'medium' | 'high';
                    setConfig({ proactiveChatLevel: v });
                    api.updateConfig({ proactive_chat_level: v });
                  }}
                  title={opt.hint}
                  className={`rounded-md border px-2 py-1.5 text-[11px] transition-colors ${
                    config.proactiveChatLevel === opt.v
                      ? 'border-companion-accent/40 bg-companion-accent/20 text-companion-accent'
                      : 'border-white/10 bg-white/5 text-white/60 hover:bg-white/10'
                  }`}
                >
                  {opt.label}
                </button>
              ))}
            </div>
          </div>

          <div>
            <label className="mb-1 block text-xs text-white/50">
              {t('Daily Idle Trigger Limit')} ({config.proactiveDailyLimit})
            </label>
            <input
              type="range"
              min="0"
              max="30"
              value={config.proactiveDailyLimit}
              onChange={(e) => {
                const v = parseInt(e.target.value, 10);
                setConfig({ proactiveDailyLimit: v });
                api.updateConfig({ proactive_daily_limit: v });
              }}
              className="w-full accent-companion-accent"
            />
            <div className="mt-1 text-[10px] text-white/40">
              {t('This limit only applies to silence-based proactive messages.')}
            </div>
          </div>

          <div className="flex items-center justify-between">
            <div>
              <div className="text-xs text-white/60">{t('Fixed Schedule Check-ins')}</div>
              <div className="mt-0.5 text-[10px] text-white/40">
                {t('Trigger at 07:00, 12:00, and 18:00 without consuming the daily limit.')}
              </div>
            </div>
            <button
              onClick={() => {
                const v = !config.proactiveFixedScheduleEnabled;
                setConfig({ proactiveFixedScheduleEnabled: v });
                api.updateConfig({ proactive_fixed_schedule_enabled: v });
              }}
              className={`h-5 w-10 flex-shrink-0 rounded-full transition-colors ${
                config.proactiveFixedScheduleEnabled ? 'bg-companion-accent' : 'bg-white/20'
              }`}
            >
              <div
                className={`mx-0.5 h-4 w-4 rounded-full bg-white transition-transform ${
                  config.proactiveFixedScheduleEnabled ? 'translate-x-5' : ''
                }`}
              />
            </button>
          </div>

          <div className="flex items-center justify-between">
            <div>
              <div className="text-xs text-white/60">{t('Auto Check Screen Before Proactive Reply')}</div>
              <div className="mt-0.5 text-[10px] text-white/40">
                {t('Gather the latest screen context before composing a proactive message.')}
              </div>
            </div>
            <button
              onClick={() => {
                const v = !config.proactiveAutoSeeScreen;
                setConfig({ proactiveAutoSeeScreen: v });
                api.updateConfig({ proactive_auto_see_screen: v });
              }}
              className={`h-5 w-10 flex-shrink-0 rounded-full transition-colors ${
                config.proactiveAutoSeeScreen ? 'bg-companion-accent' : 'bg-white/20'
              }`}
            >
              <div
                className={`mx-0.5 h-4 w-4 rounded-full bg-white transition-transform ${
                  config.proactiveAutoSeeScreen ? 'translate-x-5' : ''
                }`}
              />
            </button>
          </div>

          <div className="flex items-center justify-between">
            <div>
              <div className="text-xs text-white/60">{t('Skip Approval For Proactive Tools')}</div>
              <div className="mt-0.5 text-[10px] text-white/40">
                {t('Only affects proactive flows. Regular manual tool calls still ask for approval.')}
              </div>
            </div>
            <button
              onClick={() => {
                const v = !config.proactiveSilentToolApproval;
                setConfig({ proactiveSilentToolApproval: v });
                api.updateConfig({ proactive_silent_tool_approval: v });
              }}
              className={`h-5 w-10 flex-shrink-0 rounded-full transition-colors ${
                config.proactiveSilentToolApproval ? 'bg-companion-accent' : 'bg-white/20'
              }`}
            >
              <div
                className={`mx-0.5 h-4 w-4 rounded-full bg-white transition-transform ${
                  config.proactiveSilentToolApproval ? 'translate-x-5' : ''
                }`}
              />
            </button>
          </div>

          <div className="flex items-center justify-between">
            <div>
              <div className="text-xs text-white/60">{t('System Notification For Proactive Reply')}</div>
              <div className="mt-0.5 text-[10px] text-white/40">
                {t('When minimized or hidden, show a system notification for proactive replies.')}
              </div>
            </div>
            <button
              onClick={() => {
                const v = !config.proactiveSystemNotification;
                setConfig({ proactiveSystemNotification: v });
                api.updateConfig({ proactive_system_notification: v });
              }}
              className={`h-5 w-10 flex-shrink-0 rounded-full transition-colors ${
                config.proactiveSystemNotification ? 'bg-companion-accent' : 'bg-white/20'
              }`}
            >
              <div
                className={`mx-0.5 h-4 w-4 rounded-full bg-white transition-transform ${
                  config.proactiveSystemNotification ? 'translate-x-5' : ''
                }`}
              />
            </button>
          </div>
        </section>

        {/* ---- Voice ---- */}
        <section className="space-y-2 rounded-lg border border-white/10 bg-black/30 p-3">
          <div className="flex items-center justify-between">
            <div>
              <div className="text-xs text-white/60">{t('Voice (TTS)')}</div>
              <div className="mt-0.5 text-[10px] text-white/40">
                {t('Automatically read AI replies aloud. Voice adapts to character personality.')}
              </div>
            </div>
            <button
              onClick={() => {
                const v = !config.ttsEnabled;
                setConfig({ ttsEnabled: v });
                api.updateConfig({ tts_enabled: v });
              }}
              className={`h-5 w-10 flex-shrink-0 rounded-full transition-colors ${
                config.ttsEnabled ? 'bg-companion-accent' : 'bg-white/20'
              }`}
            >
              <div
                className={`mx-0.5 h-4 w-4 rounded-full bg-white transition-transform ${
                  config.ttsEnabled ? 'translate-x-5' : ''
                }`}
              />
            </button>
          </div>
          <div className="pt-1 border-t border-white/10">
            <VoiceClonePanel />
          </div>
        </section>

        {/* ---- Memory ---- */}
        <section className="space-y-2 rounded-lg border border-white/10 bg-black/30 p-3">
          <div className="text-xs font-medium text-white/60">{t('Memories')}</div>
          <button
            onClick={() => {
              setShowMemoryViewer(true);
              setShowSettings(false);
            }}
            className="w-full rounded-lg border border-white/15 bg-white/5 py-2 text-sm text-white/70 transition-colors hover:bg-white/10 hover:text-white"
          >
            {t('View Memories')}
          </button>
        </section>

        <button
          onClick={() => setShowSettings(false)}
          className="w-full rounded-lg border border-white/15 py-2 text-sm text-white/60 transition-colors hover:text-white"
        >
          {t('Close')}
        </button>
      </div>
    </div>
  );
}
