/**
 * Small presentational primitives.
 *
 * Grouped in one file on purpose: each is a handful of lines, and keeping them
 * together makes the shared visual language easy to see and change.
 */
import { AlertCircle, AlertTriangle, Info, Loader2, X } from 'lucide-react';

export function Button({
  variant = 'secondary',
  size,
  icon: Icon,
  loading = false,
  children,
  className = '',
  ...props
}) {
  const classes = ['btn', `btn-${variant}`, size === 'sm' ? 'btn-sm' : '', className]
    .filter(Boolean)
    .join(' ');
  return (
    <button className={classes} {...props} disabled={loading || props.disabled}>
      {loading ? (
        <Loader2 size={15} className="spin" aria-hidden="true" />
      ) : Icon ? (
        <Icon size={15} aria-hidden="true" />
      ) : null}
      {children}
    </button>
  );
}

export function Card({ title, hint, actions, children, className = '' }) {
  return (
    <section className={`card ${className}`.trim()}>
      {(title || actions) && (
        <header className="card-head">
          <div>
            {title && <h2 className="card-title">{title}</h2>}
            {hint && <p className="card-hint">{hint}</p>}
          </div>
          {actions}
        </header>
      )}
      {children}
    </section>
  );
}

export function Badge({ tone = 'neutral', dot = false, children }) {
  return (
    <span className={`badge badge-${tone}`}>
      {dot && <span className="badge-dot" aria-hidden="true" />}
      {children}
    </span>
  );
}

export function Spinner({ label = 'Loading' }) {
  return (
    <div className="row" style={{ color: 'var(--ink-muted)', padding: '20px 0' }}>
      <Loader2 size={16} className="spin" aria-hidden="true" />
      <span>{label}</span>
    </div>
  );
}

export function ProgressBar({ value, indeterminate = false, label }) {
  return (
    <div
      role="progressbar"
      aria-valuenow={indeterminate ? undefined : value}
      aria-valuemin={0}
      aria-valuemax={100}
      aria-label={label}
      className="progress"
    >
      <div
        className={`progress-bar ${indeterminate ? 'progress-indeterminate' : ''}`.trim()}
        style={indeterminate ? undefined : { width: `${Math.min(100, Math.max(0, value || 0))}%` }}
      />
    </div>
  );
}

const ALERT_ICONS = { danger: AlertCircle, warn: AlertTriangle, info: Info };

export function Alert({ tone = 'info', title, children }) {
  const Icon = ALERT_ICONS[tone] || Info;
  return (
    <div className={`alert alert-${tone}`} role={tone === 'danger' ? 'alert' : 'status'}>
      <Icon size={16} className="alert-icon" aria-hidden="true" />
      <div>
        {title && <div className="alert-title">{title}</div>}
        <div>{children}</div>
      </div>
    </div>
  );
}

export function EmptyState({ icon: Icon, title, children, action }) {
  return (
    <div className="empty">
      {Icon && <Icon size={28} className="empty-icon" aria-hidden="true" />}
      <div className="empty-title">{title}</div>
      {children && <div className="empty-body">{children}</div>}
      {action}
    </div>
  );
}

export function Tabs({ tabs, active, onChange, ariaLabel = 'Views' }) {
  return (
    <div className="tabs" role="tablist" aria-label={ariaLabel}>
      {tabs.map((tab) => (
        <button
          key={tab.id}
          role="tab"
          type="button"
          id={`tab-${tab.id}`}
          aria-selected={active === tab.id}
          aria-controls={`panel-${tab.id}`}
          className="tab"
          onClick={() => onChange(tab.id)}
        >
          {tab.label}
          {tab.count != null && <span className="text-muted"> ({tab.count})</span>}
        </button>
      ))}
    </div>
  );
}

export function TabPanel({ id, active, children }) {
  if (id !== active) return null;
  return (
    <div role="tabpanel" id={`panel-${id}`} aria-labelledby={`tab-${id}`} tabIndex={-1}>
      {children}
    </div>
  );
}

export function Stat({ value, label }) {
  return (
    <div className="stat">
      <div className="stat-value">{value}</div>
      <div className="stat-label">{label}</div>
    </div>
  );
}

export function Toast({ tone = 'info', message, onDismiss }) {
  const toneClass = tone === 'error' ? 'is-error' : tone === 'success' ? 'is-success' : '';
  return (
    <div className={`toast ${toneClass}`.trim()} role="status">
      <span>{message}</span>
      <button type="button" className="toast-close" onClick={onDismiss} aria-label="Dismiss notification">
        <X size={14} />
      </button>
    </div>
  );
}
