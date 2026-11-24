# Step K2: Clips Kitty Editor inside Clips Kitty (the contract)

Clips Kitty Editor is the full editor behind the Editor page: a separate program, based on Concat (AGPL-3.0-or-later), that the desktop app shows inside its own window. This file is the contract between the three parts:

- **the editor** (the fork, repository `clips-kitty-editor`): its command line and its `host.*` API are written down in that repository's `PATCHES.md`;
- **the engine** (this file's first half): where the editor is, its projects, taking exports back in, and the rules that keep a person's editor work from being lost;
- **the app** (Electron main process and the Editor page): the second half, written by the UI work of this step.

Nothing here is a public API: every route is internal and may change with the app.

## Engine

### What the engine does and does not do

- It finds the editor, builds a project for it with no window (the editor's own automation), and takes finished exports back in.
- It never starts the editor's window, never holds the window's token and never talks to the window: that is the main process's job.
- It takes no path from a client except a file the person picked (checked like `POST /videos/local`) and their export folder. An export is named by its file name only.
- A plain clipping run never loads any of this (`tests/test_editor_import_guard.py`).

### Files

| File | What |
|---|---|
| `core/binaries.py` | `editor_dir()`, `editor_exe()`: where the editor is |
| `kitty_editor/registry.py` | the projects' records (one folder and one manifest each) |
| `kitty_editor/automation.py` | the headless editor: `command(exe)`, `version_of(exe)`, `Session` |
| `kitty_editor/build.py` | `render_base`, `plan_clip`, `apply_clip_plan`, `build_whole`, `probe` |
| `kitty_editor/intake.py` | `take_in`, `recover` |
| `server/kitty_editor_api.py` | the routes |
| `core/state.py` | `PROJECT_EXPORTS_FOLDER`, `in_project_exports`, `StateDB.project_export_of`, `project_export_clips`, `data_dir` |
| `server/jobs.py` | `clip_lock`, the render job's and the translate job's second question, a re-rendered clip's projects follow its new id |
| `server/api.py`, `server/youtube_api.py` | `project_edits_kept`, `project_edits_left_out`, the `editor_export` key |
| `core/pipeline.py` | `_keeps_project_export` (a forced run and story reels keep an export) |
| `core/housekeeping.py` | never sweeps `data/clips/projects/` or `data/editor/`; keeps a download a project uses |

`kitty_editor/` is standard library only when imported; what it asks of the app it imports inside a function. `core/state.py`, `core/housekeeping.py` and `core/pipeline.py` never import it.

### Where things are kept

```
data/editor/projects/<id>/            <id> = 32 hex characters
    kitty.json                        the manifest; written whole (a temp file, then os.replace)
    media/<clip title>.mp4            a clip project's video: the whole window, framed, no captions
                                      (media/base.mp4 in a project made before K-D20); manifest `video_file`
    media/<video title>.<ext>         a whole video's project: a hard link to the Library's download
                                      (no copy); the download itself where the drive can't link
    media/music.<ext>, watermark.<ext>   a copy of the clip's music file and watermark picture
    p/<name>/                         the editor's own project folder (holds concat.lock while open)
    out/                              where the editor's Export writes; emptied as exports are taken in
    build.log                         what the headless editor logged while the project was built
data/clips/projects/<id>-<n>.<ext>    a clip project's export once taken in (n = 1, 2, ...)
```

No database table was added or changed. The manifests are the registry: listing is reading the folder.

The manifest, `kitty.json`:

```json
{"schema": 1, "id": "<32 hex>", "name": "My clip", "kind": "clip|video|file",
 "state": "building|ready|failed", "progress": 0.0, "message": "",
 "created_at": "2026-11-18T09:00:00.123Z", "last_opened_at": "2026-11-18T09:00:00.123Z",
 "canvas": {"width": 1080, "height": 1920, "fps_num": 30, "fps_den": 1}, "shape": "9:16",
 "project_dir": "<abs>/p/My clip", "export_dir": "<abs>/out", "export_name": "My clip",
 "clip": {"id": 5, "video_id": "abc", "start_s": 10.0, "end_s": 55.0,
          "file": "<abs path of the clip's file the project stands on>", "file_stamp": [12345, 1700000000000000000]},
 "video_id": "abc or null", "source_file": "<abs, kind file only>",
 "media": ["<abs paths the project uses outside its own folder>"],
 "notes": ["framing_baked"],
 "exports": [{"file": "My clip.mp4", "at": "<UTC>", "result": "replaced_clip|kept_beside|saved_to_folder",
              "path": "<abs>", "seconds": 36.9, "clip_id": 5}]}
```

- `clip` is null for a whole video and a file. `file_stamp` is `[size in bytes, mtime_ns]`.
- `clip.file` and `clip.file_stamp` are set when the project's video is made from the clip, and move to each export that replaced the clip. `clip.id` follows the clip when a render gives it a new id.
- `canvas` is null and `shape` may be empty until the build has ended. A clip project's `shape` is one of the four names when its size is exactly that shape, else `"<width>x<height>"`.
- Times are UTC with milliseconds (they also say which of two projects is newer).

### Finding the editor (E1)

`core.binaries.editor_dir()`: the folder named by `CLIPS_KITTY_EDITOR_DIR` when that variable is set (and nowhere else then); otherwise, in the installed app, `resources/editor` (`Path(sys.executable).parent.parent / "editor"`); it must hold `clips-kitty-editor.exe` (no `.exe` off Windows). No PATH lookup, no vendor folder: a checkout without the variable has no editor. Bundling the folder is step K3.

`GET /editor/status` answers `{"found", "version", "message", "reason"}`:

| `reason` | `message` | When |
|---|---|---|
| `""` | `""` | found: `version` is what `--version` printed after `clips-kitty-editor ` |
| `missing` | The editor is not installed with this copy of Clips Kitty. | no such file |
| `not_ours` | The editor found on this PC is not Clips Kitty Editor. | `--version` does not start with `clips-kitty-editor `, exits with an error, or does not run in 5 s |
| `windows_only` | The editor runs on Windows only. | any other system |

The `--version` run happens once per program file per engine start, with no console window.

`GET /editor/launch`: `{"exe", "cwd"}` (the program and its folder), or 503 `{"detail": {"message", "reason"}}`. Only the main process calls it.

### Routes (E3)

All need the `X-Clips-Kitty-Session` header and an Origin that is the app's own (`server.projects_api.local_origin`); 403 otherwise.

| Route | Body | Answer | Errors |
|---|---|---|---|
| `GET /editor/status` | | above | |
| `GET /editor/launch` | | above | 503 |
| `POST /editor/projects/open` | `{"kind":"clip","clip_id":5,"fresh":false}`, `{"kind":"video","video_id":"abc","shape":"16:9"}` or `{"kind":"file","path":"<picked>","shape":"16:9"}` | `{"project": Project, "created": bool}` | 400, 404, 422, 503 (below) |
| `GET /editor/projects` | | `{"projects": [Project]}`, last opened first | |
| `GET /editor/projects/{id}` | | `Project` | 404 |
| `POST /editor/projects/{id}/open` | `{}` or none | `Project` with `last_opened_at` now, plus `recovered` (a list of take-in answers, usually empty) | 404; 409 `reason: "building"`; 503 when a failed project must be made again and the editor is missing |
| `DELETE /editor/projects/{id}` | | `{"deleted": true}` | 404; 409 `reason: "building"`; 409 `reason: "open_in_editor"` |
| `POST /editor/projects/{id}/exports` | `{"file": "My clip.mp4", "export_folder": "<abs>" or null}` | a take-in answer (below) | 400, 404, 409 `reason: "busy"`, 500 |

- `id` must be 32 lowercase hex characters; anything else is 404 before a file is touched.
- 409, 503 and the 500 of a failed take-in carry `{"detail": {"message", "reason"}}` (`message` only for the 500); the others carry a plain sentence in `detail`.
- An unknown key in a body is refused by FastAPI itself with **422**, as everywhere in the app; a key that does not belong to the `kind` (a `shape` for a clip) is 400.
- `shape` is `16:9`, `9:16`, `1:1` or `4:5`: 1920x1080, 1080x1920, 1080x1080, 1080x1350. **FL3:** or `source` (also when it is left out; it was `16:9` before FL3): the video's picture size as shown, probed with `build.probe` (rotation and the sample aspect ratio counted: `shown_width`, `shown_height`), scaled down with its shape kept to at most 1080 on the short side and 3840 on the long side (`source_canvas`, never scaled up), each side even. The project is kept under the shape named the way a clip project's is (`9:16`, or `1440x1080` for none of the four) and its size in the manifest's `source_size` (`registry.canvas_of`). A project is found again only at the same shape and canvas size (`find_for_video` / `find_for_file` with `size`): on a 1080x1920 video "Same as the video" and `9:16` are one project; on a 720x1280 one they are two. A probe that could not read the file at all (`read` false: ffprobe missing or timed out, the file locked) answers 503 "could not read the video's file" (the page: "Clips Kitty could not read the video's file. Try again in a moment."); 400 "that file doesn't look like a video" is kept for a file read that has no picture.

`Project` is the manifest plus:

| Key | Meaning |
|---|---|
| `clip_changed` | a clip project whose clip is there, on another file (or the same file written again) than the one the project stands on |
| `clip_gone` | a clip project whose clip is no longer in the Library. It still opens and exports |
| `media_missing` | a file in `media`, or a ready clip project's `media/base.mp4`, is not there |
| `state` | a project left `building` by an engine that stopped is shown as `failed` with "This project was not finished. Open it again to make it." |

A project whose manifest can't be read is listed with `state: "failed"` (and `damaged: true`), never hidden; it can be deleted.

**Opening, by kind**

- `clip`: 404 for no such clip. When a project of this clip exists and `fresh` is not true, the newest is returned as it is (`created: false`), with `clip_changed` for the UI's question. Otherwise a new project is made (`fresh` leaves the older ones in the list). 503 when a build is needed and the editor is not found.
- `video`: 404 for no such video, and when its file is no longer in `data/downloads/`. The same video in the same shape returns the existing project; another shape is another project. The file is used in place and is listed in `media`.
- `file`: 400 `not a video file this app can open` when `core.paths.picked_file(path, _VIDEO_SUFFIXES)` refuses it (a folder, a network-share path, another kind of file, a file that is not there, a NUL in the name), and 400 `that file doesn't look like a video` for a file with no picture. No AI run and no Library entry. The same file in the same shape returns the existing project (choice 7).

**Building.** A new project answers at once with `state: "building"`. One background thread builds one project at a time, in the order asked. Progress is in the manifest (`GET /editor/projects/{id}`) and on the event stream as `{"type": "editor_project", "id", "state", "progress"}`, at most twice a second while building and once when it ends. A clip is held as in use while its project is built (`core.in_use.holding`). A build that fails leaves `state: "failed"` and a plain `message`; opening it again (either route) builds it again in the same folder. A project being built answers 409 `building` to `POST .../open` and `DELETE`.

### A clip's project (E2)

**The video** is `media/base.mp4`: the clip's whole window rendered by `core.pipeline._render_files` with the clip's own options, less `captions`, less `caption_lines`, less its `edit` block (cuts, speed, sound, hook, music), with `watermark: None`, and with the end card off (`kitty_editor.build.render_base`). So it carries the AI's framing, layout, colour filter and sound levelling, and nothing else; it runs from the clip's start to its end plus the render's own end pad. It is `multilingual.burn.clean_base`'s call, less the edits and the watermark that one keeps. It is made once and kept for the life of the project.

**Made the fast way when it can be (K-D24, ED28).** For a clip framed by the tracker's moving crop (the 9:16 crop filling the screen, or the face-safe 4:5 crop under the apps' top bar over a blurred copy of itself) the render is not run: `kitty_editor/fastbase.py` reads the crop back from the clip's own file (small grey copies of the clip's and the source's frames matched along the width at the tracker's sample frames, and at every frame between two samples where it jumps), then one FFmpeg pass crops the source there, lays it out as `video/cropper._render_tracked` does, with the clip's colour filter and loudness, untagged BT.601 colours as the standard render's piped frames give, and encodes it with x264 `ultrafast` CRF 16 on the CPU (a hardware encoder Clips Kitty's FFmpeg has is used as it is). The captions, a watermark or a logo in the clip's file take no part in the match (8 bands across the frame, the 5 that agree best). Any other clip, any frame that does not match (plain correlation under 0.5, or the fine detail under 0.3 unless the picture matches 0.95), or any error, falls back to the full render above, with the pipeline's own encoder (a faster encode of its cut was measured to change the tracker's layout choice) and the tracking models loaded while it cuts. Measured against the old base: the same crop to a tenth of a pixel, SSIM 0.98 to 0.99 at 5 points, one frame shorter at most, loudness within 0.1 LU.

**Reused (K-D26).** The manifest keeps `base_key`, a fingerprint of the clip's window, its framing options (not its captions' or watermark's), its file and the source (by path, size and time), and the tracking settings (`build.base_key`). A build that failed after the video was made, or a new project of the same clip (`fresh`), uses a video with the same key instead of making it again (another project's is copied, never linked: the editor may have that one open). A change to the window, the framing or either file makes it again.

- **Cost, measured** on this PC (RTX 3060, the 45 s window 30 to 75 s of the 1080p60 test video, the first render after the engine started, so with the tracking models loading): **82 s** from the request to `ready`, nearly all of it this render; the editor's part (building the project and reading it back) took 0.6 s. A whole-video or file project took **0.5 s**.
- **After K-D24** (2026-11-19, the 24.8 s clip of real_150s, 1080x1920 60 fps, face-safe layout, in the desktop app): "Open in editor" to the editor showing the project **15.1 s** (median of 3; 63.1 s before), of it the clip's video 12.6 s, the editor's project 0.4 s and the editor's start 2.0 s. A plain 9:16 clip's video takes about 7.7 s. The full render, when the fast way can't be used, 59 s (64 s before; the same file byte for byte).
- **Progress (K-D25).** The video's own progress is the project's from 5% to 80%: the fast way's frames read and encoded, the full render's time against an estimate from the window's length and frame rate (`build.time_share`: in step to 90% of it, then ever more slowly). It never goes back, and the page shows at most 99% until the editor has the project on screen; the page asks for the project at once when the engine says its build ended.
- The render runs on the build thread, beside the job worker: a build during a clipping run shares the graphics card with it.

**The canvas** is the base file's own width, height and frame rate.

**What goes on the timeline** (`plan_clip` works it out, `apply_clip_plan` makes the calls; tool names as in the editor's `docs/MCP.md`):

| Clip's setting | In the editor | How |
|---|---|---|
| the base video | `project_create(location=<id>/p, name, video)`, `media_import`, `clip_add` on the main lane at 0 | name = the clip's title made safe for a Windows folder (60 characters at most), else its hook, else "Clip <id>" |
| kept ranges (`edit.keep`), else the whole base | one piece per range, back to back | the base is cut at every edge (`clips_split`) and what is not kept is removed with the lane closing up (`clips_remove(ripple=True)`): a cut is taken back by dragging a piece's edge (choice 1) |
| `edit.mutes` | a muted stretch is its own piece with `muted: true` | cut at the same time as the kept ranges |
| `edit.mute_all` | every piece `muted: true` | |
| `edit.volume` | `clip_update(patch={"volume"})` on every piece | |
| `edit.speed` | `clip_speed(ripple=True)` on every piece | |
| `edit.fade_in`, `edit.fade_out` | `fadeIn` on the first piece, `fadeOut` on the last | |
| every piece | `clip_frame(framing="fill")` | |
| caption lines (`_clip_captions(row)`, counted from the clip's start) | `captions_add` at their program times | through the kept ranges and the speed (`video_editor.captions.remap_lines`): a line cut away is dropped, a line across a cut is held to what is kept. None when the clip has captions off |
| the other speaker's lines (second speaker on) | a second `captions_add` in that colour | marked as the render marks them (`paint_turns`, `tag_lines`) |
| caption style | `captions_add(style, offsetY)` | `color`; `uppercase` to `textCase: "upper"`; `font_size` (px at 1080x1920) to `fontSize = font_size / 1920`; today's black outline to `strokeWidth = 7 / 1920`; `font` to `fontFamily` when it is a font Windows has; `position` bottom / middle / top to `offsetY` 0.35 / 0 / -0.35, moved clear of a safe zone on a portrait clip |
| `edit.hook` | `title_add` at the top (`offsetY` -0.35) from 0 for its seconds | |
| `edit.music` (a file, or a free track's id) | the file copied to `media/`, a lane under every other (`track_add(bottom=True)`), `clip_add` from 0, repeated to the program's end and trimmed there, `volume` | |
| watermark text | `title_add` for the whole length at its corner, with its size, colour and opacity; `clip_transform(offsetX)` | the corner is approximate: 0.30 of the width and 0.44 of the height from the centre |
| watermark picture | copied to `media/`, a lane on top, the whole length, `clip_transform` (scale and corner offsets from its `scale` and `padding`), `opacity` | |
| then | `project_save`, `project_close(save=True)` | the folder's lock is let go: the window can open it |

**What is not carried**, as `notes` codes (the UI shows a sentence for each; an unknown code is to be ignored):

| Code | Meaning |
|---|---|
| `framing_baked` | always for a clip: the AI's framing is part of the video and can't be re-aimed in the editor |
| `captions_plain` | the clip lights up words or animates captions: in the editor they are plain text. Only when the clip has captions and one of the two is on |
| `font_default` | the caption font is not one Windows has, so the editor's own is used |
| `music_duck` | the music no longer dips under speech; its volume is the one set |
| `source_missing` | the original video is gone: the clip's finished file is the video, with its captions in the picture and its cuts fixed |
| `from_export` | the clip's file is already an editor export: that finished file is the video (as above), so the editor's earlier changes are kept |
| `watermark_still` | **added in this step:** a moving watermark is placed at one spot (the right edge's middle) |
| `music_missing` | **added in this step:** the clip's music file is gone, so there is no music in the project |

`source_missing` and `from_export` projects hold one piece and no caption, hook, music or watermark items. Never added: an end card.

### A whole video and a file

One piece, the whole source, shown whole in the chosen shape (`clip_frame(framing="fit")`: bars where the shapes differ), at the file's own frame rate (30 when it can't be read). The file is never copied. `DELETE /videos/{id}` works as today; the project then shows `media_missing`.

### The editor's automation, as the engine uses it

- Started as `[exe, "--mcp", "headless"]` (`kitty_editor.automation.command`, the one place that says so; tests put a stand-in there), with no console window, standard input and output as pipes, its log appended to the project's `build.log`.
- `CONCAT_MCP_ROOTS` = the project's own folder and the folders of the media it imports, and nothing else.
- MCP over standard input and output: `initialize` (protocol `2025-06-18`), `notifications/initialized`, then `tools/call`. Each call may take 120 s; on a timeout, a refusal that can't be read, or the editor ending, the process is killed and waited for. The session ends by closing its input (killed after 30 s).
- The engine uses none of the `host.*` API and none of the window's command-line options.

### Exports come back (E4)

`POST /editor/projects/{id}/exports`, `{"file", "export_folder"}`.

- `file` is any name the editor's export sheet can write (its `file_name()`): none of `/ \ : * ? " < > |` or a control character, no space at either end, not only dots, at most 255 characters, ending `.mp4`, `.mov`, `.mkv` or `.webm`; dots are fine anywhere (`Wait... what happened.mp4`, `.intro.mp4`). The editor's own unfinished work files (`.<name>.concat-video|audio|muxed.<ext>`) are refused. Else 400 (K-D6).
- The file must be a playable video: ffprobe finds a picture and a length above zero (`registry.probe_seconds`). An empty, broken or sound-only file answers 500 "The export isn't a video Clips Kitty can play." and stays in `out/`; nothing changes (K-D7). `out/<file>` must be a regular file directly in this project's `out/` (not a link); else 404. Nothing outside `out/` can be named, another project's file included.
- A name already taken in (recorded in `exports`, no longer in `out/`) answers 200 with the recorded answer and `again: true`.
- One take-in per project at a time; a report while one runs answers 409 `busy`.
- The answer: `{"result", "path", "folder", "file", "seconds", "again"}`, plus `clip_id` when a clip was involved and `reason: "clip_changed"` for `kept_beside`. `seconds` is the file's real length (ffprobe), kept in the manifest.

| Case | What happens | `result` | Events |
|---|---|---|---|
| a clip's project; the clip's row is on `clip.file` with the same `file_stamp` | under the clip's lock (`server.jobs.clip_lock`, which a re-render holds too): the export moves to `data/clips/projects/<id>-<n>.<ext>`; the row's `path` and `edited_at` are set; only then the old file is removed (when it is inside the data folder) with the clip's Quick edit preview; the manifest moves `clip.file` and `clip.file_stamp` to the export | `replaced_clip` | `{"type": "clip_file_replaced", "clip_id", "project_id"}`, `{"type": "queue"}` |
| a clip's project; the clip changed since | the export moves to `data/clips/projects/` and nothing else happens | `kept_beside` | `{"type": "editor_export", "project_id", "result", "path"}` |
| no clip to re-point (a whole video, a file, a deleted clip) | the export moves to `export_folder`, else the Downloads folder the app's own Export buttons use (Windows' Downloads known folder, wherever the person moved it; K-D22), else `data/clips/projects/`, named `<project name> - <YYYY-MM-DD>.<ext>` (copies get `-2`, `-3`). Across drives the copy's size is checked before the source goes | `saved_to_folder` | `editor_export`, as above |

- `export_folder` is used only in the last case. A network-share path, a relative path or a folder that can't be made is 400, with nothing moved.
- The clip's `start_s`, `end_s` and `render_opts` are **not** changed: they still describe the AI's window (what E5 guards). `GET /videos/{video_id}/clips` gives such a clip one more key, `"editor_export": {"seconds", "project_id"}` (`project_id` is `""` when the project was deleted); its `media_version` changes with the file as for every clip. A clip the editor never exported has no such key.
- Exporting twice: each export is one report; the second replaces the first as the clip's file, and the first is then the old file that is removed.
- A failure before the row is changed puts the export back in `out/` and answers 500 `{"detail": {"message"}}`; nothing is deleted and the clip is as it was.
- The app closed between an export's end and its report: `POST /editor/projects/{id}/open` takes in every export left in `out/`, oldest first, and returns the answers in `recovered`. Its body may carry `{"export_folder"}` (the app sends the one from Settings), used as above for an export with no clip to re-point (K-D10). The editor's unfinished work files are never taken, and a file changed in the last 3 seconds is left for the next time. A file that can't be taken in (not a video, a bad folder) stays in `out/`.
- A cancelled or failed export is never reported, so nothing changes. No end card is added to an export.

**Measured with the real editor** (headless, build 44025696954): a 35 s program at 1080x1920, 60 fps, exported in 14 s. While it rendered, `out/` held only `.<name>.concat-video.mp4` and `.<name>.concat-audio.m4a`; the final name appeared when it was complete (sampled every 0.2 s). Whether the window's export does the same is the fork's to confirm.

### Nothing of the person's is lost silently (E5)

**The rule:** a clip is an editor export when its file is directly in `data/clips/projects/` (`core.state.in_project_exports`: compared as the system compares paths, and by where links lead). It holds with no project left. `StateDB.project_export_of(clip_id)` is `""` for such a clip and `None` otherwise; the routes add which project it was (`kitty_editor.registry.project_of_export`: the `<id>-` at the start of the file's name, when that manifest is there).

| What | Rule |
|---|---|
| Apply `POST /clips/{id}/render`; the chat edit `POST /clips/{id}/ai-edit`; the caption save `PUT /clips/{id}/captions`; "Apply edits & upload" `POST /clips/{id}/publish` with `render_first` | 409 `{"detail": {"message", "reason": "project_export", "project_id": "<id>" or null}}` before anything is queued or any AI is asked, unless the body has `"replace_project_edits": true` (`render_first.replace_project_edits` for publish). `server.api.project_edits_kept` |
| the queue's `render` job | asks again when it runs, under the clip's lock: it fails with a plain sentence unless its payload has `replace_project_edits` (which the four routes add only when the person chose so) |
| Export in other languages, `POST /translate`, stage `export` with burned subtitles, a dub or subtitle files (subtitle files alone: only when the clip's last take-in did not keep every caption in place, `intake.subtitles_follow`) | 409 `{"detail": {"message", "reason": "project_export", "separate_file": true, "clip_ids", "count", "total", "project_id"}}` unless `"without_project_edits": true`. A batch is asked about once; `project_id` is set only when one clip is affected and its project is there. `server.api.project_edits_left_out` |
| the `translate` job | asks again when it runs, before any AI is set up and before anything is written |
| `POST /translate` for text only (the review stage, the post text), or only "include the video" | not asked: nothing is made from the clip's window |
| a forced run of the video; story reels made again (`core.pipeline._register_clip`, `_sport_reels`) | the clip stays on its export (`_keeps_project_export`); the fresh render stays on disk for the run |
| Make clips again | the clip counts as edited (`edited_at` is set at take-in) and is kept |
| Storage clean-up | never sweeps `data/clips/projects/` or `data/editor/`; a download a manifest's `media` names is not freed as an orphan |
| everything that only reads the clip's file (exports in a size, every publish without `render_first`, campaigns, Discord, watch) | unchanged: they use the export |

The messages (the UI shows its own strings and reads only `reason`): "This clip was changed in the editor. Applying changes here starts again from the clip as it was before, so those changes would be lost." and, for separate files, "This clip was changed in the editor. The new files are made from the clip as the AI made it, without the editor's changes."

A clip the editor never exported has no file in `data/clips/projects/`: each rule is one path comparison that says no, and every render, argv snapshot and golden file is as before.

**Ported from the hand-built editor (commit b43d905)**, with the smallest changes that keep the behaviour:

| From | Taken | Changed |
|---|---|---|
| `core/state.py` | `PROJECT_EXPORTS_FOLDER`, `in_project_exports`, `project_export_of`, `project_export_clips`, `data_dir` | `project_export_of` is the file test alone (no `project_exports` table) |
| `server/api.py` | the four request fields, `project_edits_kept`, `project_edits_left_out`, their calls in the caption save, the chat edit, Apply and `/translate` | new sentences; the project is looked up from the file's name |
| `server/youtube_api.py` | `RenderFirst.replace_project_edits` and its check | none |
| `server/jobs.py` | `clip_lock`, `PROJECT_EXPORT_KEPT`, `_rerender_clip` taking the lock and asking again, the translate job's question | without the export compiler's hooks (`before_register`, `while_waiting`, `render_opts_replace`) |
| `core/pipeline.py` | `_keeps_project_export` and its two calls | none |
| `core/housekeeping.py` | the exports folder is never an orphan clip | manifests read as plain JSON in place of the `projects` tables |
| `tests/test_step8_routes.py`, `tests/test_step8_engine.py` | the tests under "A clip whose file is a project export" and "Everything made from a clip's own window" | `tests/test_kitty_editor_guards.py`, rewritten for the file rule |

### Choices made where the contract was open

1. **Pieces are cut out of the whole base** (`clips_split`, then `clips_remove` with the lane closing up), not added and trimmed one by one. The result is the same (each piece keeps its handles, so a cut is taken back by dragging), with fewer calls and no id to look up per piece. Checked against the real editor: the removal closes the lane in one call.
2. **An export keeps its own extension** (`.mp4`, `.mov`, `.mkv`) when it is moved (`registry.unique_target`); `server.editor_lane.unique_target` only knows `.mp4`.
3. **`install()` takes one more argument,** `clip_captions`, the app's own `_clip_captions`, so the engine reads a clip's caption lines the way the Captions tab does.
4. **A re-rendered clip's projects follow its new id** (`registry.follow_clip_id`, called from `server/jobs.py` inside the function, next to the row's own `follow_clip_id`). Without it every project of a clip changed in Quick edit would say "deleted" instead of "changed".
5. **`from_export` whenever the clip's file is already an editor export,** also for `fresh: true`: re-making the base from the AI's window would drop the editor's earlier changes without a word.
6. **`captions_plain` only when something is really not carried** (the lit-up word or an animation is on, and the clip has captions).
7. **The same file from the PC in the same shape reopens its project,** as a Library video does.
8. **A failed project is made again by either open route;** a project left `building` by a stopped engine is shown as failed.
9. **Two more note codes,** `watermark_still` and `music_missing`.
10. **Short music is repeated** to the program's end (today's render loops it); ducking is not carried (`music_duck`).
11. **Recovery never takes a file whose name starts with a dot or that changed in the last 3 seconds:** those are the editor's own files of an export still running.
12. **`kept_beside` also sends an `editor_export` event,** so a window that missed the answer can still tell the person.
13. **With no export folder given,** what the app's own Export buttons use when Settings has none (`lib/exportFolder.ts`: Electron's `app.getPath('downloads')`, Windows' Downloads known folder): the main process sends that folder when the page gives none or one that is not a full path, and the engine, given none, asks Windows for the same folder (`intake.downloads_folder`, SHGetKnownFolderPath), else `~/Downloads`, else `data/clips/projects/` (K-D22).
14. **Caption fonts by name work for fonts Windows has:** checked with the real editor's `preview_frame` (Arial, Impact and Courier New each draw differently; a name nobody has draws as Arial does).
15. **The safe-zone row:** `0.5 - (the zone's edge + 20 px + the font size) / 1920` from the centre, never further out than the plain row.

### Tests

| File | What | CI light install |
|---|---|---|
| `tests/test_kitty_editor_engine.py` | the resolver; the records; `plan_clip` (kept ranges, mutes, speed, program-time captions, style, notes); projects built through the stand-in; the automation's failures; take-in for every case, twice, told twice, bad names, a crash; Storage | runs |
| `tests/test_kitty_editor_routes.py` | every route's guard; the status words; opening a clip, a video, a file; path refusals; list, reopen, delete; the exports route; the base render's options | skips (needs FastAPI) |
| `tests/test_kitty_editor_guards.py` | E5 | skips (needs FastAPI) |
| `tests/test_kitty_editor_real.py` | a clip project and a whole-video project built with the real editor and read back with `timeline_read` | skips unless Windows, `CLIPS_KITTY_EDITOR_DIR` and `CLIPS_KITTY_EDITOR_TEST_VIDEO` are there |
| `tests/test_editor_import_guard.py` | extended: nothing the pipeline loads imports `kitty_editor`; the package is standard library only; the database and the worker never import it at their top | runs |
| `tests/fakes/fake_kitty_editor.py` | the stand-in: the editor's MCP tools the builder uses, with the same arithmetic for a cut, a removal, a speed change and a trim | |

### Left for later

- **K3:** bundling the editor to `resources/editor`, its size, signing, and the data folder inside the Store package.
- The builder was developed against the unbranded build 44025696954 (its program is `concat.exe`, so the resolver does not find it: `GET /editor/status` says `missing`). Checked again with the branded build 44399491727 (`clips-kitty-editor 0.2.11`): `GET /editor/status` says `found: true`, `tests/test_kitty_editor_real.py` passes (2 tests), and a clip project built through the routes from the 45 s window of the test video was `ready` in 89.6 s (the base render, with the tracking models loading) and read back with `timeline_read` as four pieces (10 s, 5 s, the 1 s muted stretch, 19 s; 35 s in all), the hook, the watermark text and four caption lines at their program times, with the folder's lock let go.
- Progress while the base renders is two steps (5 %, then 80 %): the render gives no figure of its own.
- A build runs beside a clipping run and shares the graphics card; it is not queued behind the job worker.
- The watermark's corner and size are approximations; a moving watermark stands still.
- Caption lines keep their text and times; the lit-up word, animations and Clips Kitty's own bundled caption fonts are not carried.
- A `.mov` or `.mkv` export taken in as a clip's video keeps its extension; whether the Library's player shows it was not tried.
- An export kept beside a changed clip stays in `data/clips/projects/` until the person removes it: Storage never sweeps that folder.
- K4 to K8 as planned.

## The app

### What the app does and does not do

- The main process (`ui/src/main/editor.ts`) is the only part that starts, talks to and stops the editor. It asks the engine where the program is (`GET /editor/launch`) and where a project lives (`GET /editor/projects/{id}`); the page only ever names a project by its id.
- The page (`ui/src/renderer/src/pages/Editor.tsx`) shows recent projects and the two ways to start one, or, with a project open, a 36 px strip of its own and the area the editor covers.
- Nothing of the editor is shown anywhere while `GET /editor/status` says `found: false` (read once at start, `lib/editorStatus.ts`), or when the window has no editor bridge (the browser shim, an older preload). The app then looks exactly as before.

### Files

| File | What |
|---|---|
| `ui/src/main/editor.ts` | start, switch, close; `host.state` polling; exports handed to the engine; IPC |
| `ui/src/main/index.ts` | `setupEditor(...)`, `watchEditorWindow(win)`; the quit path closes the editor first |
| `ui/src/main/updater.ts` | "Restart and install" closes the editor first (at most 3 s) |
| `ui/src/preload/index.ts` | `window.studio.editor` |
| `ui/src/renderer/src/pages/Editor.tsx` | the page, the strip, the area and its rectangle |
| `ui/src/renderer/src/lib/editor.ts` | the page's state outside the page; `openInEditor`, `openEditorProject`; notices; notes |
| `ui/src/renderer/src/lib/editorStatus.ts` | found or not, read once |
| `ui/src/renderer/src/lib/editorCover.ts` | the cover gate (`useEditorCover`) |
| `ui/src/renderer/src/lib/projectExportPrompt.ts`, `components/editor/ProjectExportPrompt.tsx` | Quick edit's question before it makes an editor export again (ported from b43d905, new words) |
| `ui/src/renderer/src/lib/api.ts` | `editorApi` (the routes above), `renderRequest` / `newFilesRequest` (E5) |
| `tests/test_ui_editor_page.py`, `tests/test_ui_editor_main.py` | source tests |

### Starting the editor

`show(projectId, {exportFolder, destination})` (IPC `editor:show`): the same project only shows it; another project is opened with `host.open_project` (refused while an export runs: the page says "The editor is exporting. Open another project when it has finished."); if that fails the editor is closed (`host.close`, up to 10 s) and started again. With no editor running:

```
clips-kitty-editor.exe --embed <hwnd> --bounds x,y,w,h --api 127.0.0.1:<free port>
    --project <project_dir> --export-dir <export_dir> --export-name <export_name>
    [--export-note <destination>] --hidden
spawn options: detached: true, stdio: 'ignore', windowsHide: true, cwd: the Videos folder,
    env: the app's own plus CONCAT_EMBED_TOKEN (64 hex characters, new at each start),
    without ELECTRON_RUN_AS_NODE and without the engine's session secret
```

`destination` is the page's own line for the editor's Export sheet, shown read-only under the name (UX round; fork 211c751d): "Replaces this clip in the Library", "Saved next to this clip, which was changed since", "Export folder: <folder>" or "Goes to your Downloads folder". The main process keeps it per project (control characters out, at most 400 characters) and passes it as `--export-note` at start and as `destination` with `host.open_project`. An editor build older than 211c751d does not know `--export-note` and exits with code 2 ("The editor could not start.").

The sheet's name starts as `export_name`: a clip's name for a clip project, "<name> - <date>" for a whole video, a file, or a clip project whose clip was deleted (computed when the project is read, so the date is the day it is opened). What the person types there is the file's name in the export folder (copies -2, -3); a name equal to the project's plain name, as older builds started the sheet with, gets " - <date>" added.

Then `host.state` every 100 ms until it answers (at most 20 s). No answer, or `hostApi < 2`: "The editor could not start." `graphics.software`: it stays hidden and the page says "The editor cannot use this PC's graphics card, so it cannot be shown here." Otherwise `host.set_bounds`, and `host.set_visible` when the page wants it shown.

While it runs, `host.state` every 500 ms (every 2 s while hidden and not exporting). Each new `exports` entry (`seq` above the last seen): `done` goes to the engine as `POST /editor/projects/{id}/exports {file: <its file name only>, export_folder}`, matched to its project by the folder the editor reports (normalised: slashes and case); `failed` / `cancelled` call no route. Hidden for 10 minutes, not exporting, nothing unsaved: closed (`ended: "idle"`); coming back to the page starts it again on the same project.

Closing is always `host.close` (it saves first), then up to 10 s for it to leave. It is never killed: one whose window is gone saves and leaves by itself.

| Quit route | What happens |
|---|---|
| closing the window, the tray's Quit, File > Exit | `before-quit`: the model download question as before; then, when the editor is exporting, "The editor is still exporting. Quit and stop the export?" ("Keep exporting", the default, or "Quit"); then `host.close` and up to 10 s, before the engine and Ollama are stopped |
| `window-all-closed` | `host.close`, at most 3 s, then as before |
| Restart and install (updater) | `host.close`, at most 3 s, then the installer |
| Windows shutting down, an app crash | the editor's own watcher sees the window gone, saves and leaves |

A change of screen scale (`display-metrics-changed` with `scaleFactor`, or the window moved to a screen with another scale): the editor is closed and started again on the same project with new bounds (a child window gets no DPI message). Never while it exports (closing would stop the export): it is only re-placed until the export has ended, then restarted (K-D8).

### IPC (`window.studio.editor`)

Every call checks it comes from the main window's own page (as `plugin-session` does).

| Call | Does |
|---|---|
| `show(projectId, {exportFolder})` | start, switch or only show; `{ok, error}` with `error` `exporting`, `missing`, `no_project`, `cannot_start` |
| `setBounds({x, y, width, height})` | physical pixels of the window's client area; remembered for the next start |
| `setVisible(visible)` | `host.set_visible` |
| `focus()` / `blur()` | `host.focus` / `host.blur` (blur only when the editor has the keys, asked fresh) |
| `close()` | `host.close`, back to the lists |
| `prepare()` | (FL2) start the editor hidden with no project while one is made; nothing when one runs |
| `unprepare()` | (FL2) close an editor `prepare()` started that never got a project |
| `reveal(projectId, index)` | Explorer on that project's recorded export (`-1` = the latest); the path comes from the engine |
| `ask({message, detail, buttons, defaultId, cancelId})` | a native question, drawn above the editor; the button's index |
| `getState()`, `onState(cb)` | `{running, projectId, visible, focused, dirty, exporting, software, error, ended, code}` |
| `onExport(cb)` | the engine's take-in answer (plus `project_id`), or `{result: "failed" or "cancelled", error}` |
| `onMeasure(cb)` | measure again (resize, maximise, restore, full screen, scale change) |

### The page

- Sidebar entry "Editor", right after "Clips" (called "Clip Editor" until KC), only while the editor is found.
- No project shown: "Recent projects" (kind as an icon with a tooltip, last opened, delete with a native confirm, a bar while a project is being made), "Edit a whole video" (a Library video and a shape), "Edit a file from your PC" (a shape, then the file picker).
- A project shown: the strip ("← Projects", the name, one-line notes, export notices, "Clip approvals (n)", and "Saved" / "Saving…" / "Exporting 42%" at the right) and the area. The area's rectangle goes to the main process from a `ResizeObserver` (on the area and the page), `window` resize, a `matchMedia` resolution change and `onMeasure`, at most once per frame, in physical pixels (x and y floored, width and height rounded, at least 1).
- "← Projects" calls `close()` (the editor saves and leaves); while it exports it first asks natively "The editor is still exporting. Close the project and stop the export?" ("Keep exporting", the default, or "Close").
- Leaving the page hides the editor (`setVisible(false)`); the editor and its work stay as they were. The shown project lives in `lib/editor.ts`, not in the page, so coming back needs no reload.

### The editor is always drawn over the web page (U3)

The editor is a native child window above the page. One gate, `lib/editorCover.ts`: `useEditorCover(open)` is a counted hold; the page asks for the editor to be shown only while a project is shown and no hold is taken. While covered, the area shows the page's plain background.

| Thing (drawn from `App.tsx` or the sidebar) | Rule |
|---|---|
| `SetupWizard` (full window) | holds the cover while open |
| `FeedbackHub`'s dialog (sidebar) | holds the cover while open |
| `ProjectExportPrompt` (mounted in `App.tsx`) | holds the cover while open |
| `ApprovalCard` (`ApprovalCards`, fixed at the bottom right; its "Contact your attorney" `<dialog>` lives inside a card) | while a project is shown the cards are not drawn; the strip shows "Clip approvals (n)", which shows them with a Close button and holds the cover until they are closed. A clip-approval notification clicked meanwhile opens them the same way |
| `UpdateBanner` | in the page's flow above the page: outside the area, and the rectangle follows it |
| `ModelSwitcher` | in the sidebar; its list is a native select popup, a window of its own |
| the page's own questions (delete a project, "This clip was changed in Quick edit…") | native message boxes (`window.confirm`, `editor.ask`) |
| export notices, project notes | in the strip |
| `title` tooltips, the Alt menu, native dialogs, notifications | windows of their own, above the editor |
| the page's own Publish window (`PublishAllDialog` for the shown clip, FL3) | holds the cover while open (`useEditorCover(publishClip !== null)` in `pages/Editor.tsx`) |
| the page's own Other languages window (`components/editor/OtherLanguagesDialog.tsx`, KC) | holds the cover while open (`useEditorCover(languagesClip !== null)` in `pages/Editor.tsx`) |
| dialogs of other pages (the Clips page's `PublishAllDialog`, ...) | cannot overlap: the editor is hidden on other pages |

`tests/test_ui_editor_page.py` reads every component `App.tsx` imports and fails on one that draws a fixed overlay, a portal or a `<dialog>` without a rule here.

### Ways in (U4)

| Where | Control | Request |
|---|---|---|
| a Library clip card (Clips page) | "Edit" (on hover, beside Publish), tooltip "Open this clip in Clips Kitty Editor" (KC; "Open in editor" before) | `{kind: "clip", clip_id}` |
| the clip's side panel (`ClipEditor.tsx`) | the big "Edit" (KC; it opened Quick edit, with "Open in editor" beside it) | the same |
| ~~Quick edit's full page (`EditorModal.tsx`)~~ | removed in KC with Quick edit | |
| ~~a Library video (Clip Editor page's toolbar)~~ | removed in FL3: a whole video is opened from the Editor page | |
| the Editor page | a recent row; "Edit a whole video"; "Edit a file from your PC" | by id; video; file |

KC: Quick edit is gone, so there is one "Edit" per place and it opens the editor. While the editor is not found, both stay, off (`aria-disabled`), with the tooltip "Clips Kitty Editor wasn’t found with this copy of Clips Kitty, so clips can’t be edited here." The paragraph below is the K2 history.

The new ways in say "Open in editor" (UX round: a plain "Edit" beside "Edit this clip", and in the header next to "Editing — <clip>", read as the same thing). Today's clip editor is unchanged; renaming its button "Quick edit" waits for the owner's OK, so the app's own words call it by the page it is on, "the Clip Editor" ("This clip was changed in the Clip Editor …"); "Quick edit" stays only in these docs and in code comments.

Every way in switches to the Editor page at once; a project being made shows its progress there (the page's list, or the strip when another project is open), and it opens when it is ready. A reopened clip project whose clip was changed in Quick edit since asks natively: "This clip was changed in the Clip Editor after this project was made." with "Open the project" (default) and "Start a new project from the clip" (`fresh: true`).

### When it cannot be shown

| Case | Words |
|---|---|
| not found | nothing is shown at all |
| cannot start (no answer in 20 s, `hostApi < 2`, or it ended within 20 s) | "The editor could not start." + "Try again" (tooltip "If it keeps happening, restart Clips Kitty.") |
| ended later by itself | "The editor closed unexpectedly. Your work is saved as you go." + "Open again" |
| drawing in software | "The editor cannot use this PC's graphics card, so it cannot be shown here." + "Try again" (tooltip "Updating the graphics driver usually fixes this.") |
| exporting, another project asked for | "The editor is exporting. Open another project when it has finished." |
| a project's notes | "Some things from the clip are not carried over." in the strip, the sentences in its tooltip, once per open |

After an export: `replaced_clip` "Saved as this clip's video." (KC: "Saved as this clip’s video, with your caption words." when `captions` is `followed`, "Saved as this clip’s video. Other languages keep the old caption words." with the reason in its tooltip when `kept`); `kept_beside` "This clip was changed meanwhile, so the export was saved next to it." (KC; it named the Clip Editor before) + "Show in folder"; `saved_to_folder` "Saved to {folder}" + "Show in folder"; `failed` "The export did not finish." (the editor's sentence in the tooltip); `cancelled` nothing. The notice is said to a screen reader: the page keeps a `role="status"` region (and a `role="alert"` one for a failure) that is always there, and puts the notice's words in it. "Exporting n%" is shown at every poll but announced only at its start and each quarter (a separate polite region); the percent shown never goes back (the editor starts its fraction again at 0 for an export's last step). The Clips page reads its clips again on `clip_file_replaced`, and a card of an editor export shows `editor_export.seconds` as its length.

### Keyboard and focus (U5)

- A click in the editor gives it the keys (the editor does that). A `pointerdown` or `focusin` anywhere in the app's own page calls `blur()` (one listener in `App.tsx`, only while an editor runs).
- The area is in the Tab order (`tabIndex=0`, `role="application"`, `aria-label` "Editor. Press Enter to use it, Ctrl+F6 to come back."): Enter or Space gives the editor the keys. Ctrl+F6 in the editor gives them back, and focus goes to "← Projects".
- When the editor takes the keys, no page button stays focused (Space would press it).
- The strip's state text is `aria-live="polite"`.

### Choices made where the contract was open (UI)

1. **The page switches at once** and shows a project's making there, rather than waiting where the request was made: a clip's project takes about 90 s to make (the base render), and the page is where its progress and any refusal are said.
2. **The page's state lives in a module** (`lib/editor.ts`), not in `App.tsx`'s React state: the same effect (it survives page switches), and a project being made keeps being made while the person is elsewhere.
3. **"Edit whole video" starts at 16:9.** The Library has no picture size for a video, so the "9:16 when taller than wide" default could not be read; the shape is chosen in the popover. *Replaced in FL3:* the engine probes the video, and "Same as the video" is the default.
4. **`focused` is part of `onState`**, so the page can move focus to "Projects" after Ctrl+F6 (told apart from a click in the app by the click itself).
5. **`blur()` asks the editor fresh** whether it has the keys (the last poll can be half a second old), so a quick click in the app after one in the editor still gives the keys back.
6. **The engine's session secret is removed from the editor's environment** (the editor never needs it).
7. **"Show in folder" shows the project's latest recorded export** (`reveal(id, -1)`).
8. **The quit question is in English,** as `confirmQuit`'s is (the main process has no translations).
9. **A failed project is made again once** when it is opened again (the engine's choice 8); if that fails too, the page says why instead of trying again.
10. **Quick edit's "Nothing was made."** shows as a plain line, not as an error (`NothingMade` has an empty name).

### Tests (UI)

| File | What | CI light install |
|---|---|---|
| `tests/test_ui_editor_page.py` | t() on the page; the nav entry, the page and every way in behind the status; the cover list kept honest; hidden while covered and when left; bounds; keyboard and screen reader; native questions; Quick edit's question | runs |
| `tests/test_ui_editor_main.py` | the spawn options; the token only in the environment; never killed; every quit route closes it first; IPC from the app's window only, projects by id; switching; exports by name only; the preload | runs |

### Checked in the real app (2026-11-18, build 44439018733, the desktop sandbox)

| Check | Result |
|---|---|
| "Edit" on a Library clip card | the Editor page, the editor inside the window with the clip's video and its caption items; a new project was ready and shown 69 s after the click |
| playing | Space in the editor plays and pauses (00:00:07:16 while playing) |
| a cut, a caption change, Export | the clip's file became `data/clips/projects/<id>-1.mp4`; the strip said "Saved as this clip's video."; the Library card showed 25 s and its player the new picture (24.83 s, the changed caption) |
| Quick edit's Re-render on that clip | the question, first focus on "Open in editor"; Cancel left "Nothing was made." and queued nothing |
| "Apply anyway", then "Edit" again | the clip was made again (new id); "Edit" asked natively "This clip was changed in Quick edit after this project was made." above the editor; "Open the project" opened it as it was |
| "Edit whole video" on the 2-hour test video, 16:9 | shown 2.0 s after "Open" (switched with `host.open_project`), 1920x1080 at 30 fps |
| a file from the PC, 1:1 | shown 2.9 s after it was picked; its export went to the export folder as `own_clip_45s - 2026-11-18.mp4`, "Saved to <folder>" with "Show in folder" |
| resize to 1200x800, maximise, restore, minimise and restore, back to 1440x900 | the editor's rectangle equalled the area's to the pixel each time |
| page switches | hidden on Settings and Clip Editor (`visible: false`), shown again on return with the work as it was |
| Feedback dialog over the editor | the editor hidden while it was open, shown again after |
| a sidebar tooltip beside the editor | drawn above the editor |
| keyboard | Tab from "Projects" reaches the area; Enter gives the editor the keys; Space plays; Ctrl+F6 gives them back and focus lands on "← Projects"; a click in the editor takes the keys and a click in the app gives them back |
| closing the app right after a cut | the editor saved at quit (its project file was written at the moment of closing) and left in 2.7 s; reopened after a restart, the cut was there |
| no Clips Kitty Editor (the unbranded build only) | `found: false, reason: missing`; no "Editor" in the sidebar, no "Edit" on the cards, no "Edit whole video" |

The screenshots are kept outside the repository.

### QC fix round 1 (2026-11-18)

One line each; the records are in `state.json` (K1 and K2 `defects`).

- K-D1 (open, environment): the walk through the real desktop app was not done because drive C: was full; QC reruns it once C: has room, with the newest editor build.
- K-D2 (fixed): the quit-while-exporting question is in the app's language: the page sends its words through t() (`editor:words`, again on a language change); English until then.
- K-D3 (not changed): the export folder is the one from Settings, which only the page keeps (localStorage `export-folder`); the engine has no copy, and every existing export passes it the same way. The engine checks it.
- K-D4 (fixed): CI's compileall lists every engine package (campaigns, gaming, kitty_editor and remote_render added; not the vendored third_party).
- K-D5 (not changed, fork): the launch screen's two guide links open the upstream guides on a click only; the launch screen is never shown embedded.
- K-D6 (fixed): exports named with dots (a title such as "Wait... what happened?") and `.webm` exports are taken in; `safe_name` keeps a run of dots as one.
- K-D7 (fixed): an empty, broken or sound-only export never replaces a clip's video; it stays in `out/`.
- K-D8 (fixed): a screen-scale change never closes the editor while it exports: the restart waits for the export to end and asks again just before closing.
- K-D9 (fixed): clip approvals can be opened from the Editor page's strip again (App renders ClipUpdatesProvider around `Shell`).
- K-D10 (fixed): a close asks the editor's state once more and takes in exports that ended since the last poll; a reopen sends the export folder for recovered exports.
- K-D11 (fixed, fork daafd34f, build 44460212388; its brand test passed in the build, the line itself not yet seen on screen): Settings' "Concat's mirror" download source and the "Concat sounds" line keep the upstream name; branding them claimed they were this build's.
- K-D12 (fixed): a manifest write on Windows is retried while another thread reads the manifest (os.replace was refused, access denied).

### QC fix round 2 (2026-11-19)

- K-D13 (fixed, tests only): `test_project_routes.py::test_the_routes_are_internal` leaves the `/editor/` routes out of its "project" filter; `test_kitty_editor_routes.py` checks the served `/editor/` routes are exactly the editor's eight and all internal.
- K-D14 (fixed, fork 34fa4320, build 44475550509: its i18n and brand tests passed in the build, both lines are in the exe; the open Downloads list not yet seen on screen; supersedes K-D11): the Downloads source reads "Project mirror (GitHub)" and the sound shelf's offline notice "Could not reach the sound library. Check the connection and try again."; both are the brand layer's own lines (`brand::OVERRIDES`), in English for every language, the locales untouched.

### Fix round 3 (2026-11-19, before the owner's click-through)

- K-D15 (not changed in Clips Kitty): typing into the embedded editor. Real key presses arrive whole; only injected VK_PACKET characters are lost when embedded, and a press on the caption box's border takes the keyboard from the field (both in the fork). Nothing in the main process, the page or a key hook is involved: no accelerator, global shortcut or before-input-event handler exists, and the focus stays on the editor's child window for every key.
- K-D16 (fixed): taking an export in as a clip's video never deletes a file another clip row still plays (two AI windows with the same whole-second start and end share `clip_<start>-<end>.mp4`) or a Make clips again run holds: `intake._discard_old` asks `core.remake.file_in_use` with the clip's lock and the database held, after the re-point, as DELETE /clips/{id} does. recover() takes exports in through the same path; deleting a project removes only its own folder (its media are its own copies or hard links).
- K-D17 (fixed): an export reported while a reopen's recover() takes the same project's exports in is never reported as failed: the route waits up to `intake.LOCK_WAIT` (120 s) for the project's lock and then answers what was recorded (`again: true`); the main process asks again (6 times, 2 s apart) on a 409 or an engine it can't reach. A file is taken in once however often it is asked about.
- K-D18 (fixed): `tests/test_kitty_editor_routes.py` and `tests/test_kitty_editor_guards.py` run in CI's Plugin platform job.
- K-D19 (fixed, test only): the long export name in `test_an_export_named_with_dots_comes_back` is as long as the tmp folder lets `out/<name>` stay under 260 characters (up to 120), still with dots.
- K-D20 (fixed): the editor shows people's names, never ids: a clip project's video is `media/<clip title>.mp4`, a whole video's is a hard link `media/<video title>.<ext>` to the Library's download (the download itself where the drive can't link: the editor's automation has no display name for a media item, and a 2-hour file is never copied). Both names are `registry.safe_name`'s, the one the project and its export are already named with (no character Windows refuses, no device name such as CON, at most 60 characters; `core.pipeline._safe_name` keeps device names). A title "Music" or "Watermark" gets " video" added, so it never meets the project's music or logo copy. The ids stay in the manifest (`media`, `video_file`). Deleting a project removes the link only; the Library's download stays. While a project links a download, deleting that download from the Library frees its space only when the project is deleted too.
- K-D21 (fixed): "Saved as this clip's video." has no tooltip (it was the internal `data/clips/projects` folder, which a screen reader read too); "Saved to <folder>" and the kept-beside notice keep the file's path.
- K-D22 (fixed): with no export folder in Settings, a whole video's or a file's export goes where the app's own Export buttons put theirs, Windows' Downloads folder as Windows knows it (on the test PC that folder was on another drive than `~/Downloads`, the engine's guess before).
- K-D23 (fixed): Make clips again keeps a clip that has a project in Clips Kitty Editor, exported or not (counted as edited), read from the projects' manifests as plain JSON (`core.remake._editor_project_clips`); nothing else in Make clips again changed.

### First open (2026-11-19)

- K-D24 (fixed): the first "Open in editor" on a clip took 63 s, 62.5 s of it the full render of the caption-free video. A clip framed by the tracker's moving crop now has its video made from the clip's own framing (above): 15.1 s to the editor showing the project for the 24.8 s 60 fps test clip, of it 12.6 s the video. The floor on this PC: decoding the clip and the source once each to read the crop (about 3 s, CPU-bound), and one x264 encode of every 1080x1920 frame (6 to 7 s; this PC's driver gives Clips Kitty's FFmpeg no NVENC), plus the editor's own start (about 2 s).
- K-D25 (fixed): the Editor page's bar sat at 5% for the whole render and jumped to 80%; it now moves with the render, never goes back and stops at 99% until the editor shows the project.
- K-D26 (new): a clip's video is reused by a rebuild or a new project of the same clip while its window, framing and files are unchanged (`base_key`).

### Black-box fix round (2026-11-20)

- K-D27 (not changed, by choice): the Export sheet's "~31 MB" before a 30 s wide export that came to 2.9 MB. The figure is Concat's own estimate (unchanged in the fork): the bitrate the file is encoded at (8 Mbps for 1080p at 30 fps) times the length, plus the sound. x264 keeps to that average only when the picture needs it; the test videos where the gap showed are synthetic, almost still pictures (0.15 Mbps as made), so the encoder spends far less. On real footage the estimate held (S8: "~16 MB" for 16.7 MB), and the sheet says the written file's real size once the export is done. A better estimate would need each source's bitrate added to the editor's media probe and project model, a fork build and a new rule, for a figure marked "~" that only errs high on near-still video. No work is lost and no file is written wrong.

### Left for later (UI)

- The editor's own Export sheet, if left open when another project is opened, keeps showing the previous export's "Exported" line with the new project's size (the fork's; reported to it).
- Not caused for real, only read in the code: "The editor could not start.", "The editor closed unexpectedly.", software drawing, a screen scale change, the 10-minute idle close, quitting while the editor exports.
- The Alt menu bar was not seen: with the keys in the editor, Alt went to the editor.
- Typing quickly into the editor's caption text box from the test driver dropped characters (the change still landed); a person typing was not tried. GUESS: the driver's speed.
- The page's new strings are in English in every language until the translators add them (t() falls back to English).
- K3 bundles the editor; until then it is found only through `CLIPS_KITTY_EDITOR_DIR`.

## FL2 (2026-11-20): Ctrl keys, a faster first open, fixes, the first Concat update

### The first Concat update (U1, U2; the fork's side)

- Concat v0.2.12 (995b2054) merged into `kitty/embed` (merge 1a74a02a; the branch at 4d70ba5a with the PATCHES.md records). The editor build is Actions run **44855595570**, built at 1fa05725 ("Clips Kitty Editor 0.2.12, based on Concat 0.2.12").
- Effort: about 1 hour, of it 41.5 minutes the Actions build; 52 upstream commits new to the fork, 25 files changed on both sides, 2 conflicts of one hunk each (`platform.rs`, `studio/edit.rs`), built on the first try. The details are in the fork's PATCHES.md, "Updating from Concat".
- Later fork commits on `kitty/embed`: F6 **b21a33d5** (words still being typed in a field are committed before `host.close` or a project switch), cd09d2d8 (a test fix) and X1 **0bac504c** (`hostKeybinds`, below). FL2 was checked with editor build **44879315991**, built at 0bac504c.
- A weekly upstream check (`.github/workflows/upstream-check.yml` on `kitty/embed`) syncs the fork's `main` with Concat's and opens an "Update to Concat vX" pull request for a new release. Two owner steps are open: the schedule only fires from the default branch, and a `KITTY_SYNC_TOKEN` secret is needed when upstream commits touch workflow files. **Corrected 2026-11-23:** since fork commit 8c1a5c2b the check needs no `KITTY_SYNC_TOKEN` and no secret at all. It runs from `kitty/embed`, now the fork's default branch, with a read-only token; it merges Concat's newest release in the runner only, builds a clean merge through `build-windows.yml` (`concat_tag`), ends red when an update is waiting, and pushes nothing (the fork's PATCHES.md, section 8). The kept merge is made on the owner's PC.

### Ctrl shortcuts (C1, ED31)

Inside Clips Kitty no shortcut is a bare letter, and Backspace never deletes a clip. No editor code changes: before it starts the editor, the main process (`ui/src/main/editorKeys.ts`) lays this table into the editor's own settings file, as `hostKeybinds` (the editor's defaults inside Clips Kitty, below) in `%APPDATA%\com.clipskitty.editor\settings.json` (or `portable\settings.json` beside a portable build): an action's id to one chord, which replaces that action's whole default list.

| Action (id) | Concat v0.2.12 | Inside Clips Kitty |
|---|---|---|
| Freeze (`freeze`) | F | Ctrl+F |
| Delete left (`delete-left`) | Q | Ctrl+Shift+Q |
| Delete right (`delete-right`) | W | Ctrl+Shift+W |
| Split tool (`split-tool`) | B | Ctrl+Shift+B |
| Mute (`mute`) | M | Ctrl+M |
| Pan tool (`pan`) | H | Ctrl+H |
| Snap to edges (`snap`) | N | Ctrl+N |
| Preview axis (`preview-axis`) | S | Ctrl+Shift+S |
| Delete (`delete`) | Delete, Backspace | Delete |
| Delete and close the gap (`ripple-delete`) | Shift+Delete, Shift+Backspace | Shift+Delete |

- These are all the bare-key defaults in keymap.rs at v0.2.12 apart from the ones that stay: Space (play), Home, End and the four arrows (Shift+arrow steps ten frames). Every Ctrl chord the editor ships stays (save Ctrl+S, import Ctrl+I, export Ctrl+E, settings Ctrl+comma, undo Ctrl+Z, redo Ctrl+Shift+Z and Ctrl+Y, select all Ctrl+A, copy Ctrl+C, paste Ctrl+V, duplicate Ctrl+D, split at playhead Ctrl+B, zoom Ctrl+= or Ctrl++ and Ctrl+-), and none of the new ones is one of them or one the window keeps (Ctrl+O, Ctrl+W, Ctrl+Q, F10, Alt+F/E/V). Slint matches a window binding's modifiers exactly, so Ctrl+Shift+Q and Ctrl+Shift+W are not the window's Ctrl+Q and Ctrl+W (checked in the app).
- **How it is written:** read, the `keybinds` entries laid over, everything else in the file left as it is, written beside it and moved over it; never while the editor runs (only from `start()`, with no editor running). A file that is not JSON settings is left alone (the editor sets such a file aside itself) and tried again at the next start.
- **Never over the person's choice (since the UX fix round, D2):** `KEYS_TABLE_VERSION` 2. The table goes into `hostKeybinds` only, so Settings > Shortcuts shows the Ctrl chords and no action as changed. `keybinds` is the person's: none of the table's chords is written there, and at every start an entry Clips Kitty wrote is taken out, that is one still equal to what Clips Kitty last wrote for that action (remembered per settings file in Clips Kitty's own data, `editor-shortcuts.json` in the app's user data folder, not in the editor's file) or equal to the action's table chord. Any other entry (a chord the person moved, or cleared to none) stays, also one set before Clips Kitty ever ran. Table 1 (FL2 as first built) wrote the whole table into `keybinds` as well, so a reset arrow showed beside all ten actions; a file it left is cleaned at the next start.
- **An editor without `hostKeybinds`** (builds before 44879315991) would show its own bare letters again, since `keybinds` no longer carries the table. Clips Kitty does not try to tell builds apart: the app ships with a build that has it (K3 pins one), and the older builds exist only as test builds on the development PC.
- The editor's menus, tooltips and Settings > Shortcuts read the same table, so they name the Ctrl chords by themselves ("Split tool (Ctrl+Shift+B)", "Snap to edges Ctrl+N").
- **Reset goes back to the Ctrl chords (fix round, K-D36):** the same table is also written as `hostKeybinds`, the editor's defaults inside Clips Kitty. Its Reset and Reset all in Settings > Shortcuts go back to these chords, never to a bare letter or Backspace. This took one fork change (keymap.rs `defaults_of`, prefs.rs `host_keybinds`; PATCHES.md "The host's own default shortcuts"), so it needs editor build 44879315991 or later. `hostKeybinds` is Clips Kitty's own key, which the editor never writes, so it is kept equal to the table at every start; `keybinds` holds none of the table since D2. A typed field still takes its own letters, Backspace and Delete before any shortcut, as before.

### A faster first open (F1)

- **Full-frame 9:16 clips** (the source is already 9:16, so the tracker's crop is the whole frame): `fastbase.make` reads the clip's layout from 5 frames (the mean difference of the 5 of 8 bands that agree best, against the whole frame filling the screen and against the face-safe layout) and makes the base as an identity crop, with the clip's colour, the BT.601 untagging and the loudness as before. A face-safe clip of such a source, a letterboxed one, or one that matches neither goes the full way.
- **Vertical Live clips** of a 9:16 source: the base is the whole frame as the pipeline's single encode makes it (`modes.fit_filter`, the clip's colour, the source's own colours, the loudness), with no reading of the clip's file. A Vertical Live clip of another shape still goes the full way.
- **A crop that never moves** (a centred crop) is made the fast way too: its crop script is empty, which FFmpeg's `sendcmd` refused, so such clips went the full way (K-D31).
- Proof, against the clip's own render path's base (speech_vertical_20s's clip): SSIM 0.9994 to 0.9996 at 5 points, 600 frames each, -14.4 LUFS each; Vertical Live 0.9996; Vertical Live with the vintage look 0.9983; tracked with the vintage look 0.9988.
- **The editor starts while the project is made:** when the page waits for a project being built it calls `prepare()`; with no editor running, the main process starts one hidden with no `--project` (the Ctrl table is written first). `show()` then finds it running and opens the project with `host.open_project`, sets its bounds and shows it: the person never sees it empty. An open that ends without a project calls `unprepare()`, which closes only an editor that has none; one left without a project is closed 10 minutes after its start. A prepared editor that fails says nothing; the `show()` after it starts the editor again and says why if that fails too. The idle close counts from the editor's own start as well as from when the page was hidden.
- **Measured** in the desktop app (editor build 44855595570, RTX 3060, the project deleted between runs): speech_vertical_20s's clip **4.57 / 4.22 / 4.11 s** (18.5 s before); real_150s's 24.8 s clip **12.48 / 12.15 / 12.47 s** (13.6 s before). The editor process was running 0.2 to 0.8 s after the click.

### The base encoding (P1)

The fast base keeps libx264 ultrafast CRF 16 (DECISIONS.md, ED28's FL2 note). Measured in the desktop app on the real_150s clip (24.8 s, 1080x1920, 60 fps), against three other settings:

| Setting | Playback in the Editor page (of 60, median of 3) | First open | Picture (SSIM) | Base size |
|---|---|---|---|---|
| (a) ultrafast CRF 16 (kept) | 50.9 | 12.52 s | 0.9920 | 83.0 MB |
| (b) veryfast CRF 20 (the old base) | 50.9 | 16.43 s | 0.9919 | 22.3 MB |
| (c) ultrafast CRF 20 | 51.1 | 11.98 s | 0.9887 | 52.6 MB |
| (d) superfast CRF 18 | 50.9 | 14.92 s | 0.9942 | 47.7 MB |

- No setting reaches the 58 of 60 target: all four play at 50.9 to 51.1, so the encoding is not what limits playback. (c) is 0.5 s faster but softer than the renders' own encoding, and the editor's export encodes the base again. NVENC was not tried (the bundled FFmpeg has none on this driver).
- **Re-measured after FL2** (an engineer who wrote none of it): first open 12.14 s for the real_150s clip (13.63 s before) and 4.25 s for the speech_vertical_20s clip (18.47 s before); second open 1.35 s; the page shown again 0.03 s; playback 51.1 of 59.9 (51.6 before), still short of 58; the plain pipeline -2.3% to +3.5% of the base with byte-identical outputs and the golden check identical; app and editor 904 MB working set and 499 MB graphics memory, as before.

### Fixes (F2 to F5)

- **F2, K-D32:** `registry.load` reads the manifest under the lock its writers hold, and tries a refused read (a sharing violation while `os.replace` swaps the file) again for up to 0.5 s; a file that reads but is not JSON is not waited for. The two route tests that were flaky under load passed 50 runs in a row with a test loop beside them.
- **F3, K-D33:** the opening bar counts a failed project's progress as none and starts again when its build restarts (`lib/editorOpening.ts`, `nextOpeningProgress`).
- **F4, K-D34:** the Export sheet's line is Clips Kitty's. The page reads the shown project again when it is shown again (and before a start again), and when the line changed the main process sends it with `host.open_project` on the folder the editor already has open, which the editor answers at once without opening anything again (`embed.rs` `open_project`, `same_folder`). After the clip is deleted the sheet says "Export folder: …" and starts the name as "<name> - <date>".
- **F5:** state.json K-D15 fixed in the fork at ee029aff (field focus); K-D1 done (QC rounds 2 to 4 walked the desktop app).

### Black-box fix round (FL2)

- **S7, K-D35 (not a defect, no change):** "Edit a file from your PC" sends the path the dialog returns unchanged (`pickVideoFile` returns `filePaths[0]`; the page sends it with `JSON.stringify`). In the desktop app with a real backslash path through the dialog hook, the request carried the test file's full Windows path with all 5 backslashes, the engine answered 200 and own_clip_45s opened (16:9, 45 s). The reported path had lost exactly what a JavaScript string literal loses when a path is typed into it with single backslashes (`\c`, `\e`, `\m`, `\o` dropped, `\b` turned into a backspace character), so the test script's own quoting, not the app, broke it.
- **X1, K-D36 (fixed):** Reset in the editor's Settings > Shortcuts put Mute back on a bare M, and M alone then muted the selected clip. Fixed with `hostKeybinds` (section "Ctrl shortcuts" above): fork commit 0bac504c on `kitty/embed`, editor build 44879315991.

### UX fix round (FL2, 2026-11-22)

Checked in the desktop app on the owner's second monitor with editor build 44879315991.

- **D1, K-D37 (fixed):** when the window came back (Alt+Tab, the taskbar) and the editor had had the keys, they landed on "← Projects": Ctrl chords did nothing, and Space or Enter closed the project view. Leaving the window made the editor report that it lost the keys, which the page took for Ctrl+F6. Now (`ui/src/main/editorFocus.ts`, fed by `ui/src/main/editor.ts`): while the window is not active the page is not told the editor let go; when the window is activated again and the editor had the keys, the main process gives them back with `host.focus`, the same call a click in the editor amounts to (tried at 0, 150, 400 and 900 ms until the editor reports them; the page's own focus on activation does not take them meanwhile; a press in the page always wins, also one during the give back). When one of the app's controls had the keys, nothing is done and the page keeps its focus. Only this window's own activation counts: a dialog or the Feedback Hub keeps the keys, and a hidden editor gets nothing back. Ctrl+F6 in the page now also gives the keys to the editor (on F6's release), so Ctrl+F6 goes both ways; Tab from "← Projects" reaches the editor area and Enter enters it, as before. Checked: Ctrl+N toggled snap and Space played and paused after the window came back, no focus ring on the page; the sidebar kept the keys; a Feedback Hub field kept them ("abc" + "def" typed across the switch).
- **D2, K-D38 (fixed):** Settings > Shortcuts showed a reset arrow beside all ten actions Clips Kitty wrote. `keybinds` now holds none of the table ("Ctrl shortcuts" above). Checked: a fresh settings folder (a copy) shows the Ctrl chords and no arrow; a file as table 1 left it, with one chord of the person's, is cleaned at the next start and shows only that arrow; Reset and Reset all give the Ctrl chords; bare q w f m h n s b and Backspace do nothing to a selected clip; "kitty zebra words BRAKE jump" typed with real key presses lands exactly.
- **D3, K-D39 (fixed, in part):** an editor that never answered at its start is remembered (`lingering`) until it has gone, so the next start leaves the editor's settings file alone while it still runs. The next start still starts a new editor beside it, and `lingering` holds one pid only.
- **D4, K-D40 (docs):** this section's P1 table and fork commits; the base encoding in DECISIONS.md (ED28's FL2 note).
- **D5, K-D41 (docs):** the user guide names the buttons as they are ("Open in editor" on the clip card and in the clip's panel beside "Edit this clip").
- **D6, K-D42 (fixed):** "Edit a file from your PC" asks with a dialog titled "Choose a video to edit" (the page passes its own title through t(); `pick-video-file` keeps "Choose a video to make clips from" when none is given; Add videos' own dialog is unchanged).
- **D7, K-D43 (housekeeping):** the test editor's settings file had a resized layout left by the UX check; its `layout` is the editor's default again (none), the keys untouched.

### Tests (FL2)

- `tests/test_ui_editor_fl2.py`: the shortcut table against Concat v0.2.12's action table and reserved chords, the settings file's place, the merge into a real file under Node (a fresh file, a file with other settings and the person's chords, a second start, a newer table, an unreadable file), the main process writing it before the spawn; the bar and the sheet's line under Node; the prepare, unprepare and retell wiring.
- `tests/test_ui_editor_fl2_ux.py` (UX fix round): the keys' return under Node (the editor had them, an app control had them, a press wins, a give back that never lands, a hidden editor), the main-process wiring, Ctrl+F6 both ways, and D3. `tests/test_ui_editor_fl2.py` covers D2's writer (a fresh file, other settings and the person's chords, a table 1 file cleaned, an older table's entry, a table chord set again, the host's defaults, an unreadable file, the cleaning rule).
- `tests/test_kitty_editor_fl2.py`: the record read race (a refused read tried again, an unreadable one still damaged, two threads reading beside a writer), full-frame and Vertical Live bases from small FFmpeg videos, a face-safe clip left to the full render, a crop that never moves.

## FL3 (step F1, 2026-11-23): the fixes the owner saw, plus Publish

From his click-through of FL2: "when i open the editor it opens projects first", "i need a publish button", "the clip editor doesnt make sense with the edit whole video and it says 16:9 when its already 9:16". The captions on one lane come from the T0 editor build (45149430261), not from this step's code.

### Open straight to the clip (A)

- `pages/Editor.tsx` shows the project view while a project is being opened (`store.opening`), not only once one is shown (`shownId`). Before, the Projects screen stayed up for the whole build and the editor's start (about 1.3 s for a project made before, 4 to 12 s for a new one).
- The strip shows "Getting "<name>" ready… n%" (or "Getting the project ready… n%" until the engine has named the project) and a small bar; the area says "Opening the editor…" until the editor shows. What the editor said of an earlier run (could not start, closed unexpectedly) is not shown before a project is.
- "← Projects" during an open stops it (`openToken`) and shows the lists. An open that fails goes back to the lists with its reason, as before. The sidebar's "Editor" with nothing shown or opening still shows the lists.

### A whole video in its own shape (B)

- The Clip Editor page no longer has "Edit whole video" (its popover, its state and its `ShapeSelect` import are gone). A whole video is opened from the Editor page's "Edit a whole video".
- "Edit a whole video" and "Edit a file from your PC" offer "Same as the video" first and as the default, then 16:9, 9:16, 1:1 and 4:5 (`EditorShape` gains `source`).
- The engine: `shape` `source` or none probes the source before `registry.find_for_video` / `find_for_file` (above, under the routes). A 1080x1920 video opens at 1080x1920 with no bars; 16:9 picked by hand is its own project with bars, as before.

### Publish (C, review fix S1: one window, two ways in)

- A "Publish" button in the strip of a clip project whose clip is still in the Library. It opens the Clips page's own one-clip window (`PublishAllDialog` with that one clip) through the same provider: WoopSocial when it is set up, else Upload-Post (`lib/publishSetup.ts`, which the Clips page now uses too).
- It is on only when the project's last export was taken in as the clip's video (`replaced_clip`) and the clip has not changed since (`lib/editorOpening.ts`, `publishState`). Off, with the reason in its tooltip: no export yet ("Export first: Publish sends the clip in your Library."), the last export kept beside a changed clip, the clip changed after the export, an export running, or no publishing set up.
- The shown project is read again when an export is taken in (`rereadShownProject`, from `onExport`) and again just before the window opens. The editor is hidden while the window is open (the cover rule, table above).
- No engine change. It publishes the clip as last exported: edits made after that export are not in it until the next export (a real fix needs KL, live edits).
- When the clip cannot be read for the window, the strip says so (a failure notice): "Clips Kitty could not read the clip. Try again in a moment." when the Library could not be read, or "This clip is no longer in the Library." after which the project is read again and the button goes.
- Publish is on under the Clips page's rule (F1 desktop fix, U-4): once the export replaced the clip, whether or not a provider is set up; the provider is read when it is clicked and the window handles "nothing set up" as it does from the Clips page. Off is `aria-disabled` (still reached by Tab, the click ignored). The window keeps the keyboard inside it, Escape is Cancel, and focus goes back to Publish (`lib/useDialogFocus.ts`, shared with the other windows).
- An open that starts while a project is shown saves and closes that project the way "Projects" does (asked first while it exports; two opens at once share one question), so nothing of it (editor, name, notices, Publish) stays on screen. An open of the shown project itself (its id, its clip, or a video or file the engine answers with it) only shows the page: no close, no restart, no question. Export answers show in the strip only for the shown project. Entering the page again clears an earlier failed open. A failed open says why in the strip (role alert) and the area offers "Projects". "Projects" during a first open stops the open on the page only: the engine still makes the project and it stays in Recent projects.
- Not built here: the plan's "YouTubePanel when direct upload is on". It moved to KC, where YouTube upload goes inside the one Publish window.
- Publish and "Saved" sit together at the right of the strip (mockup 03). "Clip approvals (n)" shows only with a project shown: during an open the cards themselves are up.

### Stopping an open (fix round 1)

- `lib/editor.ts` `openReady` reads the show options first, checks the open's token, then calls `show()` at once. A `show()` that returns after the open was stopped, with nothing shown or being opened on the page, is followed by `close()`.
- `main/editor.ts` `closeEditor` reads which editor to close inside its call chain (`serial`), so a close sent while a start is in its first moments (before it has set `ed`) closes the editor that start made.

### Strings (FL3)

- Every new string goes through `t()` with its English fallback. Like the rest of the editor stack they have no locale entries yet: English only until the locale pass at K3.

### Tests (FL3)

- `tests/test_ui_editor_page.py`: the open goes to the project view; the strip, the area and "Projects" during an open; "Same as the video" first and the default; no "Edit whole video" on the Clip Editor page; Publish's window, provider, cover and words; `publishState` under Node.
- `tests/test_kitty_editor_routes.py`: a whole video with no shape or `source` opens at its own size and is found again by the same shape and size; 16:9 by hand is its own project; an odd size is kept and made even; a file from the PC in its own size; a 720x1280 video's 9:16 by hand is its own 1080x1920 project; a 4K and an anamorphic source; a failed source-shaped build made again keeps its size; a probe that could not read the file is a 503, not "doesn't look like a video".
- `tests/test_ui_editor_open.py`: `lib/editor.ts` run under Node with a fake bridge and `editorApi`: an open shows the project; "Projects" while the show options are read stops it before `show()`; a `show()` that returns after a stop is followed by `close()`; one that another open took over is not closed.

## KC (2026-11-23): one editor, subtitles and dubbing in it, YouTube in the Publish window

From the owner's click-through of F1: "it still says clip editor when it should say clips and the old editor is still there i don't need anymore if you put the stuff that is missing into the new editor subbing and dubbing"; earlier, "the subs and dubs should be in the editor and not clog the clips menu". KC and KR's removal run together; Concat's own features (music and audio import, volume and fades, colour looks and effects, titles and text, image overlays) count as parity and are not rebuilt.

### "Clip Editor" is "Clips"

- The sidebar (`App.tsx`, translated through the existing "Clips" key; the comment keeps the Store guard: the label before "Clip Editor" must never come back) and the page's title (`t('Clips')`).
- The Dashboard's tooltip "Open this clip in Clips", the approval card's "Open in Clips" (it opens the page for the video), the setup wizard's last page (links are pasted on the Dashboard).
- README.md's two mentions and the same two in each README translation, with the language's word for "Clips" from the app's locale files.

### Quick edit is deleted

Deleted, each used only by Quick edit (checked with `git grep` before): `components/TimelineEditor.tsx`, `EditorModal.tsx`, `ColorControls.tsx`, `FilterPicker.tsx` (only ColorControls), `EditChat.tsx`, `EditSuggestions.tsx`, `FreeMusicPicker.tsx`, `UploadPostPanel.tsx`, `lib/captionEdits.ts`, `lib/speakerTurns.ts`. `bakedToOrig` moved into `LiveTextOverlay.tsx`, its last user. Kept, used elsewhere: `CaptionStyleControls` (queue settings, the subtitles style), `GamingLayoutEditor` (the add-videos form), `WatermarkControls` (Creators, WatermarkCard), `FeatureBoundary`, `LiveTextOverlay`, `lib/editSuggestions.ts` (the card's and panel's suggestion lines), `lib/gamingLayout.ts`.

No engine route was removed. These now have no caller in the app, and stay because they are part of the local API (the MCP server, plugins, the bottom box) and later steps use them: `PUT /clips/{id}/captions`, `POST /clips/{id}/tighten`, `POST /clips/{id}/ai-edit`, `POST /clips/{id}/preview`, `GET/PUT /clips/{id}/creator-gaming-layout`, `GET /assets/library`, `POST /uploadpost/clips/{id}/publish` and `POST /woopsocial/clips/{id}/publish` (one clip's publish with per-platform text; the window uses the batch routes), and the provider refresh and retry routes. Their client functions stay in `lib/api.ts`.

### Subtitles and dubbing in the editor

- "Other languages…" in the strip, before Publish, shown when Publish is (`publishState(project) !== 'none'`: a clip project whose clip is there). It reads the clip from the Library and opens `OtherLanguagesDialog`.
- The window: `role="dialog"`, `aria-modal`, `useDialogFocus` (focus inside, Tab kept inside, Escape closes unless a text field has the focus, the focus back on the button), and the page holds the cover. It holds today's `MultilingualExport` unchanged (with `onPreview`, so its "Show on video", style and glossary show) beside the clip's video, with the language under review drawn over it by `LiveTextOverlay` and the burned captions masked under it: Quick edit's translated preview (EditorModal.tsx 162-195), moved.
- `useDialogFocus` leaves keys alone whose target is inside another `aria-modal` window over it (the "changed in the editor" question can open over this one).
- The translated burn of an edited clip still asks first (`ProjectExportPrompt`), as before.

### Translations follow editor edits

- Building a clip's project (`build_clip`) records `captions: {"follow": record}` in the manifest when the project got captions. `build.caption_record(own_lines, plan)` gives `{"lines": the captions as put on the timeline, "source": the clip's caption lines}` when they are the clip's lines one for one at the same times (within 0.01 s), else `None` (cuts or a speed moved them). A project with no captions has `captions: null`.
- When an export replaces the clip's file (`replaced_clip`), `intake.follow_captions` reads the project's `concat.json` (the active timeline's items): each recorded line must be found again as exactly one text item whose start AND end are both within 0.05 s of it, every time (review r3: the hook or a text watermark that starts at 0 s, a trimmed title and the halves of a split title never match; a line not found gives "kept"). The clip's own lines must still be ones the project was given (by their times). Then `render_opts.caption_lines` takes the items' words (spaces tidied; an emptied item drops its line), at the clip's own times, and the clip's saved translations (made from the old words) are removed, so no other language goes out with the old words; the notice's tooltip says to translate again. Every title emptied is "kept", never "followed" with no lines. Translation, `GET /clips/{id}/captions` (the MCP `clip_captions` tool) and the translated burn read those lines.
- The answer (and the export's record) carries `captions: "followed"` when words changed and were saved, `"kept"` when the project had a record but its captions could not be matched (moved, trimmed, split, cut or deleted in the editor, all emptied, or the file unreadable), and nothing when the words are the same, there were no captions, or there is no record (the clip's own cuts or speed had already moved its captions: the person never touched them, so nothing is said). The export's record alone also keeps `"same"` when every caption is still in place with the clip's own words. It never fails the take-in.
- With the second speaker's colour on, the project's captions are the lines as the render burns them (`_clip_captions(row, as_burned=True)`: a line ends where the speaker changes), so a retyped line saved back never mixes two speakers in one colour.
- Other languages on an edited clip, in the app: the window (`MultilingualExport`) always sends `burn: true`. Its two choices are "Subtitles" (burned into the picture, original audio kept) and "Subtitles + dubbed voice"; both are made from the clip as the AI made it, without the editor's cuts, music, looks or titles, so for an editor-export clip both ask the "changed in the editor" question every time. Burning onto the editor's export itself is a later step.
- Subtitle files alone (SRT, VTT; `subtitles: true` without `burn` or `dub`) are reachable only through the local API and MCP, not from the window. They are written from the clip's caption lines at their own times, and skip the question only when the take-in that made the clip's file recorded its captions `"followed"` or `"same"` (`intake.subtitles_follow`: every caption still in place). After a cut, trim or speed change that moved them (`"kept"`), with no record (the clip's own cuts or speed, captions off) or with the project gone, the question is asked, at the route and again when the job runs.
- Comparison (review r5): `follow_captions` collapses spaces and line breaks on both sides (`" ".join(text.split())`) before comparing, so a difference in spaces alone is "same", never "followed".
- A caption with the same span (within 0.1 s) as the project's hook or text watermark: `caption_record` keeps those titles' spans as `titles`, and `edited_captions` then gives None ("kept"), because with that caption deleted the other title would be found in its place (review r5). Records made before keep working without it.
- A translation running while an export is taken in (review r5): the translate stage reads the clip's `caption_lines` again just before saving. If they changed since the clip's translation started, its translations are not saved and the job fails with "The captions of "<title>" were changed in the editor while translating, so those translations were not saved. Translate again to use the new words." after the other clips are saved.
- "followed" removes every saved translation, the person's corrected ones (`edited`) and their translated post text included (`_drop_translations`). Left as it is for his click-through (review r5 #5: keep `post`, or keep corrected languages and flag them, is a design call).
- Limit: the words are read from the project's saved file. The editor saves as it goes; a word typed in the last moment before Export could be missed until the next export.

### YouTube upload and published links in the Publish window

- `lib/publishSetup.ts` also reads `GET /youtube/status`: `{ready, provider, youtube}`. The Clips page's Publish (card and side panel) and Publish all are always shown, as the Editor page's Publish is (his ask, "publishing in clips"): each click reads the setup again (`openPublish`) and opens the window, which says how to connect an account when none is set up. The page also reads it when shown and again on the window's `focus` and `visibilitychange`, so Schedule (shown only when a provider is ready) appears as soon as an account is connected (review r5).
- `PublishAllDialog` takes `providerReady` and `youtube`. With one clip: a "YouTube (direct upload)" section (a `<details>`, open when no provider is ready) holding `YouTubePanel` with `pendingRender={null}`, shown when `youtube` is on and the status says enabled; and `PublishedDiscordLinks` for the clip. With no provider ready the platform part is replaced by one line pointing to Settings. Its "done" line now points at Schedule.
- `YouTubeThumbnail` lost `currentTime` (Quick edit's playhead) and has its own frame picker: a "Frame at" slider over the clip file's own length (read from the file's metadata, as Quick edit's speed or cuts made some files shorter than their window; until it is read, `editor_export.seconds`, else the window), a still from `GET /clips/{id}/frame?t=` shown 250 ms after the slider rests, and "Use this frame".
- The still's cache name carries the clip file's stamp (`clip_<id>_<ms>_<mtime><size>.jpg`) and `clipFrameUrl` carries `media_version`: an editor export keeps the clip's id, so a still of the old video is never shown after it. The slider stops 0.1 s before the end (a seek to the file's exact length gives no frame).
- The Clips page has no new button. Its "Make clips again…" button and RemakeDialog are removed (his call: he never asked for them); the remake routes, `core/remake.py`, `lib/remake.ts` (the queue row's result line) and their tests stay.

### Not in the editor yet (deferred)

Checked in the fork's source (kitty/embed at 94aa48ff): Concat has auto captions from the sound (Whisper), SubRip import and export, voice-over and text to speech, speed, mute, split and delete, but no silence or filler removal, no censoring, no word-level transcript editing, no speakers and no gaming layouts.

| Gone with Quick edit | Comes back in |
|---|---|
| switching gaming layouts, "Remember for this creator" | KG (next) |
| Automatic framing (the AI's) | RF (per shot in KP1) |
| Letterbox / Center | KP1 (the Look panel) |
| word tools: mute or cut a word, censor, tighten pauses and fillers | KP1 (until then, split and mute or delete by hand in the editor) |
| swap speakers / back to automatic | KP1 (until then, a caption's colour by hand) |
| the Vertical Live framing note | KP1 |
| plugin suggested edits (Use, Take it back, Hide, Make again) | KP2 |
| the free music picker | KP2 (any music file can be imported in the editor now; a clip's free track is carried into its project) |
| "Duck under voice" (music ducking; Concat has none) | KP2 |
| the Sharpen slider | not missing: the fork ships the Sharpen effect (concat.sharpen) |
| the AI edit chat | K6, with HyperFrames |
| per-clip platform text and status (`UploadPostPanel`: per-platform title and description, first comment, per-platform status with refresh and retry) | no plan step yet; the lead or the owner gives it a place in the Publish window (the routes remain). Until then the one-clip Publish window sends with the clip's title and description, and Schedule shows each post |
| the Publish tab's "Through" switch (Upload-Post when both providers are on) | with the row above; until then the window sends through WoopSocial when both are on |
| Other languages burned onto the editor's export (the translated burn and dub are made from the clip as the AI made it) | a later step; subtitle files alone (local API and MCP only) already follow the editor's words |

### Tests (KC)

- `tests/test_ui_editor_page.py`: the "Clips" label and its guard comment; every Edit opens the editor and is off with the reason when it is not found; Quick edit's files are gone and nothing imports them; one Edit per place; Other languages' place, window, focus, cover and preview; the key rule in `useDialogFocus`; YouTube and Published links in the window; the thumbnail's frame picker; the export notice's caption words.
- `tests/test_kitty_editor_engine.py`: `caption_record` one for one and `None` after cuts or a speed; retyped words become the clip's lines, twice over; moved or deleted captions keep the clip's lines; same words or no captions save nothing; a hook with a caption's exact span gives "kept"; spaces alone never give "followed" (review r5).
- `tests/test_remake_worker.py` `test_translations_of_caption_words_changed_meanwhile_are_not_saved`: the translate stage does not save a clip whose caption lines changed while it ran, and says so; the other clips are saved.
- `tests/test_ui_editor_page.py` `test_the_clips_pages_schedule_follows_a_newly_connected_account`: the Clips page reads the setup again on focus and visibility.
- `tests/test_kitty_editor_routes.py`: through the routes, the manifest's record, the export's `captions`, and `GET /clips/{id}/captions` after it.
- Removed with Quick edit: `test_ui_caption_edits.py`, `test_ui_speaker_turns_sync.py` (their TypeScript is gone; the engine's caption and speaker code keeps its own tests), the Free music picker's tests in `test_ui_step4.py`, the Quick edit parts of `test_ui_edit_suggestions.py` (the library's own tests stay), `test_plugin_sdk_edit.py`'s check of Quick edit's controls, and `test_captions_off_kept.py`'s Quick edit checks (one now checks the Other languages window reads the same switch).
