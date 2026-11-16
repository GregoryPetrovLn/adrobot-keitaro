import { NavLink, Route, Routes } from 'react-router-dom'
import { CampaignsPage } from './pages/CampaignsPage'
import { StreamEditorPage } from './pages/StreamEditorPage'

export function App() {
  return (
    <>
      <header className="topbar">
        <span className="brand">AdRobot</span>
        <nav>
          <NavLink to="/" end>
            Campaigns
          </NavLink>
        </nav>
        <span className="spacer" />
        <span className="env">Keitaro campaign creator &amp; stream editor</span>
      </header>
      <Routes>
        <Route path="/" element={<CampaignsPage />} />
        <Route path="/campaigns/:campaignId" element={<StreamEditorPage />} />
        <Route path="*" element={<div className="page">Not found.</div>} />
      </Routes>
    </>
  )
}
