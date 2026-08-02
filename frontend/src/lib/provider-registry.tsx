import type { ReactNode } from 'react';
import { Network } from 'lucide-react';

import anthropicIcon from '@lobehub/icons-static-svg/icons/anthropic.svg?url';
import deepseekIcon from '@lobehub/icons-static-svg/icons/deepseek-color.svg?url';
import geminiIcon from '@lobehub/icons-static-svg/icons/gemini-color.svg?url';
import lmstudioIcon from '@lobehub/icons-static-svg/icons/lmstudio.svg?url';
import ollamaIcon from '@lobehub/icons-static-svg/icons/ollama.svg?url';
import openaiIcon from '@lobehub/icons-static-svg/icons/openai.svg?url';
import openrouterIcon from '@lobehub/icons-static-svg/icons/openrouter-color.svg?url';
import qwenIcon from '@lobehub/icons-static-svg/icons/qwen-color.svg?url';
import siliconflowIcon from '@lobehub/icons-static-svg/icons/siliconcloud-color.svg?url';
import vllmIcon from '@lobehub/icons-static-svg/icons/vllm-color.svg?url';

export interface ProviderTemplate {
  key: string;
  name: string;
  base_url: string;
  icon: string | null;
}

export const PROVIDER_TEMPLATES: Record<string, ProviderTemplate> = {
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
    base_url: 'http://127.0.0.1:8000/v1',
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

export const PROVIDER_TEMPLATE_ORDER = [
  'openai',
  'google',
  'deepseek',
  'ollama',
  'lmstudio',
  'vllm',
  'openrouter',
  'siliconflow',
  'qwen',
  'custom',
];

const PROVIDER_ICON_ALIASES: Record<string, string | null> = {
  anthropic: anthropicIcon,
  claude: anthropicIcon,
  custom: null,
  dashscope: qwenIcon,
  gemini: geminiIcon,
  google: geminiIcon,
  lm_studio: lmstudioIcon,
  lmstudio: lmstudioIcon,
  openai_compatible: null,
  siliconcloud: siliconflowIcon,
  siliconflow: siliconflowIcon,
};

export function providerTemplate(type: string): ProviderTemplate | undefined {
  return PROVIDER_TEMPLATES[type];
}

export function ProviderIcon({ type, size = 16 }: { type: string; size?: number }): ReactNode {
  const icon = PROVIDER_TEMPLATES[type]?.icon ?? PROVIDER_ICON_ALIASES[type] ?? null;
  if (!icon) {
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
  return (
    <img
      alt=""
      aria-hidden="true"
      src={icon}
      className="provider-icon-img"
      style={{ width: size, height: size }}
    />
  );
}
