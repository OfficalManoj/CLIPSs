/** "Other languages…" on the Editor page (step KC): subtitles and dubbing
 *  for the clip the editor shows, in a window of its own over the page.
 *
 *  The window holds today's MultilingualExport as it is (19 languages,
 *  review, glossary, style, dub and voices) beside the clip's video, with
 *  the language being reviewed drawn over it the way the burn will look
 *  (moved here from Quick edit, which is gone). The editor is hidden while
 *  it is open (the page holds the cover), the keyboard starts inside it,
 *  Escape closes it and the focus goes back to the button. */
import { useRef, useState, type RefObject } from 'react'
import { api } from '../../lib/api'
import { t } from '../../lib/i18n'
import { useDialogFocus } from '../../lib/useDialogFocus'
import type { Clip, LiveOverlay, TranslationPreview } from '../../lib/types'
import { DEFAULT_CAPTION_STYLE } from '../CaptionStyleControls'
import FeatureBoundary from '../FeatureBoundary'
import LiveTextOverlay from '../LiveTextOverlay'
import MultilingualExport from '../MultilingualExport'

/** Input types that are controls, not somewhere text is typed. */
const NOT_TYPING = new Set(['range', 'checkbox', 'radio', 'color', 'button', 'submit', 'reset', 'file'])

/** Escape in a text field gives up what is typed there, never the window. */
function typing(): boolean {
  const el = document.activeElement as HTMLElement | null
  return (
    !!el &&
    (el.tagName === 'TEXTAREA' ||
      el.isContentEditable ||
      (el instanceof HTMLInputElement && !NOT_TYPING.has(el.type)))
  )
}

export default function OtherLanguagesDialog({
  clip,
  onClose,
  returnFocus
}: {
  clip: Clip
  onClose: () => void
  returnFocus?: RefObject<HTMLElement>
}): JSX.Element {
  const box = useRef<HTMLDivElement>(null)
  const videoRef = useRef<HTMLVideoElement>(null)
  const [translated, setTranslated] = useState<TranslationPreview | null>(null)
  useDialogFocus(
    box,
    () => {
      if (!typing()) onClose()
    },
    returnFocus
  )
  const landscape = !!clip.render_opts?.profile
  // An editor export: the burn is made from the clip as the AI made it, not this video.
  const edited = !!clip.editor_export

  // The language being reviewed, over the clip, with the captions already
  // burned into the clip's file masked underneath (as Quick edit did).
  const captionStyle = { ...DEFAULT_CAPTION_STYLE, ...clip.render_opts?.caption_style }
  const overlay: LiveOverlay | null = translated
    ? {
        hook: null,
        captions: {
          lines: translated.lines,
          // A non-Latin script uses a font that has its letters, as the burn does.
          style: { ...translated.style, font: translated.font ?? translated.style.font }
        },
        keep: [],
        burned:
          (clip.render_opts?.captions ?? clip.render_opts?.job_captions ?? true) !== false &&
          translated.source.length > 0
            ? { lines: translated.source, style: captionStyle }
            : null,
        burnedHook: null
      }
    : null

  return (
    <div
      className="fixed inset-0 z-50 bg-base/80 backdrop-blur-sm grid place-items-center p-6"
      role="dialog"
      aria-modal="true"
      aria-label={t('Other languages')}
    >
      <div
        ref={box}
        tabIndex={-1}
        className="card w-full max-w-5xl max-h-[90vh] overflow-y-auto outline-none space-y-3"
      >
        <div className="flex items-center gap-3">
          <h3 className="font-semibold text-lg truncate">
            {t('Other languages')}: {clip.title || clip.hook || t('Untitled clip')}
          </h3>
          <button
            type="button"
            className="ml-auto btn-ghost !py-1 text-sm shrink-0"
            onClick={onClose}
            title={t('Close this window and go back to the editor. Translations and exports keep going.')}
          >
            {t('Close')}
          </button>
        </div>
        <div className="grid grid-cols-1 md:grid-cols-5 gap-5 items-start">
          <div className="md:col-span-2 space-y-1">
            <div
              className={`relative mx-auto max-w-full ${landscape ? 'w-full aspect-video' : 'h-[60vh] aspect-[9/16]'}`}
            >
              <video
                ref={videoRef}
                src={api.mediaUrl(clip.id, clip.media_version)}
                controls
                className="absolute inset-0 w-full h-full object-contain rounded-xl bg-base"
                aria-label={t('Clip preview')}
              />
              <LiveTextOverlay videoRef={videoRef} overlay={overlay} />
              {translated && (
                <span
                  className="absolute bottom-2 left-2 z-20 bg-black/70 text-white/85 text-[10px] px-2 py-0.5 rounded"
                  title={
                    edited
                      ? t(
                          'This is your editor version. The burned-in subtitles and the dub are made from the clip as the AI made it, without your editor changes.'
                        )
                      : undefined
                  }
                >
                  {translated.language.toUpperCase()}{' '}
                  {edited
                    ? t('preview of the words: the burn uses the clip as the AI made it')
                    : t('preview: this is how the burn will look')}
                </span>
              )}
            </div>
          </div>
          <div className="md:col-span-3 min-w-0">
            <FeatureBoundary name="Subtitles">
              <MultilingualExport clipId={clip.id} videoId={clip.video_id} onPreview={setTranslated} />
            </FeatureBoundary>
          </div>
        </div>
      </div>
    </div>
  )
}
