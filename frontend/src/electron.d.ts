import type { ChatRequest, ChatResponse, RuntimeConfig } from './types';

declare global {
  interface Window {
    electronAPI?: {
      getRuntimeConfig: () => Promise<RuntimeConfig>;
      sendChatMessage: (payload: ChatRequest) => Promise<ChatResponse>;
    };
  }
}

export {};
