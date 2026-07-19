import { cn } from '@/lib/utils'

interface AnnotationToolbarProps {
  count: number
  copy: {
    cancel: string
    finish: string
    title: (count: number) => string
  }
  onCancel: () => void
  onFinish: () => void
}

/**
 * Floating pill shown at the top of the preview while annotation mode is
 * active. Linear-style: translucent dark glass, hairline border, soft shadow.
 */
export function AnnotationToolbar({ copy, count, onCancel, onFinish }: AnnotationToolbarProps) {
  return (
    <div className="pointer-events-none absolute inset-x-0 top-3 z-30 flex justify-center">
      <div
        className={cn(
          'pointer-events-auto flex items-center gap-3 rounded-full py-1.5 pl-4 pr-1.5',
          'border border-white/10 bg-neutral-900/85 text-neutral-100 shadow-lg shadow-black/30',
          'backdrop-blur-md backdrop-saturate-150',
          'animate-in fade-in slide-in-from-top-2 duration-200'
        )}
        data-slot="annotation-toolbar"
      >
        <span className="flex items-center gap-2 text-xs font-medium">
          <span className="inline-block h-2 w-2 animate-pulse rounded-full bg-red-500" />
          {copy.title(count)}
        </span>

        <div className="flex items-center gap-1">
          <button
            className="rounded-full px-3 py-1 text-xs text-neutral-400 transition-colors hover:bg-white/10 hover:text-neutral-200"
            onClick={onCancel}
            type="button"
          >
            {copy.cancel}
          </button>
          <button
            className="rounded-full bg-red-500 px-3 py-1 text-xs font-medium text-white transition-colors hover:bg-red-400 disabled:cursor-not-allowed disabled:opacity-40"
            disabled={count === 0}
            onClick={onFinish}
            type="button"
          >
            {copy.finish}
          </button>
        </div>
      </div>
    </div>
  )
}
