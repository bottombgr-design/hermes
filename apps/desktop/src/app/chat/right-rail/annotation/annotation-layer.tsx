import { useCallback, useEffect, useRef, useState } from 'react'

import { requestComposerFocus, requestComposerInsert } from '@/app/chat/composer/focus'
import { useI18n } from '@/i18n'
import { notify, notifyError } from '@/store/notifications'

import { AnnotationPopover, type AnnotationDraft } from './annotation-popover'
import { buildPickerProbeSource, type PickedElement, type PickedRegion, type PickerResult } from './element-picker'
import { formatAnnotationMessage } from './message-format'
import { captureRegionDataUrl } from './screenshot'

interface AnnotatableWebview {
  capturePage?: (rect?: { height: number; width: number; x: number; y: number }) => Promise<{ toDataURL: () => string }>
  executeJavaScript?: (code: string) => Promise<unknown>
}

interface AnnotationLayerProps {
  /** The live preview webview element (null while preview is a local file). */
  webview: AnnotatableWebview | null
  /** Called when annotation mode ends for any reason (pick, cancel, error). */
  onExit: () => void
}

type Phase =
  | { name: 'picking' }
  | { kind: 'element' | 'region'; name: 'commenting'; screenshot?: string; target: PickedElement | PickedRegion }

/**
 * Drives one annotation session: injects the picker probe into the preview
 * webview, waits for a pick/region/cancel, then shows the comment popover and
 * finally appends the formatted message to the chat composer.
 *
 * Rendered only while annotation mode is active; unmounting mid-pick cancels
 * the probe via Escape-equivalent teardown on the page side (the probe also
 * tears itself down on navigation).
 */
export function AnnotationLayer({ onExit, webview }: AnnotationLayerProps) {
  const { t } = useI18n()
  const copy = t.preview.web.annotation
  const [phase, setPhase] = useState<Phase>({ name: 'picking' })
  const cancelledRef = useRef(false)

  const finish = useCallback(() => {
    cancelledRef.current = true
    onExit()
  }, [onExit])

  useEffect(() => {
    if (!webview || typeof webview.executeJavaScript !== 'function') {
      notifyError(new Error('webview unavailable'), copy.pickerFailed)
      finish()
      return
    }

    cancelledRef.current = false

    const run = async () => {
      try {
        const raw = await webview.executeJavaScript!(buildPickerProbeSource(copy.banner))
        if (cancelledRef.current) {
          return
        }

        const result = raw as PickerResult | undefined
        if (!result || result.kind === 'cancelled') {
          finish()
          return
        }

        if (result.kind === 'iframe-blocked') {
          notify({ message: copy.iframeBlocked, kind: 'warning' })
          finish()
          return
        }

        const target = result.kind === 'picked' ? result.element : result.region
        const rect = target.rect
        const page = await measureViewport(webview)

        const screenshot =
          (await captureRegionDataUrl(webview, rect, page)) ?? undefined

        if (cancelledRef.current) {
          return
        }

        setPhase({
          kind: result.kind === 'picked' ? 'element' : 'region',
          name: 'commenting',
          screenshot,
          target
        })
      } catch (error) {
        if (!cancelledRef.current) {
          notifyError(error, copy.pickerFailed)
          finish()
        }
      }
    }

    void run()

    return () => {
      cancelledRef.current = true
    }
  }, [copy.banner, copy.iframeBlocked, copy.pickerFailed, finish, webview])

  const handleSubmit = useCallback(
    (draft: AnnotationDraft) => {
      const message = formatAnnotationMessage({
        comment: draft.comment,
        kind: draft.kind,
        target: draft.target as PickedElement & PickedRegion
      })

      // The composer's external-insert bus is the only supported write path
      // (see focus.ts — "preview console, etc."). $composerDraft has no UI
      // subscriber; writing there drops the message silently.
      const screenshotBlock = draft.screenshotDataUrl
        ? `\n\n<details><summary>📎 标注截图</summary>\n\n![annotation](${draft.screenshotDataUrl})\n\n</details>`
        : ''

      requestComposerInsert(message + screenshotBlock, { mode: 'block', target: 'main' })
      requestComposerFocus('main')

      notify({ message: copy.sentToComposer, kind: 'success' })
      finish()
    },
    [copy.sentToComposer, finish]
  )

  if (phase.name !== 'commenting') {
    return null
  }

  return (
    <AnnotationPopover
      kind={phase.kind}
      onCancel={finish}
      onSubmit={handleSubmit}
      screenshotDataUrl={phase.screenshot}
      target={phase.target}
    />
  )
}

/** Viewport size is needed to clamp the capture rect to page bounds. */
async function measureViewport(webview: AnnotatableWebview): Promise<{ pageHeight: number; pageWidth: number }> {
  try {
    const size = (await webview.executeJavaScript?.(
      '({ pageWidth: window.innerWidth, pageHeight: window.innerHeight })'
    )) as { pageHeight: number; pageWidth: number } | undefined

    if (size && size.pageWidth > 0 && size.pageHeight > 0) {
      return size
    }
  } catch {
    // fall through to default
  }

  return { pageHeight: 1080, pageWidth: 1920 }
}
