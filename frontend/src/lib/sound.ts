/**
 * Sound notification utility for CoWorker chat events.
 *
 * Uses the Web Audio API to load and play MP3 sound files imported as URL
 * assets.  Vite's asset import handling guarantees the URLs resolve
 * correctly both in development (dev server) and production (electron build).
 *
 * A global SoundProvider wraps the app and exposes playSound() via React context.
 *
 * Sound events:
 *   reply_done      — assistant replied successfully
 *   reply_error     — reply failed (network error, stream error, goal failed)
 *   attention       — approval/question required (pending dock card)
 *
 * 音效來源：
 *   assets/sound/done.mp3          → reply_done
 *   assets/sound/error.mp3         → reply_error
 *   assets/sound/attention.mp3     → attention
 */

import doneMp3 from '../../../assets/sound/done.mp3?url';
import errorMp3 from '../../../assets/sound/error.mp3?url';
import attentionMp3 from '../../../assets/sound/attention.mp3?url';

type SoundEvent = 'reply_done' | 'reply_error' | 'attention';

const SOUND_FILES: Record<SoundEvent, string> = {
  reply_done: doneMp3,
  reply_error: errorMp3,
  attention: attentionMp3,
};

const STORAGE_KEY = 'cw-sound-enabled';

/** Read sound enabled flag from localStorage. */
export function isSoundEnabled(): boolean {
  try {
    const stored = localStorage.getItem(STORAGE_KEY);
    if (stored === null) return true; // default on
    return stored === 'true';
  } catch {
    return true;
  }
}

/** Persist sound setting to localStorage. */
export function setSoundEnabled(value: boolean): void {
  try {
    localStorage.setItem(STORAGE_KEY, String(value));
  } catch {
    // storage unavailable — silently ignore
  }
}

/** Load a sound buffer from the bundled audio file once, then cache it. */
class SoundCache {
  private ctx: AudioContext | null = null;
  private cache = new Map<string, AudioBuffer>();

  private getCtx(): AudioContext {
    if (!this.ctx) {
      this.ctx = new AudioContext();
    }
    if (this.ctx.state === 'suspended') {
      // Browser may delay resuming until the user interacts.
      void this.ctx.resume();
    }
    return this.ctx;
  }

  async load(url: string): Promise<void> {
    if (!url || this.cache.has(url)) return;
    try {
      const resp = await fetch(url);
      if (!resp.ok) return;
      const arrayBuffer = await resp.arrayBuffer();
      const ctx = this.getCtx();
      const buffer = await ctx.decodeAudioData(arrayBuffer);
      this.cache.set(url, buffer);
    } catch {
      // Audio file missing or fetch failed — fail silently so callers don't crash.
    }
  }

  play(event: SoundEvent): void {
    const url = SOUND_FILES[event];
    if (!url) return;
    const buffer = this.cache.get(url);
    if (!buffer) return;
    const ctx = this.getCtx();
    const source = ctx.createBufferSource();
    source.buffer = buffer;
    source.connect(ctx.destination);
    source.start(0);
  }
}

const soundCache = new SoundCache();

/**
 * Preload all sound buffers. Call once on app init so plays are instant.
 */
export function preloadSounds(): void {
  for (const url of Object.values(SOUND_FILES)) {
    soundCache.load(url);
  }
}

/**
 * Play a sound event.
 * Respects the user's sound-enabled setting and deduplicates rapid same-event calls.
 */
let lastEvent: { event: SoundEvent; time: number } | null = null;
let _enabled = true;

export function playSound(event: SoundEvent): void {
  if (!_enabled) return;
  if (!isSoundEnabled()) return;

  // Deduplicate: same event within 500ms is ignored.
  const now = Date.now();
  if (lastEvent && lastEvent.event === event && now - lastEvent.time < 500) return;
  lastEvent = { event, time: now };

  void soundCache.load(SOUND_FILES[event]).then(() => soundCache.play(event));
}

/**
 * Set whether sound is globally enabled (respects both global and per-user setting).
 * This is updated by the SoundProvider when the user toggles it in settings.
 */
export function setGlobalSoundEnabled(value: boolean): void {
  _enabled = value;
  setSoundEnabled(value);
}
