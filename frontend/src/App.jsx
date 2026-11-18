import React from "react";
import { Container, Nav, Navbar } from "react-bootstrap";
import { BrowserRouter, Link, Navigate, NavLink, Route, Routes } from "react-router-dom";
import { Copy, History as HistoryIcon, LayoutGrid, ListChecks, PlusCircle } from "lucide-react";
import Home from "./pages/Home.jsx";
import SetupWizard from "./pages/SetupWizard.jsx";
import History from "./pages/History.jsx";
import JobDetail from "./pages/JobDetail.jsx";
import Schedules from "./pages/Schedules.jsx";

const navItems = [
  { to: "/", label: "Overview", icon: LayoutGrid, end: true },
  { to: "/setup", label: "New clone", icon: PlusCircle },
  { to: "/profiles", label: "Profiles & schedules", icon: ListChecks },
  { to: "/history", label: "Clone history", icon: HistoryIcon },
];

export default function App() {
  return (
    <BrowserRouter>
      <Navbar expand="md" className="app-navbar" sticky="top">
        <Container>
          <Navbar.Brand as={Link} to="/" className="app-brand">
            <span className="app-brand-mark"><Copy size={16} aria-hidden="true" /></span>
            Property Cloner
          </Navbar.Brand>
          <Navbar.Toggle aria-controls="main-nav" />
          <Navbar.Collapse id="main-nav">
            <Nav className="ms-auto app-nav">
              {navItems.map(({ to, label, icon: Icon, end }) => (
                <Nav.Link key={to} as={NavLink} to={to} end={end}>
                  <Icon size={16} aria-hidden="true" /> {label}
                </Nav.Link>
              ))}
            </Nav>
          </Navbar.Collapse>
        </Container>
      </Navbar>
      <main>
        <Container className="app-main">
          <Routes>
            <Route path="/" element={<Home />} />
            <Route path="/setup" element={<SetupWizard />} />
            <Route path="/profiles" element={<Schedules />} />
            <Route path="/profiles/:profileId/edit" element={<SetupWizard />} />
            <Route path="/jobs/:jobId" element={<JobDetail />} />
            <Route path="/history" element={<History />} />
            <Route path="*" element={<Navigate to="/" replace />} />
          </Routes>
        </Container>
      </main>
    </BrowserRouter>
  );
}
