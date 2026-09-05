import { clsx, type ClassValue } from "clsx"
import { twMerge } from "tailwind-merge"
import { t } from "./i18n"
import type { Autonomy } from "../types"

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs))
}

/**
 * Single source of truth for the two-level permission model (默認權限 / 完整權限).
 * Any raw autonomy value entering the frontend (localStorage, persisted session
 * records, legacy transcripts) collapses onto the two valid levels: the retired
 * "supervised" level folds into the default (guarded) permission.
 */
export function normalizeAutonomy(value: string | null | undefined): Autonomy {
  return value === 'autonomous' ? 'autonomous' : 'guarded';
}

export function formatTimeAgo(updatedAt: string): string {
  const now = Date.now();
  const then = new Date(updatedAt).getTime();
  const diffMs = Math.abs(now - then);
  const diffMinutes = Math.floor(diffMs / 60000);
  if (diffMinutes < 1) return t('time.just_now');
  if (diffMinutes < 60) return `${diffMinutes}m`;
  const diffHours = Math.floor(diffMinutes / 60);
  if (diffHours < 24) return `${diffHours}h`;
  const diffDays = Math.floor(diffHours / 24);
  if (diffDays < 7) return `${diffDays}d`;
  const diffWeeks = Math.floor(diffDays / 7);
  return `${diffWeeks}w`;
}
