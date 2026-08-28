import type { ReactNode } from 'react';
import { Network } from 'lucide-react';

import anthropicIcon from '@lobehub/icons-static-svg/icons/anthropic.svg?url';
import cerebrasIcon from '@lobehub/icons-static-svg/icons/cerebras-color.svg?url';
import cohereIcon from '@lobehub/icons-static-svg/icons/cohere-color.svg?url';
import deepseekIcon from '@lobehub/icons-static-svg/icons/deepseek-color.svg?url';
import fireworksIcon from '@lobehub/icons-static-svg/icons/fireworks-color.svg?url';
import geminiIcon from '@lobehub/icons-static-svg/icons/gemini-color.svg?url';
import groqIcon from '@lobehub/icons-static-svg/icons/groq.svg?url';
import huggingfaceIcon from '@lobehub/icons-static-svg/icons/huggingface-color.svg?url';
import lmstudioIcon from '@lobehub/icons-static-svg/icons/lmstudio.svg?url';
import minimaxIcon from '@lobehub/icons-static-svg/icons/minimax-color.svg?url';
import mistralIcon from '@lobehub/icons-static-svg/icons/mistral-color.svg?url';
import nvidiaIcon from '@lobehub/icons-static-svg/icons/nvidia-color.svg?url';
import ollamaIcon from '@lobehub/icons-static-svg/icons/ollama.svg?url';
import openaiIcon from '@lobehub/icons-static-svg/icons/openai.svg?url';
import openrouterIcon from '@lobehub/icons-static-svg/icons/openrouter-color.svg?url';
import perplexityIcon from '@lobehub/icons-static-svg/icons/perplexity-color.svg?url';
import qwenIcon from '@lobehub/icons-static-svg/icons/qwen-color.svg?url';
import siliconflowIcon from '@lobehub/icons-static-svg/icons/siliconcloud-color.svg?url';
import togetherIcon from '@lobehub/icons-static-svg/icons/together-color.svg?url';
import vllmIcon from '@lobehub/icons-static-svg/icons/vllm-color.svg?url';
import alibabaIcon from '@lobehub/icons-static-svg/icons/alibaba-brand-color.svg?url';
import baichuanIcon from '@lobehub/icons-static-svg/icons/baichuan-color.svg?url';
import doubaoIcon from '@lobehub/icons-static-svg/icons/doubao-color.svg?url';
import internlmIcon from '@lobehub/icons-static-svg/icons/internlm-color.svg?url';
import kimiIcon from '@lobehub/icons-static-svg/icons/kimi-color.svg?url';
import stepfunIcon from '@lobehub/icons-static-svg/icons/stepfun-color.svg?url';
import tencentIcon from '@lobehub/icons-static-svg/icons/tencent-brand-color.svg?url';
import wenxinIcon from '@lobehub/icons-static-svg/icons/wenxin-color.svg?url';
import yiIcon from '@lobehub/icons-static-svg/icons/yi-color.svg?url';
import zhipuIcon from '@lobehub/icons-static-svg/icons/zhipu-color.svg?url';
import xaiIcon from '@lobehub/icons-static-svg/icons/xai-text.svg?url';
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
  anthropic: {
    key: 'anthropic',
    name: 'Anthropic',
    base_url: 'https://api.anthropic.com',
    icon: anthropicIcon,
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
  groq: {
    key: 'groq',
    name: 'Groq',
    base_url: 'https://api.groq.com/openai/v1',
    icon: groqIcon,
  },
  xai: {
    key: 'xai',
    name: 'xAI (Grok)',
    base_url: 'https://api.x.ai/v1',
    icon: xaiIcon,
  },
  together: {
    key: 'together',
    name: 'Together AI',
    base_url: 'https://api.together.xyz/v1',
    icon: togetherIcon,
  },
  fireworks: {
    key: 'fireworks',
    name: 'Fireworks AI',
    base_url: 'https://api.fireworks.ai/inference/v1',
    icon: fireworksIcon,
  },
  cerebras: {
    key: 'cerebras',
    name: 'Cerebras',
    base_url: 'https://api.cerebras.ai/v1',
    icon: cerebrasIcon,
  },
  nvidia: {
    key: 'nvidia',
    name: 'NVIDIA NIM',
    base_url: 'https://integrate.api.nvidia.com/v1',
    icon: nvidiaIcon,
  },
  perplexity: {
    key: 'perplexity',
    name: 'Perplexity',
    base_url: 'https://api.perplexity.ai',
    icon: perplexityIcon,
  },
  mistral: {
    key: 'mistral',
    name: 'Mistral AI',
    base_url: 'https://api.mistral.ai/v1',
    icon: mistralIcon,
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
  cohere: {
    key: 'cohere',
    name: 'Cohere',
    base_url: 'https://api.cohere.com/v2',
    icon: cohereIcon,
  },
  huggingface: {
    key: 'huggingface',
    name: 'HuggingFace Inference API',
    base_url: 'https://router.huggingface.co/hf-inference/v1',
    icon: huggingfaceIcon,
  },
  zhipu: {
    key: 'zhipu',
    name: '智谱 (Zhipu AI)',
    base_url: 'https://open.bigmodel.cn/api/paas/v4',
    icon: zhipuIcon,
  },
  moonshot: {
    key: 'moonshot',
    name: '月之暗面 (Moonshot AI)',
    base_url: 'https://api.moonshot.cn/v1',
    icon: kimiIcon,
  },
  doubao: {
    key: 'doubao',
    name: '字节跳动 (ByteDance Doubao)',
    base_url: 'https://ark-api.console.volcengine.com/v3',
    icon: doubaoIcon,
  },
  wenxin: {
    key: 'wenxin',
    name: '百度 (Baidu Wenxin)',
    base_url: 'https://qianfan.baidubce.com/v2',
    icon: wenxinIcon,
  },
  yi: {
    key: 'yi',
    name: '零一万物 (01.AI)',
    base_url: 'https://api.lingyiwanwu.com/v1',
    icon: yiIcon,
  },
  stepfun: {
    key: 'stepfun',
    name: '阶跃星辰 (StepFun)',
    base_url: 'https://api.stepfun.com/v1',
    icon: stepfunIcon,
  },
  tencent: {
    key: 'tencent',
    name: '腾讯 (Tencent Hunyuan)',
    base_url: 'https://hunyuan.tencentcloudapi.com',
    icon: tencentIcon,
  },
  baichuan: {
    key: 'baichuan',
    name: '百川 (Baichuan)',
    base_url: 'https://api.baichuan-ai.com/v1',
    icon: baichuanIcon,
  },
  alibaba: {
    key: 'alibaba',
    name: '阿里 (Alibaba Cloud)',
    base_url: 'https://dashscope.aliyuncs.com/compatible-mode/v1',
    icon: alibabaIcon,
  },
  internlm: {
    key: 'internlm',
    name: '书生 (InternLM / 上海 AI Lab)',
    base_url: 'https://internlm-chat.intern-ai.org.cn/puyu-api/v1',
    icon: internlmIcon,
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
  groq: groqIcon,
  xai: xaiIcon,
  together: togetherIcon,
  fireworks: fireworksIcon,
  cerebras: cerebrasIcon,
  nvidia: nvidiaIcon,
  perplexity: perplexityIcon,
  mistral: mistralIcon,
  ollama: ollamaIcon,
  lmstudio: lmstudioIcon,
  vllm: vllmIcon,
  openrouter: openrouterIcon,
  siliconflow: siliconflowIcon,
  minimax: minimaxIcon,
  qwen: qwenIcon,
  cohere: cohereIcon,
  huggingface: huggingfaceIcon,
  zhipu: zhipuIcon,
  moonshot: kimiIcon,
  doubao: doubaoIcon,
  wenxin: wenxinIcon,
  yi: yiIcon,
  stepfun: stepfunIcon,
  tencent: tencentIcon,
  baichuan: baichuanIcon,
  alibaba: alibabaIcon,
  internlm: internlmIcon,
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
  cerebras: cerebrasIcon,
  cohere: cohereIcon,
  custom: null,
  dashscope: qwenIcon,
  fireworks: fireworksIcon,
  gemini: geminiIcon,
  google: geminiIcon,
  groq: groqIcon,
  huggingface: huggingfaceIcon,
  lm_studio: lmstudioIcon,
  lmstudio: lmstudioIcon,
  mistral: mistralIcon,
  nvidia: nvidiaIcon,
  perplexity: perplexityIcon,
  siliconcloud: siliconflowIcon,
  siliconflow: siliconflowIcon,
  together: togetherIcon,
  xai: xaiIcon,
  minimax: minimaxIcon,
  minimax_cn: minimaxIcon,
  alibaba: alibabaIcon,
  baichuan: baichuanIcon,
  doubao: doubaoIcon,
  internlm: internlmIcon,
  kimi: kimiIcon,
  moonshot: kimiIcon,
  stepfun: stepfunIcon,
  tencent: tencentIcon,
  wenxin: wenxinIcon,
  yi: yiIcon,
  zhipu: zhipuIcon,
  openai_compatible: null,
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
