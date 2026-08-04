import { NavLink, Outlet } from "react-router-dom";

const links = [["/", "Dashboard"], ["/jobs/new", "New job"], ["/jobs", "Jobs"], ["/databases", "Databases"], ["/exports", "Exports"]];

export function AppShell() {
  return <div className="app-shell">
    <aside className="sidebar"><div className="brand"><span className="brand-mark">R</span><div><strong>Reswip</strong><small>Lead acquisition</small></div></div>
      <nav>{links.map(([to, label]) => <NavLink key={to} to={to} end={to === "/"}>{label}</NavLink>)}</nav>
      <div className="sidebar-foot"><span className="status-dot" />Pipeline ready</div>
    </aside>
    <main className="main-content"><Outlet /></main>
  </div>;
}
