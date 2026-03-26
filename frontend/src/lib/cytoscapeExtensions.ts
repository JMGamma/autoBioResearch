import Cytoscape from 'cytoscape'
import fcose from 'cytoscape-fcose'
import dagre from 'cytoscape-dagre'

// Register each extension once. Guard prevents re-registration on HMR.
let registered = false
export function registerCytoscapeExtensions() {
  if (registered) return
  Cytoscape.use(fcose)
  Cytoscape.use(dagre)
  registered = true
}
