import { Route, Routes } from "react-router-dom";
import { BookingConfirmScreen } from "./screens/BookingConfirmScreen";
import { BookingSuccessScreen } from "./screens/BookingSuccessScreen";
import { BookingWhenScreen } from "./screens/BookingWhenScreen";
import { CatalogScreen } from "./screens/CatalogScreen";
import { FeedbackScreen } from "./screens/FeedbackScreen";
import { HelloScreen } from "./screens/HelloScreen";
import { MasterConversationDetailScreen } from "./screens/MasterConversationDetailScreen";
import { MasterConversationsScreen } from "./screens/MasterConversationsScreen";
import { MasterDashboardScreen } from "./screens/MasterDashboardScreen";
import { MasterOnboardingScreen } from "./screens/MasterOnboardingScreen";
import { MasterPickerScreen } from "./screens/MasterPickerScreen";
import { MasterProfilePlaceholderScreen } from "./screens/MasterProfilePlaceholderScreen";
import { MasterScheduleScreen } from "./screens/MasterScheduleScreen";
import { MyVisitDetailScreen } from "./screens/MyVisitDetailScreen";
import { MyVisitsScreen } from "./screens/MyVisitsScreen";
import { ProfileScreen } from "./screens/ProfileScreen";
import { RescheduleScreen } from "./screens/RescheduleScreen";
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
      {/* Customer cancel + reschedule (customer-cancellation-reschedule-spec). */}
      <Route path="/my-visits" element={<MyVisitsScreen />} />
      <Route path="/my-visits/:bookingId" element={<MyVisitDetailScreen />} />
      <Route path="/my-visits/:bookingId/reschedule" element={<RescheduleScreen />} />
      {/* Phase 3 — profile (F4). */}
      <Route path="/me" element={<ProfileScreen />} />
      {/* Phase 4 — post-visit rating (F5). */}
      <Route path="/feedback/:bookingId" element={<FeedbackScreen />} />
      {/* Master surface — M0 onboarding (PR #212) + M1 dashboard (PR #455)
          + M3 schedule + M5 conversations (this PR).
          M4 profile remains a placeholder pending separate PR.
          Spec: docs/design/handoffs/2026-05-18-master-mobile-handoff.md. */}
      <Route path="/onboarding/master" element={<MasterOnboardingScreen />} />
      <Route path="/master/dashboard" element={<MasterDashboardScreen />} />
      <Route path="/master/schedule" element={<MasterScheduleScreen />} />
      <Route path="/master/conversations" element={<MasterConversationsScreen />} />
      <Route
        path="/master/conversations/:id"
        element={<MasterConversationDetailScreen />}
      />
      <Route path="/master/profile" element={<MasterProfilePlaceholderScreen />} />
      <Route path="*" element={<HelloScreen />} />
    </Routes>
  );
}
