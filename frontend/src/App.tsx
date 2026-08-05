import { createBrowserRouter, Navigate, RouterProvider } from "react-router-dom";
import { lazy, Suspense, type ReactNode } from "react";
import { PlatformDataProvider } from "./api/PlatformDataProvider";
import { AuthProvider, useAuth } from "./api/AuthProvider";
import { ProtectedRoute } from "./components/ProtectedRoute";
import { AppLayout } from "./layouts/AppLayout";
import { PlatformLayout } from "./layouts/PlatformLayout";
import { AppointmentsPage } from "./pages/AppointmentsPage";
import { AgentSettingsPage } from "./pages/AgentSettingsPage";
import { CompanyPage } from "./pages/CompanyPage";
import { CalendarSettingsPage } from "./pages/CalendarSettingsPage";
import { ConversationPage } from "./pages/ConversationPage";
import { OverviewPage } from "./pages/OverviewPage";
import { ServicesPage } from "./pages/ServicesPage";
import { StaffPage } from "./pages/StaffPage";
import { SystemPage } from "./pages/SystemPage";
import { LoginPage } from "./pages/LoginPage";
import { InitialSetupPage } from "./pages/InitialSetupPage";
import { AccountsPage } from "./pages/AccountsPage";
import { ForgotPasswordPage } from "./pages/ForgotPasswordPage";
import { InvitationAcceptPage } from "./pages/InvitationAcceptPage";
import { PasswordChangePage } from "./pages/PasswordChangePage";
import { ResetPasswordPage } from "./pages/ResetPasswordPage";

const AuditPage = lazy(() => import("./pages/AuditPage").then((module) => ({ default: module.AuditPage })));
const CompaniesPage = lazy(() => import("./pages/CompaniesPage").then((module) => ({ default: module.CompaniesPage })));
const CompanyWizardPage = lazy(() => import("./pages/CompanyWizardPage").then((module) => ({ default: module.CompanyWizardPage })));
const PlatformAdminsPage = lazy(() => import("./pages/PlatformAdminsPage").then((module) => ({ default: module.PlatformAdminsPage })));
const PlatformCompanyDetailPage = lazy(() => import("./pages/PlatformCompanyDetailPage").then((module) => ({ default: module.PlatformCompanyDetailPage })));
const PlatformHomePage = lazy(() => import("./pages/PlatformHomePage").then((module) => ({ default: module.PlatformHomePage })));

function Deferred({ children }: { children: ReactNode }) {
  return <Suspense fallback={<p className="page">Bereich wird geladen …</p>}>{children}</Suspense>;
}

function CompanyShell() {
  const { session } = useAuth();
  const company = session?.active_company ?? session?.tenant;
  if (!company) return <Navigate to="/plattform" replace />;
  return <PlatformDataProvider key={company.id}><AppLayout /></PlatformDataProvider>;
}

function ProtectedShell() {
  return <ProtectedRoute><CompanyShell /></ProtectedRoute>;
}

function PlatformShell() {
  const { session } = useAuth();
  if (!session?.user.platform_role) return <Navigate to="/" replace />;
  return <PlatformLayout />;
}

function ProtectedPlatformShell() {
  return <ProtectedRoute><PlatformShell /></ProtectedRoute>;
}

function PermissionRoute({ permission, children }: { permission: string; children: ReactNode }) {
  const { session } = useAuth();
  if (!(session?.permissions ?? []).includes(permission)) return <Navigate to="/" replace />;
  return children;
}

function buildRouter() { return createBrowserRouter([
  { path: "/login", element: <LoginPage /> },
  { path: "/einrichtung", element: <InitialSetupPage /> },
  { path: "/passwort-vergessen", element: <ForgotPasswordPage /> },
  { path: "/passwort-zuruecksetzen", element: <ResetPasswordPage /> },
  { path: "/einladung/:token", element: <InvitationAcceptPage /> },
  { path: "/passwort-aendern", element: <ProtectedRoute allowPasswordChange><PasswordChangePage /></ProtectedRoute> },
  { path: "/plattform", element: <ProtectedPlatformShell />, children: [
    { index: true, element: <Deferred><PlatformHomePage /></Deferred> },
    { path: "unternehmen", element: <Deferred><CompaniesPage /></Deferred> },
    { path: "unternehmen/neu", element: <Deferred><CompanyWizardPage /></Deferred> },
    { path: "unternehmen/:companyId", element: <Deferred><PlatformCompanyDetailPage /></Deferred> },
    { path: "administratoren", element: <Deferred><PlatformAdminsPage /></Deferred> },
    { path: "audit", element: <Deferred><AuditPage /></Deferred> },
  ]},
  { path: "/", element: <ProtectedShell />, children: [
    { index: true, element: <OverviewPage /> }, { path: "testgespraech", element: <ConversationPage /> },
    { path: "ki-konfigurieren", element: <PermissionRoute permission="company.manage"><AgentSettingsPage /></PermissionRoute> },
    { path: "kalender", element: <PermissionRoute permission="company.manage"><CalendarSettingsPage /></PermissionRoute> },
    { path: "termine", element: <AppointmentsPage /> }, { path: "leistungen", element: <PermissionRoute permission="company.manage"><ServicesPage /></PermissionRoute> },
    { path: "mitarbeiter", element: <PermissionRoute permission="company.manage"><StaffPage /></PermissionRoute> }, { path: "unternehmen", element: <PermissionRoute permission="company.manage"><CompanyPage /></PermissionRoute> },
    { path: "konten", element: <PermissionRoute permission="company.users.manage"><AccountsPage /></PermissionRoute> },
    { path: "audit", element: <PermissionRoute permission="company.audit.read"><Deferred><AuditPage company /></Deferred></PermissionRoute> },
    { path: "system", element: <PermissionRoute permission="company.manage"><SystemPage /></PermissionRoute> },
  ]},
]); }

export function App() { return <AuthProvider><RouterProvider router={buildRouter()} /></AuthProvider>; }
