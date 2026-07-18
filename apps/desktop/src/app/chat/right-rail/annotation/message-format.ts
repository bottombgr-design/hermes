import type { PickedElement, PickedRegion } from './element-picker'

export interface ElementAnnotationInput {
  comment: string
  kind: 'element'
  target: PickedElement
}

export interface RegionAnnotationInput {
  comment: string
  kind: 'region'
  target: PickedRegion
}

export type AnnotationInput = ElementAnnotationInput | RegionAnnotationInput

const MAX_COMMENT_LENGTH = 2000

function truncateComment(comment: string): string {
  const trimmed = comment.trim()
  if (trimmed.length <= MAX_COMMENT_LENGTH) {
    return trimmed
  }

  return `${trimmed.slice(0, MAX_COMMENT_LENGTH)}…`
}

function formatElementBlock(target: PickedElement): string {
  const lines = [
    `- 页面：${target.pageUrl}${target.pageTitle ? `（${target.pageTitle}）` : ''}`,
    `- 选择器：\`${target.selector}\``,
    `- 元素：<${target.tagName}>${target.id ? ` #${target.id}` : ''}${target.classes.length > 0 ? ` .${target.classes.join(' .')}` : ''}`,
  ]

  if (target.text) {
    lines.push(`- 文本："${target.text}"`)
  }

  lines.push(
    `- 位置：x=${Math.round(target.rect.x)}, y=${Math.round(target.rect.y)}, 尺寸 ${Math.round(target.rect.width)}×${Math.round(target.rect.height)}px`
  )

  return lines.join('\n')
}

function formatRegionBlock(target: PickedRegion): string {
  return [
    `- 页面：${target.pageUrl}${target.pageTitle ? `（${target.pageTitle}）` : ''}`,
    `- 框选区域：x=${Math.round(target.rect.x)}, y=${Math.round(target.rect.y)}, 尺寸 ${Math.round(target.rect.width)}×${Math.round(target.rect.height)}px`
  ].join('\n')
}

/**
 * Formats an annotation as a Markdown message the user reviews in the
 * composer before sending. The [预览标注] prefix lets the model recognise
 * the message type; the structured block gives it precise locating info.
 */
export function formatAnnotationMessage(input: AnnotationInput): string {
  const comment = truncateComment(input.comment) || '（未填写说明）'
  const block = input.kind === 'element' ? formatElementBlock(input.target) : formatRegionBlock(input.target)

  return [
    `[预览标注] ${input.kind === 'element' ? '元素' : '区域'}反馈`,
    '',
    block,
    '',
    `> ${comment}`
  ].join('\n')
}
