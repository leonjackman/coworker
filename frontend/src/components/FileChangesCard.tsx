import { useState } from 'react';
import { ChevronDown, ChevronRight, FilePenLine } from 'lucide-react';
import type { PartFileChange } from '../types';
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from './ui/collapsible';
import { FileDiffViewer } from './FileDiffViewer';
import { t } from '../lib/i18n';

const DEFAULT_VISIBLE_FILES = 10;

function FileCounts({ added, removed }: { added: number; removed: number }) {
  return (
    <span className="file-counts">
      {added > 0 && <span className="file-counts__add">+{added} lines</span>}
      {removed > 0 && <span className="file-counts__del">-{removed} lines</span>}
      {added === 0 && removed === 0 && <span className="file-counts__empty">Modified</span>}
    </span>
  );
}

export function FileChangesInline({ files }: { files: PartFileChange[] }) {
  if (!files.length) return null;
  return (
    <div className="file-changes-inline">
      {files.map((file) => (
        <span className="file-changes-inline__chip" key={`${file.kind}-${file.path}`}>
          <FilePenLine size={12} />
          <span className="file-changes-inline__path">{file.path}</span>
          <FileCounts added={file.added} removed={file.removed} />
        </span>
      ))}
    </div>
  );
}

function SingleFileChangesCard({ file }: { file: PartFileChange }) {
  const [open, setOpen] = useState(false);
  const hunks = file.hunks ?? [];
  const hasDiff = hunks.length > 0;

  return (
    <div className="file-card">
      <Collapsible open={open} onOpenChange={setOpen}>
        <CollapsibleTrigger className="file-card__trigger" asChild>
          <div role="button" tabIndex={0} aria-label={`${file.kind === 'write' ? 'Written' : 'Modified'} file ${file.path}`}>
            <span className="file-card__icon"><ChevronRight size={12} /></span>
            <span className="file-card__icon"><FilePenLine size={12} /></span>
            <span className="file-card__path" title={file.path}>{file.path}</span>
            <FileCounts added={file.added} removed={file.removed} />
            {file.too_large && (
              <span className="file-card__status">Too large to show</span>
            )}
          </div>
        </CollapsibleTrigger>
        <CollapsibleContent>
          <div className="file-card__content">
            {hasDiff ? (
              <FileDiffViewer
                path={file.path}
                kind={file.kind}
                hunks={hunks}
              />
            ) : (
              <div className="file-card__no-diff">
                No diff available for {file.path}
              </div>
            )}
          </div>
        </CollapsibleContent>
      </Collapsible>
    </div>
  );
}

export function FileChangesCard({ files }: { files: PartFileChange[] }) {
  const [expanded, setExpanded] = useState(false);
  if (!files.length) return null;

  const hiddenCount = files.length - DEFAULT_VISIBLE_FILES;
  const hasMore = hiddenCount > 0;
  const visibleFiles = expanded ? files : files.slice(0, DEFAULT_VISIBLE_FILES);

  return (
    <div className="file-changes-card" role="region" aria-label="Files modified">
      <div className="file-changes-card__header">
        <span className="file-changes-card__title">{files.length} file{files.length > 1 ? 's' : ''} modified</span>
        {hasMore && (
          <button
            type="button"
            className="file-changes-card__toggle"
            onClick={() => setExpanded((v) => !v)}
          >
            {expanded ? t('files.collapse') : t('files.expand_all', { count: hiddenCount })}
          </button>
        )}
      </div>
      <div className="file-changes-card__list">
        {visibleFiles.map((file) => (
          <SingleFileChangesCard key={`${file.kind}-${file.path}`} file={file} />
        ))}
      </div>
    </div>
  );
}
