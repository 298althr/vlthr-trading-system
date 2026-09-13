import { Component, type ReactNode } from 'react';

interface Props {
  children: ReactNode;
}

interface State {
  hasError: boolean;
  error: Error | null;
}

export class ErrorBoundary extends Component<Props, State> {
  constructor(props: Props) {
    super(props);
    this.state = { hasError: false, error: null };
  }

  static getDerivedStateFromError(error: Error): State {
    return { hasError: true, error };
  }

  componentDidCatch(error: Error, info: React.ErrorInfo) {
    console.error('[ErrorBoundary]', error, info.componentStack);
  }

  render() {
    if (this.state.hasError) {
      return (
        <div style={{
          minHeight: '100vh',
          display: 'flex',
          flexDirection: 'column',
          alignItems: 'center',
          justifyContent: 'center',
          background: '#000',
          color: '#fff',
          padding: 24,
          fontFamily: 'Inter, sans-serif',
        }}>
          <div style={{ fontSize: 14, color: '#ff5252', marginBottom: 12 }}>Something went wrong</div>
          <div style={{ fontSize: 12, color: '#888', marginBottom: 24, textAlign: 'center', maxWidth: 320 }}>
            {this.state.error?.message || 'An unexpected error occurred.'}
          </div>
          <button
            onClick={() => window.location.reload()}
            style={{
              padding: '12px 24px',
              borderRadius: 8,
              border: 'none',
              background: '#adc6ff',
              color: '#000',
              fontWeight: 700,
              cursor: 'pointer',
              fontSize: 14,
            }}
          >
            Reload App
          </button>
        </div>
      );
    }
    return this.props.children;
  }
}
