import { Navigate, Route, Routes } from "react-router-dom";
import Layout from "./components/Layout";
import FlowPage from "./pages/FlowPage";
import LoginPage from "./pages/LoginPage";
import HoldingsPage from "./pages/HoldingsPage";
import IntelPage from "./pages/IntelPage";
import CtxMatrixPage from "./pages/CtxMatrixPage";
import Level1Page from "./pages/Level1Page";
import LabPage from "./pages/LabPage";
import OverviewPage from "./pages/OverviewPage";
import RecommendationsPage from "./pages/RecommendationsPage";
import SectorDetailPage from "./pages/SectorDetailPage";
import SectorsPage from "./pages/SectorsPage";
import SettingsPage from "./pages/SettingsPage";
import StockDetailPage from "./pages/StockDetailPage";
import WatchlistsPage from "./pages/WatchlistsPage";

export default function App() {
  return (
    <Routes>
      <Route path="/login" element={<LoginPage />} />
      <Route element={<Layout />}>
        <Route index element={<Navigate to="/overview" replace />} />
        <Route path="/overview" element={<OverviewPage />} />
        <Route path="/intel" element={<IntelPage />} />
        <Route path="/recommendations" element={<RecommendationsPage />} />
        <Route path="/holdings" element={<HoldingsPage />} />
        <Route path="/sectors" element={<SectorsPage />} />
        <Route path="/sectors/:id" element={<SectorDetailPage />} />
        <Route path="/flow" element={<FlowPage />} />
        <Route path="/watchlists" element={<WatchlistsPage />} />
        <Route path="/lab" element={<LabPage />} />
        <Route path="/ctx-matrix" element={<CtxMatrixPage />} />
        <Route path="/level1" element={<Level1Page />} />
        <Route path="/settings" element={<SettingsPage />} />
        <Route path="/stocks/:id" element={<StockDetailPage />} />
        <Route path="*" element={<Navigate to="/overview" replace />} />
      </Route>
    </Routes>
  );
}
