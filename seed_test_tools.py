"""
Seed the 15 webhook tools used by the 5 test prompts, pointing at the Django app
in /Users/snakescript/Desktop/Voice_Agents (app: api).

For each tool it mirrors the /api/v2/functions create flow exactly:
  1. registers the tool in ElevenLabs (ElevenLabsAgent.create_tool)
  2. inserts a FunctionModel + FunctionApiConfig row for the target user

Idempotent: a tool whose name already exists for the user is skipped.

Usage (email as the first argument):
  WEBHOOK_BASE_URL=https://xxxx.ngrok-free.app python seed_test_tools.py you@example.com

Config:
  positional arg 1  email of the user to attach the tools to (recommended)
  WEBHOOK_BASE_URL  base url of the Django app (default http://localhost:8000)
                    ⚠️ For ElevenLabs to CALL the tool during a live call this
                    must be PUBLICLY reachable (ngrok / deployed host) —
                    localhost only works for local curl tests.
  SEED_USER_EMAIL   alternative to the positional email arg
  SEED_USER_ID      (optional) user id; overrides the email
"""
import os
import sys

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app_v2.core.config import VoiceSettings
from app_v2.databases.models import (
    FunctionModel,
    FunctionApiConfig,
    UnifiedAuthModel,
)
from app_v2.schemas.enum_types import RequestMethodEnum
from app_v2.schemas.function_schema import (
    ApiSchema,
    HttpMethod,
    ContentType,
    RequestBodySchema,
    BodyField,
    QueryParamsSchema,
    PrimitiveField,
)
from app_v2.utils.crypto_utils import encrypt_data
from app_v2.utils.elevenlabs.agent_utils import ElevenLabsAgent

BASE_URL = "https://boomless-estelle-nonoppressively.ngrok-free.dev"
# Target user: first positional CLI arg wins, else env, else default.
SEED_USER_EMAIL = "priyanshi+22@snakescript.com"
SEED_USER_ID = "22"

if len(sys.argv) > 1 and sys.argv[1].strip():
    SEED_USER_EMAIL = sys.argv[1].strip()
    SEED_USER_ID = None  # an explicit email arg overrides any env id

engine = create_engine(VoiceSettings.DB_URL)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


def _s(desc):
    return {"type": "string", "description": desc}


def _i(desc):
    return {"type": "integer", "description": desc}


def _body(properties: dict, required: list) -> RequestBodySchema:
    return RequestBodySchema(
        type="object",
        properties={k: BodyField(**v) for k, v in properties.items()},
        required=required,
    )


def _post(url, properties, required):
    return ApiSchema(
        url=url,
        method=HttpMethod.POST,
        content_type=ContentType.JSON,
        request_body_schema=_body(properties, required),
    )


def build_tools() -> list:
    """Returns [(name, description, ApiSchema), ...] for the 15 prompt tools."""
    api = f"{BASE_URL}/webhooks_tools/api"
    return [
        # ---- Prompt 1: weather bot (GET with a query param) ----
        (
            "get_weather",
            "Get the current weather for a given location. Returns temperature and conditions.",
            ApiSchema(
                url=f"{api}/get_weather",
                method=HttpMethod.GET,
                query_params_schema=QueryParamsSchema(
                    properties={"location": PrimitiveField(type="string", description="City or place to get the weather for.")},
                    required=["location"],
                ),
            ),
        ),

        # ---- Prompt 2: Grand Horizon hotel concierge ----
        (
            "get_guest_profile",
            "Look up a hotel guest's Horizon Circle membership tier and stay history by name and phone.",
            _post(f"{api}/get_guest_profile",
                  {"name": _s("Full name of the guest."), "phone": _s("Guest's phone number.")},
                  ["name", "phone"]),
        ),
        (
            "check_room_availability",
            "Check available rooms at a property for the given dates and room category.",
            _post(f"{api}/check_room_availability",
                  {"property": _s("Property/hotel name."),
                   "check_in": _s("Check-in date (YYYY-MM-DD)."),
                   "check_out": _s("Check-out date (YYYY-MM-DD)."),
                   "room_category": _s("Desired room category.")},
                  ["property", "check_in", "check_out", "room_category"]),
        ),
        (
            "create_reservation",
            "Create a hotel reservation and return a confirmation number.",
            _post(f"{api}/create_reservation",
                  {"guest_id": _s("Guest id from get_guest_profile."),
                   "property_id": _s("Property id."),
                   "room_id": _s("Room id from check_room_availability."),
                   "check_in": _s("Check-in date (YYYY-MM-DD)."),
                   "check_out": _s("Check-out date (YYYY-MM-DD).")},
                  ["guest_id", "property_id", "room_id", "check_in", "check_out"]),
        ),
        (
            "book_spa_experience",
            "Offer and book the earliest available spa treatments for a guest.",
            _post(f"{api}/book_spa_experience",
                  {"guest_id": _s("Guest id."),
                   "preferred_date": _s("Optional preferred date (YYYY-MM-DD).")},
                  ["guest_id"]),
        ),
        (
            "send_confirmation_email",
            "Send a written reservation confirmation email to the guest on file.",
            _post(f"{api}/send_confirmation_email",
                  {"guest_id": _s("Guest id."), "reservation_id": _s("Reservation id.")},
                  ["guest_id", "reservation_id"]),
        ),

        # ---- Prompt 3: Riverside clinic ----
        (
            "lookup_patient",
            "Look up whether a patient already exists by full name and date of birth.",
            _post(f"{api}/lookup_patient",
                  {"name": _s("Patient's full name."), "date_of_birth": _s("Date of birth (YYYY-MM-DD).")},
                  ["name", "date_of_birth"]),
        ),
        (
            "register_patient",
            "Register a new patient with name, date of birth and phone number.",
            _post(f"{api}/register_patient",
                  {"name": _s("Patient's full name."),
                   "date_of_birth": _s("Date of birth (YYYY-MM-DD)."),
                   "phone": _s("Patient's phone number.")},
                  ["name", "date_of_birth", "phone"]),
        ),
        (
            "get_available_slots",
            "Get open appointment slots for a specialty on a preferred date.",
            _post(f"{api}/get_available_slots",
                  {"specialty": _s("Requested specialty."), "preferred_date": _s("Preferred date (YYYY-MM-DD).")},
                  ["specialty", "preferred_date"]),
        ),
        (
            "book_appointment",
            "Book a clinic appointment for a patient in a chosen slot and return a confirmation number.",
            _post(f"{api}/book_appointment",
                  {"patient_id": _s("Patient id."),
                   "slot_id": _s("Slot id from get_available_slots."),
                   "consultation_type": _s("in-person or teleconsultation.")},
                  ["patient_id", "slot_id", "consultation_type"]),
        ),

        # ---- Prompt 4: Atlas travel ----
        (
            "search_flights",
            "Search flights for an origin, destination and date.",
            _post(f"{api}/search_flights",
                  {"origin": _s("Origin city or airport."),
                   "destination": _s("Destination city or airport."),
                   "date": _s("Travel date (YYYY-MM-DD).")},
                  ["origin", "destination", "date"]),
        ),
        (
            "check_hotel_availability",
            "Check hotel availability in a city for the given dates.",
            _post(f"{api}/check_hotel_availability",
                  {"city": _s("City to stay in."),
                   "check_in": _s("Check-in date (YYYY-MM-DD)."),
                   "check_out": _s("Check-out date (YYYY-MM-DD).")},
                  ["city", "check_in", "check_out"]),
        ),
        (
            "create_booking",
            "Finalize a trip booking for a chosen flight and hotel; returns a booking reference.",
            _post(f"{api}/create_booking",
                  {"flight_id": _s("Chosen flight id."), "hotel_id": _s("Chosen hotel id.")},
                  ["flight_id", "hotel_id"]),
        ),

        # ---- Prompt 5: Nova support (GET, no params) ----
        (
            "get_user_details",
            "Look up the details of the calling user (id, name, email, plan). Takes no parameters.",
            ApiSchema(url=f"{api}/get_user_details", method=HttpMethod.GET),
        ),
        (
            "get_open_tickets",
            "Fetch the open support tickets for a given user id.",
            _post(f"{api}/get_open_tickets",
                  {"user_id": _s("The user id to fetch open tickets for.")},
                  ["user_id"]),
        ),

        # ---- Original enquiry/appointment webhooks ----
        (
            "get_services",
            "List the services the agency offers. Takes no parameters.",
            ApiSchema(url=f"{api}/get_services", method=HttpMethod.GET),
        ),
        (
            "get_existing_enquiries",
            "Look up an existing user and all their past enquiries and appointments by email.",
            _post(f"{api}/get_existing_enquiries",
                  {"email": _s("The caller's email address.")},
                  ["email"]),
        ),
        (
            "create_user_if_not_exists",
            "Register a new user (name, email, phone) if they do not already exist.",
            _post(f"{api}/create_user_if_not_exists",
                  {"name": _s("Full name of the user."),
                   "email": _s("Email address of the user."),
                   "phone": _s("Phone number of the user.")},
                  ["name", "email", "phone"]),
        ),
        (
            "create_enquiry",
            "Create a new project enquiry for a user (creates the user if needed).",
            _post(f"{api}/create_enquiry",
                  {"name": _s("Full name of the user."),
                   "email": _s("Email address of the user."),
                   "phone": _s("Phone number of the user."),
                   "service": _s("The service the user is enquiring about."),
                   "chatbot_type": _s("Optional: type of chatbot."),
                   "chatbot_tech": _s("Optional: chatbot technology."),
                   "preferred_tech": _s("Optional: preferred technology stack."),
                   "project_description": _s("Optional: description of the project."),
                   "other_requirements": _s("Optional: any other requirements.")},
                  ["name", "email", "phone", "service"]),
        ),
        (
            "create_appointment",
            "Book an appointment for an existing enquiry at a given date and time.",
            _post(f"{api}/create_appointment",
                  {"enquiry_id": _i("The id of the enquiry to attach the appointment to."),
                   "iso_datetime_str": _s("Appointment time in 'YYYY-MM-DD HH:MM' format."),
                   "notes": _s("Optional notes for the appointment.")},
                  ["enquiry_id", "iso_datetime_str"]),
        ),
    ]


def _resolve_user(session) -> UnifiedAuthModel:
    if SEED_USER_ID:
        user = session.query(UnifiedAuthModel).filter(UnifiedAuthModel.id == int(SEED_USER_ID)).first()
        if not user:
            sys.exit(f"❌ No user with id {SEED_USER_ID}")
        return user
    user = session.query(UnifiedAuthModel).filter(UnifiedAuthModel.email == SEED_USER_EMAIL).first()
    if not user:
        sys.exit(f"❌ No user with email {SEED_USER_EMAIL}. Pass an email arg or set SEED_USER_ID.")
    return user


def seed():
    session = SessionLocal()
    el_client = ElevenLabsAgent()
    created, skipped, failed = 0, 0, 0
    try:
        user = _resolve_user(session)
        print(f"Seeding {len(build_tools())} tools for user #{user.id} ({user.email})")
        print(f"Base URL: {BASE_URL}")
        if BASE_URL.startswith("http://localhost") or BASE_URL.startswith("http://127."):
            print("⚠️  localhost base URL — tools will register but ElevenLabs cannot "
                  "reach them during a live call. Use a public (ngrok/deployed) URL for real tests.")

        for name, description, api_config in build_tools():
            existing = (
                session.query(FunctionModel)
                .filter(FunctionModel.name == name, FunctionModel.user_id == user.id)
                .first()
            )
            if existing:
                print(f"  ↷ skip '{name}' (already exists, id={existing.id})")
                skipped += 1
                continue

            resp = el_client.create_tool(name=name, description=description, api_schema=api_config)
            if not resp.status:
                print(f"  ✗ '{name}' — ElevenLabs create_tool failed: {resp.error_message}")
                failed += 1
                continue
            elevenlabs_tool_id = resp.data.get("id")

            try:
                fn = FunctionModel(
                    name=name,
                    description=description,
                    user_id=user.id,
                    elevenlabs_tool_id=elevenlabs_tool_id,
                )
                session.add(fn)
                session.flush()

                headers = api_config.request_headers or {}
                sensitive = {"authorization", "x-api-key", "api-key", "token"}
                enc_headers = {
                    k: (encrypt_data(v) if k.lower() in sensitive else v)
                    for k, v in headers.items()
                }

                cfg = FunctionApiConfig(
                    function_id=fn.id,
                    endpoint_url=api_config.url,
                    http_method=RequestMethodEnum(api_config.method.value),
                    headers=enc_headers,
                    path_params=(
                        {k: v.model_dump(exclude_none=True) for k, v in api_config.path_params_schema.items()}
                        if api_config.path_params_schema else None
                    ),
                    query_params=(
                        api_config.query_params_schema.model_dump(exclude_none=True)
                        if api_config.query_params_schema else None
                    ),
                    body_schema=(
                        api_config.request_body_schema.model_dump()
                        if api_config.request_body_schema else None
                    ),
                    response_variables=api_config.response_variables,
                    timeout_ms=30000,
                    speak_while_execution=False,
                    speak_after_execution=True,
                )
                session.add(cfg)
                session.commit()
                created += 1
                print(f"  ✓ '{name}' → fn id={fn.id}, EL tool={elevenlabs_tool_id}")
            except Exception as db_err:
                session.rollback()
                try:
                    el_client.delete_tool(elevenlabs_tool_id)
                except Exception:
                    pass
                failed += 1
                print(f"  ✗ '{name}' — DB error: {db_err}")

        print(f"Done. created={created}, skipped={skipped}, failed={failed}")
    finally:
        session.close()


if __name__ == "__main__":
    seed()
