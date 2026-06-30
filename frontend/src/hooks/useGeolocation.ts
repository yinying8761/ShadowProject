import { useEffect } from 'react';
import { useChatStore } from '../stores/chatStore';

/**
 * On mount, request browser geolocation and send coordinates to backend.
 * Only fires once per session. Requires HTTPS or localhost in Electron.
 */
export function useGeolocation() {
  useEffect(() => {
    if (!('geolocation' in navigator)) {
      console.log('[Geo] browser does not support geolocation');
      return;
    }

    const sendCoords = (lat: number, lng: number) => {
      const fn = useChatStore.getState().wsSendJson;
      if (!fn) {
        console.log('[Geo] ws not ready, retrying in 2s');
        setTimeout(() => sendCoords(lat, lng), 2000);
        return;
      }
      fn({ type: 'update_location', lat, lng });
      console.log('[Geo] sent coords:', lat.toFixed(4), lng.toFixed(4));
    };

    navigator.geolocation.getCurrentPosition(
      (pos) => {
        sendCoords(pos.coords.latitude, pos.coords.longitude);
      },
      (err) => {
        console.log('[Geo] permission denied or error:', err.message);
      },
      { enableHighAccuracy: false, timeout: 10000, maximumAge: 600000 }, // cache 10 min
    );
  }, []);
}
