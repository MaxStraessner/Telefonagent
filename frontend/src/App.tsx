import { createBrowserRouter, RouterProvider } from "react-router-dom";
import { PlatformDataProvider } from "./api/PlatformDataProvider";
import { AppLayout } from "./layouts/AppLayout";
import { AppointmentsPage } from "./pages/AppointmentsPage";
import { AgentSettingsPage } from "./pages/AgentSettingsPage";
import { CompanyPage } from "./pages/CompanyPage";
import { CalendarSettingsPage } from "./pages/CalendarSettingsPage";
import { ConversationPage } from "./pages/ConversationPage";
import { OverviewPage } from "./pages/OverviewPage";
import { ServicesPage } from "./pages/ServicesPage";
import { StaffPage } from "./pages/StaffPage";
import { SystemPage } from "./pages/SystemPage";

function buildRouter() { return createBrowserRouter([{ path: "/", element: <AppLayout />, children: [
    { index: true, element: <OverviewPage /> }, { path: "testgespraech", element: <ConversationPage /> },
    { path: "ki-konfigurieren", element: <AgentSettingsPage /> },
    { path: "kalender", element: <CalendarSettingsPage /> },
    { path: "termine", element: <AppointmentsPage /> }, { path: "leistungen", element: <ServicesPage /> },
    { path: "mitarbeiter", element: <StaffPage /> }, { path: "unternehmen", element: <CompanyPage /> },
    { path: "system", element: <SystemPage /> },
  ]}]); }

export function App() { return <PlatformDataProvider><RouterProvider router={buildRouter()} /></PlatformDataProvider>; }
