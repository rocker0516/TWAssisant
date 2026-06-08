import { Navigate, Route, Routes } from "react-router-dom";
import Layout from "./components/Layout";
import HoldingsPage from "./pages/HoldingsPage";
import RecommendationsPage from "./pages/RecommendationsPage";
import SectorDetailPage from "./pages/SectorDetailPage";
import SectorsPage from "./pages/SectorsPage";
import StockDetailPage from "./pages/StockDetailPage";

export default function App() {
  return (
    <Routes>
      <Route element={<Layout />}>
        <Route index element={<Navigate to="/recommendations" replace />} />
        <Route path="/recommendations" element={<RecommendationsPage />} />
        <Route path="/holdings" element={<HoldingsPage />} />
        <Route path="/sectors" element={<SectorsPage />} />
        <Route path="/sectors/:id" element={<SectorDetailPage />} />
        <Route path="/stocks/:id" element={<StockDetailPage />} />
        <Route path="*" element={<Navigate to="/recommendations" replace />} />
      </Route>
    </Routes>
  );
}
