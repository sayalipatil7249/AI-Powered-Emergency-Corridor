import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import Root from './Root.jsx'
import { applyTheme, savedTheme } from './theme'

// Before the first paint, so the page never flashes the other theme.
applyTheme(savedTheme())

createRoot(document.getElementById('root')).render(
  <StrictMode>
    <Root />
  </StrictMode>,
)
