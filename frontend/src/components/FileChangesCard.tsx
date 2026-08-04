import { useState } from 'react';
import { ChevronDown, ChevronRight, FilePenLine } from 'lucide-react';
import type { PartFileChange } from '../types';
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from './ui/collapsible';
import { FileDiffViewer } from './FileDiffViewer';

function FileCounts({ added, removed }: { added: number; removed: number }) {
  return (
    <span className="file-counts">
      {added > 0 && <span className="file-counts__add">+{added}</span>}
      {removed > 0 && <span className="file-counts__del">-{removed}</span>}
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

export function FileChangesCard({ files }: { files: PartFileChange[] }) {
  const [open, setOpen] = useState(false);
  if (!files.length) return null;

  const totalAdded = files.reduce((sum, file) => sum + file.added, 0);
  const totalRemoved = files.reduce((sum, file) => sum + file.removed, 0);
  const hasDiffs = files.some((file) => file.hunks && file.hunks.length > 0);

  return (
    <div className="file-changes-card">
      <Collapsible open={open} onOpenChange={setOpen}>
        <CollapsibleTrigger className="file-changes-card__trigger">
          <span className="file-changes-card__icon">{open ? <ChevronDown size={14} /> : <ChevronRight size={14} />}</span>
          <FilePenLine size={14} className="file-changes-card__sign" />
          <span className="file-changes-card__label">
            {files.length} file{files.length === 1 ? '' : 's'} changed
          </span>
          <span className="file-changes-card__counts">
            <FileCounts added={totalAdded} removed={totalRemoved} />
          </span>
        </CollapsibleTrigger>
        <CollapsibleContent>
          <div className="file-changes-card__list">
            {files.map((file) => (
              <div className="file-changes-card__row" key={`${file.kind}-${file.path}`}>
                <FilePenLine size={12} />
                <span className="file-changes-card__path">{file.path}</span>
                <FileCounts added={file.added} removed={file.removed} />
              </div>
            ))}
          </div>
          {hasDiffs && (
            <div className="file-changes-card__diffs">
              {files
                .filter((file) => file.hunks && file.hunks.length > 0)
                .map((file) => (
                  <FileDiffViewer
                    key={`${file.kind}-${file.path}`}
                    path={file.path}
                    {...(file.hunks && file.hunks.length > 0 ? { hunks: file.hunks } : {})}
                    kind={file.kind}
                  />
                ))}
            </div>
          )}
        </CollapsibleContent>
      </Collapsible>
    </div>
  );
}
