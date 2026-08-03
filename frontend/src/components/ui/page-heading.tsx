import type { ReactNode } from 'react';

interface PageHeadingProps {
  eyebrow?: ReactNode;
  title: ReactNode;
  description: ReactNode;
  action?: ReactNode;
}

export function PageHeading({ eyebrow, title, description, action }: PageHeadingProps) {
  return (
    <div className="panel-heading">
      <div>
        {eyebrow && <p className="panel-heading__eyebrow">{eyebrow}</p>}
        <h1>{title}</h1>
        {description && <p>{description}</p>}
      </div>
      {action}
    </div>
  );
}
