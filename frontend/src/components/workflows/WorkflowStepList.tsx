import { Fragment, type ReactNode } from 'react';
import { CheckCircle2, Zap } from 'lucide-react';
import { orderSteps } from './flowGraph';
import { kindIcon, kindLabelKey, kindStripe } from './kinds';
import { t } from '../../lib/i18n';
import type { WorkflowStep } from '../../types';

const SLOTS: Array<'then' | 'else' | 'body'> = ['then', 'else', 'body'];

function StepRow({ step, depth }: { step: WorkflowStep; depth: number }) {
  const Icon = kindIcon(step.kind);
  const onError = (step.on_error as { then?: string } | undefined)?.then;
  const success = step.success ?? [];
  const action = step.do ?? '';
  return (
    <li className="wf-flow-row" style={{ marginLeft: depth * 18 }}>
      <span className="wf-flow-row__rail" style={{ background: kindStripe(step.kind) }} />
      <span className="wf-flow-row__icon" style={{ color: kindStripe(step.kind) }}>
        <Icon size={14} />
      </span>
      {/* Capability-based label (system-defined); raw id is a muted tag. */}
      <span className="wf-flow-row__cap">{t(kindLabelKey(step.kind))}</span>
      {action ? <code className="wf-flow-row__action">{action}</code> : null}
      <span className="wf-flow-row__id">#{step.id}</span>
      {step.goal ? <span className="wf-flow-row__goal">{step.goal}</span> : null}
      <span className="wf-flow-row__badges">
        {step.mode === 'agent' ? <span className="wf-badge wf-badge--agent">agent</span> : null}
        {step.approval ? <span className="wf-badge wf-badge--human">approval</span> : null}
        {onError ? <span className="wf-badge">on_error: {onError}</span> : null}
        {success.length > 0 ? <span className="wf-badge">success ×{success.length}</span> : null}
        {step.when ? <span className="wf-badge">when {step.when}</span> : null}
      </span>
    </li>
  );
}

interface Props {
  steps: WorkflowStep[];
  triggers?: string[];
  outputs?: Record<string, string>;
}

/** Read-only single-card view of a workflow: trigger → steps → outputs. */
export function WorkflowStepList({ steps, triggers = [], outputs = {} }: Props) {
  const render = (list: WorkflowStep[], depth: number) =>
    list.map((step, index) => {
      const nodes: ReactNode[] = [<StepRow key={`row-${depth}-${index}`} step={step} depth={depth} />];
      for (const slot of SLOTS) {
        const kids = (step[slot] as WorkflowStep[] | undefined) ?? [];
        if (kids.length === 0) continue;
        nodes.push(
          <li key={`slot-${slot}-${depth}-${index}`} className="wf-flow-slot" style={{ marginLeft: depth * 18 + 24 }}>
            {slot}
          </li>,
        );
        nodes.push(...render(orderSteps(kids), depth + 1));
      }
      return <Fragment key={`${depth}-${index}-${step.id}`}>{nodes}</Fragment>;
    });

  const outputEntries = Object.entries(outputs);
  const triggerList = triggers.length ? triggers : ['manual'];

  return (
    <div className="wf-flow">
      <div className="wf-flow__endpoint wf-flow__endpoint--trigger">
        <Zap size={14} />
        <span className="wf-flow__endpoint-label">{t('workflows.trigger_node')}</span>
        <span className="settings-chip">{triggerList.join(', ')}</span>
      </div>

      <div className="wf-flow__steps">
        {steps.length === 0 ? (
          <p className="skill-empty">{t('workflows.no_steps')}</p>
        ) : (
          <ul className="wf-steplist">{render(orderSteps(steps), 0)}</ul>
        )}
      </div>

      <div className="wf-flow__endpoint wf-flow__endpoint--output">
        <CheckCircle2 size={14} />
        <span className="wf-flow__endpoint-label">{t('workflows.output_node')}</span>
        {outputEntries.length > 0 ? (
          outputEntries.map(([key]) => (
            <span className="settings-chip" key={key}>
              {key}
            </span>
          ))
        ) : (
          <span className="settings-chip settings-chip--dim">—</span>
        )}
      </div>
    </div>
  );
}
