/**
 * Action catalog: a friendly "action" per step kind, each with the minimal set
 * of fields it actually needs. Selecting an action shows only its fields, so
 * users fill intent (打开软件 → 哪个软件) instead of raw tool arguments.
 *
 * Field keys map to the step's ``params`` (or ``$do`` → the step's ``do``).
 */

export type FieldType = 'text' | 'textarea' | 'number' | 'boolean' | 'csv';

export interface ActionField {
  key: string;
  labelKey: string;
  type: FieldType;
  placeholder?: string;
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
  { action: 'launch_app', labelKey: 'workflows.act_launch_app', fields: [f('app')] },
  { action: 'press_hotkey', labelKey: 'workflows.act_press_hotkey', fields: [f('modifiers', 'csv'), f('key')] },
  { action: 'click_ref', labelKey: 'workflows.act_click_ref', fields: [f('ref')] },
  { action: 'double_click_ref', labelKey: 'workflows.act_double_click_ref', fields: [f('ref')] },
  { action: 'right_click_ref', labelKey: 'workflows.act_right_click_ref', fields: [f('ref')] },
  { action: 'type_into', labelKey: 'workflows.act_type_into', fields: [f('ref'), f('text'), f('submit', 'boolean')] },
  { action: 'type_text', labelKey: 'workflows.act_type_text', fields: [f('text')] },
  { action: 'scroll', labelKey: 'workflows.act_scroll', fields: [f('dy', 'number')] },
  { action: 'scroll_to', labelKey: 'workflows.act_scroll_to', fields: [f('scroll_app'), f('scroll_y', 'number')] },
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

export function actionsFor(kind: string): WorkflowAction[] {
  return ACTION_CATALOG[kind] ?? [];
}

export function actionDef(kind: string, doValue: string): WorkflowAction | undefined {
  return actionsFor(kind).find((a) => a.action === doValue);
}
