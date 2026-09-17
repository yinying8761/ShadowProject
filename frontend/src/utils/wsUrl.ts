/**
 * WebSocket URL for a backend path.
 *
 * The backend port is fixed (8722) in dev and packaged builds alike; only the
 * host varies, so it comes from the page.
 */
export function wsUrl(path: string): string {
  return `ws://${window.location.hostname}:8722${path}`;
}
