export function canAutoFocusChatInput() {
  if (typeof window === 'undefined') return false

  // Only auto-focus on desktop pointers to avoid opening the mobile keyboard
  // on embedded storefront pages.
  return window.matchMedia('(hover: hover) and (pointer: fine)').matches
}

export function focusChatInput(inputRef) {
  if (!canAutoFocusChatInput()) return

  requestAnimationFrame(() => {
    inputRef.current?.focus({ preventScroll: true })
  })
}
