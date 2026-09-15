import { useEffect, useState } from 'react'
import { useChat } from './hooks/useChat'
import { useLauncherTooltip } from './hooks/useLauncherTooltip'
import { useBodyScrollLock } from './hooks/useBodyScrollLock'
import UserForm from './components/UserForm'
import ChatWindow from './components/ChatWindow'
import './App.css'
import './styles/products.css'

function readHostOpen(hostElement) {
  return Boolean(
    hostElement?.hasAttribute('open') && hostElement.getAttribute('open') !== 'false'
  )
}

// ── Size modes ────────────────────────────────────────────────────────────────
// 'normal'     → default floating box (380 × 560 px)
// 'minimised'  → only the launcher icon is visible
// 'fullscreen' → fills the viewport

export default function App({ hostElement = null }) {
  const [isOpen, setIsOpen] = useState(() => readHostOpen(hostElement))
  const [sizeMode, setSizeMode] = useState('normal')  // 'normal' | 'fullscreen'
  const [formLoading, setFormLoading] = useState(false)
  const [formError, setFormError] = useState('')

  const chat = useChat()
  const { showTooltip, hideTooltip } = useLauncherTooltip(isOpen)
  useBodyScrollLock(isOpen)

  useEffect(() => {
    if (!hostElement) return

    const syncFromHost = () => {
      const shouldOpen = readHostOpen(hostElement)
      setIsOpen((prev) => {
        if (shouldOpen && !prev) {
          setSizeMode('normal')
        }
        return shouldOpen
      })
    }

    const observer = new MutationObserver(syncFromHost)
    syncFromHost()
    observer.observe(hostElement, { attributes: true, attributeFilter: ['open'] })
    return () => observer.disconnect()
  }, [hostElement])

  function setOpen(next) {
    if (next && !isOpen) setSizeMode('normal')
    setIsOpen(next)
    if (hostElement) {
      if (next) hostElement.setAttribute('open', 'true')
      else hostElement.removeAttribute('open')
    }
  }

  // ── User registration ──────────────────────────────────────────────────────
  async function handleFormSubmit(name, email) {
    setFormLoading(true)
    setFormError('')
    try {
      await chat.registerUser(name, email)
    } catch {
      setFormError('Could not connect. Please try again.')
    } finally {
      setFormLoading(false)
    }
  }

  // ── Toggle open/close ──────────────────────────────────────────────────────
  function toggleOpen() {
    setOpen(!isOpen)
  }

  // ── Determine panel CSS class ──────────────────────────────────────────────
  const panelClass = [
    'enox-panel',
    isOpen ? 'enox-panel--open' : '',
    sizeMode === 'fullscreen' ? 'enox-panel--fullscreen' : '',
  ]
    .filter(Boolean)
    .join(' ')

  return (
    <div className="enox-root">
      {/* ── Floating Chat Panel ──────────────────────────────────────────── */}
      <div className={panelClass} role="dialog" aria-label="StyleHub AIAI chat">
        {/* Header */}
        <div className="enox-header">
          <div className="enox-header-brand">
            <span className="enox-logo">✦</span>
            <span className="enox-brand-name">StyleHub AI</span>
            <span className="enox-demo-badge" title="Portfolio demonstration">Demo</span>
          </div>
          <div className="enox-header-actions">
            {/* Fullscreen toggle */}
            {/* <button
              className="enox-icon-btn"
              onClick={() =>
                setSizeMode((m) => (m === 'fullscreen' ? 'normal' : 'fullscreen'))
              }
              title={sizeMode === 'fullscreen' ? 'Exit fullscreen' : 'Fullscreen'}
            >
              {sizeMode === 'fullscreen' ? '⊡' : '⊞'}
            </button> */}
            {/* Minimise / close */}
            <button
              className="enox-icon-btn"
              onClick={toggleOpen}
              title="Minimise"
            >
              ✕
            </button>
          </div>
        </div>

        {/* Body — show form if no user, otherwise chat */}
        <div className="enox-body">
          {!chat.user ? (
            isOpen && (
              <>
                <UserForm
                  onSubmit={handleFormSubmit}
                  isLoading={formLoading}
                />
                {formError && <p className="enox-error enox-error--global">{formError}</p>}
              </>
            )
          ) : (
            isOpen && <ChatWindow {...chat} isPanelOpen={isOpen} />
          )}
        </div>
      </div>

      {/* ── Floating Launcher Button ─────────────────────────────────────── */}
      <div className="enox-launcher-wrap">
        {showTooltip && (
          <div
            className="enox-launcher-tooltip"
            role="status"
            aria-live="polite"
          >
            <button
              type="button"
              className="enox-launcher-tooltip-body"
              onClick={() => setOpen(true)}
            >
              <span className="enox-launcher-tooltip-text">
                Hi! I&apos;m StyleHub AI, Need any assistance? I&apos;d be happy to help!
              </span>
            </button>
            <button
              type="button"
              className="enox-launcher-tooltip-close"
              onClick={hideTooltip}
              aria-label="Dismiss"
            >
              ×
            </button>
          </div>
        )}

        <button
          className={`enox-launcher ${isOpen ? 'enox-launcher--active' : ''}`}
          onClick={toggleOpen}
          aria-label={isOpen ? 'Close chat' : 'Open StyleHub AIAI chat'}
          title="StyleHub AIAI"
        >
          {isOpen ? (
            <svg xmlns="http://www.w3.org/2000/svg" width="15" height="15" viewBox="0 0 24 24"><path fill="none" stroke="#767676" stroke-width="2" d="m2 8.35l10.173 9.823L21.997 8"/></svg>
          ) : (
            <svg xmlns="http://www.w3.org/2000/svg" width="32" height="32" viewBox="0 0 24 24"><path fill="#767676" d="M1.5 2h21v16H6.876L1.5 22.704V2Zm2 2v14.296L6.124 16H20.5V4h-17Z"></path></svg>
          )}
        </button>
      </div>
    </div>
  )
}
