"""Managed storage counts retained originals without revealing hidden collections."""

import io
import pytest
from sqlalchemy import insert, update
from tests.test_release_backend import release, bootstrap, collection, upload, get
from tests.test_release_billing import billing_app, PLANS
from release_backend import models as m
from release_billing import models as b, BillingService, BillingError


def seed(app, *, status="active", until=1000, plan="fixture"):
    app.config["BILLING_PLANS"] = PLANS
    with app.extensions["release_store"].transaction() as db:
        db.execute(
            insert(m.collections),
            [
                {
                    "id": "visible",
                    "household_id": "a",
                    "name": "Shared",
                    "settings": {},
                },
                {
                    "id": "hidden",
                    "household_id": "a",
                    "name": "Private",
                    "settings": {},
                },
            ],
        )
        db.execute(
            insert(m.collection_grants).values(
                id="member-visible",
                household_id="a",
                membership_id="membership-member",
                collection_id="visible",
                role="contributor",
            )
        )
        for aid, cid, size, deleted in [
            ("active", "visible", 100, False),
            ("hidden-active", "hidden", 200, False),
            ("retained", "hidden", 300, True),
        ]:
            db.execute(
                insert(m.assets).values(
                    id=aid,
                    household_id="a",
                    collection_id=cid,
                    filename=aid + ".jpg",
                    storage_key="a/" + aid,
                    thumbnail_key="a/" + aid + "/thumb",
                    byte_size=size,
                    mime_type="image/jpeg",
                    metadata={},
                    deleted=deleted,
                    created_at=1,
                )
            )
        db.execute(
            insert(m.frames).values(
                id="frame-a",
                household_id="a",
                name="Frame",
                settings={},
                state={},
                schedules=[],
            )
        )
        db.execute(
            insert(b.subscriptions).values(
                household_id="a",
                provider="fixture",
                subscription_id="synthetic-subscription",
                plan_id=plan,
                status=status,
                valid_until=until,
                revision=1,
                updated_at=1,
            )
        )


def request(app, identity="owner"):
    return app.test_client().get(
        "/api/v1/households/a/storage",
        headers={"Authorization": "Bearer " + identity + "-token"},
    )


def test_owner_and_admin_storage_usage_include_hidden_retained_originals_only(
    billing_app,
):
    seed(billing_app)
    response = request(billing_app)
    assert response.status_code == 200, response.json
    data = response.json
    assert data["accounting"] == "original_bytes"
    assert data["usage"] == {
        "active_bytes": 300,
        "retained_deleted_bytes": 300,
        "total_bytes": 600,
        "active_assets": 2,
        "frames": 1,
    }
    assert data["limits"] == {"storage_bytes": 1000, "asset_limit": 5, "frame_limit": 2}
    assert data["remaining"] == {"storage_bytes": 400, "assets": 3, "frames": 1}
    assert data["entitlement"]["active"] is True
    assert "provider" not in data["entitlement"]
    assert request(billing_app, "member").status_code == 403
    with billing_app.extensions["release_store"].transaction() as db:
        db.execute(
            update(m.memberships)
            .where(m.memberships.c.id == "membership-member")
            .values(role="admin")
        )
    assert request(billing_app, "member").json["usage"] == data["usage"]


def test_expired_plan_keeps_configured_limits_visible_and_remaining_never_negative(
    billing_app,
):
    seed(billing_app, status="expired", until=99)
    billing_app.config["BILLING_PLANS"] = [
        dict(PLANS[0], storage_bytes=400, asset_limit=1, frame_limit=1)
    ]
    response = request(billing_app)
    assert response.status_code == 200
    assert response.json["entitlement"]["active"] is False
    assert response.json["limits"]["storage_bytes"] == 400
    assert response.json["remaining"] == {"storage_bytes": 0, "assets": 0, "frames": 0}


def test_self_hosted_usage_does_not_invent_paid_plan_limits(release):
    owner = bootstrap(release)
    hid = owner["households"][0]["id"]
    cid = collection(release, owner, hid)
    uploaded = upload(release, owner, hid, cid).json["asset"]
    response = get(release, f"/households/{hid}/storage", owner)
    assert response.status_code == 200, response.json
    assert response.json["usage"]["total_bytes"] == uploaded["byte_size"]
    assert response.json["usage"]["active_bytes"] == uploaded["byte_size"]
    assert response.json["usage"]["retained_deleted_bytes"] == 0
    assert response.json["limits"] is None and response.json["remaining"] is None
    assert response.json["entitlement"] == {
        "active": True,
        "status": "self_hosted",
        "plan_id": None,
        "valid_until": None,
    }


@pytest.mark.parametrize(
    "increment,expected",
    [
        ({"bytes_to_add": 401}, "storage_quota_exceeded"),
        ({"assets_to_add": 4}, "asset_quota_exceeded"),
        ({"frames_to_add": 2}, "frame_quota_exceeded"),
    ],
)
def test_quota_errors_identify_limit_without_disclosing_household_totals(
    billing_app, increment, expected
):
    seed(billing_app)
    service = BillingService(PLANS, clock=lambda: 100)
    with billing_app.extensions["release_store"].transaction() as db:
        with pytest.raises(BillingError) as error:
            service.check_quota(db, "a", authorize=lambda: None, **increment)
        assert error.value.code == expected and error.value.status == 409
        assert "600" not in str(error.value)
        assert "administrator" in str(error.value)


def test_http_member_upload_reports_storage_limit_without_hidden_counts(billing_app):
    from PIL import Image

    seed(billing_app)
    billing_app.config["BILLING_PLANS"] = [dict(PLANS[0], storage_bytes=600)]
    output = io.BytesIO()
    Image.new("RGB", (10, 10), "red").save(output, format="PNG")
    response = billing_app.test_client().post(
        "/api/v1/households/a/collections/visible/assets",
        headers={"Authorization": "Bearer member-token"},
        data={"file": (io.BytesIO(output.getvalue()), "New.png")},
    )
    assert response.status_code == 409, response.json
    assert response.json["error"]["code"] == "storage_quota_exceeded"
    assert set(response.json) == {"error"} and set(response.json["error"]) == {
        "code",
        "message",
    }
    assert "600" not in response.get_data(as_text=True)
    assert "retained deleted" in response.json["error"]["message"]


def test_inactive_subscription_and_unconfigured_plan_are_distinct_from_quota(
    billing_app,
):
    seed(billing_app, status="expired", until=99)
    with billing_app.extensions["release_store"].transaction() as db:
        service = BillingService(PLANS, clock=lambda: 100)
        with pytest.raises(BillingError) as error:
            service.check_quota(db, "a", authorize=lambda: None, bytes_to_add=10000)
        assert (error.value.code, error.value.status) == ("subscription_required", 402)
        db.execute(
            update(b.subscriptions)
            .where(b.subscriptions.c.household_id == "a")
            .values(plan_id="unknown")
        )
        with pytest.raises(BillingError) as error:
            service.check_quota(db, "a", authorize=lambda: None, bytes_to_add=1)
        assert (error.value.code, error.value.status) == ("billing_unavailable", 503)


def test_usage_authorization_precedes_invalid_billing_configuration(billing_app):
    billing_app.config["BILLING_PLANS"] = [{"private-invalid-plan": "do not disclose"}]
    assert request(billing_app, "member").status_code == 403
    response = request(billing_app)
    assert (
        response.status_code == 503
        and response.json["error"]["code"] == "billing_unavailable"
    )
    assert "private-invalid-plan" not in response.get_data(as_text=True)
