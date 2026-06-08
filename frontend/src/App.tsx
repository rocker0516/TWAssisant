import { Navigate, Route, Routes } from "react-router-dom";
import Layout from "./components/Layout";
import HoldingsPage from "./pages/HoldingsPage";
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
      <Route element={<Layout />}>
        <Route index element={<Navigate to="/overview" replace />} />
        <Route path="/overview" element={<OverviewPage />} />
        <Route path="/recommendations" element={<RecommendationsPage />} />
        <Route path="/holdings" element={<HoldingsPage />} />
        <Route path="/sectors" element={<SectorsPage />} />
        <Route path="/sectors/:id" element={<SectorDetailPage />} />
        <Route path="/watchlists" element={<WatchlistsPage />} />
        <Route path="/settings" element={<SettingsPage />} />
        <Route path="/stocks/:id" element={<StockDetailPage />} />
        <Route path="*" element={<Navigate to="/overview" replace />} />
      </Route>
    </Routes>
  );
}
