import { useRef, useState, type KeyboardEvent } from 'react'
import DashboardReports from './components/DashboardReports'
import TransactionsSection from './components/TransactionsSection'
import LoansSection from './components/LoansSection'
import SavingsGoalsSection from './components/SavingsGoalsSection'
import TravelBudgetSection from './components/TravelBudgetSection'
import { AuthProvider, useAuth } from './auth/AuthContext'
import { AuthScreens, SessionRestoreScreen } from './auth/AuthScreens'
import './App.css'

const tabs = [
  { id: 'dashboard', label: 'Dashboard' },
  { id: 'transactions', label: 'Transactions' },
  { id: 'loans', label: 'Loans' },
  { id: 'savings-goals', label: 'Savings Goals' },
  { id: 'travel-budget', label: 'Travel Budget' },
] as const

type TabId = typeof tabs[number]['id']

function FinPathApp() {
  const { user, logout } = useAuth()
  const [activeTab, setActiveTab] = useState<TabId>('dashboard')
  const tabRefs = useRef<Array<HTMLButtonElement | null>>([])

  function handleTabKeyDown(event: KeyboardEvent<HTMLButtonElement>, index: number) {
    let nextIndex: number | undefined
    if (event.key === 'ArrowRight' || event.key === 'ArrowDown') {
      nextIndex = (index + 1) % tabs.length
    } else if (event.key === 'ArrowLeft' || event.key === 'ArrowUp') {
      nextIndex = (index - 1 + tabs.length) % tabs.length
    } else if (event.key === 'Home') {
      nextIndex = 0
    } else if (event.key === 'End') {
      nextIndex = tabs.length - 1
    }

    if (nextIndex !== undefined) {
      event.preventDefault()
      setActiveTab(tabs[nextIndex].id)
      tabRefs.current[nextIndex]?.focus()
    }
  }

  return (
    <main className="dashboard-shell min-h-screen">
      <header className="dashboard-header">
        <a className="wordmark" href="/" aria-label="FinPath home">
          <span className="wordmark-mark" aria-hidden="true">F</span>
          <span>FinPath</span>
        </a>
        <div className="header-meta">
          <span className="sample-label">
            <span aria-hidden="true" />
            {activeTab === 'dashboard'
              ? 'Live dashboard'
              : activeTab === 'transactions'
                ? 'Live transactions'
                : activeTab === 'loans'
                  ? 'Live loans'
                  : activeTab === 'savings-goals'
                    ? 'Live savings'
                    : 'Live travel'}
          </span>
          <div className="header-user">
            <span className="avatar" aria-hidden="true">
              {user?.email.charAt(0).toUpperCase() ?? '?'}
            </span>
            <span className="header-email">{user?.email}</span>
            <button
              className="text-button header-logout"
              type="button"
              onClick={() => { void logout() }}
            >
              Log out
            </button>
          </div>
        </div>
      </header>

      <div className="dashboard-content">
        <nav className="primary-navigation" role="tablist" aria-label="Main navigation">
          {tabs.map((tab, index) => (
            <button
              key={tab.id}
              ref={(element) => { tabRefs.current[index] = element }}
              className="navigation-tab"
              id={`tab-${tab.id}`}
              type="button"
              role="tab"
              aria-selected={activeTab === tab.id}
              aria-controls={`panel-${tab.id}`}
              tabIndex={activeTab === tab.id ? 0 : -1}
              onClick={() => setActiveTab(tab.id)}
              onKeyDown={(event) => handleTabKeyDown(event, index)}
            >
              {tab.label}
            </button>
          ))}
        </nav>

        <section
          className="tab-panel dashboard-tab"
          id="panel-dashboard"
          role="tabpanel"
          aria-labelledby="tab-dashboard"
          tabIndex={0}
          hidden={activeTab !== 'dashboard'}
        >
          <DashboardReports active={activeTab === 'dashboard'} />
        </section>

        <section
          className="tab-panel feature-tab"
          id="panel-transactions"
          role="tabpanel"
          aria-labelledby="tab-transactions"
          tabIndex={0}
          hidden={activeTab !== 'transactions'}
        >
          <TransactionsSection />
        </section>

        <section
          className="tab-panel feature-tab"
          id="panel-loans"
          role="tabpanel"
          aria-labelledby="tab-loans"
          tabIndex={0}
          hidden={activeTab !== 'loans'}
        >
          <LoansSection />
        </section>

        <section
          className="tab-panel feature-tab"
          id="panel-savings-goals"
          role="tabpanel"
          aria-labelledby="tab-savings-goals"
          tabIndex={0}
          hidden={activeTab !== 'savings-goals'}
        >
          <SavingsGoalsSection />
        </section>

        <section
          className="tab-panel feature-tab"
          id="panel-travel-budget"
          role="tabpanel"
          aria-labelledby="tab-travel-budget"
          tabIndex={0}
          hidden={activeTab !== 'travel-budget'}
        >
          <TravelBudgetSection />
        </section>
      </div>
    </main>
  )
}

const REFRESH_RECOVERY_MESSAGE =
  'FinPath could not restore your session because its server was unavailable. To protect your sign-in, the request was not retried automatically. Sign in again to continue.'

function AppContent() {
  const { status, restoreIssue, retryRestore } = useAuth()

  if (status === 'restoring') {
    return <SessionRestoreScreen />
  }

  if (status === 'restore_failed') {
    return (
      <SessionRestoreScreen
        issue={restoreIssue}
        onRetry={retryRestore}
      />
    )
  }

  if (status !== 'authenticated') {
    return (
      <AuthScreens
        initialNotice={restoreIssue === 'refresh_uncertain'
          ? REFRESH_RECOVERY_MESSAGE
          : null}
      />
    )
  }

  return <FinPathApp />
}

function App() {
  return (
    <AuthProvider>
      <AppContent />
    </AuthProvider>
  )
}

export default App