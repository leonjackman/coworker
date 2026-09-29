/**
 * Action catalog: a friendly "action" per step kind, each with the minimal set
 * of fields it actually needs. Selecting an action shows only its fields, so
 * users fill intent (打开软件 → 哪个软件) instead of raw tool arguments.
 *
 * Field keys map to the step's ``params`` (or ``$do`` → the step's ``do``).
 */

export type FieldType = 'text' | 'textarea' | 'number' | 'boolean' | 'csv' | 'app';

export interface ActionField {
  key: string;
  labelKey: string;
  type: FieldType;
  placeholder?: string;
  /** Optional help text from the capability registry (shown under the field). */
  help?: string;
}

export interface WorkflowAction {
  action: string;
  labelKey: string;
  fields: ActionField[];
}

const f = (key: string, type: FieldType = 'text'): ActionField => ({
  key,
  type,
  labelKey: `workflows.fld_${key}`,
});

const COMPUTER_ACTIONS: WorkflowAction[] = [
  { action: 'launch_app', labelKey: 'workflows.act_launch_app', fields: [f('app', 'app')] },
  { action: 'press_hotkey', labelKey: 'workflows.act_press_hotkey', fields: [f('modifiers', 'csv'), f('key')] },
  { action: 'click_ref', labelKey: 'workflows.act_click_ref', fields: [f('ref')] },
  { action: 'double_click_ref', labelKey: 'workflows.act_double_click_ref', fields: [f('ref')] },
  { action: 'right_click_ref', labelKey: 'workflows.act_right_click_ref', fields: [f('ref')] },
  { action: 'type_into', labelKey: 'workflows.act_type_into', fields: [f('ref'), f('text'), f('submit', 'boolean')] },
  { action: 'type_text', labelKey: 'workflows.act_type_text', fields: [f('text')] },
  { action: 'scroll', labelKey: 'workflows.act_scroll', fields: [f('dy', 'number')] },
  { action: 'scroll_to', labelKey: 'workflows.act_scroll_to', fields: [f('scroll_app', 'app'), f('scroll_y', 'number')] },
  { action: 'go_back', labelKey: 'workflows.act_go_back', fields: [] },
  { action: 'show', labelKey: 'workflows.act_show', fields: [f('ref')] },
  { action: 'click_coords', labelKey: 'workflows.act_click_coords', fields: [f('x', 'number'), f('y', 'number')] },
];

export const ACTION_CATALOG: Record<string, WorkflowAction[]> = {
  browser: [
    { action: 'navigate', labelKey: 'workflows.act_navigate', fields: [f('url')] },
    { action: 'snapshot', labelKey: 'workflows.act_snapshot', fields: [] },
    { action: 'get_text', labelKey: 'workflows.act_get_text', fields: [] },
    { action: 'get_state', labelKey: 'workflows.act_get_state', fields: [] },
    { action: 'screenshot', labelKey: 'workflows.act_screenshot', fields: [] },
    { action: 'click', labelKey: 'workflows.act_click', fields: [f('x', 'number'), f('y', 'number')] },
    { action: 'type', labelKey: 'workflows.act_type', fields: [f('text')] },
    { action: 'press', labelKey: 'workflows.act_press', fields: [f('key')] },
    { action: 'scroll', labelKey: 'workflows.act_scroll', fields: [f('dy', 'number')] },
    { action: 'back', labelKey: 'workflows.act_back', fields: [] },
    { action: 'forward', labelKey: 'workflows.act_forward', fields: [] },
    { action: 'reload', labelKey: 'workflows.act_reload', fields: [] },
    { action: 'evaluate', labelKey: 'workflows.act_evaluate', fields: [f('expression', 'textarea')] },
  ],
  app: COMPUTER_ACTIONS,
  computer: COMPUTER_ACTIONS,
  tool: [
    { action: 'web_search', labelKey: 'workflows.act_web_search', fields: [f('query')] },
    { action: 'web_fetch', labelKey: 'workflows.act_web_fetch', fields: [f('url')] },
  ],
  command: [
    { action: 'run', labelKey: 'workflows.act_run', fields: [f('command', 'textarea'), f('cwd'), f('timeout', 'number')] },
  ],
  set: [
    { action: 'set', labelKey: 'workflows.act_set', fields: [f('name'), f('value')] },
  ],
  wait: [
    { action: 'wait', labelKey: 'workflows.act_wait', fields: [f('seconds', 'number')] },
  ],
};

/**
 * "Value kinds": the step's ``do`` IS its primary input (no action select).
 * e.g. a skill step's do = the skill name; a subworkflow step's do = its name.
 */
export const VALUE_KINDS: Record<string, { labelKey: string; type: FieldType }> = {
  skill: { labelKey: 'workflows.fld_skill', type: 'text' },
  subworkflow: { labelKey: 'workflows.fld_workflow', type: 'text' },
  agentic: { labelKey: 'workflows.fld_prompt', type: 'textarea' },
  human: { labelKey: 'workflows.fld_question', type: 'text' },
  assert: { labelKey: 'workflows.fld_spec', type: 'text' },
};

export interface CapabilityParam {
  name: string;
  type: string;
  required?: boolean;
  description?: string;
}

export interface CapabilityAction {
  name: string;
  params?: CapabilityParam[];
  outputs?: string[];
}

export interface CapabilityKind {
  kind: string;
  actions?: CapabilityAction[];
}

// Backend-derived catalog (single source of truth). When present it overrides
// the static fallback above so the editor can never offer a non-existent action.
let REMOTE_CATALOG: Record<string, WorkflowAction[]> | null = null;
let REMOTE_OUTPUTS: Record<string, Record<string, string[]>> = {};

const FIELD_TYPE: Record<string, FieldType> = {
  string: 'text',
  number: 'number',
  boolean: 'boolean',
  list: 'csv',
  object: 'text',
};

// Params that name an app on the local machine → render the App picker.
const APP_PARAM_KEYS = new Set(['app', 'scroll_app']);

function fieldTypeFor(kind: string, param: CapabilityParam): FieldType {
  if (kind === 'computer' && APP_PARAM_KEYS.has(param.name)) return 'app';
  return FIELD_TYPE[param.type] ?? 'text';
}

export function setCapabilities(kinds: CapabilityKind[]): void {
  const next: Record<string, WorkflowAction[]> = {};
  const outputs: Record<string, Record<string, string[]>> = {};
  for (const k of kinds) {
    for (const a of k.actions ?? []) {
      if (a.outputs?.length) {
        const bucket = outputs[k.kind] ?? {};
        bucket[a.name] = a.outputs;
        outputs[k.kind] = bucket;
      }
    }
    const actions = k.actions ?? [];
    if (!actions.length) continue;
    next[k.kind] = actions.map((a) => ({
      action: a.name,
      labelKey: `workflows.act_${a.name}`,
      fields: (a.params ?? []).map((p) => ({
        key: p.name,
        type: fieldTypeFor(k.kind, p),
        labelKey: `workflows.fld_${p.name}`,
        ...(p.description ? { help: p.description } : {}),
      })),
    }));
  }
  REMOTE_CATALOG = next;
  REMOTE_OUTPUTS = outputs;
}

/** Declared result fields for a step (usable as {{steps.<id>.<field>}}). */
export function outputsFor(kind: string, doValue: string): string[] {
  return REMOTE_OUTPUTS[kind]?.[doValue] ?? [];
}

export function actionsFor(kind: string): WorkflowAction[] {
  return REMOTE_CATALOG?.[kind] ?? ACTION_CATALOG[kind] ?? [];
}

export function actionDef(kind: string, doValue: string): WorkflowAction | undefined {
  return actionsFor(kind).find((a) => a.action === doValue);
}
