export {};

declare global {
  interface Window {
    electronAPI?: {
      getAppVersion: () => Promise<string>;
      setAlwaysOnTop: (flag: boolean) => Promise<void>;
      minimize: () => Promise<void>;
      minimizeWindow: () => Promise<void>;
      windowMinimize: () => Promise<void>;
      hide: () => Promise<void>;
      hideWindow: () => Promise<void>;
      windowHide: () => Promise<void>;
      close: () => Promise<void>;
      closeWindow: () => Promise<void>;
      windowClose: () => Promise<void>;
      setFloatingEnabled: (flag: boolean) => Promise<void>;
      setFloatingAvatar: (info: {
        url?: string;
        initial?: string;
      }) => Promise<boolean>;
      refreshFloatingAvatar: () => Promise<void>;
      notifyProactiveReply: (payload: {
        title?: string;
        body?: string;
        enabled?: boolean;
      }) => Promise<boolean>;
      setContentProtection: (enable: boolean) => Promise<boolean>;
      resizeWindow: (width: number, height: number) => Promise<void>;
      getWindowBounds: () => Promise<{ width: number; height: number }>;
      onWindowVisibilityChanged: (cb: (value: { visible: boolean }) => void) => void;
      selectFolder: () => Promise<string | null>;
    };
  }
}
