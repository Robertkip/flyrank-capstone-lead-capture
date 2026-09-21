"""Seed demo data: two tenants, three widgets, a handful of submissions.

Two tenants exist on purpose — proving tenant isolation needs a second party
whose data must stay invisible.

    docker compose exec api python scripts/seed.py
"""

import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select  # noqa: E402

from app.db import SessionLocal  # noqa: E402
from app.models import Tenant  # noqa: E402
from app.schemas import SubmissionCreate, WidgetCreate, WidgetField  # noqa: E402
from app.security import hash_password  # noqa: E402
from app.services import submission_service, widget_service  # noqa: E402

DEMO_PASSWORD = "demo-password-123"

TENANTS = [
    {"email": "acme@example.com", "name": "Acme Corp"},
    {"email": "globex@example.com", "name": "Globex Inc"},
]


def upsert_tenant(db, email: str, name: str) -> Tenant:
    tenant = db.execute(select(Tenant).where(Tenant.email == email)).scalar_one_or_none()
    if tenant:
        return tenant
    tenant = Tenant(email=email, name=name, password_hash=hash_password(DEMO_PASSWORD))
    db.add(tenant)
    db.commit()
    db.refresh(tenant)
    return tenant


async def main() -> None:
    db = SessionLocal()
    try:
        acme = upsert_tenant(db, **TENANTS[0])
        globex = upsert_tenant(db, **TENANTS[1])

        if widget_service.count_widgets(db, acme.id) == 0:
            newsletter = widget_service.create_widget(
                db,
                acme.id,
                WidgetCreate(
                    name="Newsletter signup",
                    type="signup_form",
                    title="Join the Acme newsletter",
                    description="Product news once a month. No spam, unsubscribe anytime.",
                    button_text="Subscribe",
                    success_message="You're on the list — check your inbox.",
                    fields=[
                        WidgetField(name="email", label="Email", type="email", required=True,
                                    placeholder="you@company.com"),
                        WidgetField(name="first_name", label="First name", type="text"),
                    ],
                    display={"accent": "#2563eb", "theme": "light"},
                    notify_email="leads@acme.example.com",
                ),
            )
            contact = widget_service.create_widget(
                db,
                acme.id,
                WidgetCreate(
                    name="Contact sales",
                    type="cta",
                    title="Talk to sales",
                    description="Tell us what you need and we'll reply within one business day.",
                    button_text="Request a call",
                    fields=[
                        WidgetField(name="email", label="Work email", type="email", required=True),
                        WidgetField(name="company", label="Company", type="text", required=True),
                        WidgetField(name="message", label="What do you need?", type="textarea"),
                    ],
                    display={"accent": "#0f766e"},
                    notify_email="sales@acme.example.com",
                ),
            )
        else:
            widgets = widget_service.list_widgets(db, acme.id)
            newsletter, contact = widgets[-1], widgets[0]

        if widget_service.count_widgets(db, globex.id) == 0:
            widget_service.create_widget(
                db,
                globex.id,
                WidgetCreate(
                    name="Globex beta waitlist",
                    type="popover",
                    title="Get early access",
                    fields=[WidgetField(name="email", label="Email", type="email", required=True)],
                    display={"accent": "#7c3aed"},
                ),
            )

        demo_leads = [
            {"email": "dana@northwind.example", "first_name": "Dana"},
            {"email": "sam@initech.example", "first_name": "Sam"},
            {"email": "rae@umbrella.example", "first_name": "Rae"},
        ]
        for lead in demo_leads:
            await submission_service.create_submission(
                db,
                newsletter,
                SubmissionCreate(widget_id=newsletter.public_id, data=lead),
                ip="8.8.8.8",
                user_agent="seed-script/1.0",
                referer=None,
                origin="http://localhost:5500",
            )

        print("\nSeed complete.\n")
        print(f"  Tenant A : {acme.email} / {DEMO_PASSWORD}")
        print(f"  Tenant B : {globex.email} / {DEMO_PASSWORD}")
        print("\n  Acme widgets:")
        for widget in widget_service.list_widgets(db, acme.id):
            print(f"    {widget.name:22} public_id={widget.public_id}")
            print(f"      {widget_service.embed_snippet(widget.public_id)}")
        site_port = os.getenv("TESTSITE_PORT", "5500")
        api_port = os.getenv("API_PORT", "8000")
        print(f"\n  Test page: http://localhost:{site_port}/index.html")
        print(f"  API docs : http://localhost:{api_port}/docs\n")
    finally:
        db.close()


if __name__ == "__main__":
    asyncio.run(main())
