import { BrowserRouter, Route, Routes } from "react-router-dom";
import { AppShell } from "./components/AppShell";
import { DashboardPage } from "./pages/DashboardPage";
import { JobDetailPage } from "./pages/JobDetailPage";
import { JobsPage } from "./pages/JobsPage";
import { NewJobPage } from "./pages/NewJobPage";
import "./styles.css";

export function App() { return <BrowserRouter><Routes><Route element={<AppShell />}><Route path="/" element={<DashboardPage />} /><Route path="/jobs" element={<JobsPage />} /><Route path="/jobs/new" element={<NewJobPage />} /><Route path="/jobs/:id" element={<JobDetailPage />} /><Route path="*" element={<DashboardPage />} /></Route></Routes></BrowserRouter>; }
