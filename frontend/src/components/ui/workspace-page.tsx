import type { ReactNode } from 'react';
import { PageHeading } from './page-heading';

interface WorkspacePageProps {
  eyebrow?: ReactNode;
  title: ReactNode;
  description?: ReactNode;
  action?: ReactNode;
  children: ReactNode;
  className?: string;
  contentClassName?: string;
}

export function WorkspacePage({
  eyebrow,
  title,
  description,
  action,
  children,
  className = '',
  contentClassName = '',
}: WorkspacePageProps) {
  return (
    <section className={`workspace-page ${className}`.trim()}>
      {title && (
        <PageHeading
          eyebrow={eyebrow}
          title={title}
          description={description}
          action={action}
        />
      )}
      <div className={`workspace-page__content ${contentClassName}`.trim()}>
        {children}
      </div>
    </section>
  );
}
