import { Route, Routes } from "react-router-dom";
import { HelloScreen } from "./screens/HelloScreen";

export function App() {
  return (
    <Routes>
      <Route path="/" element={<HelloScreen />} />
      {/*
        Phase 1+ routes land here:
          /catalog            — F1 service catalog
          /catalog/:serviceId — F1 detail
          /masters            — F2
          /book               — booking flow
          /visits             — F3 my visits
          /profile            — F4 profile
          /feedback/:recordId — rating form (Phase 4)
      */}
      <Route path="*" element={<HelloScreen />} />
    </Routes>
  );
}
