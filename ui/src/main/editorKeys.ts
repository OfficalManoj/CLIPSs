// Ctrl shortcuts inside Clips Kitty (editor suite FL2 C1, ED31).
//
// Clips Kitty Editor (the fork of Concat) keeps one table of keyboard actions
// with their chords (src/crates/concat/src/keymap.rs) and lets a person move
// any of them in Settings > Shortcuts, which writes the editor's own settings
// file: "keybinds", an action's id to one chord ("" for none). Its shipped
// defaults put eight actions on bare letters (Q, W, F, B, M, H, N, S) and
// delete clips with Backspace too. Inside Clips Kitty the editor sits beside
// the app's own page, so a stray letter must never cut the timeline: every
// one of those gets a Ctrl chord, and Delete alone deletes.
//
// No editor code changes for this (ED31): the chords are written into the
// editor's settings file as data, by the main process, before it starts the
// editor (never while it runs), merged into the file without touching any
// other key, written beside it and moved over it. They are written once per
// version of the table below, and never over a chord the person changed in
// Settings > Shortcuts afterwards: an action is set only where the file still
// holds what Clips Kitty last wrote there, or nothing. What was last written
// is remembered in Clips Kitty's own data folder, per settings file, not in
// the editor's file. The editor's menus and tooltips name each action's chord
// from the same table, so they follow by themselves.
//
// The same table is also written as "hostKeybinds" (FL2 X1), the editor's
// defaults inside Clips Kitty: Reset and Reset all in Settings > Shortcuts go
// back to it rather than to the bare letters. That key is Clips Kitty's own
// (the editor never writes it), so it is kept equal to the table at every
// start. This needed one fork change (PATCHES.md, "The host's own default
// shortcuts"); an editor without it ignores the key.
//
// Node built-ins only, so tests/test_ui_editor_fl2.py runs it under Node.
import { existsSync, mkdirSync, readFileSync, renameSync, statSync, unlinkSync, writeFileSync } from 'node:fs'
import { dirname, join } from 'node:path'

/** Raise when KITTY_KEYS changes: each settings file is then written once more
 *  (still never over a chord the person chose). */
export const KEYS_TABLE_VERSION = 1

/** The editor's action ids (keymap.rs ACTIONS) and their chords inside Clips
 *  Kitty: the eight bare-letter defaults of Concat v0.2.12, each on a free
 *  Ctrl or Ctrl+Shift chord (none the table ships or keymap::reserved keeps
 *  for the window: Ctrl+O, Ctrl+W, Ctrl+Q, F10, Alt+F/E/V), and the two
 *  delete actions without their Backspace aliases. An entry replaces the
 *  action's whole default list (keymap::answers). Space, the arrows,
 *  Home/End and every Ctrl chord the editor ships stay as they are. */
export const KITTY_KEYS: Readonly<Record<string, string>> = Object.freeze({
  freeze: 'ctrl+f',
  'delete-left': 'ctrl+shift+q',
  'delete-right': 'ctrl+shift+w',
  'split-tool': 'ctrl+shift+b',
  mute: 'ctrl+m',
  pan: 'ctrl+h',
  snap: 'ctrl+n',
  'preview-axis': 'ctrl+shift+s',
  delete: 'delete',
  'ripple-delete': 'shift+delete'
})

/** The editor's folder name (concat-host brand.rs IDENTIFIER). */
export const EDITOR_IDENTIFIER = 'com.clipskitty.editor'
/** Its settings file (concat prefs.rs FILE), in its config folder. */
export const EDITOR_SETTINGS = 'settings.json'
/** Its key for the host's own default chords (concat prefs.rs
 *  host_keybinds, FL2 X1): what Reset in Settings > Shortcuts goes back to. */
export const HOST_KEYS = 'hostKeybinds'

/** Where the editor at `exe` keeps its settings (concat-host dirs.rs): a
 *  `portable` folder beside the program when there is one, else
 *  %APPDATA%\com.clipskitty.editor. The editor is started with this
 *  process's environment, so it reads the same APPDATA. Null when neither
 *  can be told. */
export function editorSettingsPath(exe: string, env: Record<string, string | undefined>): string | null {
  const portable = join(dirname(exe), 'portable')
  try {
    if (statSync(portable).isDirectory()) return join(portable, EDITOR_SETTINGS)
  } catch {
    // no portable folder
  }
  const appData = env.APPDATA
  return appData ? join(appData, EDITOR_IDENTIFIER, EDITOR_SETTINGS) : null
}

export interface KeysRecord {
  table: number
  /** What Clips Kitty last wrote for each action in that file. */
  wrote: Record<string, string>
}

/** The keybinds after Clips Kitty's table is laid over `current` (what the
 *  file holds), given what it wrote last time (`last`): each action of the
 *  table is set where the file holds nothing for it, or still holds what was
 *  last written; anything else is the person's choice and stays. Every other
 *  action in the file is left as it is. `wrote` is what to remember: the
 *  chord written, or for an action the person changed, what had been written
 *  before (so a later table still knows the change was theirs). */
export function mergeKeybinds(
  current: Record<string, unknown> | null | undefined,
  last: Record<string, string> | null | undefined,
  table: Readonly<Record<string, string>> = KITTY_KEYS
): { keybinds: Record<string, unknown>; wrote: Record<string, string>; changed: boolean } {
  const keybinds: Record<string, unknown> = { ...(current ?? {}) }
  const wrote: Record<string, string> = {}
  let changed = false
  for (const [id, chord] of Object.entries(table)) {
    const there = keybinds[id]
    const before = last?.[id]
    if (there === undefined || there === chord || (before !== undefined && there === before)) {
      if (there !== chord) changed = true
      keybinds[id] = chord
      wrote[id] = chord
    } else if (before !== undefined) {
      wrote[id] = before
    }
  }
  return { keybinds, wrote, changed }
}

function readJson(path: string): unknown {
  return JSON.parse(readFileSync(path, 'utf8').replace(/^﻿/, ''))
}

/** Written beside the file and moved over it: a reader never sees half. */
function writeWhole(path: string, value: unknown): void {
  mkdirSync(dirname(path), { recursive: true })
  const staging = `${path}.${process.pid}.kitty.part`
  try {
    writeFileSync(staging, JSON.stringify(value, null, 2), 'utf8')
    renameSync(staging, path)
  } catch (e) {
    try {
      unlinkSync(staging)
    } catch {
      // nothing left behind
    }
    throw e
  }
}

function recordKey(settingsPath: string): string {
  return settingsPath.replace(/\//g, '\\').toLowerCase()
}

export type KeysOutcome = 'written' | 'current' | 'unreadable' | 'failed'

/** Lay Clips Kitty's chords into the editor's settings file at
 *  `settingsPath`, remembering what was written in `recordPath` (Clips
 *  Kitty's own data). The keybinds only when this file has not had this
 *  table version yet; "hostKeybinds" whenever it is not the table. Call it
 *  only while the editor is not running. Never throws:
 *
 *  - 'current': this file already had this table; nothing touched.
 *  - 'written': the file now has the table (or already held it).
 *  - 'unreadable': the file is there but is not settings (the editor sets
 *    such a file aside itself); nothing is written, and it is tried again
 *    at the next start.
 *  - 'failed': it could not be written; tried again at the next start. */
export function applyShortcutDefaults(settingsPath: string, recordPath: string): KeysOutcome {
  let records: Record<string, KeysRecord> = {}
  try {
    const found = readJson(recordPath)
    if (found && typeof found === 'object' && !Array.isArray(found)) records = found as Record<string, KeysRecord>
  } catch {
    // none yet, or unreadable: as if nothing had been written
  }
  const key = recordKey(settingsPath)
  const record = records[key]
  const tableDone = !!record && typeof record.table === 'number' && record.table >= KEYS_TABLE_VERSION

  let settings: Record<string, unknown> = {}
  if (existsSync(settingsPath)) {
    try {
      const found = readJson(settingsPath)
      if (!found || typeof found !== 'object' || Array.isArray(found)) return 'unreadable'
      settings = found as Record<string, unknown>
    } catch {
      return 'unreadable'
    }
  }
  // The table as the editor's own defaults (FL2 X1): "hostKeybinds", which
  // the editor never writes, is what its Reset and Reset all in Settings >
  // Shortcuts go back to (keymap.rs defaults_of), so a reset never puts an
  // action back on a bare letter. Clips Kitty's own key: kept equal to the
  // table at every start.
  const hostOk = sameKeys(settings[HOST_KEYS], KITTY_KEYS)
  if (tableDone && hostOk) return 'current'
  const next: Record<string, unknown> = { ...settings }
  let write = !hostOk
  if (!hostOk) next[HOST_KEYS] = { ...KITTY_KEYS }
  let merged: ReturnType<typeof mergeKeybinds> | null = null
  if (!tableDone) {
    const there = settings.keybinds
    const current = there && typeof there === 'object' && !Array.isArray(there) ? (there as Record<string, unknown>) : {}
    merged = mergeKeybinds(current, record?.wrote)
    if (merged.changed || !(there && typeof there === 'object')) {
      next.keybinds = merged.keybinds
      write = true
    }
  }
  try {
    if (write) writeWhole(settingsPath, next)
    if (merged) {
      records[key] = { table: KEYS_TABLE_VERSION, wrote: merged.wrote }
      writeWhole(recordPath, records)
    }
  } catch {
    return 'failed'
  }
  return 'written'
}

function sameKeys(found: unknown, table: Readonly<Record<string, string>>): boolean {
  if (!found || typeof found !== 'object' || Array.isArray(found)) return false
  const there = found as Record<string, unknown>
  const ids = Object.keys(table)
  return Object.keys(there).length === ids.length && ids.every((id) => there[id] === table[id])
}
