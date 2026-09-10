import { useState } from 'react';
import { NavLink, Outlet, Link } from 'react-router-dom';
import { AudioLines, LayoutDashboard, ListVideo, Menu, Upload } from 'lucide-react';
import { Badge } from '../components/ui';
import { useSystemHealth } from '../hooks/useSystemHealth';

const NAV = [
  { to: '/', label: 'Dashboard', icon: LayoutDashboard, end: true },
  { to: '/upload', label: 'Upload meeting', icon: Upload },
  { to: '/meetings', label: 'Meetings', icon: ListVideo },
];

export function AppLayout() {
  const [navOpen, setNavOpen] = useState(false);
  const { health, offline } = useSystemHealth();

  return (
    <div className="app">
      <aside className={`sidebar ${navOpen ? 'is-open' : ''}`.trim()}>
        <Link to="/" className="brand" onClick={() => setNavOpen(false)}>
          <span className="brand-mark" aria-hidden="true">
            <AudioLines size={17} />
          </span>
          <span className="brand-name">Meeting Intelligence</span>
        </Link>

        <nav className="nav" aria-label="Main">
          {NAV.map(({ to, label, icon: Icon, end }) => (
            <NavLink
              key={to}
              to={to}
              end={end}
              className={({ isActive }) => `nav-link ${isActive ? 'is-active' : ''}`.trim()}
              onClick={() => setNavOpen(false)}
            >
              <Icon size={16} aria-hidden="true" />
              {label}
            </NavLink>
          ))}
        </nav>

        <div className="sidebar-footer">
          {health?.whisper_model && (
            <div>
              Whisper {health.whisper_model} &middot; {health.whisper_backend}
            </div>
          )}
          <div>Milestone 1 + 2</div>
        </div>
      </aside>

      {navOpen && (
        <button
          type="button"
          className="sidebar-scrim"
          aria-label="Close navigation"
          onClick={() => setNavOpen(false)}
        />
      )}

      <div className="main">
        <header className="topbar">
          <button
            type="button"
            className="topbar-mobile-toggle"
            onClick={() => setNavOpen((open) => !open)}
            aria-label="Toggle navigation"
            aria-expanded={navOpen}
          >
            <Menu size={18} />
          </button>

          <div className="row" style={{ marginLeft: 'auto' }}>
            <ServiceStatus health={health} offline={offline} />
          </div>
        </header>

        <main className="content">
          <Outlet />
        </main>
      </div>
    </div>
  );
}

/** One honest pill: green only when everything the pipeline needs is present. */
function ServiceStatus({ health, offline }) {
  if (offline) {
    return (
      <Badge tone="danger" dot>
        Backend unreachable
      </Badge>
    );
  }
  if (!health) return null;

  const missing = [];
  if (!health.ffmpeg_available) missing.push('FFmpeg');
  if (!health.supabase_configured) missing.push('Supabase');
  // `llm_configured` covers the whole Grok -> Gemini -> Groq chain. Older
  // backends only reported `grok_configured`, so fall back to it.
  const aiConfigured = health.llm_configured ?? health.grok_configured;
  if (!aiConfigured) missing.push('AI provider');

  if (!missing.length) {
    return (
      <Badge tone="success" dot>
        All services ready
      </Badge>
    );
  }
  return (
    <Badge tone="warn" dot>
      {missing.join(', ')} not configured
    </Badge>
  );
}
