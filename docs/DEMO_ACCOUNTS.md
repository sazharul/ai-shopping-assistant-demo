# Demo Accounts

## Admin panel

| Field | Value |
|-------|-------|
| Email | `admin@stylehub.demo` |
| Password | `DemoAdmin123!` |

Admin API routes are under `/api/v1/admin/*`. Use the admin API key from `.env.example` (`demo-admin-key`) for authenticated requests.

## Commerce API stubs (DEMO_MODE)

When `DEMO_MODE=true`, these fictional order IDs return canned responses:

| Order ID | Status |
|----------|--------|
| `SH-10492` | Dispatched |
| `SH-20311` | Processing |
| `SH-99001` | Delivered |

Example prompts:

- "What's the status of order SH-10492?"
- "I want to return order SH-99001"
- "Validate discount code WELCOME10"

## Chat widget

No login required for the customer-facing chat widget. Open http://localhost:5173 after starting the stack.
