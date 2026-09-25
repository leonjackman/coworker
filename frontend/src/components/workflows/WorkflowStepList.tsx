import { Fragment, type ReactNode } from 'react';
import { kindIcon, kindStripe } from './kinds';
import { t } from '../../lib/i18n';
import type { WorkflowStep } from '../../types';

const SLOTS: Array<'then' | 'else' | 'body'> = ['then', 'else', 'body'];

function StepRow({ step, depth }: { step: WorkflowStep; depth: number }) {
  const Icon = kindIcon(step.kind);
  const onError = (step.on_error as { then?: string } | undefined)?.then;
  const success = step.success ?? [];
  return (
    <li className="wf-step" style={{ marginLeft: depth * 18 }}>
      <span className="wf-step__rail" style={{ background: kindStripe(step.kind) }} />
      <div className="wf-step__card">
        <div className="wf-step__head">
          <span className="wf-step__icon" style={{ color: kindStripe(step.kind) }}>
            <Icon size={14} />
          </span>
          <span className="wf-step__id">{step.id}</span>
          <span className="settings-chip">{step.kind}</span>
          {step.mode === 'agent' ? <span className="wf-badge wf-badge--agent">agent</span> : null}
          {step.approval ? <span className="wf-badge wf-badge--human">approval</span> : null}
          {onError ? <span className="wf-badge">on_error: {onError}</span> : null}
          {success.length > 0 ? <span className="wf-badge">success ×{success.length}</span> : null}
        </div>
        {(step.goal || step.do || step.description || step.when || step.foreach) && (
          <div className="wf-step__meta">
            {step.goal ? <span className="wf-step__goal">{step.goal}</span> : null}
            {step.do ? <code>{step.do}</code> : null}
            {step.when ? <span className="wf-step__cond">when {step.when}</span> : null}
            {step.foreach ? <span className="wf-step__cond">each {step.foreach}</span> : null}
          </div>
        )}
      </div>
    </li>
  );
}

/** Read-only, human-readable view of a workflow's step tree. */
export function WorkflowStepList({ steps }: { steps: WorkflowStep[] }) {
  const render = (list: WorkflowStep[], depth: number) =>
    list.map((step, index) => {
      const nodes: ReactNode[] = [<StepRow key={`row-${depth}-${index}`} step={step} depth={depth} />];
      for (const slot of SLOTS) {
        const kids = (step[slot] as WorkflowStep[] | undefined) ?? [];
        if (kids.length === 0) continue;
        nodes.push(
          <li key={`slot-${slot}-${depth}-${index}`} className="wf-step__slot" style={{ marginLeft: depth * 18 + 34 }}>
            {slot}
          </li>,
        );
        nodes.push(...render(kids, depth + 1));
      }
      return <Fragment key={`${depth}-${index}-${step.id}`}>{nodes}</Fragment>;
    });

  if (steps.length === 0) {
    return <p className="skill-empty">{t('workflows.no_steps')}</p>;
  }
  return <ul className="wf-steplist">{render(steps, 0)}</ul>;
}
