import { Navigate, Route, Routes } from "react-router-dom";
import { Calendar } from "lucide-react";
import { AppShell } from "./components/layout/AppShell";
import Today from "./pages/Today";
import Briefings from "./pages/Briefings";
import Attention from "./pages/Attention";
import Decisions from "./pages/Decisions";
import Knowledge from "./pages/Knowledge";
import Settings from "./pages/Settings";
import { SectionPlaceholder } from "./pages/SectionPlaceholder";

export default function App() {
  return (
    <Routes>
      <Route element={<AppShell />}>
        <Route index element={<Today />} />
        <Route path="briefings" element={<Briefings />} />
        <Route path="attention" element={<Attention />} />
        <Route path="decisions" element={<Decisions />} />
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
        <Route path="knowledge" element={<Knowledge />} />
        <Route path="settings" element={<Settings />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Route>
    </Routes>
  );
}
