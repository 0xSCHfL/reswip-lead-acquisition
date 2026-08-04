import { useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api } from "../api/client";
import type { CreateJobRequest } from "../types";

const initial: CreateJobRequest = { workflow: "enrich_existing", input_path: "", profile_path: "profiles/energy.yaml", enricher: "both", use_kbo: true, use_pappers_fallback: true, deduplicate: true, output_format: "csv" };

export function NewJobPage() {
  const navigate = useNavigate(); const inputRef = useRef<HTMLInputElement>(null); const [form, setForm] = useState(initial); const [sourceName, setSourceName] = useState(""); const [error, setError] = useState(""); const [uploading, setUploading] = useState(false); const [submitting, setSubmitting] = useState(false);
  const upload = async (file?: File) => { if (!file) return; setUploading(true); setError(""); try { const uploaded = await api.uploadInput(file); setForm(current => ({ ...current, input_path: uploaded.path })); setSourceName(uploaded.name); } catch (e) { setError(e instanceof Error ? e.message : "Could not upload file"); } finally { setUploading(false); } };
  const submit = async () => { setSubmitting(true); setError(""); try { const job = await api.createJob(form); navigate(`/jobs/${job.id}`); } catch (e) { setError(e instanceof Error ? e.message : "Could not create job"); setSubmitting(false); } };
  return <section className="page narrow"><div className="page-heading"><div><p className="eyebrow">Automatic enrichment</p><h1>Enrich your iQualif database.</h1><p className="muted">We’ll use the latest file in your configured iQualif folder, verify every company, and find missing decision-maker names.</p></div></div>
    <div className="form-card auto-start-card"><div className="auto-start-icon">✦</div><div><p className="eyebrow">Source database</p><h2>{sourceName || "Upload your iQualif database"}</h2><p className="muted">Upload a CSV or Excel file. We’ll store it securely and select it automatically for enrichment.</p></div><input ref={inputRef} className="hidden-file-input" type="file" accept=".csv,.xlsx" onChange={event => upload(event.target.files?.[0])} /><button className="button secondary upload-button" disabled={uploading || submitting} onClick={() => inputRef.current?.click()}>{uploading ? "Uploading…" : sourceName ? "Replace database" : "Upload iQualif database"}</button>{sourceName && <div className="uploaded-file">✓ {sourceName} is ready</div>}<div className="auto-pipeline"><span><b>1</b> KBO company status</span><span><b>2</b> First and last name</span><span><b>3</b> Pappers fallback if needed</span><span><b>4</b> New enriched output</span></div>{error && <div className="error">{error}</div>}<button className="button primary start-enrichment" disabled={submitting || uploading || !form.input_path} onClick={submit}>{submitting ? "Starting enrichment…" : "Start enrichment"}</button><small className="auto-note">Your original upload will not be overwritten.</small></div>
  </section>;
}
