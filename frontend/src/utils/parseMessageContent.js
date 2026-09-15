function stripJsonFences(text) {
  if (!text || typeof text !== 'string') return ''
  return text
    .replace(/^```(?:json)?\s*/i, '')
    .replace(/\s*```$/i, '')
    .trim()
}

function tryParseJsonObject(text) {
  const stripped = stripJsonFences(text)
  if (!stripped) return null

  const candidates = [stripped]
  const match = stripped.match(/\{[\s\S]*\}/)
  if (match && match[0] !== stripped) {
    candidates.push(match[0])
  }

  for (const candidate of candidates) {
    try {
      const parsed = JSON.parse(candidate)
      if (parsed && typeof parsed === 'object' && !Array.isArray(parsed)) {
        return parsed
      }
    } catch {
      // try next candidate
    }
  }

  return null
}

function isProductEnvelope(parsed) {
  return (
    parsed &&
    typeof parsed === 'object' &&
    ('message' in parsed || 'products' in parsed || 'product_data' in parsed)
  )
}

function formatProductNames(names) {
  if (!Array.isArray(names) || names.length === 0) return ''
  const titles = names.filter((name) => typeof name === 'string' && name.trim())
  if (titles.length === 0) return ''
  return `I found these products: ${titles.join(', ')}`
}

function extractRichProducts(items) {
  if (!Array.isArray(items) || items.length === 0) return undefined
  const rich = items.filter((item) => item && typeof item === 'object' && item.product_name)
  return rich.length > 0 ? rich : undefined
}

function looksLikeRawJson(text) {
  if (!text || typeof text !== 'string') return false
  const trimmed = stripJsonFences(text)
  if (!trimmed.startsWith('{') || !trimmed.endsWith('}')) return false
  return (
    trimmed.includes('"message"') ||
    trimmed.includes('"products"') ||
    trimmed.includes('"product_data"')
  )
}

function displayTextFromEnvelope(parsed, fallback = '') {
  if (!parsed) return fallback

  if (typeof parsed.message === 'string' && parsed.message.trim()) {
    return parsed.message.trim()
  }

  if (Array.isArray(parsed.products) && parsed.products.length > 0) {
    return formatProductNames(parsed.products) || fallback
  }

  if (Array.isArray(parsed.product_data) && parsed.product_data.length > 0) {
    return fallback
  }

  return fallback
}

/**
 * Normalize assistant message content from history, SSE, or render props.
 * Never returns raw JSON as user-visible text.
 */
export function parseAssistantMessage(rawContent, existingProducts) {
  let displayText = typeof rawContent === 'string' ? rawContent.trim() : ''
  let productData = extractRichProducts(existingProducts)

  const parsed = tryParseJsonObject(displayText)
  if (isProductEnvelope(parsed)) {
    displayText = displayTextFromEnvelope(parsed, displayText)

    if (!productData) {
      productData = extractRichProducts(parsed.product_data)
    }
  }

  if (looksLikeRawJson(displayText)) {
    const retry = tryParseJsonObject(displayText)
    if (isProductEnvelope(retry)) {
      displayText = displayTextFromEnvelope(
        retry,
        productData ? '' : "Sorry, I couldn't display that response."
      )
      if (!productData) {
        productData = extractRichProducts(retry.product_data)
      }
    } else {
      displayText = productData
        ? ''
        : "Sorry, I couldn't display that response."
    }
  }

  return {
    content: displayText,
    products: productData,
  }
}
