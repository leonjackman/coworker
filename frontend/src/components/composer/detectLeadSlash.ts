/**
 * Pure detector for the leading "/" command menu.
 *
 * A trigger is only active when the composer text STARTS with "/" (industry
 * rule: commands live at the head of the composer). Once whitespace follows
 * the token the command is considered committed and the menu closes. The
 * legacy typed command "/skill" is excluded (it is not a menu entry).
 *
 * Kept as a pure string function so it can be reasoned about / tested without
 * touching the DOM, and so the menu state is derived rather than mutated.
 */
export interface LeadSlashMatch {
  /** Characters typed after the leading slash, up to the first whitespace. */
  query: string;
  /** True once whitespace follows the token (the command is committed). */
  committed: boolean;
}

export function detectLeadSlash(text: string): LeadSlashMatch | null {
  if (text.charAt(0) !== "/") return null;
  const token = text.slice(1).split(/\s/, 1)[0] ?? "";
  if (token === "skill") return null;
  const committed = text.length > token.length + 1;
  if (committed) return { query: "", committed: true };
  return { query: token, committed: false };
}
