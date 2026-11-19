import React from 'react';
import { AsciiBackground } from '@/components/ascii-background/AsciiBackground';
import { Sidebar } from '@/components/sidebar/Sidebar';
import { ChatArea } from '@/components/chat/ChatArea';
import { SettingsPanel } from '@/components/settings/SettingsPanel';
import { useWebSocket } from '@/hooks/useWebSocket';
import { useChat } from '@/hooks/useChat';

export const App: React.FC = () => {
  // Maintain WebSocket connection
  useWebSocket();

  // Chat actions
  const { sendMessage, stopGeneration, isStreaming } = useChat();

  return (
    <div className="relative w-screen h-screen overflow-hidden flex bg-[#07080d] text-gray-100 select-none">
      {/* 1. Full-screen living ASCII background from ascii-things */}
      <AsciiBackground />

      {/* 2. Glassmorphism Application Interface Layer */}
      <div className="relative z-10 flex w-full h-full">
        {/* Left Navigation & Status Sidebar */}
        <Sidebar />

        {/* Central Chat & Coding Workspace */}
        <main className="flex-1 flex flex-col h-full overflow-hidden">
          <ChatArea
            onSendMessage={sendMessage}
            onStop={stopGeneration}
            isStreaming={isStreaming}
          />
        </main>
      </div>

      {/* 3. Settings Modal */}
      <SettingsPanel />
    </div>
  );
};

export default App;

