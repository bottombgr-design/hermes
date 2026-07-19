import { useCallback, useEffect, useRef, useState } from 'react'

import { requestComposerFocus, requestComposerInsert } from '@/app/chat/composer/focus'
import { useI18n } from '@/i18n'
import { notify, notifyError } from '@/store/notifications'

import { AnnotationPopover } from './annotation-popover'
import { AnnotationToolbar } from './annotation-toolbar'
import type { PickedElement, PickedRegion } from './element-picker'
import {
  buildAddBadgeCall,
  buildRemoveBadgeCall,
  buildSessionProbeSource,
  buildSetPickingCall,
  buildTeardownCall,
  parseSessionEvent
} from './element-picker-session'
import { formatAnnotationSessionMessage } from './message-format'
import { captureRegionDataUrl } from './screenshot'

interface AnnotatableWebview {
  addEventListener?: (type: string, listener: (event: Event) => void) => void
  capturePage?: (rect?: { height: number; width: number; x: number; y: number }) => Promise<{ toDataURL: () => string }>
  executeJavaScript?: (code: string) => Promise<unknown>
  removeEventListener?: (type: string, listener: (event: Event) => void) => void
}

interface AnnotationLayerProps {
  /** The live preview webview element (null while preview is a local file). */
  webview: AnnotatableWebview | null
  /** Called when annotation mode ends for any reason (finish, cancel, error). */
  onExit: () => void
}

export interface AnnotationItem {
  comment: string
  id: string
  kind: 'element' | 'region'
  number: number
  screenshot?: string
  target: PickedElement | PickedRegion
}

interface PendingPick {
  kind: 'element' | 'region'
  screenshot?: string
  target: PickedElement | PickedRegion
}

let idCounter = 0
function nextId(): string {
  idCounter += 1
  return `ann-${Date.now()}-${idCounter}`
}

/**
 * Drives one annotation *session*: injects the persistent probe, collects any
 * number of element/region annotations (each gets a numbered badge pinned on
 * the page), and finally assembles one composer message when the user hits
 * "完成". Badges are clickable to remove a single annotation.
 */
export function AnnotationLayer({ onExit, webview }: AnnotationLayerProps) {
  const { t } = useI18n()
  const copy = t.preview.web.annotation
  const [items, setItems] = useState<AnnotationItem[]>([])
  const [pending, setPending] = useState<PendingPick | null>(null)
  const itemsRef = useRef<AnnotationItem[]>([])
  const exitedRef = useRef(false)

  itemsRef.current = items

  const exit = useCallback(() => {
    if (exitedRef.current) {
      return
    }
    exitedRef.current = true
    if (webview?.executeJavaScript) {
      void webview.executeJavaScript(buildTeardownCall()).catch(() => undefined)
    }
    onExit()
  }, [onExit, webview])

  const submitCollected = useCallback(() => {
    const collected = itemsRef.current
    if (collected.length === 0) {
      return false
    }

    const message = formatAnnotationSessionMessage(collected)
    const screenshotBlocks = collected
      .filter(item => item.screenshot)
      .map(item => `<details><summary>📎 标注 ${item.number} 截图</summary>\n\n![annotation-${item.number}](${item.screenshot})\n\n</details>`)
      .join('\n\n')

    requestComposerInsert(screenshotBlocks ? `${message}\n\n${screenshotBlocks}` : message, {
      mode: 'block',
      target: 'main'
    })
    requestComposerFocus('main')
    return true
  }, [])

  // Inject the probe + subscribe to its console channel.
  useEffect(() => {
    if (!webview || typeof webview.executeJavaScript !== 'function') {
      notifyError(new Error('webview unavailable'), copy.pickerFailed)
      onExit()
      return
    }

    let disposed = false

    const onConsoleMessage = (raw: Event) => {
      const message = (raw as Event & { message?: string }).message || ''
      const event = parseSessionEvent(message)
      if (!event || disposed) {
        return
      }

      if (event.type === 'cancel-request') {
        exit()
        return
      }

      if (event.type === 'iframe-blocked') {
        notify({ message: copy.iframeBlocked, kind: 'warning' })
        void webview.executeJavaScript!(buildSetPickingCall(true)).catch(() => undefined)
        return
      }

      if (event.type === 'badge-click') {
        setItems(prev => {
          const next = prev.filter(item => item.id !== event.id)
          // Renumber survivors and re-pin their badges.
          const renumbered = next.map((item, index) => ({ ...item, number: index + 1 }))
          for (const item of renumbered) {
            void webview.executeJavaScript!(
              buildAddBadgeCall(item.id, item.number, item.target.rect.x, item.target.rect.y)
            ).catch(() => undefined)
          }
          return renumbered
        })
        void webview.executeJavaScript!(buildRemoveBadgeCall(event.id)).catch(() => undefined)
        return
      }

      // pick event — the probe already paused picking and hid its highlight,
      // so the page is clean for the screenshot.
      void (async () => {
        const page = await measureViewport(webview)
        const screenshot = (await captureRegionDataUrl(webview, event.target.rect, page)) ?? undefined

        if (disposed) {
          return
        }

        setPending({ kind: event.kind, screenshot, target: event.target })
      })()
    }

    const onNavigate = () => {
      // Full navigation wipes the probe — submit collected work, then exit.
      if (disposed) {
        return
      }
      const submitted = submitCollected()
      notify({ message: submitted ? copy.navigatedAwaySubmitted : copy.navigatedAway, kind: 'warning' })
      exit()
    }

    webview.addEventListener?.('console-message', onConsoleMessage)
    webview.addEventListener?.('did-navigate', onNavigate)
    void webview.executeJavaScript(buildSessionProbeSource(copy.banner)).catch(error => {
      if (!disposed) {
        notifyError(error, copy.pickerFailed)
        onExit()
      }
    })

    return () => {
      disposed = true
      webview.removeEventListener?.('console-message', onConsoleMessage)
      webview.removeEventListener?.('did-navigate', onNavigate)
      // Best-effort probe cleanup on unmount (mode exit also tears down).
      void webview.executeJavaScript?.(buildTeardownCall()).catch(() => undefined)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [webview])

  const resumePicking = useCallback(() => {
    void webview?.executeJavaScript?.(buildSetPickingCall(true)).catch(() => undefined)
  }, [webview])


  const handleAdd = useCallback(
    (draft: { comment: string; kind: 'element' | 'region'; screenshotDataUrl?: string; target: PickedElement | PickedRegion }) => {
      const item: AnnotationItem = {
        comment: draft.comment,
        id: nextId(),
        kind: draft.kind,
        number: itemsRef.current.length + 1,
        screenshot: draft.screenshotDataUrl,
        target: draft.target
      }

      setItems(prev => [...prev, item])
      void webview?.executeJavaScript?.(
        buildAddBadgeCall(item.id, item.number, item.target.rect.x, item.target.rect.y)
      ).catch(() => undefined)

      setPending(null)
      resumePicking()
    },
    [resumePicking, webview]
  )

  const handleDiscard = useCallback(() => {
    setPending(null)
    resumePicking()
  }, [resumePicking])

  const handleFinish = useCallback(() => {
    if (!submitCollected()) {
      return
    }
    notify({ message: copy.sentToComposer, kind: 'success' })
    exit()
  }, [copy.sentToComposer, exit, submitCollected])

  return (
    <>
      <AnnotationToolbar
        copy={{
          cancel: copy.cancelSession,
          finish: copy.finishSession,
          title: count => copy.sessionTitle(count)
        }}
        count={items.length}
        onCancel={exit}
        onFinish={handleFinish}
      />

      {pending && (
        <AnnotationPopover
          kind={pending.kind}
          number={items.length + 1}
          onAdd={handleAdd}
          onDiscard={handleDiscard}
          screenshotDataUrl={pending.screenshot}
          target={pending.target}
        />
      )}
    </>
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
