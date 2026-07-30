import { Component, ReactNode } from 'react'
import { useT } from '../i18n/useT'

interface Props { children: ReactNode }
interface State { error: Error | null }

/**
 * 错误展示区：抽成函数子组件，好在里面用 useT()——ErrorBoundary 本体是 class
 * 组件（React 错误边界要求 class,不能在其中直接用 hook）。
 */
function ErrorFallback({ message, onReload }: { message: string; onReload: () => void }) {
  const t = useT()
  return (
    <div className="flex items-center justify-center h-screen">
      <div className="text-center">
        <h2 className="text-xl font-bold text-neg mb-2">{t('error.boundaryTitle')}</h2>
        <p className="text-fg-muted text-sm">{message}</p>
        <button
          className="mt-4 px-4 py-2 bg-accent text-accent-fg rounded-md hover:opacity-90"
          onClick={onReload}
        >
          {t('error.reload')}
        </button>
      </div>
    </div>
  )
}

export default class ErrorBoundary extends Component<Props, State> {
  state: State = { error: null }

  static getDerivedStateFromError(error: Error) {
    return { error }
  }

  handleReload = () => {
    this.setState({ error: null })
    window.location.reload()
  }

  render() {
    if (this.state.error) {
      return <ErrorFallback message={this.state.error.message} onReload={this.handleReload} />
    }
    return this.props.children
  }
}
