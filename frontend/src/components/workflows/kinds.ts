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

export const KIND_META: Record<string, { family: KindFamily; icon: LucideIcon }> = {
  command: { family: 'action', icon: Terminal },
  tool: { family: 'action', icon: Wrench },
  browser: { family: 'action', icon: Globe },
  app: { family: 'action', icon: Monitor },
  computer: { family: 'action', icon: Monitor },
  skill: { family: 'agent', icon: Brain },
  agentic: { family: 'agent', icon: Bot },
  human: { family: 'human', icon: UserCheck },
  set: { family: 'verify', icon: Pin },
  assert: { family: 'verify', icon: ShieldCheck },
  wait: { family: 'verify', icon: Timer },
  branch: { family: 'control', icon: GitBranch },
  loop: { family: 'control', icon: Repeat },
  parallel: { family: 'control', icon: LayoutGrid },
  subworkflow: { family: 'control', icon: Boxes },
};

export const KIND_OPTIONS = Object.keys(KIND_META);

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
