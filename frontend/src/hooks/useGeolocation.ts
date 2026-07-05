import { useEffect } from 'react';
import { useChatStore } from '../stores/chatStore';

const MAX_RETRIES = 60;

export function useGeolocation() {
  useEffect(() => {
    if (!('geolocation' in navigator)) return;

    let lat = 0;
    let lng = 0;

    navigator.geolocation.getCurrentPosition(
      (pos) => {
        lat = pos.coords.latitude;
        lng = pos.coords.longitude;
        console.log('[Geo] got coords:', lat.toFixed(4), lng.toFixed(4));
        trySend();
      },
      (err) => {
        console.log('[Geo] denied or error:', err.message);
      },
      { enableHighAccuracy: false, timeout: 10000, maximumAge: 600000 },
    );

    function trySend(attempt: number = 0) {
      if (!lat || !lng) return; // no coords yet
      const fn = useChatStore.getState().wsSendJson;
      if (!fn) {
        if (attempt < MAX_RETRIES) {
          setTimeout(() => trySend(attempt + 1), 1000);
        } else {
          console.log('[Geo] gave up after', MAX_RETRIES, 'retries');
        }
        return;
      }
      fn({ type: 'update_location', lat, lng });
      console.log('[Geo] sent successfully');
    }
  }, []);
}
