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
// The chords are written into the editor's settings file as data, by the
// main process, before it starts the editor (never while it runs), merged
// into the file without touching any other key, written beside it and moved
// over it. They go into "hostKeybinds" (FL2 X1), the editor's defaults inside
// Clips Kitty: its menus, tooltips and Settings > Shortcuts name each action's
// chord from there, and Reset and Reset all go back to it rather than to the
// bare letters. That key is Clips Kitty's own (the editor never writes it),
// so it is kept equal to the table at every start. This needed one fork
// change (PATCHES.md, "The host's own default shortcuts"), so it needs editor
// build 44879315991 or later; an older editor ignores the key.
//
// "keybinds" is the person's: Settings > Shortcuts writes an action there
// only when they change it. FL2's first writer (table 1) put the whole table
// there as well, so every action showed as changed (D2); now none of the
// table's chords is written there, and an entry Clips Kitty wrote (still
// what it last wrote, or the action's table chord) is taken out. A chord the
// person chose stays. What was last written is remembered in Clips Kitty's
// own data folder, per settings file, not in the editor's file.
//
// Node built-ins only, so tests/test_ui_editor_fl2.py runs it under Node.
import { existsSync, mkdirSync, readFileSync, renameSync, statSync, unlinkSync, writeFileSync } from 'node:fs'
import { dirname, join } from 'node:path'

/** Raise when KITTY_KEYS or the way it is written changes. 2 (FL2 D2): the
 *  table lives in "hostKeybinds" only, and what table 1 wrote into
 *  "keybinds" is taken out again (never a chord the person chose). */
export const KEYS_TABLE_VERSION = 2

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
  /** What Clips Kitty last wrote into "keybinds" for each action in that
   *  file (since table 2: nothing). */
  wrote: Record<string, string>
}

/** The keybinds with the entries Clips Kitty itself wrote taken out (FL2
 *  D2): since the table is the editor's defaults ("hostKeybinds"), an entry
 *  holding a table chord only makes Settings > Shortcuts show that action
 *  as changed. An entry of the table's actions is taken out when it is that
 *  action's chord in the table, or still what Clips Kitty last wrote there
 *  (`last`); any other entry is the person's choice (moved, or cleared to
 *  "") and stays, as does every other action. */
export function cleanKeybinds(
  current: Record<string, unknown> | null | undefined,
  last: Record<string, string> | null | undefined,
  table: Readonly<Record<string, string>> = KITTY_KEYS
): { keybinds: Record<string, unknown>; removed: string[] } {
  const keybinds: Record<string, unknown> = { ...(current ?? {}) }
  const removed: string[] = []
  for (const [id, chord] of Object.entries(table)) {
    if (!Object.prototype.hasOwnProperty.call(keybinds, id)) continue
    const there = keybinds[id]
    const before = last?.[id]
    if (there === chord || (before !== undefined && there === before)) {
      delete keybinds[id]
      removed.push(id)
    }
  }
  return { keybinds, removed }
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

/** Make Clips Kitty's chords the editor's defaults in its settings file at
 *  `settingsPath`: "hostKeybinds" kept equal to the table, and in
 *  "keybinds" none of the table's chords (cleanKeybinds), so Settings >
 *  Shortcuts shows nothing as changed that the person did not change. What
 *  was written is remembered in `recordPath` (Clips Kitty's own data). Call
 *  it only while the editor is not running. Never throws:
 *
 *  - 'current': nothing to change; nothing touched.
 *  - 'written': the file now holds the table as its defaults.
 *  - 'unreadable': the file is there but is not settings (the editor sets
 *    such a file aside itself); nothing is written, and it is tried again
 *    at the next start.
 *  - 'failed': it could not be written; tried again at the next start.
 *
 *  Needs an editor that reads "hostKeybinds" (build 44879315991 or later,
 *  the build the app ships with); an older one would show its own bare
 *  letters again. */
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
  const next: Record<string, unknown> = { ...settings }
  let write = false
  if (!sameKeys(settings[HOST_KEYS], KITTY_KEYS)) {
    next[HOST_KEYS] = { ...KITTY_KEYS }
    write = true
  }
  // The table's chords out of "keybinds" (D2): what an earlier Clips Kitty
  // wrote there (table 1 wrote the whole table) and an entry equal to its
  // default. Every start, so a person's chord stays and the rest is clean.
  const there = settings.keybinds
  if (there && typeof there === 'object' && !Array.isArray(there)) {
    const cleaned = cleanKeybinds(there as Record<string, unknown>, tableDone ? null : record?.wrote)
    if (cleaned.removed.length > 0) {
      next.keybinds = cleaned.keybinds
      write = true
    }
  }
  if (!write && tableDone) return 'current'
  try {
    if (write) writeWhole(settingsPath, next)
    if (!tableDone) {
      records[key] = { table: KEYS_TABLE_VERSION, wrote: {} }
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
