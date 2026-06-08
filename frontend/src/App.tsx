import { Navigate, Route, Routes } from "react-router-dom";
import Layout from "./components/Layout";
import RecommendationsPage from "./pages/RecommendationsPage";
import StockDetailPage from "./pages/StockDetailPage";

export default function App() {
  return (
    <Routes>
      <Route element={<Layout />}>
        <Route index element={<Navigate to="/recommendations" replace />} />
        <Route path="/recommendations" element={<RecommendationsPage />} />
        <Route path="/stocks/:id" element={<StockDetailPage />} />
        <Route path="*" element={<Navigate to="/recommendations" replace />} />
      </Route>
    </Routes>
  );
}
