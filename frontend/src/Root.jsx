import { useEffect, useState } from 'react'

import App from './App.jsx'
import AdminDashboard from './admin/AdminDashboard.jsx'
import TripDetail from './admin/TripDetail.jsx'

// Pages: the live map (#/), the admin dashboard (#/admin) and one
// trip's details (#/admin/trip/<request id>).
function Root() {
  const [hash, setHash] = useState(window.location.hash)

  useEffect(() => {
    const onChange = () => setHash(window.location.hash)
    window.addEventListener('hashchange', onChange)
    return () => window.removeEventListener('hashchange', onChange)
  }, [])

  if (hash.startsWith('#/admin/trip/')) {
    const requestId = decodeURIComponent(hash.slice('#/admin/trip/'.length))
    return <TripDetail key={requestId} requestId={requestId} />
  }
  return hash.startsWith('#/admin') ? <AdminDashboard /> : <App />
}

export default Root
