import { memo, useMemo } from 'react';
import { parsePatch } from 'diff';
import type { DiffHunk, DiffLine } from '../types';

interface RenderLine {
  type: 'context' | 'del' | 'add';
  oldNo: number | null;
  newNo: number | null;
  text: string;
}

function hunksFromUnified(diffText: string): DiffHunk[] {
  const patches = parsePatch(diffText);
  const hunks: DiffHunk[] = [];
  for (const patch of patches) {
    for (const hunk of patch.hunks ?? []) {
      hunks.push({
        old_start: hunk.oldStart,
        old_lines: hunk.oldLines,
        new_start: hunk.newStart,
        new_lines: hunk.newLines,
        lines: (hunk.lines ?? []).map((lineStr): DiffLine => {
          const trimmed = lineStr.replace(/\n$/, '');
          if (trimmed.startsWith('+')) return { type: 'add', old_no: null, new_no: null, text: trimmed.slice(1) };
          if (trimmed.startsWith('-')) return { type: 'del', old_no: null, new_no: null, text: trimmed.slice(1) };
          return { type: 'context', old_no: null, new_no: null, text: trimmed.startsWith(' ') ? trimmed.slice(1) : trimmed };
        }),
      });
    }
  }
  return hunks;
}

function flattenLines(hunks: DiffHunk[]): { hunk: DiffHunk; line: RenderLine }[] {
  const out: { hunk: DiffHunk; line: RenderLine }[] = [];
  for (const hunk of hunks) {
    let oldNo = hunk.old_start;
    let newNo = hunk.new_start;
    for (const line of hunk.lines ?? []) {
      let renderOld: number | null = null;
      let renderNew: number | null = null;
      let type: RenderLine['type'] = 'context';
      if (line.type === 'del') {
        renderOld = oldNo;
        oldNo += 1;
        type = 'del';
      } else if (line.type === 'add') {
        renderNew = newNo;
        newNo += 1;
        type = 'add';
      } else {
        renderOld = oldNo;
        renderNew = newNo;
        oldNo += 1;
        newNo += 1;
      }
      out.push({ hunk, line: { type, oldNo: renderOld, newNo: renderNew, text: line.text } });
    }
  }
  return out;
}

export interface FileDiffViewerProps {
  path?: string;
  hunks?: DiffHunk[];
  diffText?: string;
  kind?: 'write' | 'edit';
  expanded?: boolean;
}

function DiffBody({ hunks }: { hunks: DiffHunk[] }) {
  const rows = useMemo(() => flattenLines(hunks), [hunks]);
  if (rows.length === 0) {
    return <div className="file-diff__empty">No changes</div>;
  }
  return (
    <div className="file-diff__body" role="list">
      {rows.map((row, index) => (
        <div
          className={`file-diff__line file-diff__line--${row.line.type}`}
          key={`${row.hunk.old_start}-${row.hunk.new_start}-${index}`}
          role="listitem"
        >
          <span className="file-diff__num file-diff__num--old">{row.line.oldNo ?? ''}</span>
          <span className="file-diff__num file-diff__num--new">{row.line.newNo ?? ''}</span>
          <span className="file-diff__sign">{row.line.type === 'add' ? '+' : row.line.type === 'del' ? '-' : ' '}</span>
          <code className="file-diff__text">{row.line.text || ' '}</code>
        </div>
      ))}
    </div>
  );
}

export const FileDiffViewer = memo(function FileDiffViewer({ path, hunks, diffText, expanded = true }: FileDiffViewerProps) {
  const resolvedHunks = useMemo<DiffHunk[]>(() => {
    if (hunks && hunks.length > 0) return hunks;
    if (diffText) return hunksFromUnified(diffText);
    return [];
  }, [hunks, diffText]);

  if (resolvedHunks.length === 0) {
    return null;
  }

  const added = resolvedHunks.reduce((sum, h) => sum + (h.lines ?? []).filter((l) => l.type === 'add').length, 0);
  const removed = resolvedHunks.reduce((sum, h) => sum + (h.lines ?? []).filter((l) => l.type === 'del').length, 0);

  return (
    <div className="file-diff">
      <div className="file-diff__header">
        {path ? <span className="file-diff__path">{path}</span> : null}
        <span className="file-diff__stats">
          {added > 0 && <span className="file-diff__add">+{added}</span>}
          {removed > 0 && <span className="file-diff__del">-{removed}</span>}
        </span>
      </div>
      {expanded ? (
        <DiffBody hunks={resolvedHunks} />
      ) : (
        <div className="file-diff__collapsed">+{added} / -{removed}</div>
      )}
    </div>
  );
});
