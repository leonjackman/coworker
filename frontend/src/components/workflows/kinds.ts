import {
  Bot,
  Boxes,
  Brain,
  Globe,
  GitBranch,
  LayoutGrid,
  Monitor,
  Pin,
  Repeat,
  ShieldCheck,
  Terminal,
  Timer,
  UserCheck,
  Wrench,
  type LucideIcon,
} from 'lucide-react';

/** Shared workflow step-kind metadata (single source of truth for editor + detail). */

export type KindFamily = 'action' | 'control' | 'verify' | 'agent' | 'human';

export const FAMILY_VAR: Record<KindFamily, string> = {
  action: 'var(--info)',
  control: 'var(--warning)',
  verify: 'var(--success)',
  agent: 'var(--accent)',
  human: 'var(--warning)',
};

export const FAMILY_LABEL: Record<KindFamily, string> = {
  action: 'action',
  control: 'control',
  verify: 'verify',
  agent: 'agent',
  human: 'human',
};

export type KindGroupId = 'web' | 'cmd' | 'ai' | 'human' | 'check' | 'control';

export interface KindMeta {
  family: KindFamily;
  icon: LucideIcon;
  group: KindGroupId;
}

export const KIND_META: Record<string, KindMeta> = {
  browser: { family: 'action', icon: Globe, group: 'web' },
  app: { family: 'action', icon: Monitor, group: 'web' },
  computer: { family: 'action', icon: Monitor, group: 'web' },
  command: { family: 'action', icon: Terminal, group: 'cmd' },
  tool: { family: 'action', icon: Wrench, group: 'cmd' },
  agentic: { family: 'agent', icon: Bot, group: 'ai' },
  skill: { family: 'agent', icon: Brain, group: 'ai' },
  human: { family: 'human', icon: UserCheck, group: 'human' },
  assert: { family: 'verify', icon: ShieldCheck, group: 'check' },
  set: { family: 'verify', icon: Pin, group: 'check' },
  wait: { family: 'verify', icon: Timer, group: 'check' },
  branch: { family: 'control', icon: GitBranch, group: 'control' },
  loop: { family: 'control', icon: Repeat, group: 'control' },
  parallel: { family: 'control', icon: LayoutGrid, group: 'control' },
  subworkflow: { family: 'control', icon: Boxes, group: 'control' },
};

export const KIND_OPTIONS = Object.keys(KIND_META);

/** Ordered groups for the friendly step-type picker. */
export const KIND_GROUPS: Array<{ id: KindGroupId; labelKey: string; kinds: string[] }> = [
  { id: 'web', labelKey: 'workflows.group_web', kinds: ['browser', 'app', 'computer'] },
  { id: 'cmd', labelKey: 'workflows.group_cmd', kinds: ['command', 'tool'] },
  { id: 'ai', labelKey: 'workflows.group_ai', kinds: ['agentic', 'skill'] },
  { id: 'human', labelKey: 'workflows.group_human', kinds: ['human'] },
  { id: 'check', labelKey: 'workflows.group_check', kinds: ['assert', 'set', 'wait'] },
  { id: 'control', labelKey: 'workflows.group_control', kinds: ['branch', 'loop', 'parallel', 'subworkflow'] },
];

/** Common action suggestions for the "action" field, per kind. */
export const DO_SUGGESTIONS: Record<string, string[]> = {
  browser: ['navigate', 'click', 'type', 'set_files', 'scroll', 'screenshot', 'snapshot', 'evaluate'],
  app: ['click_ref', 'type_into', 'set_value', 'press_hotkey', 'launch_app', 'screenshot'],
  computer: ['click_ref', 'type_into', 'set_value', 'press_hotkey', 'launch_app', 'screenshot'],
  tool: ['web_search', 'web_fetch'],
};

export function kindLabelKey(kind: string): string {
  return `workflows.kind_${kind}`;
}

export function kindDescKey(kind: string): string {
  return `workflows.kinddesc_${kind}`;
}

export const ACTION_KINDS = new Set(['command', 'tool', 'browser', 'app', 'computer', 'skill', 'human', 'agentic']);
export const DO_KINDS = new Set([...ACTION_KINDS, 'subworkflow', 'assert']);
export const PARAM_KINDS = new Set([...ACTION_KINDS, 'set', 'wait', 'subworkflow']);
export const LOCATOR_KINDS = new Set(['browser', 'app', 'computer']);

export function kindFamily(kind: string): KindFamily {
  return KIND_META[kind]?.family ?? 'action';
}

export function kindStripe(kind: string): string {
  return FAMILY_VAR[kindFamily(kind)];
}

export function kindIcon(kind: string): LucideIcon {
  return KIND_META[kind]?.icon ?? Wrench;
}
