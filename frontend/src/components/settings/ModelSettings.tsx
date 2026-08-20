import { useEffect, useState } from 'react';
import { useAppStore } from '../../stores/appStore';
import { useTranslation } from '../../i18n/useTranslation';
import { api } from '../../services/api';
import type { CustomProvider, ProviderPreset } from '../../types';

/**
 * Editable LLM config form (Workflow H). Provider dropdown (built-in presets +
 * custom providers + add-custom), masked key (3-state: empty=keep, clear, set),
 * model dropdown + free text, and pull-models / test / save buttons.
 */
export function ModelSettings() {
  const config = useAppStore((s) => s.config);
  const setConfig = useAppStore((s) => s.setConfig);
  const { t } = useTranslation();

  const [providers, setProviders] = useState<ProviderPreset[]>([]);
  const [customProviders, setCustomProviders] = useState<CustomProvider[]>([]);
  const [provider, setProvider] = useState(config.llmProvider);
  const [model, setModel] = useState(config.llmModel);
  const [apiKey, setApiKey] = useState('');
  const [models, setModels] = useState<string[]>([]);

  const [showAddProvider, setShowAddProvider] = useState(false);
  const [newProviderName, setNewProviderName] = useState('');
  const [newProviderUrl, setNewProviderUrl] = useState('');

  const [fetchingModels, setFetchingModels] = useState(false);
  const [testing, setTesting] = useState(false);
  const [saving, setSaving] = useState(false);
  const [modelsError, setModelsError] = useState('');
  const [testResult, setTestResult] = useState<{
    ok: boolean;
    latency_ms?: number;
    error?: string;
  } | null>(null);
  const [message, setMessage] = useState('');

  useEffect(() => {
    api
      .fetchProviders()
      .then((res) => {
        setProviders(res.providers);
        setCustomProviders(
          res.providers
            .filter((p) => p.is_custom)
            .map((p) => ({ id: p.id, name: p.name, base_url: p.base_url ?? '' })),
        );
      })
      .catch(console.error);
  }, []);

  // Keep the form in sync with the store (config is fetched once when the
  // settings panel opens, and refreshed after a save).
  useEffect(() => {
    setProvider(config.llmProvider);
    setModel(config.llmModel);
  }, [config.llmProvider, config.llmModel]);

  const resolvedBaseUrl = providers.find((p) => p.id === provider)?.base_url ?? '';

  const handleProviderChange = (id: string) => {
    setProvider(id);
    const preset = providers.find((p) => p.id === id);
    if (preset?.default_model) setModel(preset.default_model);
    setModels([]);
    setModelsError('');
    setTestResult(null);
  };

  const handleAddProvider = () => {
    const name = newProviderName.trim();
    const url = newProviderUrl.trim();
    if (!name || !url) return;
    const entry: CustomProvider = { id: name, name, base_url: url };
    setCustomProviders((prev) => [...prev, entry]);
    setProviders((prev) => [
      ...prev.filter((p) => p.id !== name),
      { id: name, name, base_url: url, default_model: null, sdk_type: 'openai', is_custom: true },
    ]);
    setProvider(name);
    setModel('');
    setNewProviderName('');
    setNewProviderUrl('');
    setShowAddProvider(false);
    setModels([]);
    setTestResult(null);
  };

  const handleFetchModels = async () => {
    setFetchingModels(true);
    setModelsError('');
    try {
      const res = await api.fetchModels({
        provider,
        base_url: resolvedBaseUrl || undefined,
        api_key: apiKey.trim() || undefined,
      });
      if (res.error) {
        setModelsError(res.error);
        setModels([]);
      } else {
        setModels(res.models ?? []);
      }
    } catch (e) {
      setModelsError((e as Error).message || t('Model list fetch failed'));
    } finally {
      setFetchingModels(false);
    }
  };

  const handleTest = async () => {
    setTesting(true);
    setTestResult(null);
    try {
      const res = await api.testConnection({
        provider,
        base_url: resolvedBaseUrl || undefined,
        api_key: apiKey.trim() || undefined,
        model: model || '',
      });
      setTestResult(res);
    } catch (e) {
      setTestResult({ ok: false, error: (e as Error).message || t('Connection failed') });
    } finally {
      setTesting(false);
    }
  };

  const handleSave = async () => {
    setSaving(true);
    setMessage('');
    try {
      await api.updateLlmConfig({
        llm_provider: provider,
        llm_model: model,
        base_url: resolvedBaseUrl,
        api_key: apiKey.trim() ? apiKey.trim() : null, // null = keep existing key
        custom_providers: customProviders,
      });
      setConfig({
        llmProvider: provider,
        llmModel: model,
        hasApiKey: apiKey.trim() ? true : config.hasApiKey,
        apiKeyHint: apiKey.trim() ? apiKey.trim().slice(-4) : config.apiKeyHint,
      });
      setApiKey('');
      setMessage(t('Saved'));
    } catch (e) {
      setMessage((e as Error).message || t('Failed to save'));
    } finally {
      setSaving(false);
    }
  };

  const handleClearKey = async () => {
    setMessage('');
    try {
      await api.updateLlmConfig({ api_key: '' });
      setConfig({ hasApiKey: false, apiKeyHint: undefined });
      setApiKey('');
      setMessage(t('Key cleared'));
    } catch (e) {
      setMessage((e as Error).message || t('Failed to save'));
    }
  };

  return (
    <div className="space-y-3">
      {/* Provider */}
      <div className="rounded-lg border border-white/10 bg-black/30 p-3">
        <label className="mb-1 block text-xs text-white/50">{t('Provider')}</label>
        <select
          value={provider}
          onChange={(e) => handleProviderChange(e.target.value)}
          className="w-full rounded-md border border-white/10 bg-white/5 px-2 py-1.5 text-xs text-white/90"
        >
          {providers.map((p) => (
            <option key={p.id} value={p.id} className="bg-neutral-900">
              {p.name}
            </option>
          ))}
        </select>

        <button
          onClick={() => setShowAddProvider((v) => !v)}
          className="mt-2 text-[11px] text-companion-accent/80 transition-colors hover:text-companion-accent"
        >
          + {t('Add custom provider')}
        </button>

        {showAddProvider && (
          <div className="mt-2 space-y-2 border-t border-white/10 pt-2">
            <input
              value={newProviderName}
              onChange={(e) => setNewProviderName(e.target.value)}
              placeholder={t('Provider name')}
              className="w-full rounded-md border border-white/10 bg-white/5 px-2 py-1.5 text-xs text-white/90 placeholder:text-white/30"
            />
            <input
              value={newProviderUrl}
              onChange={(e) => setNewProviderUrl(e.target.value)}
              placeholder={t('Provider URL (e.g. https://x/v1)')}
              className="w-full rounded-md border border-white/10 bg-white/5 px-2 py-1.5 text-xs text-white/90 placeholder:text-white/30"
            />
            <button
              onClick={handleAddProvider}
              className="rounded-md border border-white/15 bg-white/5 px-3 py-1 text-xs text-white/80 transition-colors hover:bg-white/10"
            >
              {t('Add')}
            </button>
          </div>
        )}
      </div>

      {/* API Key */}
      <div className="rounded-lg border border-white/10 bg-black/30 p-3">
        <div className="mb-1 flex items-center justify-between">
          <label className="text-xs text-white/50">{t('API Key')}</label>
          {config.apiKeyHint ? (
            <span className="text-[10px] text-emerald-400">
              {t('Configured (tail {hint})', { hint: config.apiKeyHint })}
            </span>
          ) : config.hasApiKey ? (
            <span className="text-[10px] text-emerald-400">{t('Configured')}</span>
          ) : (
            <span className="text-[10px] text-red-400">{t('Missing')}</span>
          )}
        </div>
        <input
          type="password"
          value={apiKey}
          onChange={(e) => setApiKey(e.target.value)}
          placeholder={t('Leave empty to keep the current key')}
          className="w-full rounded-md border border-white/10 bg-white/5 px-2 py-1.5 text-xs text-white/90 placeholder:text-white/30"
        />
        {config.apiKeyHint && (
          <button
            onClick={handleClearKey}
            className="mt-1.5 text-[10px] text-white/40 transition-colors hover:text-red-400"
          >
            {t('Clear key')}
          </button>
        )}
      </div>

      {/* Model */}
      <div className="rounded-lg border border-white/10 bg-black/30 p-3">
        <label className="mb-1 block text-xs text-white/50">{t('Model')}</label>
        <input
          list="llm-model-options"
          value={model}
          onChange={(e) => setModel(e.target.value)}
          placeholder={t('Select or type a model')}
          className="w-full rounded-md border border-white/10 bg-white/5 px-2 py-1.5 text-xs text-white/90 placeholder:text-white/30"
        />
        <datalist id="llm-model-options">
          {models.map((m) => (
            <option key={m} value={m} />
          ))}
        </datalist>
      </div>

      {/* Buttons */}
      <div className="flex flex-wrap gap-2">
        <button
          onClick={handleFetchModels}
          disabled={fetchingModels}
          className="rounded-md border border-white/15 bg-white/5 px-3 py-1.5 text-xs text-white/80 transition-colors hover:bg-white/10 disabled:opacity-50"
        >
          {fetchingModels ? t('Checking') : t('Pull model list')}
        </button>
        <button
          onClick={handleTest}
          disabled={testing}
          className="rounded-md border border-white/15 bg-white/5 px-3 py-1.5 text-xs text-white/80 transition-colors hover:bg-white/10 disabled:opacity-50"
        >
          {testing ? t('Checking') : t('Test Connection')}
        </button>
        <button
          onClick={handleSave}
          disabled={saving}
          className="rounded-md border border-companion-accent/40 bg-companion-accent/20 px-3 py-1.5 text-xs text-companion-accent transition-colors hover:bg-companion-accent/30 disabled:opacity-50"
        >
          {saving ? t('Saving...') : t('Save')}
        </button>
      </div>

      {/* Feedback */}
      {modelsError && <p className="text-[11px] text-red-400">{modelsError}</p>}
      {testResult && (
        <div
          className={`rounded-md border p-2 text-[11px] ${
            testResult.ok
              ? 'border-emerald-500/30 bg-emerald-500/10 text-emerald-400'
              : 'border-red-500/30 bg-red-500/10 text-red-400'
          }`}
        >
          {testResult.ok
            ? t('Connection OK') +
              (testResult.latency_ms != null ? ` (${testResult.latency_ms} ms)` : '')
            : testResult.error || t('Connection failed')}
        </div>
      )}
      {message && <p className="text-[11px] text-white/60">{message}</p>}
    </div>
  );
}
