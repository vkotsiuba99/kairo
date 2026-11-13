import { useState, useEffect, useCallback } from 'react';
import { Masthead } from './components/Masthead';
import { StudioTab } from './components/StudioTab';
import { OverviewTab } from './components/OverviewTab';
import { DemandTab } from './components/DemandTab';
import { ExperimentsTab } from './components/ExperimentsTab';
import { AudienceTab } from './components/AudienceTab';
import { ChannelsTab } from './components/ChannelsTab';
import { HelpTab } from './components/HelpTab';
import { SchedulerTab } from './components/SchedulerTab';
import { LogsTab } from './components/LogsTab';
import { api, type DashboardMetrics } from './api/client';

export function App() {
  const [activeTab, setActiveTab] = useState<string>('studio');
  const [metrics, setMetrics] = useState<DashboardMetrics | null>(null);

  const loadMetrics = useCallback(async () => {
    try {
      setMetrics(await api.getDashboardMetrics());
    } catch (e) {
      console.error('Metrics fetch error:', e);
    }
  }, []);

  useEffect(() => {
    loadMetrics();
    const interval = setInterval(loadMetrics, 15000);
    return () => clearInterval(interval);
  }, [loadMetrics]);

  return (
    <div className="min-h-screen flex flex-col">
      <div className="stage-glow" aria-hidden />
      <Masthead activeTab={activeTab} setActiveTab={setActiveTab} metrics={metrics} />

      <main className="flex-1 w-full max-w-6xl mx-auto px-6 py-12">
        {activeTab === 'studio' && <StudioTab onPublished={loadMetrics} />}
        {activeTab === 'overview' && <OverviewTab metrics={metrics} setActiveTab={setActiveTab} />}
        {activeTab === 'demand' && <DemandTab />}
        {activeTab === 'experiments' && <ExperimentsTab />}
        {activeTab === 'audience' && <AudienceTab />}
        {activeTab === 'scheduler' && <SchedulerTab setActiveTab={setActiveTab} />}
        {activeTab === 'logs' && <LogsTab />}
        {activeTab === 'channels' && <ChannelsTab />}
        {activeTab === 'help' && <HelpTab />}
      </main>

      <footer className="border-t border-[color:var(--hairline)] py-6 text-xs text-faint">
        <div className="max-w-6xl mx-auto px-6 flex flex-wrap items-center justify-between gap-3">
          <span>
            Kairo — social media content generation powered by a factorized
            contextual bandit
          </span>
          <span className="num">master’s thesis · 2026</span>
        </div>
      </footer>
    </div>
  );
}

export default App;
