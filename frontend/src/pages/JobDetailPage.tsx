import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api } from "../api/client";
import type { JobDetail } from "../types";

export function JobDetailPage() {
  const { id = "" } = useParams(); const [job, setJob] = useState<JobDetail | null>(null); const [error, setError] = useState("");
  useEffect(() => { let stop = () => {}; const load = () => api.getJob(id).then(setJob).catch((e: Error) => setError(e.message)); load(); stop = api.subscribe(id, load); return stop; }, [id]);
  if (error) return <section className="page"><div className="error">{error}</div></section>;
  if (!job) return <section className="page"><p className="muted">Loading job…</p></section>;
  return <section className="page"><Link className="back-link" to="/jobs">← All jobs</Link><div className="page-heading"><div><p className="eyebrow">Job {job.id.slice(0, 8)}</p><h1>{job.workflow === "enrich_existing" ? "Enrich existing file" : "Scrape new companies"}</h1><p className="muted">{job.input_path}</p></div><span className={`badge large ${job.status}`}>{job.status.replaceAll("_", " ")}</span></div>
    <div className="stats"><Metric label="Rows" value={job.total_rows} /><Metric label="Names found" value={job.names_found} /><Metric label="Needs review" value={job.review_rows} /></div><div className="detail-grid"><div className="form-card"><h2>Pipeline progress</h2>{job.stages.map(stage => <div className="stage" key={stage.name}><div><strong>{stage.name}</strong><span>{stage.status}</span></div><div className="progress"><i style={{ width: `${stage.total ? Math.min(100, stage.completed / stage.total * 100) : stage.status === "completed" ? 100 : 0}%` }} /></div></div>)}</div><div className="form-card"><h2>Output files</h2>{job.artifacts.length ? job.artifacts.map(artifact => <a className="artifact" key={artifact.name} href={api.artifactUrl(job.id, artifact.name)}>{artifact.name}<small>{Math.round(artifact.size_bytes / 1024)} KB · Download</small></a>) : <p className="muted">Outputs will appear when the job finishes.</p>}{job.error && <div className="error">{job.error}</div>}</div></div>
  </section>;
}
function Metric({ label, value }: { label: string; value: number }) { return <div><span>{label}</span><strong>{value}</strong><small>Updated live</small></div>; }
