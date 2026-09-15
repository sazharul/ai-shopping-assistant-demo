import { useEffect, useRef, useState } from 'react'
import Message from './Message'
import { getClipboardImageFile } from '../utils/imagePreview'
import { focusChatInput } from '../utils/focus'

const RATING_LABELS = {
    1: 'Poor',
    2: 'Fair',
    3: 'Okay',
    4: 'Good',
    5: 'Excellent',
}

export default function ChatWindow({
    user,
    messages,
    inputValue,
    setInputValue,
    isStreaming,
    loadingHistory,
    historyError,
    historyPage,
    totalPages,
    sendMessage,
    cancelStream,
    loadOlderMessages,
    clearUser,
    selectedImage,
    imagePreviewUrl,
    imagePreviewLoading,
    selectImage,
    clearSelectedImage,
    isPanelOpen = true,
    chatMode = 'bot',
    handoffInfo,
    handoffError,
    startHandoff,
    endAgentChat,
    showFeedback,
    feedbackSubmitted,
    feedbackRating,
    setFeedbackRating,
    feedbackComment,
    setFeedbackComment,
    sendFeedback,
    continueWithAI,
}) {
    const bottomRef = useRef(null)
    const listRef = useRef(null)
    const fileInputRef = useRef(null)
    const inputRef = useRef(null)
    const wasStreamingRef = useRef(false)
    const wasPanelOpenRef = useRef(isPanelOpen)
    const [hoverRating, setHoverRating] = useState(0)

    useEffect(() => {
        bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
    }, [messages])

    useEffect(() => {
        const openedByUser = !wasPanelOpenRef.current && isPanelOpen
        wasPanelOpenRef.current = isPanelOpen

        if (openedByUser && !isStreaming) {
            focusChatInput(inputRef)
        }
    }, [isPanelOpen, isStreaming])

    useEffect(() => {
        if (wasStreamingRef.current && !isStreaming && isPanelOpen) {
            focusChatInput(inputRef)
        }
        wasStreamingRef.current = isStreaming
    }, [isStreaming, isPanelOpen])

    function handleClearImage() {
        clearSelectedImage()
        if (fileInputRef.current) {
            fileInputRef.current.value = ''
        }
        focusChatInput(inputRef)
    }

    function handleImageSelect(e) {
        const file = e.target.files?.[0]
        if (file) {
            selectImage(file)
        }
        if (fileInputRef.current) {
            fileInputRef.current.value = ''
        }
        focusChatInput(inputRef)
    }

    function canSend() {
        return Boolean(inputValue.trim() || selectedImage)
    }

    function handleSend() {
        if (!canSend() || isStreaming) return
        if (chatMode === 'queued') return
        sendMessage(inputValue)
    }

    function handleKey(e) {
        if (e.key === 'Enter' && !e.shiftKey) {
            e.preventDefault()
            handleSend()
        }
    }

    function handlePaste(e) {
        if (isStreaming) return

        const file = getClipboardImageFile(e.clipboardData)
        if (!file) return

        e.preventDefault()
        selectImage(file)
        focusChatInput(inputRef)
    }

    const showImagePreview = Boolean(selectedImage)
    const activeRating = hoverRating || feedbackRating || 0

    return (
        <div className="enox-chat-window" onPasteCapture={handlePaste}>
            <div className="enox-chat-greeting">
                <span>Hi, {user.name}</span>
                <div className="enox-chat-actions">
                    {chatMode === 'bot' && (
                        <button
                            type="button"
                            className="enox-btn-human"
                            onClick={() => startHandoff()}
                            title="Talk to a person"
                        >
                            <svg width="13" height="13" viewBox="0 0 24 24" fill="none" aria-hidden="true">
                                <circle cx="12" cy="8" r="3.25" stroke="currentColor" strokeWidth="1.8" />
                                <path d="M5.5 19.5c.8-3.4 3.3-5 6.5-5s5.7 1.6 6.5 5" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" />
                            </svg>
                            Talk to a person
                        </button>
                    )}
                    {chatMode === 'agent' && (
                        <button
                            type="button"
                            className="enox-btn-end"
                            onClick={endAgentChat}
                            title="End conversation"
                        >
                            End chat
                        </button>
                    )}
                    <button
                        type="button"
                        className="enox-btn-ghost enox-btn-sm"
                        onClick={clearUser}
                        title="Sign out"
                    >
                        Sign out
                    </button>
                </div>
            </div>

            {chatMode === 'queued' && (
                <div className="enox-status-banner" role="status">
                    <span className="enox-status-dot" aria-hidden="true" />
                    <span>
                        Connecting you to a specialist
                        {handoffInfo?.queue_position ? ` · #${handoffInfo.queue_position} in queue` : '…'}
                    </span>
                </div>
            )}

            {chatMode === 'agent' && (
                <div className="enox-status-banner enox-status-banner--agent" role="status">
                    <span className="enox-status-dot enox-status-dot--live" aria-hidden="true" />
                    <span>You are chatting with {handoffInfo?.agent_name || 'a specialist'}</span>
                </div>
            )}

            {handoffError && (
                <div className="enox-status-banner enox-status-banner--error" role="alert">{handoffError}</div>
            )}

            {showFeedback && !feedbackSubmitted && (
                <div className="enox-feedback" role="group" aria-label="Rate your conversation">
                    <p className="enox-feedback-title">How was your conversation?</p>
                    <p className="enox-feedback-sub">Your rating helps us improve support.</p>
                    <div
                        className="enox-stars"
                        role="radiogroup"
                        aria-label="Rating"
                        onMouseLeave={() => setHoverRating(0)}
                        onBlur={(e) => {
                            if (!e.currentTarget.contains(e.relatedTarget)) setHoverRating(0)
                        }}
                    >
                        {[1, 2, 3, 4, 5].map((n) => {
                            const filled = n <= activeRating
                            return (
                                <button
                                    key={n}
                                    type="button"
                                    role="radio"
                                    aria-checked={feedbackRating === n}
                                    aria-label={`${n} star${n === 1 ? '' : 's'}, ${RATING_LABELS[n]}`}
                                    className={`enox-star${filled ? ' is-filled' : ''}`}
                                    onMouseEnter={() => setHoverRating(n)}
                                    onFocus={() => setHoverRating(n)}
                                    onClick={() => setFeedbackRating(n)}
                                >
                                    <svg width="26" height="26" viewBox="0 0 24 24" aria-hidden="true">
                                        <path
                                            d="M12 2.8l2.62 5.54 6.08.74-4.47 4.2 1.2 6.02L12 16.7 6.57 19.3l1.2-6.02-4.47-4.2 6.08-.74L12 2.8z"
                                            fill={filled ? 'currentColor' : 'none'}
                                            stroke="currentColor"
                                            strokeWidth="1.4"
                                            strokeLinejoin="round"
                                        />
                                    </svg>
                                </button>
                            )
                        })}
                    </div>
                    <p className={`enox-feedback-label${activeRating ? '' : ' is-placeholder'}`}>
                        {activeRating ? RATING_LABELS[activeRating] : 'Select a rating'}
                    </p>
                    <textarea
                        className="enox-feedback-comment"
                        rows={2}
                        placeholder="Optional comment…"
                        value={feedbackComment}
                        onChange={(e) => setFeedbackComment(e.target.value)}
                    />
                    <div className="enox-feedback-actions">
                        <button
                            type="button"
                            className="enox-feedback-submit"
                            onClick={() => sendFeedback()}
                            disabled={!feedbackRating}
                        >
                            Send feedback
                        </button>
                        <button
                            type="button"
                            className="enox-btn-ghost enox-btn-sm"
                            onClick={continueWithAI}
                        >
                            Skip
                        </button>
                    </div>
                </div>
            )}

            {feedbackSubmitted && (
                <div className="enox-feedback enox-feedback--thanks">
                    <div className="enox-feedback-check" aria-hidden="true">
                        <svg width="18" height="18" viewBox="0 0 24 24" fill="none">
                            <path d="M5 12.5l4.2 4.2L19 7.5" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round" />
                        </svg>
                    </div>
                    <p className="enox-feedback-title">Thank you</p>
                    <p className="enox-feedback-sub">Your feedback has been received.</p>
                    <button type="button" className="enox-feedback-submit" onClick={continueWithAI}>
                        Continue with AI
                    </button>
                </div>
            )}

            <div className="enox-msg-list" ref={listRef}>
                {historyError && (
                    <div className="enox-empty">
                        <span>{historyError}</span>
                    </div>
                )}

                {historyPage < totalPages && (
                    <button
                        className="enox-btn-ghost enox-btn-center"
                        onClick={loadOlderMessages}
                        disabled={loadingHistory}
                    >
                        {loadingHistory ? 'Loading…' : '↑ Load older messages'}
                    </button>
                )}

                {messages.length === 0 && !loadingHistory && (
                    <div className="enox-empty">
                        <span>Ask me anything about your order, products, or returns.</span>
                    </div>
                )}

                {messages.map((m) => (
                    <Message
                        key={m.id}
                        role={m.role}
                        content={m.content}
                        imageUrl={m.imageUrl}
                        products={m.products}
                        streaming={m.streaming}
                        cancelled={m.cancelled}
                    />
                ))}

                <div ref={bottomRef} />
            </div>

            <div className="enox-input-area">
                {showImagePreview && (
                    <div className="enox-image-preview">
                        <div
                            className="enox-image-preview-thumb"
                            aria-busy={imagePreviewLoading}
                            aria-label={imagePreviewLoading ? 'Loading image preview' : 'Image preview'}
                        >
                            {imagePreviewUrl ? (
                                <img
                                    src={imagePreviewUrl}
                                    alt="Attachment preview"
                                />
                            ) : (
                                <div className="enox-image-preview-placeholder" aria-hidden="true" />
                            )}
                            {imagePreviewLoading && (
                                <div className="enox-image-preview-overlay" aria-hidden="true">
                                    <div className="enox-image-preview-spinner" />
                                </div>
                            )}
                            <button
                                type="button"
                                className="enox-image-preview-remove"
                                onClick={handleClearImage}
                                aria-label="Remove image"
                                title="Remove image"
                            >
                                ×
                            </button>
                        </div>
                        <div className="enox-image-preview-meta">
                            <span className="enox-image-preview-name">{selectedImage.name}</span>
                            {imagePreviewLoading && (
                                <span className="enox-image-preview-status">Preparing preview…</span>
                            )}
                        </div>
                    </div>
                )}

                <div className="enox-input-bar">
                    <input
                        ref={fileInputRef}
                        type="file"
                        accept="image/jpeg,image/png,image/webp,image/gif"
                        id="image-upload"
                        hidden
                        onChange={handleImageSelect}
                        disabled={isStreaming}
                    />

                    <label
                        htmlFor="image-upload"
                        className={`enox-btn-image${isStreaming ? ' enox-btn-image--disabled' : ''}`}
                        title="Attach image"
                        aria-label="Attach image"
                    >
                        <svg width="20" height="20" viewBox="0 0 24 24" fill="none" aria-hidden="true">
                            <rect x="3" y="5" width="18" height="14" rx="2" stroke="currentColor" strokeWidth="1.5" />
                            <circle cx="8.5" cy="10" r="1.5" fill="currentColor" />
                            <path d="M3 16l4.5-4.5 3 3L14 11l7 7" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
                        </svg>
                    </label>

                    <textarea
                        ref={inputRef}
                        className="enox-input"
                        placeholder={
                            chatMode === 'agent'
                                ? 'Message the agent…'
                                : chatMode === 'queued'
                                    ? 'Waiting for an agent…'
                                    : selectedImage
                                        ? 'Describe this image…'
                                        : 'Type your message…'
                        }
                        value={inputValue}
                        onChange={(e) => setInputValue(e.target.value)}
                        onKeyDown={handleKey}
                        rows={1}
                        disabled={isStreaming || chatMode === 'queued'}
                        aria-label="Message input"
                    />

                    {isStreaming ? (
                        <button
                            type="button"
                            className="enox-btn-stop"
                            onClick={cancelStream}
                            title="Stop response"
                            aria-label="Stop response"
                        >
                            <svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">
                                <rect x="6" y="6" width="12" height="12" rx="1" />
                            </svg>
                        </button>
                    ) : (
                        <button
                            type="button"
                            className="enox-btn-send"
                            onClick={handleSend}
                            disabled={!canSend()}
                            title="Send message"
                            aria-label="Send message"
                        >
                            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" aria-hidden="true">
                                <path d="M5 12h12M13 7l5 5-5 5" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
                            </svg>
                        </button>
                    )}
                </div>
            </div>
        </div>
    )
}
