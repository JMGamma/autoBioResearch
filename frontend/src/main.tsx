import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import { registerCytoscapeExtensions } from './lib/cytoscapeExtensions'
import App from './App.tsx'

registerCytoscapeExtensions()

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <App />
  </StrictMode>,
)
