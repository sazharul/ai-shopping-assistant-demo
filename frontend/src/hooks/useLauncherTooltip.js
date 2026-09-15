import { useCallback, useEffect, useRef, useState } from 'react'

const TOOLTIP_SEEN_KEY = 'enox_launcher_tooltip_seen_at'
const TOOLTIP_INITIAL_DELAY_MS = 1200
const TOOLTIP_VISIBLE_MS = 15000
const TOOLTIP_COOLDOWN_MS = 24 * 60 * 60 * 1000

function isTooltipInCooldown() {
  try {
    const seenAt = localStorage.getItem(TOOLTIP_SEEN_KEY)
    if (!seenAt) return false
    return Date.now() - Number(seenAt) < TOOLTIP_COOLDOWN_MS
  } catch {
    return false
  }
}

function markTooltipSeen() {
  try {
    localStorage.setItem(TOOLTIP_SEEN_KEY, String(Date.now()))
  } catch {
    // ignore storage errors (private browsing, etc.)
  }
}

export function useLauncherTooltip(isOpen) {
  const [showTooltip, setShowTooltip] = useState(false)
  const hideTimerRef = useRef(null)
  const hasScheduledRef = useRef(false)

  const hideTooltip = useCallback(() => {
    markTooltipSeen()
    setShowTooltip(false)
    if (hideTimerRef.current) {
      clearTimeout(hideTimerRef.current)
      hideTimerRef.current = null
    }
  }, [])

  useEffect(() => {
    if (!isOpen) return
    hideTooltip()
  }, [isOpen, hideTooltip])

  useEffect(() => {
    if (isOpen || isTooltipInCooldown() || hasScheduledRef.current) return

    hasScheduledRef.current = true

    const initialTimer = setTimeout(() => {
      if (isOpen || isTooltipInCooldown()) return
      markTooltipSeen()
      setShowTooltip(true)
    }, TOOLTIP_INITIAL_DELAY_MS)

    return () => clearTimeout(initialTimer)
  }, [isOpen])

  useEffect(() => {
    if (!showTooltip) return

    hideTimerRef.current = setTimeout(() => {
      setShowTooltip(false)
    }, TOOLTIP_VISIBLE_MS)

    return () => {
      if (hideTimerRef.current) {
        clearTimeout(hideTimerRef.current)
        hideTimerRef.current = null
      }
    }
  }, [showTooltip])

  return {
    showTooltip: showTooltip && !isOpen,
    hideTooltip,
  }
}
