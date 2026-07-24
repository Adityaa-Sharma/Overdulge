import { BrowserRouter, Navigate, Route, Routes } from 'react-router-dom'
import AppShell from './components/AppShell'
import { SessionProvider } from './lib/session'
import Dashboard from './routes/Dashboard'
import Login from './routes/Login'
import Query from './routes/Query'
import RequireAuth from './routes/RequireAuth'
import Settings from './routes/Settings'

function App() {
  return (
    <BrowserRouter basename={import.meta.env.BASE_URL}>
      <SessionProvider>
        <Routes>
          <Route path="/login" element={<Login />} />
          <Route element={<RequireAuth />}>
            {/* Every authenticated screen shares the same header + nav. */}
            <Route element={<AppShell />}>
              <Route path="/" element={<Dashboard />} />
              <Route path="/query" element={<Query />} />
              <Route path="/settings" element={<Settings />} />
            </Route>
          </Route>
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </SessionProvider>
    </BrowserRouter>
  )
}

export default App
