import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from 'react'
import { isApiError } from '../api/errors'
import { isColdStartFailure } from '../api/retry'
import {
  consumeVerificationToken,
  fetchCsrfToken,
  fetchCurrentUser,
  loginAccount,
  logoutSession,
  refreshSession as refreshSessionRequest,
  registerAccount,
  requestVerificationEmail,
  type AuthSession,
  type AuthUser,
  type LoginCredentials,
  type RegistrationResult,
} from './api'

export type AuthStatus =
  | 'restoring'
  | 'restore_failed'
  | 'anonymous'
  | 'authenticated'

export type RestoreIssue = 'server_unavailable' | 'refresh_uncertain'

type AuthState = {
  status: AuthStatus
  user: AuthUser | null
  restoreIssue: RestoreIssue | null
}

export type AuthContextValue = {
  status: AuthStatus
  user: AuthUser | null
  restoreIssue: RestoreIssue | null
  retryRestore: () => void
  login: (credentials: LoginCredentials) => Promise<AuthUser>
  logout: () => Promise<void>
  refresh: () => Promise<AuthSession>
  register: (
    credentials: LoginCredentials,
    idempotencyKey?: string,
  ) => Promise<RegistrationResult>
  verifyEmail: (token: string, idempotencyKey?: string) => Promise<void>
  resendVerification: (
    email: string,
    idempotencyKey?: string,
  ) => Promise<void>
  authorizedRequest: <T>(
    request: (accessToken: string) => Promise<T>,
  ) => Promise<T>
}

const AuthContext = createContext<AuthContextValue | null>(null)

type RestoredSession = {
  session: AuthSession
  user: AuthUser
}

type RestoreStage = 'csrf' | 'refresh' | 'profile'

class SessionRestoreError extends Error {
  readonly stage: RestoreStage
  readonly originalError: unknown

  constructor(stage: RestoreStage, originalError: unknown) {
    super('Session restoration failed.')
    this.name = 'SessionRestoreError'
    this.stage = stage
    this.originalError = originalError
  }
}

async function restoreExistingSession(): Promise<RestoredSession> {
  let csrfToken: string
  try {
    csrfToken = await fetchCsrfToken()
  } catch (error) {
    throw new SessionRestoreError('csrf', error)
  }

  // A refresh POST is deliberately attempted only once. If its response is
  // lost after the server rotated the token, retrying would present the old
  // token and can trigger refresh-token reuse detection.
  let session: AuthSession
  try {
    session = await refreshSessionRequest(csrfToken)
  } catch (error) {
    throw new SessionRestoreError('refresh', error)
  }

  let user: AuthUser
  try {
    user = await fetchCurrentUser(session.access_token)
  } catch (error) {
    throw new SessionRestoreError('profile', error)
  }

  return { session, user }
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const [state, setState] = useState<AuthState>({
    status: 'restoring',
    user: null,
    restoreIssue: null,
  })
  const sessionRef = useRef<AuthSession | null>(null)
  const restorePromiseRef = useRef<Promise<RestoredSession> | null>(null)
  const refreshPromiseRef = useRef<Promise<AuthSession> | null>(null)
  const activeRef = useRef(true)

  const performRestore = useCallback(() => {
    if (!restorePromiseRef.current) {
      restorePromiseRef.current = restoreExistingSession()
    }

    restorePromiseRef.current
      .then(({ session, user }) => {
        if (!activeRef.current) return
        sessionRef.current = session
        setState({ status: 'authenticated', user, restoreIssue: null })
      })
      .catch((error: unknown) => {
        if (!activeRef.current) return

        sessionRef.current = null
        const stage = error instanceof SessionRestoreError ? error.stage : null
        const originalError = error instanceof SessionRestoreError
          ? error.originalError
          : error
        const coldStart = isColdStartFailure(originalError)

        // A failed safe GET can be retried by the user. A failed refresh POST
        // must not be retried: the server may already have rotated the token
        // even though the response never reached the browser.
        if (stage !== 'refresh' && coldStart) {
          setState({
            status: 'restore_failed',
            user: null,
            restoreIssue: 'server_unavailable',
          })
          return
        }

        setState({
          status: 'anonymous',
          user: null,
          restoreIssue: stage === 'refresh' && coldStart
            ? 'refresh_uncertain'
            : null,
        })
      })
  }, [])

  useEffect(() => {
    activeRef.current = true
    performRestore()

    return () => {
      activeRef.current = false
    }
  }, [performRestore])

  const retryRestore = useCallback(() => {
    restorePromiseRef.current = null
    setState({ status: 'restoring', user: null, restoreIssue: null })
    performRestore()
  }, [performRestore])

  const refresh = useCallback(async (): Promise<AuthSession> => {
    if (refreshPromiseRef.current) {
      return refreshPromiseRef.current
    }

    const existingSession = sessionRef.current
    let tracked: Promise<AuthSession>
    const request = (async () => {
      const csrfToken = existingSession?.csrf_token ?? await fetchCsrfToken()
      return refreshSessionRequest(csrfToken)
    })()
      .then((session) => {
        sessionRef.current = session
        setState({ status: 'authenticated', user: session.user, restoreIssue: null })
        return session
      })
      .catch((error: unknown) => {
        sessionRef.current = null
        setState({ status: 'anonymous', user: null, restoreIssue: null })
        throw error
      })

    tracked = request.finally(() => {
      if (refreshPromiseRef.current === tracked) {
        refreshPromiseRef.current = null
      }
    })
    refreshPromiseRef.current = tracked
    return tracked
  }, [])

  const login = useCallback(async (credentials: LoginCredentials) => {
    const session = await loginAccount(credentials)
    sessionRef.current = session
    setState({ status: 'authenticated', user: session.user, restoreIssue: null })
    return session.user
  }, [])

  const logout = useCallback(async () => {
    const session = sessionRef.current
    sessionRef.current = null
    setState({ status: 'anonymous', user: null, restoreIssue: null })

    if (!session) return

    try {
      await logoutSession(session.csrf_token)
    } catch {
      // The local session is cleared before the request so a network or
      // server failure cannot leave authenticated UI state behind.
    }
  }, [])

  const authorizedRequest = useCallback(
    async <T,>(request: (accessToken: string) => Promise<T>): Promise<T> => {
      const session = sessionRef.current ?? await refresh()

      try {
        return await request(session.access_token)
      } catch (error) {
        if (!isApiError(error) || error.status !== 401) {
          throw error
        }

        // One refresh attempt and one retry at most. A second 401 is returned
        // to the caller instead of starting another refresh loop.
        const refreshed = await refresh()
        return request(refreshed.access_token)
      }
    },
    [refresh],
  )

  const value = useMemo<AuthContextValue>(() => ({
    status: state.status,
    user: state.user,
    restoreIssue: state.restoreIssue,
    retryRestore,
    login,
    logout,
    refresh,
    register: registerAccount,
    verifyEmail: consumeVerificationToken,
    resendVerification: requestVerificationEmail,
    authorizedRequest,
  }), [
    state.status,
    state.user,
    state.restoreIssue,
    retryRestore,
    login,
    logout,
    refresh,
    authorizedRequest,
  ])

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>
}

export function useAuth(): AuthContextValue {
  const context = useContext(AuthContext)
  if (!context) {
    throw new Error('useAuth must be used inside an AuthProvider.')
  }
  return context
}
