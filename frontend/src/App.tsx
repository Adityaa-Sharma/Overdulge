import { BrowserRouter, Navigate, Route, Routes } from 'react-router-dom'
import Login from './routes/Login'
import Protected from './routes/Protected'

function App() {
  return (
    <BrowserRouter basename={import.meta.env.BASE_URL}>
      <Routes>
        <Route path="/" element={<Protected />} />
        <Route path="/login" element={<Login />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </BrowserRouter>
  )
}

export default App
