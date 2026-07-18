import { useStore } from '@nanostores/react'
import { useEffect, useRef, useState } from 'react'

import { Button } from '@/components/ui/button'
import { Textarea } from '@/components/ui/textarea'
import { useI18n } from '@/i18n'
import { cn } from '@/lib/utils'

import type { PickedElement, PickedRegion } from './element-picker'

export interface AnnotationDraft {
  comment: string
  kind: 'element' | 'region'
  screenshotDataUrl?: string
  target: PickedElement | PickedRegion
}

interface AnnotationPopoverProps {
  onCancel: () => void
  onSubmit: (draft: AnnotationDraft) => void
  screenshotDataUrl?: string
  target: PickedElement | PickedRegion
  kind: 'element' | 'region'
}

function isPickedElement(target: PickedElement | PickedRegion): target is PickedElement {
  return 'selector' in target
}

function targetSummary(target: PickedElement | PickedRegion): string {
  if (isPickedElement(target)) {
    const text = target.text ? ` "${target.text}"` : ''
    return `<${target.tagName.toLowerCase()}>${target.id ? ` #${target.id}` : ''}${text}`
  }

  return `${Math.round(target.rect.width)}×${Math.round(target.rect.height)}px`
}

export function AnnotationPopover({ kind, onCancel, onSubmit, screenshotDataUrl, target }: AnnotationPopoverProps) {
  const { t } = useI18n()
  const copy = t.preview.web.annotation
  const [comment, setComment] = useState('')
  const textareaRef = useRef<HTMLTextAreaElement | null>(null)

  useEffect(() => {
    textareaRef.current?.focus()
  }, [])

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        event.stopPropagation()
        onCancel()
      }

      if (event.key === 'Enter' && (event.metaKey || event.ctrlKey)) {
        event.stopPropagation()
        onSubmit({ comment, kind, screenshotDataUrl, target })
      }
    }

    window.addEventListener('keydown', onKey, true)
    return () => window.removeEventListener('keydown', onKey, true)
  }, [comment, kind, onCancel, onSubmit, screenshotDataUrl, target])

  return (
    <div
      className="pointer-events-auto absolute bottom-4 left-1/2 z-50 w-[22rem] -translate-x-1/2 rounded-lg border border-border bg-popover shadow-xl"
      data-testid="annotation-popover"
    >
      <div className="flex items-center justify-between border-b border-border px-3 py-2">
        <div className="min-w-0">
          <div className="text-xs font-medium text-foreground">
            {kind === 'element' ? copy.elementTitle : copy.regionTitle}
          </div>
          <div className="truncate font-mono text-[0.6875rem] text-muted-foreground">{targetSummary(target)}</div>
        </div>
        {screenshotDataUrl && (
          <img
            alt={copy.screenshotAlt}
            className="ml-2 h-10 w-16 shrink-0 rounded border border-border object-cover"
            src={screenshotDataUrl}
          />
        )}
      </div>

      <div className="p-3">
        <Textarea
          ref={textareaRef}
          className="min-h-20 resize-none text-sm"
          onChange={event => setComment(event.target.value)}
          placeholder={copy.placeholder}
          value={comment}
        />
        <div className="mt-2 flex items-center justify-between">
          <span className="text-[0.6875rem] text-muted-foreground">{copy.hint}</span>
          <div className="flex gap-2">
            <Button onClick={onCancel} size="sm" variant="ghost">
              {copy.cancel}
            </Button>
            <Button
              className={cn(!comment.trim() && 'opacity-60')}
              onClick={() => onSubmit({ comment, kind, screenshotDataUrl, target })}
              size="sm"
            >
              {copy.submit}
            </Button>
          </div>
        </div>
      </div>
    </div>
  )
}
