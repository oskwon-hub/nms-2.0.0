import { NavLink, Route, Routes } from "react-router-dom";
import DevicesTreePage from "./pages/DevicesTreePage";
import TopologyGraphPage from "./pages/TopologyGraphPage";
import DiscoveryPage from "./pages/DiscoveryPage";
import ConfigurationPage from "./pages/ConfigurationPage";
import DeviceDetailPage from "./pages/DeviceDetailPage";
import AlarmsPage from "./pages/AlarmsPage";
import ReportsPage from "./pages/ReportsPage";
import SettingsPage from "./pages/SettingsPage";
import HelpPage from "./pages/HelpPage";

// 17.6절: NMS의 최종 권장 상단 메뉴 = Dashboard / Devices / Topology / Discovery /
// Alarms / Reports / Settings.
// [KOS20260922] Help(About/라이선스 정보)를 Settings 오른쪽에 추가했다.
export default function App() {
  return (
    <div className="app-shell">
      <header className="app-header">
        <div className="app-title">NMS</div>
        <nav className="app-nav">
          <NavLink to="/devices" className={({ isActive }) => (isActive ? "active" : "")}>
            Devices
          </NavLink>
          <NavLink to="/topology" className={({ isActive }) => (isActive ? "active" : "")}>
            Topology
          </NavLink>
          <NavLink to="/discovery" className={({ isActive }) => (isActive ? "active" : "")}>
            Discovery
          </NavLink>
          <NavLink to="/configuration" className={({ isActive }) => (isActive ? "active" : "")}>
            구성 관리
          </NavLink>
          <NavLink to="/alarms" className={({ isActive }) => (isActive ? "active" : "")}>
            Alarms
          </NavLink>
          <NavLink to="/reports" className={({ isActive }) => (isActive ? "active" : "")}>
            Reports
          </NavLink>
          <NavLink to="/settings" className={({ isActive }) => (isActive ? "active" : "")}>
            Settings
          </NavLink>
          <NavLink to="/help" className={({ isActive }) => (isActive ? "active" : "")}>
            Help
          </NavLink>
        </nav>
      </header>
      <main className="app-main">
        <Routes>
          <Route path="/" element={<DevicesTreePage />} />
          <Route path="/devices" element={<DevicesTreePage />} />
          <Route path="/devices/:id" element={<DeviceDetailPage />} />
          <Route path="/topology" element={<TopologyGraphPage />} />
          <Route path="/discovery" element={<DiscoveryPage />} />
          <Route path="/configuration" element={<ConfigurationPage />} />
          <Route path="/alarms" element={<AlarmsPage />} />
          <Route path="/reports" element={<ReportsPage />} />
          <Route path="/settings" element={<SettingsPage />} />
          <Route path="/help" element={<HelpPage />} />
        </Routes>
      </main>
    </div>
  );
}
