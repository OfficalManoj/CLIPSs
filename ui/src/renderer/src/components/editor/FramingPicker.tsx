/** "Layout" on the Editor page for a clip that is not a gaming clip (step
 *  KG): Auto, Letterbox or Center, the same three the old clip editor had.
 *  Picking one rebuilds the editor's project in that framing (its cuts and
 *  captions kept); the editor is hidden while this window is open (the page
 *  holds the cover). */
import { useRef, type RefObject } from 'react'
import type { EditorFraming } from '../../lib/api'
import { t } from '../../lib/i18n'
import { useDialogFocus } from '../../lib/useDialogFocus'

const OPTIONS: { value: EditorFraming; label: string; tip: string }[] = [
  { value: 'track', label: 'Auto (AI)', tip: 'The AI picks: subject tracking or letterbox as needed' },
  {
    value: 'letterbox',
    label: 'Letterbox',
    tip: 'Show the whole frame on a blurred background. Use it when the crop cuts someone off.'
  },
  { value: 'center', label: 'Center', tip: 'A still crop from the middle, no tracking' }
]

export default function FramingPicker({
  current,
  onPick,
  onClose,
  returnFocus
}: {
  current: EditorFraming
  onPick: (crop: EditorFraming) => void
  onClose: () => void
  returnFocus?: RefObject<HTMLElement>
}): JSX.Element {
  const box = useRef<HTMLDivElement>(null)
  useDialogFocus(box, onClose, returnFocus)
  return (
    <div
      className="fixed inset-0 z-50 bg-base/80 backdrop-blur-sm grid place-items-center p-6"
      role="dialog"
      aria-modal="true"
      aria-label={t('Layout')}
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose()
      }}
    >
      <div ref={box} tabIndex={-1} className="card w-full max-w-sm outline-none space-y-3">
        <h2 className="font-semibold">{t('Layout')}</h2>
        <p className="text-sm text-muted">{t('How the video fills the Short. Your cuts and captions stay.')}</p>
        <div className="flex flex-wrap gap-2">
          {OPTIONS.map((o) => (
            <button
              key={o.value}
              type="button"
              aria-pressed={current === o.value}
              className={`px-3 py-1.5 rounded-md text-sm ${
                current === o.value ? 'bg-accent/20 text-accent font-medium' : 'bg-raised hover:bg-raised/70'
              }`}
              title={t(o.tip)}
              onClick={() => (o.value === current ? onClose() : onPick(o.value))}
            >
              {t(o.label)}
            </button>
          ))}
        </div>
        <div className="flex justify-end">
          <button type="button" className="btn-ghost !py-1 text-sm" onClick={onClose}>
            {t('Cancel')}
          </button>
        </div>
      </div>
    </div>
  )
}
