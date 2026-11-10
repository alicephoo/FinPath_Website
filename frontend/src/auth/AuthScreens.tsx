import { useRef, useState, type FormEvent } from 'react'
import { isApiError, type ApiFieldError } from '../api/errors'
import { createIdempotencyKey } from './api'
import { useAuth, type RestoreIssue } from './AuthContext'

type AuthView = 'login' | 'register' | 'verify'

type Notice = {
  kind: 'error' | 'success'
  message: string
  fieldErrors?: ApiFieldError[]
}

type AuthScreensProps = {
  initialView?: AuthView
  initialNotice?: string | null
}

function noticeFromError(error: unknown): Notice {
  if (isApiError(error)) {
    return {
      kind: 'error',
      message: error.message,
      fieldErrors: error.fieldErrors,
    }
  }
  return {
    kind: 'error',
    message: 'Something went wrong. Please try again.',
  }
}

function NoticeMessage({ notice }: { notice: Notice }) {
  return (
    <div
      className={`auth-notice ${notice.kind}`}
      role={notice.kind === 'error' ? 'alert' : 'status'}
    >
      <p>{notice.message}</p>
      {notice.fieldErrors && notice.fieldErrors.length > 0 && (
        <ul>
          {notice.fieldErrors.map((fieldError, index) => (
            <li key={`${fieldError.field}-${fieldError.code}-${index}`}>
              {fieldError.field && fieldError.field !== 'body'
                ? `${fieldError.field}: ${fieldError.message}`
                : fieldError.message}
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}

function AuthCardHeader({ title, subtitle }: { title: string; subtitle: string }) {
  return (
    <div className="auth-card-heading">
      <p className="eyebrow">FinPath account</p>
      <h1 id="auth-heading">{title}</h1>
      <p>{subtitle}</p>
    </div>
  )
}

function LoginForm({
  initialEmail,
  notice,
  onNeedsVerification,
  onShowRegister,
  onShowVerify,
}: {
  initialEmail: string
  notice: Notice | null
  onNeedsVerification: (email: string) => void
  onShowRegister: () => void
  onShowVerify: (email: string) => void
}) {
  const { login } = useAuth()
  const [email, setEmail] = useState(initialEmail)
  const [password, setPassword] = useState('')
  const [feedback, setFeedback] = useState<Notice | null>(null)
  const [busy, setBusy] = useState(false)
  const displayedNotice = feedback ?? notice

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    setFeedback(null)
    setBusy(true)
    try {
      await login({ email: email.trim(), password })
    } catch (error) {
      if (isApiError(error) && error.code === 'email_verification_required') {
        onNeedsVerification(email.trim())
        return
      }
      setFeedback(noticeFromError(error))
    } finally {
      setBusy(false)
    }
  }

  return (
    <>
      <AuthCardHeader
        title="Sign in to FinPath"
        subtitle="Use your verified account to open your financial dashboard."
      />
      {displayedNotice && <NoticeMessage notice={displayedNotice} />}
      <form className="auth-form" onSubmit={handleSubmit}>
        <label htmlFor="login-email">
          Email
          <input
            id="login-email"
            type="email"
            autoComplete="email"
            required
            value={email}
            onChange={(event) => setEmail(event.target.value)}
          />
        </label>
        <label htmlFor="login-password">
          Password
          <input
            id="login-password"
            type="password"
            autoComplete="current-password"
            required
            value={password}
            onChange={(event) => setPassword(event.target.value)}
          />
        </label>
        <button className="primary-button auth-submit" type="submit" disabled={busy}>
          {busy ? 'Signing in…' : 'Sign in'}
        </button>
      </form>
      <div className="auth-links">
        <button type="button" className="auth-link" onClick={onShowRegister}>
          Create an account
        </button>
        <button
          type="button"
          className="auth-link"
          onClick={() => onShowVerify(email.trim())}
        >
          Verify a new account
        </button>
      </div>
    </>
  )
}

function RegisterForm({
  onRegistered,
  onShowLogin,
}: {
  onRegistered: (email: string, verificationRequired: boolean) => void
  onShowLogin: () => void
}) {
  const { register } = useAuth()
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [feedback, setFeedback] = useState<Notice | null>(null)
  const [busy, setBusy] = useState(false)
  const idempotencyKeyRef = useRef(createIdempotencyKey())

  function changeEmail(value: string) {
    idempotencyKeyRef.current = createIdempotencyKey()
    setEmail(value)
  }

  function changePassword(value: string) {
    idempotencyKeyRef.current = createIdempotencyKey()
    setPassword(value)
  }

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    setFeedback(null)
    setBusy(true)
    try {
      const result = await register(
        { email: email.trim(), password },
        idempotencyKeyRef.current,
      )
      onRegistered(
        email.trim(),
        result.verification_status !== 'not_required',
      )
    } catch (error) {
      setFeedback(noticeFromError(error))
    } finally {
      setBusy(false)
    }
  }

  return (
    <>
      <AuthCardHeader
        title="Create your account"
        subtitle="Register with your email address to get started."
      />
      {feedback && <NoticeMessage notice={feedback} />}
      <form className="auth-form" onSubmit={handleSubmit}>
        <label htmlFor="register-email">
          Email
          <input
            id="register-email"
            type="email"
            autoComplete="email"
            required
            value={email}
            onChange={(event) => changeEmail(event.target.value)}
          />
        </label>
        <label htmlFor="register-password">
          Password
          <input
            id="register-password"
            type="password"
            autoComplete="new-password"
            required
            minLength={12}
            value={password}
            onChange={(event) => changePassword(event.target.value)}
          />
          <small>At least 12 characters.</small>
        </label>
        <button className="primary-button auth-submit" type="submit" disabled={busy}>
          {busy ? 'Creating account…' : 'Create account'}
        </button>
      </form>
      <div className="auth-links">
        <button type="button" className="auth-link" onClick={onShowLogin}>
          Already have an account? Sign in
        </button>
      </div>
    </>
  )
}

function VerifyEmailForm({
  initialEmail,
  notice,
  verified,
  onVerified,
  onShowLogin,
}: {
  initialEmail: string
  notice: Notice | null
  verified: boolean
  onVerified: () => void
  onShowLogin: () => void
}) {
  const { verifyEmail, resendVerification } = useAuth()
  const [email, setEmail] = useState(initialEmail)
  const [token, setToken] = useState('')
  const [feedback, setFeedback] = useState<Notice | null>(null)
  const [busy, setBusy] = useState(false)
  const [resending, setResending] = useState(false)
  const tokenKeyRef = useRef(createIdempotencyKey())

  function changeToken(value: string) {
    tokenKeyRef.current = createIdempotencyKey()
    setToken(value)
  }

  async function handleVerify(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    setFeedback(null)
    setBusy(true)
    try {
      await verifyEmail(token.trim(), tokenKeyRef.current)
      onVerified()
    } catch (error) {
      setFeedback(noticeFromError(error))
    } finally {
      setBusy(false)
    }
  }

  async function handleResend(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    setFeedback(null)
    setResending(true)
    try {
      await resendVerification(email.trim(), createIdempotencyKey())
      setFeedback({
        kind: 'success',
        message: 'If the address belongs to an unverified account, a new verification message has been sent.',
      })
    } catch (error) {
      setFeedback(noticeFromError(error))
    } finally {
      setResending(false)
    }
  }

  const displayedNotice = feedback ?? notice

  if (verified) {
    return (
      <>
        <AuthCardHeader
          title="Email verified"
          subtitle="Your account is ready. Sign in to continue."
        />
        <div className="auth-notice success" role="status">
          <p>Your email address has been verified.</p>
        </div>
        <button
          className="primary-button auth-submit"
          type="button"
          onClick={onShowLogin}
        >
          Continue to sign in
        </button>
      </>
    )
  }

  return (
    <>
      <AuthCardHeader
        title="Verify your email"
        subtitle="Enter the verification token from your email. Tokens expire after 24 hours."
      />
      {displayedNotice && <NoticeMessage notice={displayedNotice} />}
      <form className="auth-form" onSubmit={handleVerify}>
        <label htmlFor="verify-token">
          Verification token
          <input
            id="verify-token"
            type="text"
            autoComplete="one-time-code"
            required
            minLength={16}
            value={token}
            onChange={(event) => changeToken(event.target.value)}
          />
        </label>
        <button className="primary-button auth-submit" type="submit" disabled={busy}>
          {busy ? 'Verifying…' : 'Verify email'}
        </button>
      </form>

      <form className="auth-form auth-resend-form" onSubmit={handleResend}>
        <h2>Need a new token?</h2>
        <label htmlFor="verify-email">
          Email
          <input
            id="verify-email"
            type="email"
            autoComplete="email"
            required
            value={email}
            onChange={(event) => setEmail(event.target.value)}
          />
        </label>
        <button className="text-button auth-secondary" type="submit" disabled={resending}>
          {resending ? 'Sending…' : 'Send new verification token'}
        </button>
      </form>

      <div className="auth-links">
        <button type="button" className="auth-link" onClick={onShowLogin}>
          Back to sign in
        </button>
      </div>
    </>
  )
}

export function AuthScreens({
  initialView = 'login',
  initialNotice = null,
}: AuthScreensProps) {
  const [view, setView] = useState<AuthView>(initialView)
  const [email, setEmail] = useState('')
  const [notice, setNotice] = useState<Notice | null>(
    initialNotice ? { kind: 'error', message: initialNotice } : null,
  )
  const [verified, setVerified] = useState(false)

  function showLogin(nextNotice: Notice | null = null) {
    setNotice(nextNotice)
    setView('login')
  }

  function showRegister() {
    setNotice(null)
    setVerified(false)
    setView('register')
  }

  function showVerify(nextNotice: Notice | null = null) {
    setNotice(nextNotice)
    setVerified(false)
    setView('verify')
  }

  return (
    <main className="auth-shell">
      <section className="auth-card" aria-labelledby="auth-heading">
        <div className="auth-brand">
          <span className="wordmark-mark" aria-hidden="true">F</span>
          <span>FinPath</span>
        </div>
        {view === 'login' && (
          <LoginForm
            initialEmail={email}
            notice={notice}
            onNeedsVerification={(nextEmail) => {
              setEmail(nextEmail)
              showVerify({
                kind: 'error',
                message: 'Verify your email address before signing in. Request a new token below if needed.',
              })
            }}
            onShowRegister={showRegister}
            onShowVerify={(nextEmail) => {
              setEmail(nextEmail)
              showVerify()
            }}
          />
        )}
        {view === 'register' && (
          <RegisterForm
            onRegistered={(nextEmail, verificationRequired) => {
              setEmail(nextEmail)
              if (verificationRequired) {
                showVerify({
                  kind: 'success',
                  message: 'Account created. Check your email for a verification token, then enter it below.',
                })
                return
              }
              showLogin({
                kind: 'success',
                message: 'Account created. Sign in with your new credentials.',
              })
            }}
            onShowLogin={() => showLogin()}
          />
        )}
        {view === 'verify' && (
          <VerifyEmailForm
            key={email}
            initialEmail={email}
            notice={notice}
            verified={verified}
            onVerified={() => {
              setVerified(true)
              setNotice({
                kind: 'success',
                message: 'Email verified. You can now sign in.',
              })
            }}
            onShowLogin={() => showLogin({
              kind: 'success',
              message: 'Email verified. Sign in with your new account.',
            })}
          />
        )}
      </section>
      <p className="auth-footer">
        FinPath keeps access tokens in memory and uses an HttpOnly refresh cookie.
      </p>
    </main>
  )
}

const RESTORE_UNAVAILABLE_MESSAGE =
  'FinPath tried several times but could not reach its server. It may be starting after a period of inactivity.'

export function SessionRestoreScreen({
  issue = null,
  onRetry,
}: {
  issue?: RestoreIssue | null
  onRetry?: () => void
}) {
  if (issue === 'server_unavailable' && onRetry) {
    return (
      <main className="auth-shell">
        <section className="auth-card session-card" role="alert">
          <div className="auth-brand">
            <span className="wordmark-mark" aria-hidden="true">F</span>
            <span>FinPath</span>
          </div>
          <h1>Session restore paused</h1>
          <p>{RESTORE_UNAVAILABLE_MESSAGE}</p>
          <p>
            Your saved sign-in has not been discarded. You can try again safely.
          </p>
          <button
            className="primary-button auth-submit"
            type="button"
            onClick={onRetry}
          >
            Try again
          </button>
        </section>
      </main>
    )
  }

  return (
    <main className="auth-shell">
      <section className="auth-card session-card" role="status" aria-live="polite">
        <div className="auth-brand">
          <span className="wordmark-mark" aria-hidden="true">F</span>
          <span>FinPath</span>
        </div>
        <p>Restoring your session…</p>
      </section>
    </main>
  )
}
