import { useState, useEffect, useRef, useCallback } from 'react'
import { createUser, fetchHistory, streamMessage, BASE_URL } from '../api/chat'
import {
  requestHandoff,
  getHandoffStatus,
  sendHandoffMessage,
  resolveHandoff,
  submitFeedback,
  connectHandoffWebSocket,
  connectHandoffEventSource,
} from '../api/handoff'
import { parseAssistantMessage } from '../utils/parseMessageContent'
import { createThumbnailPreview, createDisplayPreview, fileToBase64, validateImageFile } from '../utils/imagePreview'

const STORAGE_KEY = 'enox_user'

function loadUser() {
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    return raw ? JSON.parse(raw) : null
  } catch {
    return null
  }
}

function saveUser(user) {
  localStorage.setItem(STORAGE_KEY, JSON.stringify(user))
}


export function useChat() {
  const [user, setUser] = useState(loadUser)
  const [messages, setMessages] = useState([])
  const [inputValue, setInputValue] = useState('')
  const [isStreaming, setIsStreaming] = useState(false)
  const [historyPage, setHistoryPage] = useState(1)
  const [totalPages, setTotalPages] = useState(1)
  const [loadingHistory, setLoadingHistory] = useState(false)
  const [historyError, setHistoryError] = useState('')
  const [selectedImage, setSelectedImage] = useState(null)
  const [imagePreviewUrl, setImagePreviewUrl] = useState(null)
  const [imagePreviewLoading, setImagePreviewLoading] = useState(false)
  const [chatMode, setChatMode] = useState('bot') // bot | queued | agent
  const [handoffInfo, setHandoffInfo] = useState(null)
  const [handoffError, setHandoffError] = useState('')
  const [showFeedback, setShowFeedback] = useState(false)
  const [feedbackSubmitted, setFeedbackSubmitted] = useState(false)
  const [feedbackRating, setFeedbackRating] = useState(null)
  const [feedbackComment, setFeedbackComment] = useState('')

  const streamController = useRef(null)
  const handoffWsRef = useRef(null)
  const handoffEsRef = useRef(null)
  const handoffConnGenRef = useRef(0)
  const queuePollRef = useRef(null)
  const nextId = useRef(0)
  const imageTaskRef = useRef(0)
  const preparedBase64Ref = useRef(null)
  const previewUrlRef = useRef(null)
  const displayUrlRef = useRef(null)

  function revokeImageUrl(url) {
    if (url) URL.revokeObjectURL(url)
  }

  function clearImageUrls() {
    revokeImageUrl(previewUrlRef.current)
    revokeImageUrl(displayUrlRef.current)
    previewUrlRef.current = null
    displayUrlRef.current = null
  }

  function makeId() {
    return ++nextId.current
  }

  const clearSelectedImage = useCallback(() => {
    imageTaskRef.current += 1
    preparedBase64Ref.current = null
    setSelectedImage(null)
    setImagePreviewLoading(false)
    clearImageUrls()
    setImagePreviewUrl(null)
  }, [])

  const selectImage = useCallback(async (file) => {
    if (!file) return

    try {
      validateImageFile(file)
    } catch (err) {
      setHistoryError(err.message || 'Unsupported image file.')
      return
    }

    const taskId = ++imageTaskRef.current
    preparedBase64Ref.current = null
    clearImageUrls()

    setSelectedImage(file)
    setImagePreviewUrl(null)
    setImagePreviewLoading(true)

    fileToBase64(file)
      .then((base64) => {
        if (imageTaskRef.current === taskId) {
          preparedBase64Ref.current = base64
        }
      })
      .catch(() => {
        if (imageTaskRef.current === taskId) {
          preparedBase64Ref.current = null
        }
      })

    try {
      const [thumbUrl, displayUrl] = await Promise.all([
        createThumbnailPreview(file),
        createDisplayPreview(file),
      ])
      if (imageTaskRef.current !== taskId) {
        revokeImageUrl(thumbUrl)
        revokeImageUrl(displayUrl)
        return
      }
      previewUrlRef.current = thumbUrl
      displayUrlRef.current = displayUrl
      setImagePreviewUrl(thumbUrl)
    } catch {
      if (imageTaskRef.current === taskId) {
        previewUrlRef.current = null
        setImagePreviewUrl(null)
      }
    } finally {
      if (imageTaskRef.current === taskId) {
        setImagePreviewLoading(false)
      }
    }
  }, [])

  async function registerUser(name, email) {
    const result = await createUser(name, email)
    saveUser(result)
    setUser(result)
    return result
  }

  useEffect(() => {
    if (!user) return
    loadHistory(user.id, 1, true)
    refreshHandoffStatus()
  }, [user?.id])

  function appendUniqueAssistantMessage(content) {
    setMessages((prev) => {
      const last = prev[prev.length - 1]
      if (last?.role === 'assistant' && last.content === content && !last.streaming) {
        return prev
      }
      return [
        ...prev,
        { id: makeId(), role: 'assistant', content, streaming: false },
      ]
    })
  }

  function handleHandoffEvent(event, data) {
    if (event === 'agent_joined') {
      setChatMode('agent')
      setHandoffInfo((prev) => ({ ...prev, agent_name: data.agent_name, status: 'with_agent' }))
      appendUniqueAssistantMessage(`${data.agent_name || 'An agent'} has joined the chat.`)
    }
    if (event === 'queue_update') {
      setHandoffInfo(data)
      if (data.status === 'queued') setChatMode('queued')
      else if (data.status === 'with_agent') setChatMode('agent')
    }
    if (event === 'new_message' && data.sender_type === 'agent') {
      appendUniqueAssistantMessage(data.message)
    }
    if (event === 'conversation_resolved') {
      setChatMode('bot')
      setShowFeedback(true)
      setHandoffInfo(null)
    }
  }

  function stopHandoffRealtime() {
    handoffConnGenRef.current += 1
    handoffWsRef.current?.close()
    handoffWsRef.current = null
    handoffEsRef.current?.close()
    handoffEsRef.current = null
    if (queuePollRef.current) {
      clearInterval(queuePollRef.current)
      queuePollRef.current = null
    }
  }

  function startQueuePolling() {
    if (queuePollRef.current || !user?.session_id) return
    queuePollRef.current = setInterval(async () => {
      try {
        const status = await getHandoffStatus(user.session_id)
        setHandoffInfo(status)
        if (status.status === 'with_agent') {
          setChatMode('agent')
          stopHandoffRealtime()
          startHandoffRealtime('agent')
        } else if (status.status === 'queued') {
          setChatMode('queued')
        } else if (status.status === 'bot' || status.status === 'resolved') {
          setChatMode('bot')
          stopHandoffRealtime()
        }
      } catch {
        // ignore transient polling errors
      }
    }, 5000)
  }

  function startHandoffRealtime(mode) {
    if (!user?.session_id) return
    stopHandoffRealtime()
    const connGen = handoffConnGenRef.current

    const isCurrentConnection = () => connGen === handoffConnGenRef.current

    const startSseFallback = () => {
      if (!isCurrentConnection() || handoffEsRef.current) return
      handoffEsRef.current = connectHandoffEventSource(user.session_id, {
        onEvent: (event, data) => {
          if (!isCurrentConnection()) return
          handleHandoffEvent(event, data)
        },
      })
    }

    handoffWsRef.current = connectHandoffWebSocket(user.session_id, {
      onEvent: (event, data) => {
        if (!isCurrentConnection()) return
        handleHandoffEvent(event, data)
      },
      onClose: () => {
        if (!isCurrentConnection()) return
        handoffWsRef.current = null
        startSseFallback()
        if (mode === 'queued') startQueuePolling()
      },
      onError: () => {
        if (!isCurrentConnection()) return
        handoffWsRef.current?.close()
        handoffWsRef.current = null
        startSseFallback()
      },
    })

    if (mode === 'queued') startQueuePolling()
  }

  useEffect(() => {
    if (!user?.session_id || chatMode === 'bot') {
      stopHandoffRealtime()
      return
    }
    startHandoffRealtime(chatMode)
    return () => stopHandoffRealtime()
  }, [user?.session_id, chatMode])

  async function refreshHandoffStatus() {
    if (!user?.session_id) return
    try {
      const status = await getHandoffStatus(user.session_id)
      setHandoffInfo(status)
      if (status.status === 'queued') setChatMode('queued')
      else if (status.status === 'with_agent') setChatMode('agent')
      else setChatMode('bot')
    } catch {
      setChatMode('bot')
    }
  }

  async function startHandoff(reason = 'Customer requested human support') {
    if (!user || chatMode !== 'bot') return
    setHandoffError('')
    try {
      const status = await requestHandoff(user.session_id, reason)
      if (status.status === 'offline' || status.handoff_available === false) {
        setHandoffInfo(status)
        setChatMode('bot')
        setMessages((prev) => [
          ...prev,
          {
            id: makeId(),
            role: 'assistant',
            content: status.message || 'Agents are currently offline. A support ticket has been created for you.',
            streaming: false,
          },
        ])
        return
      }
      setHandoffInfo(status)
      setChatMode('queued')
      setMessages((prev) => [
        ...prev,
        {
          id: makeId(),
          role: 'assistant',
          content: 'Connecting you to a human agent. Please wait…',
          streaming: false,
        },
      ])
    } catch (err) {
      setHandoffError(err.message || 'Could not connect to an agent.')
    }
  }

  async function sendAgentMessage(text) {
    if (!user || chatMode !== 'agent') return
    const trimmed = text.trim()
    if (!trimmed) return
    setMessages((prev) => [
      ...prev,
      { id: makeId(), role: 'user', content: trimmed, streaming: false },
    ])
    await sendHandoffMessage(user.session_id, trimmed)
  }

  async function endAgentChat() {
    if (!user) return
    try {
      await resolveHandoff(user.session_id)
      setChatMode('bot')
      setShowFeedback(true)
      setHandoffInfo(null)
    } catch {
      setHandoffError('Could not end agent chat.')
    }
  }

  async function sendFeedback(rating, comment = '') {
    if (!user) return
    const finalRating = rating ?? feedbackRating
    const finalComment = comment || feedbackComment
    if (!finalRating) return
    try {
      await submitFeedback(user.session_id, finalRating, finalComment)
      setFeedbackSubmitted(true)
      setShowFeedback(false)
      setFeedbackRating(null)
      setFeedbackComment('')
    } catch {
      setHandoffError('Could not submit feedback.')
    }
  }

  async function loadHistory(userId, page, replace = false) {
    if (!user?.session_id) return
    setLoadingHistory(true)
    setHistoryError('')
    try {
      const res = await fetchHistory(userId, user.session_id, page)
      setTotalPages(res.pagination.total_pages)
      setHistoryPage(res.pagination.current_page)

      const mapped = [...res.data].reverse().map((m) => {
        let imageUrl = undefined

        if (m.image_path) {
          imageUrl = `${BASE_URL}/chat/uploads/${m.image_path}`
        }

        const isAssistant = m.role === 'ai' || m.role === 'agent'
        const normalized = isAssistant
          ? parseAssistantMessage(m.message)
          : { content: m.message, products: undefined }

        return {
          id: makeId(),
          role: isAssistant ? 'assistant' : 'user',
          content: normalized.content,
          imageUrl,
          ts: m.timestamp,
          products: normalized.products,
          streaming: false,
        }
      })

      if (replace) {
        setMessages(mapped)
      } else {
        setMessages((prev) => [...mapped, ...prev])
      }
    } catch {
      setHistoryError('Could not load chat history. You can still send new messages.')
    } finally {
      setLoadingHistory(false)
    }
  }

  async function loadOlderMessages() {
    if (!user || historyPage >= totalPages || loadingHistory) return
    await loadHistory(user.id, historyPage + 1, false)
  }

  const sendMessage = useCallback(
    async (text) => {
      const trimmedText = text.trim()
      const imageFile = selectedImage
      if ((!trimmedText && !imageFile) || isStreaming || !user) return

      if (chatMode === 'agent') {
        await sendAgentMessage(trimmedText)
        setInputValue('')
        return
      }

      if (chatMode === 'queued') return

      const messageImageUrl = displayUrlRef.current || imagePreviewUrl
      const messageText = trimmedText || 'Please help me with this image.'

      const userMsg = {
        id: makeId(),
        role: 'user',
        content: messageText,
        imageUrl: messageImageUrl,
        ts: null,
        streaming: false,
      }
      const botMsgId = makeId()
      const botMsg = { id: botMsgId, role: 'assistant', content: '', ts: null, products: undefined, streaming: true }

      setMessages((prev) => [...prev, userMsg, botMsg])
      setIsStreaming(true)
      setInputValue('')

      // Detach image URLs from composer — keep display URL alive for the sent message
      imageTaskRef.current += 1
      const preparedBase64 = preparedBase64Ref.current
      preparedBase64Ref.current = null

      const thumbUrl = previewUrlRef.current
      const sentDisplayUrl = displayUrlRef.current
      previewUrlRef.current = null
      displayUrlRef.current = null

      if (thumbUrl && thumbUrl !== sentDisplayUrl) {
        revokeImageUrl(thumbUrl)
      }

      setSelectedImage(null)
      setImagePreviewUrl(null)
      setImagePreviewLoading(false)

      let image_base64 = null
      if (imageFile) {
        image_base64 = preparedBase64
        if (!image_base64) {
          image_base64 = await fileToBase64(imageFile)
        }
      }

      streamController.current = streamMessage(
        messageText,
        user.session_id,
        image_base64,
        // onToken — accumulate content
        (token) => {
          setMessages((prev) =>
            prev.map((m) => {
              if (m.id === botMsgId) {
                return { ...m, content: m.content + token }
              }
              return m
            })
          )
        },

        // onProductData — set products + friendly message from structured SSE event
        (products, productMessage) => {
          setMessages((prev) =>
            prev.map((m) => {
              if (m.id === botMsgId) {
                const normalized = parseAssistantMessage(
                  productMessage || m.content,
                  products
                )
                return {
                  ...m,
                  content: normalized.content,
                  products: normalized.products,
                }
              }
              return m
            })
          )
        },

        // onDone — mark streaming complete
        () => {
          setMessages((prev) =>
            prev.map((m) => {
              if (m.id === botMsgId) {
                const normalized = parseAssistantMessage(m.content, m.products)
                return {
                  ...m,
                  streaming: false,
                  content: normalized.content,
                  products: normalized.products,
                }
              }
              return m
            })
          )
          setIsStreaming(false)
        },

        // onError
        (err) => {
          const userMessage = import.meta.env.DEV
            ? `⚠️ Error: ${err}`
            : 'Something went wrong. Please try again.'
          setMessages((prev) =>
            prev.map((m) =>
              m.id === botMsgId
                ? { ...m, content: userMessage, streaming: false }
                : m
            )
          )
          setIsStreaming(false)
        }
      )
    },
    [isStreaming, user, selectedImage, imagePreviewUrl, chatMode]
  )

  function cancelStream() {
    streamController.current?.abort()
    setMessages((prev) =>
      prev.map((m) => (
        m.streaming
          ? {
              ...m,
              streaming: false,
              cancelled: !m.content?.trim(),
              content: m.content?.trim() ? m.content : 'Response cancelled.',
            }
          : m
      ))
    )
    setIsStreaming(false)
  }

  async function continueWithAI() {
    setShowFeedback(false)
    setFeedbackSubmitted(false)
    setFeedbackRating(null)
    setFeedbackComment('')
    setHandoffError('')
    setMessages((prev) => [
      ...prev,
      {
        id: makeId(),
        role: 'assistant',
        content: 'You are back with StyleHub AI. How can I help you?',
        streaming: false,
      },
    ])
  }

  function clearUser() {
    localStorage.removeItem(STORAGE_KEY)
    stopHandoffRealtime()
    setUser(null)
    setMessages([])
    setChatMode('bot')
    setHandoffInfo(null)
    setShowFeedback(false)
    setFeedbackSubmitted(false)
    setFeedbackRating(null)
    setFeedbackComment('')
  }

  return {
    user,
    messages,
    inputValue,
    setInputValue,
    isStreaming,
    loadingHistory,
    historyError,
    historyPage,
    totalPages,
    registerUser,
    sendMessage,
    cancelStream,
    loadOlderMessages,
    clearUser,
    selectedImage,
    imagePreviewUrl,
    imagePreviewLoading,
    selectImage,
    clearSelectedImage,
    chatMode,
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
  }
}