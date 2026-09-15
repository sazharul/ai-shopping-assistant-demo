import {useState, useRef} from 'react'

function isSafeProductUrl(url) {
    if (!url || typeof url !== 'string') return false
    try {
        const parsed = new URL(url, 'https://stylehub.demo')
        return parsed.protocol === 'https:' || parsed.protocol === 'http:'
    } catch {
        return false
    }
}

export default function ProductCards({products}) {
    const [index, setIndex] = useState(0)
    const touchStartX = useRef(null)

    if (!products || products.length === 0) return null

    const total = products.length
    const product = products[index]

    function prev(e) {
        e.preventDefault();
        e.stopPropagation();
        setIndex((i) => (i - 1 + total) % total)
    }

    function next(e) {
        e.preventDefault();
        e.stopPropagation();
        setIndex((i) => (i + 1) % total)
    }

    function onTouchStart(e) {
        touchStartX.current = e.touches[0].clientX
    }

    function onTouchEnd(e) {
        if (touchStartX.current === null) return
        const delta = touchStartX.current - e.changedTouches[0].clientX
        if (Math.abs(delta) > 40) delta > 0 ? next(e) : prev(e)
        touchStartX.current = null
    }

    return (
        <div
            className="enox-product-carousel"
            onTouchStart={onTouchStart}
            onTouchEnd={onTouchEnd}
        >
            {/* key forces a remount on product change, so hover/reveal/selection
          state from the previous card never leaks into the next one */}
            <ProductCard
                key={product.product_id ?? index}
                product={product}
                onPrev={total > 1 ? prev : null}
                onNext={total > 1 ? next : null}
            />

            {/* Bottom strip — dots only. No arrows, no extra padding/chrome. */}
            {total > 1 && (
                <div className="enox-carousel-dots-bar">
                    {products.map((_, i) => (
                        <button
                            key={i}
                            className={`enox-carousel-dot${i === index ? ' enox-carousel-dot--active' : ''}`}
                            onClick={() => setIndex(i)}
                            aria-label={`Product ${i + 1}`}
                        />
                    ))}
                </div>
            )}
        </div>
    )
}

function ProductCard({product, onPrev, onNext}) {
    const [selectedColor, setSelectedColor] = useState(null)
    const [selectedSize, setSelectedSize] = useState(null)
    const [hoveredColor, setHoveredColor] = useState(null)
    const [revealed, setRevealed] = useState(false)

    const {
        product_name,
        product_url,
        product_image,
        price,
        currency = 'GBP',
        discount_price,
        has_discount,
        discount_percent,
        in_stock,
        colors = [],
        sizes = [],
    } = product

    const symbol =
        currency === 'GBP' ? '£' :
            currency === 'USD' ? '$' :
                currency === 'EUR' ? '€' : currency

    // Hover doesn't exist on touch devices — first tap reveals the slide-up
    // panel instead of navigating away; a second tap (or tapping the title)
    // then follows the link, same as a normal hover-then-click flow.
    function handleCardClick(e) {
        const isTouch = typeof window !== 'undefined' && window.matchMedia('(hover: none)').matches
        if (isTouch && !revealed) {
            e.preventDefault()
            setRevealed(true)
        }
    }

    const safeProductUrl = isSafeProductUrl(product_url) ? product_url : '#'

    const apiBase = (import.meta.env.VITE_API_BASE_URL || 'http://127.0.0.1:8001/api/v1').replace(/\/$/, '')
    const imageSrc = product_image
        ? (product_image.startsWith('http') ? product_image : `${apiBase}/demo-images/${product_image}`)
        : null

    return (
        <a
            className={`enox-product-card-v2${revealed ? ' enox-product-card-v2--revealed' : ''}`}
            href={safeProductUrl}
            target="_blank"
            rel="noopener noreferrer"
            aria-label={product_name}
            onClick={handleCardClick}
        >
            <div className="enox-product-img-block">
                {imageSrc ? (
                    <div className="image_parent_fixed_ratio">
                        <img
                            className="enox-product-img-v2"
                            src={imageSrc}
                            alt={product_name}
                            loading="eager"
                            decoding="async"
                        />
                    </div>
                ) : (
                    <div className="enox-product-img-placeholder-v2">✦</div>
                )}

                {has_discount && discount_percent && (
                    <span className="enox-product-badge-v2">−{Math.round(discount_percent)}%</span>
                )}

                {!in_stock && (
                    <div className="enox-product-oos-v2">Out of stock</div>
                )}

                {/* The only navigation control on the image — centred, glass-style */}

                {/* Title always visible on the image. Price/colour/size slide up
            on hover (or on first tap on touch devices). */}
                <div className="enox-product-overlay">
                    <div className="enox-overlay-title-bar">
                        <span className="enox-overlay-name">{product_name}</span>
                    </div>

                    <div className="enox-overlay-body">
                        <div className="enox-overlay-body-inner">
                            <div className="enox-product-price-row-v2">
                                {has_discount && discount_price != null ? (
                                    <>
                                        <span className="enox-price-sale">{symbol}{Number(discount_price).toFixed(2)}</span>
                                        <span className="enox-price-original">{symbol}{Number(price).toFixed(2)}</span>
                                    </>
                                ) : (
                                    <span className="enox-price-main">{symbol}{Number(price).toFixed(2)}</span>
                                )}
                            </div>

                            {colors.length > 0 && (
                                <div className="enox-product-attr">
                                    <span className="enox-attr-label">Colour</span>
                                    <div className="enox-color-row">
                                        {colors.map((c) => (
                                            <span key={c} className="enox-color-wrap">
                        <button
                            className={`enox-color-swatch${selectedColor === c ? ' enox-color-swatch--active' : ''}`}
                            style={{background: colorToHex(c)}}
                            onClick={(e) => {
                                e.preventDefault();
                                e.stopPropagation();
                                setSelectedColor(selectedColor === c ? null : c)
                            }}
                            onMouseEnter={() => setHoveredColor(c)}
                            onMouseLeave={() => setHoveredColor(null)}
                            aria-label={c}
                            aria-pressed={selectedColor === c}
                        />
                                                {hoveredColor === c && (
                                                    <span className="enox-color-tooltip">{c}</span>
                                                )}
                      </span>
                                        ))}
                                    </div>
                                </div>
                            )}

                            {sizes.length > 0 && (
                                <div className="enox-product-attr">
                                    <span className="enox-attr-label">Size</span>
                                    <div className="enox-size-row">
                                        {sizes.map((s) => (
                                            <button
                                                key={s}
                                                className={`enox-size-chip${selectedSize === s ? ' enox-size-chip--active' : ''}`}
                                                onClick={(e) => {
                                                    e.preventDefault();
                                                    e.stopPropagation();
                                                    setSelectedSize(selectedSize === s ? null : s)
                                                }}
                                                aria-pressed={selectedSize === s}
                                            >
                                                {s}
                                            </button>
                                        ))}
                                    </div>
                                </div>
                            )}
                        </div>
                    </div>
                </div>
            </div>

            {onPrev && (
                <button className="enox-img-arrow enox-img-arrow--left" onClick={onPrev} aria-label="Previous product">
                    ‹
                </button>
            )}
            {onNext && (
                <button className="enox-img-arrow enox-img-arrow--right" onClick={onNext} aria-label="Next product">
                    ›
                </button>
            )}
        </a>
    )
}

const COLOR_MAP = {
    black: '#111111', white: '#f5f5f5', red: '#e63030', pink: '#f472b6',
    rose: '#f43f5e', fuchsia: '#d946ef', purple: '#9333ea', violet: '#7c3aed',
    blue: '#2563eb', navy: '#1e3a5f', teal: '#0d9488', green: '#16a34a',
    olive: '#657234', yellow: '#eab308', orange: '#f97316', coral: '#ff6b6b',
    brown: '#92400e', camel: '#c19a6b', cream: '#fffdd0', beige: '#f5f0e8',
    ivory: '#fffff0', grey: '#9ca3af', gray: '#9ca3af', silver: '#c0c0c0',
    gold: '#d4af37', khaki: '#c3b091', nude: '#e8cdb2', lilac: '#c8a2c8',
    mint: '#98ead0', denim: '#1560bd', leopard: '#c68642', animal: '#c68642',
}

function colorToHex(name) {
    const key = name.toLowerCase().trim()
    return COLOR_MAP[key] || '#d1d5db'
}