import { Navigate, Route, Routes } from "react-router-dom";
import { Bell, Calendar, FileText, Library, ListChecks, Settings } from "lucide-react";
import { AppShell } from "./components/layout/AppShell";
import Today from "./pages/Today";
import { SectionPlaceholder } from "./pages/SectionPlaceholder";

export default function App() {
  return (
    <Routes>
      <Route element={<AppShell />}>
        <Route index element={<Today />} />
        <Route
          path="briefings"
          element={
            <SectionPlaceholder
              title="Briefings"
              description="Executive briefings, prepared from your calendar and mail."
              icon={FileText}
            />
          }
        />
        <Route
          path="attention"
          element={
            <SectionPlaceholder
              title="Attention"
              description="One quiet queue for everything that needs your attention."
              icon={Bell}
            />
          }
        />
        <Route
          path="decisions"
          element={
            <SectionPlaceholder
              title="Decisions"
              description="Decisions surfaced from attention, ready to record."
              icon={ListChecks}
            />
          }
        />
        <Route
          path="calendar"
          element={
            <SectionPlaceholder
              title="Calendar"
              description="Your day, week and conflicts at a glance."
              icon={Calendar}
            />
          }
        />
        <Route
          path="knowledge"
          element={
            <SectionPlaceholder
              title="Knowledge"
              description="Organizational context and past interactions."
              icon={Library}
            />
          }
        />
        <Route
          path="settings"
          element={
            <SectionPlaceholder
              title="Settings"
              description="AI engine, integrations and preferences."
              icon={Settings}
            />
          }
        />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Route>
    </Routes>
  );
}
