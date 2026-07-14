import { createContext, useContext, useState, useCallback, useEffect } from 'react'
import client from '../api/client'

const AuthContext = createContext(null)

export function AuthProvider({ children }) {
  const [admin, setAdmin] = useState(null)
  const [loading, setLoading] = useState(true)

  const login = useCallback(async (email, password) => {
    const data = await client.post('/login', { email, password })
    setAdmin(data.admin)
    return data
  }, [])

  const logout = useCallback(async () => {
    try {
      await client.post('/logout')
    } catch {
      // ignore network errors on logout
    }
    setAdmin(null)
  }, [])

  const checkAuth = useCallback(async () => {
    try {
      const me = await client.get('/me')
      setAdmin(me)
    } catch {
      setAdmin(null)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    checkAuth()
  }, [checkAuth])

  return <AuthContext.Provider value={{ admin, loading, login, logout }}>{children}</AuthContext.Provider>
}

export function useAuth() {
  const ctx = useContext(AuthContext)
  if (!ctx) throw new Error('useAuth must be used within AuthProvider')
  return ctx
}
