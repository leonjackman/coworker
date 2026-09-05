import type { CommandChip } from '../components/ChatInput';

export function parseSkillMarker(content: string): { chip: CommandChip; text: string } | null {
  const match = /^\[skill:([A-Za-z0-9][A-Za-z0-9_.-]*)(?::([A-Za-z0-9][A-Za-z0-9_.-]*))?\](?:\n\n|\n)?/.exec(content);
  if (!match) return null;
  const [, pkg, cmd] = match;
  const text = content.slice(match[0].length);
  if (cmd) {
    return { chip: { command: `/${cmd}`, type: 'skill', packageName: pkg as string }, text };
  }
  return { chip: { command: `/${pkg as string}`, type: 'skill' }, text };
}

export const encodeSkillMarker = (chip: CommandChip): string =>
  chip.packageName ? `[skill:${chip.packageName}:${chip.command.slice(1)}]` : `[skill:${chip.command.slice(1)}]`;
