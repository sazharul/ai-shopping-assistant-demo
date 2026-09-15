import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import App from './App.jsx'

class StyleHub AIChatWidget extends HTMLElement {
  constructor() {
    super()
    this._root = null
    this._mountPoint = null
  }

  connectedCallback() {
    this._mountPoint = document.createElement('div')
    this.appendChild(this._mountPoint)
    this._root = createRoot(this._mountPoint)
    this._root.render(
      <StrictMode>
        <App hostElement={this} />
      </StrictMode>
    )
  }

  disconnectedCallback() {
    this._root?.unmount()
  }
}

// Register as a custom element
if (!customElements.get('enox-chat')) {
  customElements.define('enox-chat', StyleHub AIChatWidget)
}

window.openEnoxChat = () => {
  document.querySelector('enox-chat')?.setAttribute('open', 'true')
}
