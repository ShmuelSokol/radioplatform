import { create } from 'zustand';

type UiVersion = 'v1' | 'v2';

interface UiState {
  version: UiVersion;
  setVersion: (v: UiVersion) => void;
  toggleVersion: () => void;
}

const KEY = 'ui_version';

function loadVersion(): UiVersion {
  try {
    return localStorage.getItem(KEY) === 'v2' ? 'v2' : 'v1';
  } catch {
    return 'v1';
  }
}

export const useUiStore = create<UiState>((set) => ({
  version: loadVersion(),
  setVersion: (version) => {
    try { localStorage.setItem(KEY, version); } catch {}
    set({ version });
  },
  toggleVersion: () =>
    set((s) => {
      const version = s.version === 'v1' ? 'v2' : 'v1';
      try { localStorage.setItem(KEY, version); } catch {}
      return { version };
    }),
}));
