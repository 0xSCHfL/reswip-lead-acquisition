import { Link } from "react-router-dom";

export function DashboardPage() {
  return <section className="page"><div className="page-heading"><div><p className="eyebrow">Workspace</p><h1>Good morning, let’s find better leads.</h1><p className="muted">Enrich an existing database or build a fresh one from a trusted source.</p></div><Link className="button primary" to="/jobs/new">Create new job</Link></div>
    <div className="stats"><div><span>Active jobs</span><strong>—</strong><small>Connect a worker to begin</small></div><div><span>Names found</span><strong>—</strong><small>Across completed jobs</small></div><div><span>Review queue</span><strong>—</strong><small>Needs your attention</small></div></div>
    <div className="empty-card"><div className="empty-icon">✦</div><h2>Your lead workspace is ready</h2><p>Start with your full iQualif database. The pipeline will preserve the source file and create a separate enriched output.</p><Link className="button secondary" to="/jobs/new">Start your first job</Link></div>
  </section>;
}
