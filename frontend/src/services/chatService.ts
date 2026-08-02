import type {
  ChatRequest,
  ChatResponse,
  ProviderPayload,
  ProvidersListResponse,
  ProviderTestResult,
  ProviderUpdatePayload,
  RuntimeConfig,
} from '../types';

const BACKEND_URL = import.meta.env.VITE_COWORKER_BACKEND_URL || 'http://localhost:8000';

export interface ChatService {
  getRuntimeConfig: () => Promise<RuntimeConfig>;
  sendMessage: (request: ChatRequest) => Promise<ChatResponse>;
  listProviders: () => Promise<ProvidersListResponse>;
  createProvider: (request: ProviderPayload) => Promise<void>;
  updateProvider: (providerId: string, request: ProviderUpdatePayload) => Promise<void>;
  deleteProvider: (providerId: string) => Promise<void>;
  setDefaultProvider: (providerId: string, model: string) => Promise<void>;
  testProvider: (request: { base_url: string; api_key: string; model: string }) => Promise<ProviderTestResult>;
  fetchProviderModels: (request: { base_url: string; api_key: string; provider_type: string }) => Promise<{ models: string[]; error?: string }>;
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

  async listProviders(): Promise<ProvidersListResponse> {
    if (!window.electronAPI) throw new Error('Electron API is unavailable');
    return window.electronAPI.listProviders();
  }

  async createProvider(request: ProviderPayload): Promise<void> {
    if (!window.electronAPI) throw new Error('Electron API is unavailable');
    await window.electronAPI.createProvider(request);
  }

  async updateProvider(providerId: string, request: ProviderUpdatePayload): Promise<void> {
    if (!window.electronAPI) throw new Error('Electron API is unavailable');
    await window.electronAPI.updateProvider(providerId, request);
  }

  async deleteProvider(providerId: string): Promise<void> {
    if (!window.electronAPI) throw new Error('Electron API is unavailable');
    await window.electronAPI.deleteProvider(providerId);
  }

  async setDefaultProvider(providerId: string, model: string): Promise<void> {
    if (!window.electronAPI) throw new Error('Electron API is unavailable');
    await window.electronAPI.setDefaultProvider({ provider_id: providerId, model });
  }

  async testProvider(request: { base_url: string; api_key: string; model: string }): Promise<ProviderTestResult> {
    if (!window.electronAPI) throw new Error('Electron API is unavailable');
    const response = await window.electronAPI.testProvider(request);
    return response.result;
  }

  async fetchProviderModels(request: { base_url: string; api_key: string; provider_type: string }): Promise<{ models: string[]; error?: string }> {
    if (!window.electronAPI) throw new Error('Electron API is unavailable');
    const response = await window.electronAPI.fetchProviderModels(request);
    return response.error ? { models: response.models, error: response.error } : { models: response.models };
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

  async listProviders(): Promise<ProvidersListResponse> {
    return this.request<ProvidersListResponse>('/providers');
  }

  async createProvider(request: ProviderPayload): Promise<void> {
    await this.request('/providers', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(request),
    });
  }

  async updateProvider(providerId: string, request: ProviderUpdatePayload): Promise<void> {
    await this.request(`/providers/${providerId}`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(request),
    });
  }

  async deleteProvider(providerId: string): Promise<void> {
    await this.request(`/providers/${providerId}`, { method: 'DELETE' });
  }

  async setDefaultProvider(providerId: string, model: string): Promise<void> {
    await this.request('/providers/default', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ provider_id: providerId, model }),
    });
  }

  async testProvider(request: { base_url: string; api_key: string; model: string }): Promise<ProviderTestResult> {
    const response = await this.request<{ status: string; result: ProviderTestResult }>('/providers/test', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(request),
    });
    return response.result;
  }

  async fetchProviderModels(request: { base_url: string; api_key: string; provider_type: string }): Promise<{ models: string[]; error?: string }> {
    return this.request<{ status: string; models: string[]; error?: string }>('/providers/fetch-models', {
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
