import { createElement, type ReactNode } from 'react'
import { ThemeProvider } from '../../../../../../AI资源/src/components/design-system/ThemeProvider.js'
import inkRender, {
  type Instance,
  createRoot as inkCreateRoot,
  type RenderOptions,
  type Root,
} from '../../../../../../AI资源/src/ink/root.js'

export type { RenderOptions, Instance, Root }

// Wrap all CC render calls with ThemeProvider so ThemedBox/ThemedText work
// without every call site having to mount it. Ink itself is theme-agnostic.
function withTheme(node: ReactNode): ReactNode {
  return createElement(ThemeProvider, null, node)
}

export async function render(
  node: ReactNode,
  options?: NodeJS.WriteStream | RenderOptions,
): Promise<Instance> {
  return inkRender(withTheme(node), options)
}

export async function createRoot(options?: RenderOptions): Promise<Root> {
  const root = await inkCreateRoot(options)
  return {
    ...root,
    render: node => root.render(withTheme(node)),
  }
}

export { color } from '../../../../../../AI资源/src/components/design-system/color.js'
export type { Props as BoxProps } from '../../../../../../AI资源/src/components/design-system/ThemedBox.js'
export { default as Box } from '../../../../../../AI资源/src/components/design-system/ThemedBox.js'
export type { Props as TextProps } from '../../../../../../AI资源/src/components/design-system/ThemedText.js'
export { default as Text } from '../../../../../../AI资源/src/components/design-system/ThemedText.js'
export {
  ThemeProvider,
  usePreviewTheme,
  useTheme,
  useThemeSetting,
} from '../../../../../../AI资源/src/components/design-system/ThemeProvider.js'
export { Ansi } from '../../../../../../AI资源/src/ink/Ansi.js'
export type { Props as AppProps } from '../../../../../../AI资源/src/ink/components/AppContext.js'
export type { Props as BaseBoxProps } from '../../../../../../AI资源/src/ink/components/Box.js'
export { default as BaseBox } from '../../../../../../AI资源/src/ink/components/Box.js'
export type {
  ButtonState,
  Props as ButtonProps,
} from '../../../../../../AI资源/src/ink/components/Button.js'
export { default as Button } from '../../../../../../AI资源/src/ink/components/Button.js'
export type { Props as LinkProps } from '../../../../../../AI资源/src/ink/components/Link.js'
export { default as Link } from '../../../../../../AI资源/src/ink/components/Link.js'
export type { Props as NewlineProps } from '../../../../../../AI资源/src/ink/components/Newline.js'
export { default as Newline } from '../../../../../../AI资源/src/ink/components/Newline.js'
export { NoSelect } from '../../../../../../AI资源/src/ink/components/NoSelect.js'
export { RawAnsi } from '../../../../../../AI资源/src/ink/components/RawAnsi.js'
export { default as Spacer } from '../../../../../../AI资源/src/ink/components/Spacer.js'
export type { Props as StdinProps } from '../../../../../../AI资源/src/ink/components/StdinContext.js'
export type { Props as BaseTextProps } from '../../../../../../AI资源/src/ink/components/Text.js'
export { default as BaseText } from '../../../../../../AI资源/src/ink/components/Text.js'
export type { DOMElement } from '../../../../../../AI资源/src/ink/dom.js'
export { ClickEvent } from '../../../../../../AI资源/src/ink/events/click-event.js'
export { EventEmitter } from '../../../../../../AI资源/src/ink/events/emitter.js'
export { Event } from '../../../../../../AI资源/src/ink/events/event.js'
export type { Key } from '../../../../../../AI资源/src/ink/events/input-event.js'
export { InputEvent } from '../../../../../../AI资源/src/ink/events/input-event.js'
export type { TerminalFocusEventType } from '../../../../../../AI资源/src/ink/events/terminal-focus-event.js'
export { TerminalFocusEvent } from '../../../../../../AI资源/src/ink/events/terminal-focus-event.js'
export { FocusManager } from '../../../../../../AI资源/src/ink/focus.js'
export type { FlickerReason } from '../../../../../../AI资源/src/ink/frame.js'
export { useAnimationFrame } from '../../../../../../AI资源/src/ink/hooks/use-animation-frame.js'
export { default as useApp } from '../../../../../../AI资源/src/ink/hooks/use-app.js'
export { default as useInput } from '../../../../../../AI资源/src/ink/hooks/use-input.js'
export { useAnimationTimer, useInterval } from '../../../../../../AI资源/src/ink/hooks/use-interval.js'
export { useSelection } from '../../../../../../AI资源/src/ink/hooks/use-selection.js'
export { default as useStdin } from '../../../../../../AI资源/src/ink/hooks/use-stdin.js'
export { useTabStatus } from '../../../../../../AI资源/src/ink/hooks/use-tab-status.js'
export { useTerminalFocus } from '../../../../../../AI资源/src/ink/hooks/use-terminal-focus.js'
export { useTerminalTitle } from '../../../../../../AI资源/src/ink/hooks/use-terminal-title.js'
export { useTerminalViewport } from '../../../../../../AI资源/src/ink/hooks/use-terminal-viewport.js'
export { default as measureElement } from '../../../../../../AI资源/src/ink/measure-element.js'
export { supportsTabStatus } from '../../../../../../AI资源/src/ink/termio/osc.js'
export { default as wrapText } from '../../../../../../AI资源/src/ink/wrap-text.js'
