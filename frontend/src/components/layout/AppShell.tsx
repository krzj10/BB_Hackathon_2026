import { Outlet } from "react-router-dom";
import { Sidebar } from "./Sidebar";
import { MobileTabBar } from "./MobileTabBar";
import { Header } from "./Header";
import { VoiceControl } from "../voice/VoiceControl";

export function AppShell() {
  return (
    <div className="flex h-svh overflow-hidden bg-background text-foreground">
      <Sidebar />
      <div className="flex min-w-0 flex-1 flex-col">
        <Header />
        <main className="flex-1 overflow-y-auto">
          <div className="pb-20 md:pb-0">
            <Outlet />
          </div>
        </main>
      </div>
      <MobileTabBar />
      <VoiceControl />
    </div>
  );
}
