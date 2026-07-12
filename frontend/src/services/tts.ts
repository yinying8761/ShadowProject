const TTS_API = '/api/tts/speak';

let currentAudio: HTMLAudioElement | null = null;

export async function speak(text: string, characterId: string): Promise<boolean> {
  if (!text.trim() || !characterId) return false;
  stop();

  try {
    console.log('[TTS] requesting speak, text_len:', text.length);
    const resp = await fetch(TTS_API, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ character_id: characterId, text, speed: 1.0 }),
    });

    if (!resp.ok) {
      console.log('[TTS] API returned', resp.status);
      return false;
    }

    const blob = await resp.blob();
    console.log('[TTS] got blob, size:', blob.size, 'type:', blob.type);
    if (blob.size === 0) {
      console.log('[TTS] empty audio');
      return false;
    }

    const url = URL.createObjectURL(blob);
    const audio = new Audio(url);
    currentAudio = audio;

    return new Promise((resolve) => {
      audio.onended = () => {
        URL.revokeObjectURL(url);
        currentAudio = null;
        console.log('[TTS] playback complete');
        resolve(true);
      };
      audio.onerror = (e) => {
        console.log('[TTS] audio error:', audio.error?.message || e);
        URL.revokeObjectURL(url);
        currentAudio = null;
        resolve(false);
      };
      audio.play().catch((e) => {
        console.log('[TTS] play rejected:', e.message);
        URL.revokeObjectURL(url);
        currentAudio = null;
        resolve(false);
      });
    });
  } catch (e) {
    console.log('[TTS] request failed:', e);
    return false;
  }
}

export function stop() {
  if (currentAudio) {
    currentAudio.pause();
    currentAudio = null;
  }
}

export function speaking() {
  return currentAudio !== null && !currentAudio.paused;
}

export function isReady() { return true; }

// Voice personalization happens server-side via GPT-SoVITS reference audio
export function characterVoice() { return { voices: [], pitch: 1, rate: 1 }; }
