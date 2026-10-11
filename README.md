# VisitorPlan

VisitorPlan is a working simulated Alexa+ planning experience for the Amazon App Developer Challenge. Its assistant checks date-specific capacity, group size, step-free access, travel time and a total group budget before proposing an itinerary. Explicit confirmation records a persistent demo reservation; cancellation releases its places.

Alexa+ is simulated. This application does not connect to Amazon Alexa or real attraction providers. Its venues, prices and reservation inventory are fictional. No payment is taken.

## Run

Requires Python 3.10+ and jsonschema 4.23.0.

    python -m pip install jsonschema==4.23.0
    python -B app.py

Open http://127.0.0.1:8877. The MCP endpoint is http://127.0.0.1:8877/mcp.

Optional environment configuration:

- GEMINI_API_KEY enables live interpretation of free-text planning requests. Without it, structured preferences still drive the same live MCP planning workflow.
- GEMINI_MODEL defaults to gemini-3.1-flash-lite.
- HOST and PORT set the bind address and port.
- VISITORPLAN_DB sets the SQLite storage path.
- APP_ORIGINS lists comma-separated allowed public origins when deploying.

The Dockerfile provides the same application with a persistent /data volume. Configure a public HTTPS origin before hosting for reviewers.

## Actual MCP workflow

The application opens a real HTTP MCP session, negotiates 2025-11-25, sends notifications/initialized, discovers tools, and calls them over the endpoint. JSON responses implement the documented Streamable HTTP option. The optional listening GET stream is not provided and returns 405. Session termination uses DELETE.

The service exposes:

- list_attractions: date-specific capacity and accessibility.
- plan_visit: one or two activities with nonoverlapping times, travel allowance and total group-budget validation.
- confirm_plan: explicit confirmation, a fresh capacity check and atomic allocations; repeat confirmation returns the existing reservation.
- list_reservations: the current visitor session’s records.
- cancel_reservation: explicit cancellation and capacity release; repeat cancellation is idempotent.

Each tool has an input schema and appropriate read-only, mutation and idempotency annotations. Tool results include readable content and structured content. The protocol implementation is limited to its declared tool capability; it does not claim optional MCP task, sampling, resource or elicitation support.

## Demonstrate

1. Plan an art-and-nature day for two people with a €60 total budget.
2. Inspect the date, activity times, available places, travel allowance and group total.
3. Confirm the demo itinerary and reopen the saved reservation.
4. Request a larger group to observe date/slot capacity constraints.
5. Cancel the reservation and observe its places becoming available again.

