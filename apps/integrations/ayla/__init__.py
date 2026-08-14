"""Ayla backend client.

The nutrition client is the only public surface — handlers compose it with
their own state machines. See ``nutrition_client`` for the API.
"""

from apps.integrations.ayla.url_builder import (
    AylaUrlBuilder,
    AylaUrlError,
)
from apps.integrations.ayla.user_proxy import (
    external_user_id_for,
    parse_external_user_id,
)
from apps.integrations.ayla.booking_client import (
    AylaBookingClient,
    AylaBookingHTTPClient,
    AylaBookingPage,
    AylaBookingRecord,
    AylaMaster,
    AylaRepeatIntent,
    AylaService,
    AylaSlot,
    AylaUserRecord,
    BookingAPIError,
    BookingBadRequestError,
    BookingUnavailableError,
    RepeatIntentUnusableError,
    get_ayla_booking_client,
    reset_ayla_booking_client,
)
from apps.integrations.ayla.nutrition_client import (
    CrossDomainInsight,
    DeficitsResponse,
    FoodLogResponse,
    FoodNotRecognizedError,
    NutritionAPIError,
    NutritionClient,
    NutritionUnavailableError,
    ProfileResponse,
    ScanResponse,
    SummaryResponse,
    WaterEntryResponse,
    WaterTodayResponse,
    get_nutrition_client,
    reset_nutrition_client,
)

__all__ = [
    "AylaBookingClient",
    "AylaBookingHTTPClient",
    "AylaBookingPage",
    "AylaBookingRecord",
    "AylaMaster",
    "AylaRepeatIntent",
    "AylaService",
    "AylaSlot",
    "AylaUrlBuilder",
    "AylaUrlError",
    "AylaUserRecord",
    "BookingAPIError",
    "BookingBadRequestError",
    "BookingUnavailableError",
    "CrossDomainInsight",
    "DeficitsResponse",
    "FoodLogResponse",
    "FoodNotRecognizedError",
    "NutritionAPIError",
    "NutritionClient",
    "NutritionUnavailableError",
    "ProfileResponse",
    "RepeatIntentUnusableError",
    "ScanResponse",
    "SummaryResponse",
    "WaterEntryResponse",
    "WaterTodayResponse",
    "external_user_id_for",
    "get_ayla_booking_client",
    "get_nutrition_client",
    "parse_external_user_id",
    "reset_ayla_booking_client",
    "reset_nutrition_client",
]
