import { Component, ErrorInfo, ReactNode } from 'react';

interface Props { children: ReactNode; }
interface State { hasError: boolean; }

class ErrorBoundary extends Component<Props, State> {
  constructor(props: Props) {
    super(props);
    this.state = { hasError: false };
  }

  static getDerivedStateFromError(_: Error): State {
    return { hasError: true };
  }

  componentDidCatch(error: Error, errorInfo: ErrorInfo): void {
    const sentryDsn = import.meta.env.VITE_SENTRY_DSN;
    if (sentryDsn) {
      // Sentry.captureException is available if @sentry/react is installed
      console.error('Error captured:', error, errorInfo);
    }
    console.error('Application error:', error, errorInfo);
  }

  render(): ReactNode {
    if (this.state.hasError) {
      return (
        <div style={{ padding: '48px', textAlign: 'center' }}>
          <h2>应用发生错误</h2>
          <p>请刷新页面重试，或联系管理员。</p>
          <button onClick={() => window.location.reload()} style={{ marginTop: '16px' }}>
            刷新页面
          </button>
        </div>
      );
    }
    return this.props.children;
  }
}

export default ErrorBoundary;
