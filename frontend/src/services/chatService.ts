import type { ChatRequest, ChatResponse, RuntimeConfig } from '../types';

const BACKEND_URL = import.meta.env.VITE_COWORKER_BACKEND_URL || 'http://localhost:8000';

export interface ChatService {
  getRuntimeConfig: () => Promise<RuntimeConfig>;
  sendMessage: (request: ChatRequest) => Promise<ChatResponse>;
}

class ElectronChatService implements ChatService {
  async getRuntimeConfig(): Promise<RuntimeConfig> {
    if (!window.electronAPI) {
      throw new Error('Electron API is unavailable');
    }
    return window.electronAPI.getRuntimeConfig();
  }

  async sendMessage(request: ChatRequest): Promise<ChatResponse> {
    if (!window.electronAPI) {
      throw new Error('Electron API is unavailable');
    }
    return window.electronAPI.sendChatMessage(request);
  }
}

class HttpChatService implements ChatService {
  async getRuntimeConfig(): Promise<RuntimeConfig> {
    return this.request<RuntimeConfig>('/config');
  }

  async sendMessage(request: ChatRequest): Promise<ChatResponse> {
    return this.request<ChatResponse>('/chat', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(request),
    });
  }

  private async request<T>(path: string, init?: RequestInit): Promise<T> {
    const response = await fetch(`${BACKEND_URL}${path}`, init);
    const payload = await response.json();
    if (!response.ok) {
      throw new Error(payload.detail || `Backend returned ${response.status}`);
    }
    return payload as T;
  }
}

export function createChatService(): ChatService {
  return window.electronAPI ? new ElectronChatService() : new HttpChatService();
}

export const chatService = createChatService();
