import React from 'react';
import type { DashboardMetrics } from '../api/client';

export const TABS = [
  { id: 'studio', label: 'Studio' },
  { id: 'overview', label: 'Overview' },
  { id: 'demand', label: 'Demand' },
  { id: 'audience', label: 'Audience' },
  { id: 'scheduler', label: 'Scheduler' },
  { id: 'experiments', label: 'Experiments' },
  { id: 'channels', label: 'Channels' },
  { id: 'logs', label: 'Activity log' },
  { id: 'help', label: 'Help' },
] as const;

export const Masthead: React.FC<{
  activeTab: string;
  setActiveTab: (t: string) => void;
  metrics: DashboardMetrics | null;
}> = ({ activeTab, setActiveTab, metrics }) => {
  const llm = metrics?.system_status?.llm_inference;
  const online = !!llm?.online;

  return (
    <header className="border-b border-[color:var(--hairline)] bg-[#ffffff]/95 backdrop-blur-sm sticky top-0 z-40">
      <div className="max-w-6xl mx-auto px-6">
        <div className="flex items-center justify-between py-5">
          <button
            onClick={() => setActiveTab('studio')}
            className="text-left group"
            aria-label="Kairo — go to home"
          >
            <div className="dim-title font-[family-name:var(--font-display)] text-[1.75rem] leading-none group-hover:text-butter transition-colors">
              KAI<span className="text-ruby group-hover:text-gold transition-colors">RO</span>
            </div>
            <div className="eyebrow mt-1.5 opacity-70">content studio</div>
          </button>

          <div className="flex items-center gap-3 text-xs">
            <span
              className={`inline-block w-1.5 h-1.5 rounded-full ${online ? 'bg-gold' : 'bg-brick'}`}
              aria-hidden
            />
            <span className="text-dim">
              {online ? 'Local model online' : 'Local model offline'}
            </span>
            <span className="num text-faint hidden sm:inline">
              {llm?.model ?? '—'}
            </span>
          </div>
        </div>

        <nav className="flex flex-wrap gap-x-7 gap-y-1">
          {TABS.map((t) => {
            const active = activeTab === t.id;
            return (
              <button
                key={t.id}
                onClick={() => setActiveTab(t.id)}
                className={`relative pb-3 text-[0.95rem] whitespace-nowrap transition-colors ${
                  active ? 'text-butter' : 'text-dim hover:text-vanilla'
                }`}
              >
                {t.label}
                {active && (
                  <span className="absolute left-0 right-0 -bottom-px h-[2px] bg-gold" />
                )}
              </button>
            );
          })}
        </nav>
      </div>
    </header>
  );
};
