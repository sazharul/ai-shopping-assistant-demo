import { useState } from 'react'

// Shown the first time a visitor opens the chat widget.
// Collects name + email, then calls onSubmit.
export default function UserForm({ onSubmit, isLoading }) {
  const [name, setName] = useState('')
  const [email, setEmail] = useState('')
  const [error, setError] = useState('')

  function handleSubmit() {
    if (!name.trim()) return setError('Please enter your name.')
    if (!email.trim() || !email.includes('@'))
      return setError('Please enter a valid email.')
    setError('')
    onSubmit(name.trim(), email.trim())
  }

  function handleKey(e) {
    if (e.key === 'Enter') handleSubmit()
  }

  return (
    <div className="enox-form">
      <div className="enox-form-header">
        <div className="enox-avatar">✦</div>
        <h2>Start chatting</h2>
        <p>Tell us who you are to begin.</p>
      </div>

      <div className="enox-field">
        <label htmlFor="enox-name">Name</label>
        <input
          id="enox-name"
          type="text"
          placeholder="Your name"
          value={name}
          onChange={(e) => setName(e.target.value)}
          onKeyDown={handleKey}
          disabled={isLoading}
        />
      </div>

      <div className="enox-field">
        <label htmlFor="enox-email">Email</label>
        <input
          id="enox-email"
          type="email"
          placeholder="you@example.com"
          value={email}
          onChange={(e) => setEmail(e.target.value)}
          onKeyDown={handleKey}
          disabled={isLoading}
        />
      </div>

      {error && <p className="enox-error">{error}</p>}

      <button
        className="enox-btn-primary"
        onClick={handleSubmit}
        disabled={isLoading}
      >
        {isLoading ? 'Starting…' : 'Start Chat →'}
      </button>
    </div>
  )
}
