import React from "react";
import { Container, Nav, Navbar } from "react-bootstrap";
import { BrowserRouter, Link, Route, Routes } from "react-router-dom";
import SetupWizard from "./pages/SetupWizard.jsx";
import History from "./pages/History.jsx";
import JobDetail from "./pages/JobDetail.jsx";
import Schedules from "./pages/Schedules.jsx";

export default function App() {
  return (
    <BrowserRouter>
      <Navbar bg="dark" variant="dark" expand="lg">
        <Container>
          <Navbar.Brand>Property Cloner</Navbar.Brand>
          <Nav>
            <Nav.Link as={Link} to="/">
              Setup Wizard
            </Nav.Link>
            <Nav.Link as={Link} to="/profiles">
              Profiles &amp; Schedules
            </Nav.Link>
            <Nav.Link as={Link} to="/history">
              Clone History
            </Nav.Link>
          </Nav>
        </Container>
      </Navbar>
      <Container className="py-4">
        <Routes>
          <Route path="/" element={<SetupWizard />} />
          <Route path="/profiles" element={<Schedules />} />
          <Route path="/profiles/:profileId/edit" element={<SetupWizard />} />
          <Route path="/jobs/:jobId" element={<JobDetail />} />
          <Route path="/history" element={<History />} />
        </Routes>
      </Container>
    </BrowserRouter>
  );
}
