import { useCallback, useEffect, useState } from 'react';
import { defaultPlotPreferences, plotPreferencesApi, type PlotPalette, type PlotPreferences } from '../api/plotPreferences';

const changedEvent = 'vasp-doctor:plot-preferences-changed';
export function usePlotPalette(enabled = true) {
  const [preferences, setPreferences] = useState<PlotPreferences>(defaultPlotPreferences);
  const [loading, setLoading] = useState(enabled);
  const [error, setError] = useState<string | null>(null);
  const [reloadToken, setReloadToken] = useState(0);
  useEffect(() => {
    if (!enabled) return;
    const controller = new AbortController();
    setLoading(true); setError(null);
    plotPreferencesApi.get(controller.signal).then(result => {
      if (!controller.signal.aborted) setPreferences(result.preferences);
    }).catch(err => {
      if (!controller.signal.aborted) setError(err instanceof Error ? err.message : '默认配色读取失败');
    }).finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [enabled, reloadToken]);
  useEffect(() => {
    const receive = (event: Event) => setPreferences((event as CustomEvent<PlotPreferences>).detail);
    window.addEventListener(changedEvent, receive);
    return () => window.removeEventListener(changedEvent, receive);
  }, []);
  const saveDefault = useCallback(async (palette: PlotPalette) => {
    const result = await plotPreferencesApi.save(preferences.revision, {
      preset: palette.preset, colors: palette.colors.map(color => color.toUpperCase()),
    });
    setPreferences(result.preferences);
    window.dispatchEvent(new CustomEvent(changedEvent, { detail: result.preferences }));
    return result.preferences;
  }, [preferences.revision]);
  return { preferences, colors: preferences.palette.colors, loading, error, saveDefault,
    reload: () => setReloadToken(token => token + 1) };
}
