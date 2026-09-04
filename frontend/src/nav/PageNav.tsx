import { createContext, useContext, type ReactNode } from 'react';
import { ChevronRight } from 'lucide-react';
import { t } from '../lib/i18n';

/**
 * Unified breadcrumb navigation for the non-chat top-level pages.
 *
 * Every "host" view (providers / mcp / skills / settings / memory / org /
 * dashboard) publishes its current location as a root label plus an optional
 * leaf label. The app renders a breadcrumb bar (`對話 › 提供商 › 添加…`) and
 * maps the Escape shortcut to "go up one crumb": leaf → root → chat.
 */

export interface CrumbNav {
  /** Label of the top-level page, e.g. t('providers.title'). */
  viewLabel: ReactNode;
  /** When present, the view is showing a second-level page (form/catalog/…). */
  leafLabel?: ReactNode | undefined;
  /** Called to leave the leaf page and return to the view root. */
  onBackToRoot?: (() => void) | undefined;
}

type Publish = (nav: CrumbNav | null) => void;

const PageNavCtx = createContext<Publish>(() => {});

/** Supplies the publish function (owned by App) to every mounted host. */
export function PageNavHost({ publish, children }: { publish: Publish; children: ReactNode }) {
  return <PageNavCtx.Provider value={publish}>{children}</PageNavCtx.Provider>;
}

/** Host components publish their current breadcrumb location. */
export function usePageNavPublish(): Publish {
  return useContext(PageNavCtx);
}

export interface PageCrumbsBarProps {
  nav: CrumbNav | null;
  onHome: () => void;
}

/** Render the `對話 › view › leaf` trail for the current non-chat page. */
export function PageCrumbsBar({ nav, onHome }: PageCrumbsBarProps) {
  if (!nav) return null;
  const items: Array<{ key: string; label: ReactNode; onClick?: (() => void) | undefined }> = [
    { key: 'home', label: t('nav.home'), onClick: onHome },
    { key: 'view', label: nav.viewLabel, onClick: nav.onBackToRoot },
  ];
  if (nav.leafLabel) items.push({ key: 'leaf', label: nav.leafLabel });
  const lastIndex = items.length - 1;
  return (
    <nav className="page-breadcrumbs" aria-label={t('nav.breadcrumbs_label')}>
      {items.map((item, index) => (
        <span className="page-breadcrumbs__item" key={item.key}>
          {index === lastIndex ? (
            <span className="page-breadcrumbs__current" aria-current="page">
              {item.label}
            </span>
          ) : item.onClick ? (
            <button type="button" className="page-breadcrumbs__link" onClick={item.onClick}>
              {item.label}
            </button>
          ) : (
            <span className="page-breadcrumbs__static">{item.label}</span>
          )}
          {index < lastIndex && <ChevronRight size={12} className="page-breadcrumbs__sep" />}
        </span>
      ))}
    </nav>
  );
}
