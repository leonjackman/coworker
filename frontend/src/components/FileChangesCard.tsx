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

function SingleFileChangesCard({ file }: { file: PartFileChange }) {
  const [open, setOpen] = useState(true);
  const hasDiff = !!(file.hunks && file.hunks.length > 0);

  return (
    <Collapsible open={open} onOpenChange={setOpen}>
      <CollapsibleTrigger className="file-changes-list__item-trigger">
        <span className="file-changes-list__icon">{open ? <ChevronDown size={12} /> : <ChevronRight size={12} />}</span>
        <FilePenLine size={12} className="file-changes-list__sign" />
        <span className="file-changes-list__path">{file.path}</span>
        <FileCounts added={file.added} removed={file.removed} />
        {file.too_large && (
          <span className="file-changes-list__too-large">Too large</span>
        )}
      </CollapsibleTrigger>
      <CollapsibleContent>
        {file.kind === 'write' && (
          <button
            type="button"
            className="file-changes-list__edit-btn"
            onClick={() => {
              void window.open(`/file/${encodeURIComponent(file.path)}/edit`, '_blank');
            }}
          >
            在编辑器中打开
          </button>
        )}
        {hasDiff ? (
          <div className="file-changes-list__diff">
            <FileDiffViewer
              path={file.path}
              kind={file.kind}
              hunks={file.hunks}
            />
          </div>
        ) : (
          <div className="file-changes-list__no-diff">No diff available</div>
        )}
      </CollapsibleContent>
    </Collapsible>
  );
}

export function FileChangesCard({ files }: { files: PartFileChange[] }) {
  if (!files.length) return null;

  return (
    <div className="file-changes-list">
      {files.map((file) => (
        <SingleFileChangesCard key={`${file.kind}-${file.path}`} file={file} />
      ))}
    </div>
  );
}
