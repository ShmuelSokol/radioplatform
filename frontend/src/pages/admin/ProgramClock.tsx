import { useState, useMemo, useCallback } from 'react';
import { useStations, useUpdateStation } from '../../hooks/useStations';
import { useStationRules, useCreateRule, useUpdateRule, useDeleteRule } from '../../hooks/useRules';
import type { ScheduleRule, Station } from '../../types';

const DAYS = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'] as const;
const HOURS = Array.from({ length: 24 }, (_, i) => i);
const ALL_MINUTES = [0, 5, 10, 15, 20, 25, 30, 35, 40, 45, 50, 55];
const RULE_TYPES = ['daypart', 'rotation', 'interval', 'fixed_time'];
const ASSET_TYPES = ['music', 'spot', 'shiur', 'jingle', 'zmanim'];

const TYPE_COLORS: Record<string, string> = {
  music: 'bg-cyan-800/60 border-cyan-600',
  shiur: 'bg-purple-800/60 border-purple-600',
  spot: 'bg-orange-800/60 border-orange-600',
  jingle: 'bg-yellow-800/60 border-yellow-600',
  zmanim: 'bg-green-800/60 border-green-600',
};
const TYPE_TEXT: Record<string, string> = {
  music: 'text-cyan-300',
  shiur: 'text-purple-300',
  spot: 'text-orange-300',
  jingle: 'text-yellow-300',
  zmanim: 'text-green-300',
};

function formatHour(h: number): string {
  if (h === 0 || h === 24) return '12 AM';
  if (h === 12) return '12 PM';
  return h < 12 ? `${h} AM` : `${h - 12} PM`;
}

type PanelMode = 'config' | 'edit' | 'create';

interface RuleFormData {
  name: string;
  rule_type: string;
  asset_type: string;
  category: string;
  hour_start: number;
  hour_end: number;
  days_of_week: string;
  priority: number;
  is_active: boolean;
  songs_between: number | null;
  interval_minutes: number | null;
}

function emptyForm(hour?: number, dayIdx?: number): RuleFormData {
  return {
    name: '',
    rule_type: 'daypart',
    asset_type: 'music',
    category: '',
    hour_start: hour ?? 0,
    hour_end: hour != null ? hour + 1 : 24,
    days_of_week: dayIdx != null ? String(dayIdx) : '0,1,2,3,4,5,6',
    priority: 10,
    is_active: true,
    songs_between: null,
    interval_minutes: null,
  };
}

function ruleToForm(r: ScheduleRule): RuleFormData {
  return {
    name: r.name,
    rule_type: r.rule_type,
    asset_type: r.asset_type,
    category: r.category ?? '',
    hour_start: r.hour_start,
    hour_end: r.hour_end,
    days_of_week: r.days_of_week,
    priority: r.priority,
    is_active: r.is_active,
    songs_between: r.songs_between,
    interval_minutes: r.interval_minutes,
  };
}

export default function ProgramClock() {
  const { data: stationData } = useStations();
  const stations = stationData?.stations ?? [];
  const [stationId, setStationId] = useState<string | null>(null);
  const [selectedDay, setSelectedDay] = useState(() => new Date().getDay());
  const [panelMode, setPanelMode] = useState<PanelMode>('config');
  const [selectedRuleId, setSelectedRuleId] = useState<string | null>(null);
  const [form, setForm] = useState<RuleFormData>(emptyForm());
  const [saving, setSaving] = useState(false);

  const station: Station | undefined = stations.find((s: Station) => s.id === stationId);
  const autoConfig = station?.automation_config ?? {};

  // Auto-select first station
  if (!stationId && stations.length > 0) {
    setStationId(stations[0].id);
  }

  const { data: rulesData, isLoading: rulesLoading } = useStationRules(stationId);
  const createRule = useCreateRule();
  const updateRule = useUpdateRule();
  const deleteRule = useDeleteRule();
  const updateStation = useUpdateStation();

  // Filter rules by selected day
  const dayRules = useMemo(() => {
    if (!rulesData?.rules) return [];
    return rulesData.rules.filter((r: ScheduleRule) => {
      const days = r.days_of_week.split(',').map(Number);
      return days.includes(selectedDay);
    });
  }, [rulesData, selectedDay]);

  // Build hour map: for each hour, find highest-priority rule
  const hourMap = useMemo(() => {
    const map: Record<number, ScheduleRule | null> = {};
    for (const h of HOURS) {
      const matching = dayRules
        .filter((r: ScheduleRule) => r.is_active && h >= r.hour_start && h < r.hour_end)
        .sort((a: ScheduleRule, b: ScheduleRule) => b.priority - a.priority);
      map[h] = matching[0] ?? null;
    }
    return map;
  }, [dayRules]);

  const adMinutes: number[] = autoConfig.ad_slot_minutes ?? [15, 30, 45];
  const hourlyAnnouncement = autoConfig.hourly_time_announcement ?? false;
  const weatherEnabled = autoConfig.weather_enabled ?? false;

  const handleClickHour = useCallback((h: number) => {
    const rule = hourMap[h];
    if (rule) {
      setSelectedRuleId(rule.id);
      setForm(ruleToForm(rule));
      setPanelMode('edit');
    } else {
      setSelectedRuleId(null);
      setForm(emptyForm(h, selectedDay));
      setPanelMode('create');
    }
  }, [hourMap, selectedDay]);

  const handleSaveRule = async () => {
    if (!stationId || saving) return;
    setSaving(true);
    const payload: Partial<ScheduleRule> = {
      ...form,
      category: form.category || null,
      station_id: stationId,
    };
    try {
      if (panelMode === 'edit' && selectedRuleId) {
        await updateRule.mutateAsync({ id: selectedRuleId, data: payload });
      } else {
        await createRule.mutateAsync(payload);
      }
      setPanelMode('config');
      setSelectedRuleId(null);
    } finally {
      setSaving(false);
    }
  };

  const handleDeleteRule = async () => {
    if (!selectedRuleId || saving) return;
    if (!confirm('Delete this rule?')) return;
    setSaving(true);
    try {
      await deleteRule.mutateAsync(selectedRuleId);
      setPanelMode('config');
      setSelectedRuleId(null);
    } finally {
      setSaving(false);
    }
  };

  const handleSaveAutoConfig = async (patch: Record<string, unknown>) => {
    if (!stationId) return;
    const merged = { ...autoConfig, ...patch };
    await updateStation.mutateAsync({ id: stationId, data: { automation_config: merged } });
  };

  const toggleAdMinute = (m: number) => {
    const next = adMinutes.includes(m) ? adMinutes.filter((x: number) => x !== m) : [...adMinutes, m].sort((a, b) => a - b);
    handleSaveAutoConfig({ ad_slot_minutes: next });
  };

  const updateFormField = <K extends keyof RuleFormData>(key: K, value: RuleFormData[K]) => {
    setForm(prev => ({ ...prev, [key]: value }));
  };

  const toggleFormDay = (dayIdx: number) => {
    const days = form.days_of_week ? form.days_of_week.split(',').map(Number) : [];
    const next = days.includes(dayIdx) ? days.filter(d => d !== dayIdx) : [...days, dayIdx].sort((a, b) => a - b);
    updateFormField('days_of_week', next.join(','));
  };

  return (
    <div className="min-h-screen bg-[#0a0a1a] text-white p-4 md:p-6">
      {/* Header */}
      <div className="flex flex-col md:flex-row md:items-center justify-between gap-4 mb-6">
        <h1 className="text-2xl font-bold text-cyan-300">Program Clock</h1>
        <div className="flex flex-wrap items-center gap-3">
          {/* Station picker */}
          <select
            value={stationId ?? ''}
            onChange={e => setStationId(e.target.value || null)}
            className="bg-[#12123a] border border-[#2a2a5e] rounded px-3 py-1.5 text-sm text-cyan-200"
          >
            {stations.map((s: Station) => (
              <option key={s.id} value={s.id}>{s.name}</option>
            ))}
          </select>
          {/* Day tabs */}
          <div className="flex gap-1">
            {DAYS.map((d, i) => (
              <button
                key={d}
                onClick={() => setSelectedDay(i)}
                className={`px-2.5 py-1 text-xs rounded font-medium transition ${
                  i === selectedDay
                    ? 'bg-cyan-600 text-white'
                    : 'bg-[#1a1a3e] text-gray-400 hover:text-white hover:bg-[#2a2a5e]'
                }`}
              >
                {d}
              </button>
            ))}
          </div>
        </div>
      </div>

      {/* Legend */}
      <div className="flex flex-wrap gap-3 mb-4 text-[11px]">
        {ASSET_TYPES.map(t => (
          <span key={t} className={`px-2 py-0.5 rounded border ${TYPE_COLORS[t]} ${TYPE_TEXT[t]}`}>
            {t}
          </span>
        ))}
        <span className="text-orange-400">◆ ad slot</span>
        <span className="text-blue-400">⏰ announcement</span>
      </div>

      {/* Main layout */}
      <div className="flex flex-col lg:flex-row gap-4">
        {/* Timeline */}
        <div className="flex-1 min-w-0">
          {rulesLoading ? (
            <div className="text-gray-500 text-center py-10">Loading rules...</div>
          ) : (
            <div className="space-y-1">
              {HOURS.map(h => {
                const rule = hourMap[h];
                const at = rule?.asset_type ?? 'none';
                const colorClass = rule ? TYPE_COLORS[at] ?? 'bg-gray-800/40 border-gray-700' : 'bg-gray-800/30 border-gray-700/50';
                const textClass = rule ? TYPE_TEXT[at] ?? 'text-gray-300' : 'text-gray-600';
                const isSelected = panelMode !== 'config' && rule?.id === selectedRuleId;

                return (
                  <button
                    key={h}
                    onClick={() => handleClickHour(h)}
                    className={`w-full flex items-center gap-3 px-3 py-2 rounded border transition hover:brightness-125 text-left ${colorClass} ${
                      isSelected ? 'ring-2 ring-cyan-400' : ''
                    }`}
                  >
                    {/* Hour label */}
                    <span className="w-16 text-xs font-mono text-gray-400 flex-shrink-0">{formatHour(h)}</span>

                    {/* Content */}
                    <span className={`flex-1 text-sm font-medium truncate ${textClass}`}>
                      {rule ? `${rule.name}` : 'No rule — default fill'}
                    </span>

                    {/* Rule metadata */}
                    {rule && (
                      <span className="text-[10px] text-gray-500 flex-shrink-0">
                        {rule.asset_type} · p{rule.priority}
                      </span>
                    )}

                    {/* Markers */}
                    <div className="flex items-center gap-1 flex-shrink-0">
                      {adMinutes.length > 0 && (
                        <span className="text-orange-400 text-[10px]" title={`Ads at :${adMinutes.join(', :')}`}>
                          {adMinutes.map((_, i) => <span key={i}>◆</span>)}
                        </span>
                      )}
                      {(hourlyAnnouncement || weatherEnabled) && (
                        <span className="text-blue-400 text-xs" title="Hourly announcement">⏰</span>
                      )}
                    </div>
                  </button>
                );
              })}
            </div>
          )}
        </div>

        {/* Side Panel */}
        <div className="w-full lg:w-96 flex-shrink-0">
          <div className="bg-[#12123a] border border-[#2a2a5e] rounded-lg p-4 sticky top-4">
            {/* Panel tabs */}
            <div className="flex gap-2 mb-4">
              <button
                onClick={() => { setPanelMode('config'); setSelectedRuleId(null); }}
                className={`px-3 py-1 text-xs rounded font-medium transition ${
                  panelMode === 'config' ? 'bg-purple-700 text-white' : 'bg-[#1a1a3e] text-gray-400 hover:text-white'
                }`}
              >
                ⚙ Automation
              </button>
              <button
                onClick={() => { setPanelMode('create'); setSelectedRuleId(null); setForm(emptyForm(undefined, selectedDay)); }}
                className={`px-3 py-1 text-xs rounded font-medium transition ${
                  panelMode === 'create' ? 'bg-cyan-700 text-white' : 'bg-[#1a1a3e] text-gray-400 hover:text-white'
                }`}
              >
                + New Rule
              </button>
            </div>

            {/* Automation Config Panel */}
            {panelMode === 'config' && (
              <div className="space-y-4">
                <h3 className="text-sm font-bold text-purple-300">Automation Config</h3>

                {/* Toggles */}
                <div className="space-y-2">
                  {[
                    { key: 'hourly_time_announcement', label: 'Hourly Time Announcement' },
                    { key: 'weather_enabled', label: 'Weather Reports' },
                    { key: 'requests_only', label: 'Requests Only Mode' },
                    { key: 'oldies_only', label: 'Oldies Only Mode' },
                  ].map(({ key, label }) => (
                    <label key={key} className="flex items-center gap-2 cursor-pointer">
                      <input
                        type="checkbox"
                        checked={!!autoConfig[key]}
                        onChange={e => handleSaveAutoConfig({ [key]: e.target.checked })}
                        className="accent-cyan-500"
                      />
                      <span className="text-sm text-gray-300">{label}</span>
                    </label>
                  ))}
                </div>

                {autoConfig.oldies_only && (
                  <div>
                    <label className="block text-xs text-gray-400 mb-1">Oldies Min Years</label>
                    <input
                      type="number"
                      value={autoConfig.oldies_min_years ?? 5}
                      onChange={e => handleSaveAutoConfig({ oldies_min_years: Number(e.target.value) })}
                      className="w-20 bg-[#0a0a1a] border border-[#2a2a5e] rounded px-2 py-1 text-sm text-white"
                    />
                  </div>
                )}

                {/* Ad Slot Minutes */}
                <div>
                  <h4 className="text-xs font-bold text-orange-300 mb-2">Ad Slot Minutes</h4>
                  <div className="flex flex-wrap gap-1.5">
                    {ALL_MINUTES.map(m => (
                      <button
                        key={m}
                        onClick={() => toggleAdMinute(m)}
                        className={`px-2 py-1 text-xs rounded font-mono transition ${
                          adMinutes.includes(m)
                            ? 'bg-orange-700 text-orange-100 ring-1 ring-orange-500'
                            : 'bg-[#1a1a3e] text-gray-500 hover:text-gray-300'
                        }`}
                      >
                        :{String(m).padStart(2, '0')}
                      </button>
                    ))}
                  </div>
                </div>

                {/* Ad Asset Title */}
                <div>
                  <label className="block text-xs text-gray-400 mb-1">Ad Asset Title</label>
                  <input
                    type="text"
                    value={autoConfig.ad_slot_asset_title ?? 'KOL BRAMAH TEST SPONSOR'}
                    onChange={e => handleSaveAutoConfig({ ad_slot_asset_title: e.target.value })}
                    className="w-full bg-[#0a0a1a] border border-[#2a2a5e] rounded px-2 py-1 text-sm text-white"
                    placeholder="Sponsor asset title"
                  />
                </div>
              </div>
            )}

            {/* Rule Editor Panel */}
            {(panelMode === 'edit' || panelMode === 'create') && (
              <div className="space-y-3">
                <h3 className="text-sm font-bold text-cyan-300">
                  {panelMode === 'edit' ? 'Edit Rule' : 'New Rule'}
                </h3>

                {/* Name */}
                <div>
                  <label className="block text-xs text-gray-400 mb-1">Name</label>
                  <input
                    type="text"
                    value={form.name}
                    onChange={e => updateFormField('name', e.target.value)}
                    className="w-full bg-[#0a0a1a] border border-[#2a2a5e] rounded px-2 py-1 text-sm text-white"
                    placeholder="e.g. Morning Music"
                  />
                </div>

                {/* Rule Type + Asset Type */}
                <div className="grid grid-cols-2 gap-2">
                  <div>
                    <label className="block text-xs text-gray-400 mb-1">Rule Type</label>
                    <select
                      value={form.rule_type}
                      onChange={e => updateFormField('rule_type', e.target.value)}
                      className="w-full bg-[#0a0a1a] border border-[#2a2a5e] rounded px-2 py-1 text-sm text-white"
                    >
                      {RULE_TYPES.map(t => <option key={t} value={t}>{t}</option>)}
                    </select>
                  </div>
                  <div>
                    <label className="block text-xs text-gray-400 mb-1">Asset Type</label>
                    <select
                      value={form.asset_type}
                      onChange={e => updateFormField('asset_type', e.target.value)}
                      className="w-full bg-[#0a0a1a] border border-[#2a2a5e] rounded px-2 py-1 text-sm text-white"
                    >
                      {ASSET_TYPES.map(t => <option key={t} value={t}>{t}</option>)}
                    </select>
                  </div>
                </div>

                {/* Category */}
                <div>
                  <label className="block text-xs text-gray-400 mb-1">Category (optional)</label>
                  <input
                    type="text"
                    value={form.category}
                    onChange={e => updateFormField('category', e.target.value)}
                    className="w-full bg-[#0a0a1a] border border-[#2a2a5e] rounded px-2 py-1 text-sm text-white"
                    placeholder="e.g. pop, classical"
                  />
                </div>

                {/* Hour range */}
                <div className="grid grid-cols-2 gap-2">
                  <div>
                    <label className="block text-xs text-gray-400 mb-1">Hour Start</label>
                    <input
                      type="number"
                      min={0}
                      max={23}
                      value={form.hour_start}
                      onChange={e => updateFormField('hour_start', Number(e.target.value))}
                      className="w-full bg-[#0a0a1a] border border-[#2a2a5e] rounded px-2 py-1 text-sm text-white"
                    />
                  </div>
                  <div>
                    <label className="block text-xs text-gray-400 mb-1">Hour End</label>
                    <input
                      type="number"
                      min={1}
                      max={24}
                      value={form.hour_end}
                      onChange={e => updateFormField('hour_end', Number(e.target.value))}
                      className="w-full bg-[#0a0a1a] border border-[#2a2a5e] rounded px-2 py-1 text-sm text-white"
                    />
                  </div>
                </div>

                {/* Visual hour range bar */}
                <div className="h-3 bg-[#0a0a1a] rounded-full flex overflow-hidden border border-[#2a2a5e]">
                  {HOURS.map(h => (
                    <div
                      key={h}
                      className={`flex-1 ${
                        h >= form.hour_start && h < form.hour_end
                          ? TYPE_COLORS[form.asset_type]?.replace('border-', 'border-t-') || 'bg-cyan-800/60'
                          : ''
                      }`}
                      title={formatHour(h)}
                    />
                  ))}
                </div>
                <div className="flex justify-between text-[9px] text-gray-600 -mt-1">
                  <span>12AM</span><span>6AM</span><span>12PM</span><span>6PM</span><span>12AM</span>
                </div>

                {/* Days of week */}
                <div>
                  <label className="block text-xs text-gray-400 mb-1">Days of Week</label>
                  <div className="flex gap-1">
                    {DAYS.map((d, i) => {
                      const active = form.days_of_week.split(',').map(Number).includes(i);
                      return (
                        <button
                          key={d}
                          onClick={() => toggleFormDay(i)}
                          className={`px-2 py-1 text-xs rounded font-medium transition ${
                            active
                              ? 'bg-cyan-600 text-white'
                              : 'bg-[#1a1a3e] text-gray-500 hover:text-gray-300'
                          }`}
                        >
                          {d}
                        </button>
                      );
                    })}
                  </div>
                </div>

                {/* Priority + Active */}
                <div className="grid grid-cols-2 gap-2">
                  <div>
                    <label className="block text-xs text-gray-400 mb-1">Priority</label>
                    <input
                      type="number"
                      min={0}
                      max={100}
                      value={form.priority}
                      onChange={e => updateFormField('priority', Number(e.target.value))}
                      className="w-full bg-[#0a0a1a] border border-[#2a2a5e] rounded px-2 py-1 text-sm text-white"
                    />
                  </div>
                  <div className="flex items-end pb-1">
                    <label className="flex items-center gap-2 cursor-pointer">
                      <input
                        type="checkbox"
                        checked={form.is_active}
                        onChange={e => updateFormField('is_active', e.target.checked)}
                        className="accent-cyan-500"
                      />
                      <span className="text-sm text-gray-300">Active</span>
                    </label>
                  </div>
                </div>

                {/* Conditional fields */}
                {form.rule_type === 'rotation' && (
                  <div>
                    <label className="block text-xs text-gray-400 mb-1">Songs Between</label>
                    <input
                      type="number"
                      min={0}
                      value={form.songs_between ?? 0}
                      onChange={e => updateFormField('songs_between', Number(e.target.value))}
                      className="w-full bg-[#0a0a1a] border border-[#2a2a5e] rounded px-2 py-1 text-sm text-white"
                    />
                  </div>
                )}
                {form.rule_type === 'interval' && (
                  <div>
                    <label className="block text-xs text-gray-400 mb-1">Interval Minutes</label>
                    <input
                      type="number"
                      min={1}
                      value={form.interval_minutes ?? 15}
                      onChange={e => updateFormField('interval_minutes', Number(e.target.value))}
                      className="w-full bg-[#0a0a1a] border border-[#2a2a5e] rounded px-2 py-1 text-sm text-white"
                    />
                  </div>
                )}

                {/* Action buttons */}
                <div className="flex gap-2 pt-2">
                  <button
                    onClick={handleSaveRule}
                    disabled={saving || !form.name}
                    className="flex-1 bg-cyan-700 hover:bg-cyan-600 disabled:opacity-40 text-white text-sm font-medium py-2 rounded transition"
                  >
                    {saving ? 'Saving...' : panelMode === 'edit' ? 'Update Rule' : 'Create Rule'}
                  </button>
                  {panelMode === 'edit' && (
                    <button
                      onClick={handleDeleteRule}
                      disabled={saving}
                      className="bg-red-900 hover:bg-red-800 text-red-300 text-sm px-3 py-2 rounded transition"
                    >
                      Delete
                    </button>
                  )}
                  <button
                    onClick={() => { setPanelMode('config'); setSelectedRuleId(null); }}
                    className="bg-[#1a1a3e] hover:bg-[#2a2a5e] text-gray-400 text-sm px-3 py-2 rounded transition"
                  >
                    Cancel
                  </button>
                </div>
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
