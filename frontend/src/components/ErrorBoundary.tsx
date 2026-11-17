import { Component, type ReactNode } from 'react';
import { isStaleCodeError, reloadForStaleCode } from '../lib/appUpdate';

const STALE_RELOAD_DELAY_MS = 300;

interface Props {
  children: ReactNode;
  fallback?: ReactNode;
  /** When this changes (the page address), a shown error is cleared, so moving to another screen works again. */
  resetKey?: string;
}

interface State {
  hasError: boolean;
  error: Error | null;
}

export { isStaleCodeError };

export default class ErrorBoundary extends Component<Props, State> {
  state: State = { hasError: false, error: null };

  static getDerivedStateFromError(error: Error): State {
    return { hasError: true, error };
  }

  componentDidCatch(error: Error, errorInfo: React.ErrorInfo) {
    console.error('ErrorBoundary caught:', error, errorInfo.componentStack);
    // Wait for the screen being left (often Listen) to finish unmounting: its player
    // stops and lets updates go in effect cleanups that run after this commit.
    if (isStaleCodeError(error)) setTimeout(() => reloadForStaleCode(), STALE_RELOAD_DELAY_MS);
  }

  componentDidUpdate(prev: Props) {
    if (this.state.hasError && prev.resetKey !== this.props.resetKey) {
      this.setState({ hasError: false, error: null });
    }
  }

  render() {
    if (this.state.hasError) {
      if (this.props.fallback) return this.props.fallback;
      const stale = isStaleCodeError(this.state.error);
      return (
        <div className="max-w-xl mx-auto mt-20 p-6 bg-red-50 border border-red-200 rounded-lg text-center" data-testid="error-boundary">
          <h2 className="text-lg font-bold text-red-700 mb-2">
            {stale ? 'The site was updated while this tab was open' : 'Something went wrong'}
          </h2>
          <p className="text-sm text-red-600 mb-4">
            {stale ? 'Reload to open this screen in the new version.' : this.state.error?.message}
          </p>
          <button
            onClick={() => {
              this.setState({ hasError: false, error: null });
              window.location.reload();
            }}
            className="bg-red-600 hover:bg-red-700 text-white px-4 py-2 rounded text-sm transition"
          >
            Reload Page
          </button>
        </div>
      );
    }
    return this.props.children;
  }
}
