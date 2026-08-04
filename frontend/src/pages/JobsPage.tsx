import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api/client";
import type { Job } from "../types";

export function JobsPage() {
  const [jobs, setJobs] = useState<Job[]>([]);
  useEffect(() => { api.listJobs().then(setJobs).catch(() => setJobs([])); }, []);
  return <section className="page"><div className="page-heading"><div><p className="eyebrow">Operations</p><h1>Jobs</h1><p className="muted">Every run is preserved with its source, progress, and outputs.</p></div><Link className="button primary" to="/jobs/new">New job</Link></div>
    <div className="table-card">{jobs.length === 0 ? <div className="table-empty">No jobs yet. Create one to see progress here.</div> : <table><thead><tr><th>Workflow</th><th>Status</th><th>Created</th><th /></tr></thead><tbody>{jobs.map(job => <tr key={job.id}><td><Link to={`/jobs/${job.id}`}>{job.workflow === "enrich_existing" ? "Enrich existing file" : "Scrape new companies"}</Link><small>{job.input_path}</small></td><td><span className={`badge ${job.status}`}>{job.status.replaceAll("_", " ")}</span></td><td>{new Date(job.created_at).toLocaleString()}</td><td><Link to={`/jobs/${job.id}`}>Open →</Link></td></tr>)}</tbody></table>}</div>
  </section>;
}
