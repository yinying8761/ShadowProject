import { useState, useRef } from 'react';
import { useAppStore } from '../../stores/appStore';
import { useTranslation } from '../../i18n/useTranslation';

interface CharInfo { id: string; name: string; has_voice: boolean; }

export function VoiceClonePanel() {
  const [show, setShow] = useState(false);
  const { t } = useTranslation();

  const [ttsOnline, setTtsOnline] = useState<boolean | null>(null);
  const [ttsRunning, setTtsRunning] = useState(false);
  const [controlLoading, setControlLoading] = useState(false);
  const [ttsPath, setTtsPath] = useState('');
  const [audioFile, setAudioFile] = useState<File | null>(null);
  const [promptText, setPromptText] = useState('');
  const [testText, setTestText] = useState('你好，这是我的声音测试～');
  const [generating, setGenerating] = useState(false);
  const [audioUrl, setAudioUrl] = useState<string | null>(null);
  const [refAudioPath, setRefAudioPath] = useState('');
  const [characters, setCharacters] = useState<CharInfo[]>([]);
  const [targetCharId, setTargetCharId] = useState('');
  const [applying, setApplying] = useState(false);
  const [status, setStatus] = useState('');
  const audioRef = useRef<HTMLAudioElement>(null);

  const checkStatus = async () => {
    try {
      const r = await fetch('/api/tts/status');
      const d = await r.json();
      setTtsOnline(d.connected);
      setTtsRunning(d.running);
      if (d.tts_ref_base) setTtsPath(d.tts_ref_base);
    } catch { setTtsOnline(false); }
  };

  const browsePath = async () => {
    const folder = await window.electronAPI?.selectFolder();
    if (folder) {
      setTtsPath(folder);
      await fetch('/api/tts/control/path', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ path: folder }),
      });
    }
  };

  const open = async () => {
    setShow(true);
    setStatus('');
    // Auto-start GPT-SoVITS
    setControlLoading(true);
    try {
      await fetch('/api/tts/control/start', { method: 'POST' });
    } catch { /* ignore */ }
    setControlLoading(false);
    await checkStatus();
    // Load characters
    try {
      const r = await fetch('/api/tts/characters');
      const d = await r.json();
      setCharacters(d);
    } catch { /* ignore */ }
  };

  const handleStartStop = async () => {
    setControlLoading(true);
    if (ttsRunning || ttsOnline) {
      await fetch('/api/tts/control/stop', { method: 'POST' });
    } else {
      await fetch('/api/tts/control/start', { method: 'POST' });
    }
    setControlLoading(false);
    // Wait a moment then check status
    setTimeout(checkStatus, ttsRunning ? 500 : 3000);
  };

  const handleFile = (e: React.ChangeEvent<HTMLInputElement>) => {
    const f = e.target.files?.[0];
    if (f) {
      setAudioFile(f);
      setAudioUrl(null);
      setRefAudioPath('');
    }
  };

  const handleTest = async () => {
    if (!audioFile || !promptText.trim() || !testText.trim()) return;
    setGenerating(true);
    setStatus('');
    try {
      const form = new FormData();
      form.append('file', audioFile);
      form.append('prompt_text', promptText);
      form.append('test_text', testText);
      const r = await fetch('/api/tts/clone/test', { method: 'POST', body: form });
      if (!r.ok) {
        const e = await r.json().catch(() => ({ detail: 'Unknown error' }));
        setStatus('Error: ' + (e.detail || r.statusText));
        setGenerating(false);
        return;
      }
      const blob = await r.blob();
      const url = URL.createObjectURL(blob);
      setAudioUrl(url);
      setRefAudioPath(r.headers.get('X-Ref-Audio') || '');
      setStatus('Test generated — click play to listen');
    } catch (e) {
      setStatus('Failed: ' + (e instanceof Error ? e.message : 'network error'));
    }
    setGenerating(false);
  };

  const handleApply = async () => {
    if (!targetCharId || !refAudioPath || !promptText.trim()) return;
    setApplying(true);
    setStatus('');
    try {
      const r = await fetch('/api/tts/clone/apply', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ character_id: targetCharId, ref_audio: refAudioPath, prompt_text: promptText }),
      });
      if (!r.ok) {
        const e = await r.json().catch(() => ({ detail: 'Unknown' }));
        setStatus('Error: ' + (e.detail || r.statusText));
      } else {
        const d = await r.json();
        setStatus(`Voice applied to ${d.character}!`);
        // Refresh character list
        const r2 = await fetch('/api/tts/characters');
        setCharacters(await r2.json());
      }
    } catch (e) {
      setStatus('Failed: ' + (e instanceof Error ? e.message : 'network error'));
    }
    setApplying(false);
  };

  if (!show) {
    return (
      <button
        onClick={open}
        className="text-xs text-companion-accent transition-colors hover:text-companion-accent-hover"
      >
        {t('Voice Cloning')} →
      </button>
    );
  }

  return (
    <div className="fixed inset-0 bg-black/60 flex items-center justify-center z-50" onClick={() => setShow(false)}>
      <div
        className="bg-companion-sidebar border border-companion-border rounded-2xl w-[460px] max-h-[85vh] overflow-y-auto p-6 space-y-4"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between">
          <h2 className="text-lg font-semibold text-white">{t('Voice Cloning')}</h2>
          <button onClick={() => setShow(false)} className="text-white/40 hover:text-white">✕</button>
        </div>

        {/* GPT-SoVITS status */}
        <div className="flex items-center gap-2 text-xs">
          <span className={`w-2 h-2 rounded-full ${ttsOnline ? 'bg-emerald-400' : ttsOnline === false ? 'bg-red-400' : 'bg-white/30'}`} />
          <span className="text-white/60 flex-1">
            {ttsOnline ? 'GPT-SoVITS 已连接' : ttsOnline === false ? 'GPT-SoVITS 未连接' : '检测中...'}
          </span>
          <button
            onClick={handleStartStop}
            disabled={controlLoading}
            className={`px-2 py-0.5 rounded text-[10px] transition-colors ${
              ttsRunning || ttsOnline
                ? 'bg-red-500/20 text-red-400 hover:bg-red-500/30'
                : 'bg-emerald-500/20 text-emerald-400 hover:bg-emerald-500/30'
            } disabled:opacity-40`}
          >
            {controlLoading ? '...' : ttsRunning || ttsOnline ? '关闭' : '启动'}
          </button>
        </div>

        {/* Path override */}
        <div
          className="text-[10px] text-white/30 truncate cursor-pointer hover:text-white/50 flex items-center gap-1"
          onClick={browsePath}
        >
          📁 {ttsPath || '未设置 — 点击选择 GPT-SoVITS 目录'}
        </div>

        {/* Audio file */}
        <label className="block w-full rounded-lg border-2 border-dashed border-white/20 hover:border-companion-accent/50 p-6 text-center cursor-pointer transition-colors">
          <input type="file" accept="audio/*" onChange={handleFile} className="hidden" />
          <div className="text-sm text-white/60">
            {audioFile ? audioFile.name : '点击选择参考音频（3-10秒，无背景噪音）'}
          </div>
          {audioFile && <div className="text-[10px] text-white/40 mt-1">{(audioFile.size / 1024).toFixed(0)} KB</div>}
        </label>

        {/* Prompt text */}
        <div>
          <label className="text-xs text-white/60 mb-1 block">参考音频说的内容</label>
          <input
            value={promptText}
            onChange={(e) => setPromptText(e.target.value)}
            placeholder="音频里说的是什么文本？"
            className="w-full bg-black/30 border border-white/10 rounded-lg px-3 py-2 text-sm text-white outline-none focus:border-companion-accent/50"
          />
        </div>

        {/* Test text */}
        <div>
          <label className="text-xs text-white/60 mb-1 block">测试文本（生成预览）</label>
          <input
            value={testText}
            onChange={(e) => setTestText(e.target.value)}
            className="w-full bg-black/30 border border-white/10 rounded-lg px-3 py-2 text-sm text-white outline-none focus:border-companion-accent/50"
          />
        </div>

        {/* Generate + Play */}
        <div className="flex items-center gap-3">
          <button
            onClick={handleTest}
            disabled={!audioFile || !promptText.trim() || generating}
            className="px-4 py-2 rounded-lg bg-companion-accent hover:bg-companion-accent-hover disabled:opacity-30 text-white text-sm font-medium transition-colors"
          >
            {generating ? '生成中...' : '生成测试'}
          </button>
          {audioUrl && (
            <div className="flex items-center gap-2">
              <button
                onClick={() => audioRef.current?.play()}
                className="text-sm text-companion-accent hover:text-companion-accent-hover"
              >
                ▶ 播放
              </button>
              <audio ref={audioRef} src={audioUrl} className="hidden" />
            </div>
          )}
        </div>

        {/* Status */}
        {status && (
          <div className={`text-xs ${status.startsWith('Error') || status.startsWith('Failed') ? 'text-red-400' : 'text-emerald-400'}`}>
            {status}
          </div>
        )}

        {refAudioPath && (
          <>
            <hr className="border-white/10" />
            {/* Apply to character */}
            <div>
              <label className="text-xs text-white/60 mb-1 block">应用声线到角色</label>
              <div className="flex items-center gap-2">
                <select
                  value={targetCharId}
                  onChange={(e) => setTargetCharId(e.target.value)}
                  className="flex-1 bg-black/30 border border-white/10 rounded-lg px-3 py-2 text-sm text-white outline-none"
                >
                  <option value="">选择角色...</option>
                  {characters.map((c) => (
                    <option key={c.id} value={c.id}>
                      {c.name} {c.has_voice ? '(已配语音)' : ''}
                    </option>
                  ))}
                </select>
                <button
                  onClick={handleApply}
                  disabled={!targetCharId || applying}
                  className="px-4 py-2 rounded-lg bg-emerald-600 hover:bg-emerald-700 disabled:opacity-30 text-white text-sm transition-colors whitespace-nowrap"
                >
                  {applying ? '应用中...' : '应用'}
                </button>
              </div>
            </div>
          </>
        )}

        <div className="text-[10px] text-white/30 pt-2 border-t border-white/10">
          音频上传后保存到 GPT-SoVITS references 目录。每个角色独立配置，切换角色自动换声线。
        </div>
      </div>
    </div>
  );
}
