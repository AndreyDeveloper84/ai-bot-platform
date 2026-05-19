import { Route, Routes } from "react-router-dom";
import { BookingConfirmScreen } from "./screens/BookingConfirmScreen";
import { BookingSuccessScreen } from "./screens/BookingSuccessScreen";
import { BookingWhenScreen } from "./screens/BookingWhenScreen";
import { CatalogScreen } from "./screens/CatalogScreen";
import { HelloScreen } from "./screens/HelloScreen";
import { MasterPickerScreen } from "./screens/MasterPickerScreen";
import { FeedbackScreen } from "./screens/FeedbackScreen";
import { MyVisitsScreen } from "./screens/MyVisitsScreen";
import { ProfileScreen } from "./screens/ProfileScreen";
import { ServiceDetailScreen } from "./screens/ServiceDetailScreen";

export function App() {
  return (
    <Routes>
      <Route path="/" element={<HelloScreen />} />
      <Route path="/catalog" element={<CatalogScreen />} />
      <Route path="/catalog/:serviceId" element={<ServiceDetailScreen />} />
      <Route path="/book/master" element={<MasterPickerScreen />} />
      <Route path="/book/when" element={<BookingWhenScreen />} />
      <Route path="/book/confirm" element={<BookingConfirmScreen />} />
      <Route path="/book/success/:bookingId" element={<BookingSuccessScreen />} />
      <Route path="/visits" element={<MyVisitsScreen />} />
      <Route path="/me" element={<ProfileScreen />} />
      <Route path="/feedback/:bookingId" element={<FeedbackScreen />} />
      <Route path="*" element={<HelloScreen />} />
    </Routes>
  );
}
