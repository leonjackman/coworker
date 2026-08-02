export type AgentMode = 'single';
export type Language = 'zh' | 'en';

export interface ChatMessage {
  id: string;
  role: 'user' | 'assistant';
  content: string;
  timestamp: number;
}

export interface RuntimeConfig {
  workspace: string;
  default_mode: AgentMode;
  agent_provider: string;
  available_modes: AgentMode[];
}

export interface ChatRequest {
  message: string;
  session_id?: string;
  mode: AgentMode;
  language: Language;
}

export interface ChatResponse {
  response: string;
  session_id: string;
  mode: AgentMode;
  provider: string;
}
