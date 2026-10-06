import React, { useState } from 'react';
import { Outlet } from 'react-router-dom';
import { useFeatureFlags } from './hooks/useApi';
import LlmSettingsModal from './components/settings/LlmSettingsModal';
import ChatPanel from './components/chat/ChatPanel';
import ScientificWorkflowShell from './components/workflow/ScientificWorkflowShell';

const App: React.FC = () => {
  const fakeHpcEnabled = useFeatureFlags().data?.ENABLE_FAKE_HPC === true;
  const [settingsOpen, setSettingsOpen] = useState(false);
  return (
    <ScientificWorkflowShell fakeHpcEnabled={fakeHpcEnabled} auxiliary={<>
      <LlmSettingsModal open={settingsOpen} onClose={() => setSettingsOpen(false)} />
      <ChatPanel onOpenSettings={() => setSettingsOpen(true)} />
    </>}><Outlet /></ScientificWorkflowShell>
  );
};

export default App;
