import type { ReactNode } from 'react';
import { Network } from 'lucide-react';

import anthropicIcon from '@lobehub/icons-static-svg/icons/anthropic.svg?url';
import deepseekIcon from '@lobehub/icons-static-svg/icons/deepseek-color.svg?url';
import geminiIcon from '@lobehub/icons-static-svg/icons/gemini-color.svg?url';
import lmstudioIcon from '@lobehub/icons-static-svg/icons/lmstudio.svg?url';
import minimaxIcon from '@lobehub/icons-static-svg/icons/minimax-color.svg?url';
import ollamaIcon from '@lobehub/icons-static-svg/icons/ollama.svg?url';
import openaiIcon from '@lobehub/icons-static-svg/icons/openai.svg?url';
import openrouterIcon from '@lobehub/icons-static-svg/icons/openrouter-color.svg?url';
import qwenIcon from '@lobehub/icons-static-svg/icons/qwen-color.svg?url';
import siliconflowIcon from '@lobehub/icons-static-svg/icons/siliconcloud-color.svg?url';
import vllmIcon from '@lobehub/icons-static-svg/icons/vllm-color.svg?url';
import { chatService } from '../services/chatService';

export interface ProviderTemplate {
  key: string;
  name: string;
  base_url: string;
  icon: string | null;
}

// Legacy static templates for backward compatibility (existing provider records
// that were created before the catalog API existed still carry these keys).
const LEGACY_TEMPLATES: Record<string, ProviderTemplate> = {
  openai: {
    key: 'openai',
    name: 'OpenAI',
    base_url: 'https://api.openai.com/v1',
    icon: openaiIcon,
  },
  google: {
    key: 'google',
    name: 'Google Gemini',
    base_url: 'https://generativelanguage.googleapis.com/v1beta/openai/',
    icon: geminiIcon,
  },
  deepseek: {
    key: 'deepseek',
    name: 'DeepSeek',
    base_url: 'https://api.deepseek.com/v1',
    icon: deepseekIcon,
  },
  ollama: {
    key: 'ollama',
    name: 'Ollama (Local)',
    base_url: 'http://127.0.0.1:11434',
    icon: ollamaIcon,
  },
  lmstudio: {
    key: 'lmstudio',
    name: 'LM Studio (Local)',
    base_url: 'http://127.0.0.1:1234/v1',
    icon: lmstudioIcon,
  },
  vllm: {
    key: 'vllm',
    name: 'vLLM (Local)',
    base_url: 'http://127.0.0.1:9527/v1',
    icon: vllmIcon,
  },
  openrouter: {
    key: 'openrouter',
    name: 'OpenRouter',
    base_url: 'https://openrouter.ai/api/v1',
    icon: openrouterIcon,
  },
  siliconflow: {
    key: 'siliconflow',
    name: 'SiliconFlow',
    base_url: 'https://api.siliconflow.cn/v1',
    icon: siliconflowIcon,
  },
  minimax: {
    key: 'minimax',
    name: 'Minimax (International)',
    base_url: 'https://api.minimax.chat/v1',
    icon: minimaxIcon,
  },
  minimax_cn: {
    key: 'minimax_cn',
    name: 'Minimax (China)',
    base_url: 'https://api.minimaxi.com/v1',
    icon: minimaxIcon,
  },
  qwen: {
    key: 'qwen',
    name: 'Qwen / DashScope',
    base_url: 'https://dashscope.aliyuncs.com/compatible-mode/v1',
    icon: qwenIcon,
  },
  custom: {
    key: 'custom',
    name: 'Custom',
    base_url: '',
    icon: null,
  },
};

// Icon key → SVG mapping (used by ProviderIcon to resolve catalog icon keys).
const KEY_TO_ICON: Record<string, string | null> = {
  openai: openaiIcon,
  anthropic: anthropicIcon,
  gemini: geminiIcon,
  deepseek: deepseekIcon,
  ollama: ollamaIcon,
  lmstudio: lmstudioIcon,
  vllm: vllmIcon,
  openrouter: openrouterIcon,
  siliconflow: siliconflowIcon,
  minimax: minimaxIcon,
  qwen: qwenIcon,
};

// Cached catalog data loaded from backend API.
let _cached: {
  templates: Record<string, ProviderTemplate>;
  order: string[];
  aliases: Record<string, string | null>;
} | null = null;

/** Load provider templates from the backend catalog API. */
export async function loadProviderTemplates(): Promise<typeof _cached> {
  if (_cached) return _cached;
  try {
    const res = await chatService.getProviderTemplates();
    const templates: Record<string, ProviderTemplate> = {};
    for (const t of res.templates) {
      templates[t.key] = t;
    }
    _cached = { templates, order: res.order, aliases: res.icon_aliases };
  } catch {
    // Fallback to legacy static data if the API is unavailable.
    _cached = { templates: {}, order: [], aliases: {} };
  }
  return _cached;
}

/** Get a template by key. Checks catalog first, then legacy fallback. */
export function providerTemplate(key: string): ProviderTemplate | undefined {
  if (_cached?.templates[key]) return _cached.templates[key];
  return LEGACY_TEMPLATES[key];
}

/** Get the ordered list of provider template keys. */
export async function getProviderTemplateOrder(): Promise<string[]> {
  await loadProviderTemplates();
  if (_cached && _cached.order.length > 0) return _cached.order;
  return Object.keys(LEGACY_TEMPLATES);
}

/** Get the icon aliases map from the catalog. */
export async function getIconAliases(): Promise<Record<string, string | null>> {
  await loadProviderTemplates();
  return _cached?.aliases ?? {};
}

const PROVIDER_ICON_ALIASES: Record<string, string | null> = {
  anthropic: anthropicIcon,
  claude: anthropicIcon,
  custom: null,
  dashscope: qwenIcon,
  minimax: minimaxIcon,
  minimax_cn: minimaxIcon,
  gemini: geminiIcon,
  google: geminiIcon,
  lm_studio: lmstudioIcon,
  lmstudio: lmstudioIcon,
  openai_compatible: null,
  siliconcloud: siliconflowIcon,
  siliconflow: siliconflowIcon,
};

export function ProviderIcon({ type, size = 16 }: { type: string; size?: number }): ReactNode {
  // 1. Check catalog icon key → SVG
  if (_cached) {
    const iconKey = _cached.templates[type]?.icon ?? _cached.aliases[type];
    if (iconKey && KEY_TO_ICON[iconKey]) {
      return (
        <img
          alt=""
          aria-hidden="true"
          src={KEY_TO_ICON[iconKey]}
          className="provider-icon-img"
          style={{ width: size, height: size }}
        />
      );
    }
  }
  // 2. Check legacy templates directly
  const legacyIcon = LEGACY_TEMPLATES[type]?.icon;
  if (legacyIcon) {
    return (
      <img
        alt=""
        aria-hidden="true"
        src={legacyIcon}
        className="provider-icon-img"
        style={{ width: size, height: size }}
      />
    );
  }
  // 3. Check icon aliases
  const aliasIcon = PROVIDER_ICON_ALIASES[type];
  if (aliasIcon) {
    return (
      <img
        alt=""
        aria-hidden="true"
        src={aliasIcon}
        className="provider-icon-img"
        style={{ width: size, height: size }}
      />
    );
  }
  // 4. Fallback
  return (
    <span
      aria-hidden="true"
      className="provider-icon-fallback"
      style={{ width: size, height: size }}
    >
      <Network size={Math.max(12, size - 4)} />
    </span>
  );
}
