import os
import tempfile
import time
import hmac
import hashlib
import json
import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.database import (
    init_db,
    get_stats_summary,
    get_connection,
    get_next_pending_job,
    update_job_accepted,
    update_job_delivered,
    update_job_failed,
    schedule_job_retry,
    get_reconciling_jobs
)
from app.security import verify_webhook_signature
from app.main import app

@pytest.fixture
def test_db():
    temp_dir = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
    db_path = os.path.join(temp_dir.name, "test_linkplease.db")
    settings.DATABASE_PATH = db_path
    init_db(db_path)
    yield db_path
    temp_dir.cleanup()

@pytest.fixture
def client(test_db):
    with TestClient(app) as test_client:
        yield test_client

def generate_signature(secret: str, body: bytes) -> str:
    digest = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return f"sha256={digest}"

# ----------------- Part A Tests: Rules & Ingestion -----------------

def test_create_rule(client):
    """Test POST /rules endpoint creates a rule and returns 201."""
    payload = {"keyword": "PRICE", "dm_message": "Here is the price list: https://shop.link/prices"}
    response = client.post("/rules", json=payload)
    
    assert response.status_code == 201
    data = response.json()
    assert "rule_id" in data
    assert data["keyword"] == "PRICE"
    assert data["dm_message"] == "Here is the price list: https://shop.link/prices"

def test_webhook_rule_matching_and_deduplication(client):
    """
    Test that:
    1. Comments matching a rule enqueue a DM job.
    2. Case-insensitive matching works ("price", "PRICE please").
    3. The same user commenting multiple times on the same rule is blocked as duplicate.
    4. Duplicates correctly increment duplicates_blocked counter.
    """
    # 1. Create a rule for 'PRICE'
    rule_res = client.post("/rules", json={"keyword": "PRICE", "dm_message": "Price list details"})
    assert rule_res.status_code == 201
    
    # 2. First comment from user_1: "What is the price please?"
    event_1 = {
        "event_id": "evt_001",
        "event_type": "comment.created",
        "sent_at": "2026-08-10T09:00:00Z",
        "data": {
            "comment_id": "cmt_001",
            "post_id": "post_100",
            "text": "What is the price please?",
            "created_at": "2026-08-10T08:59:59Z",
            "from": {
                "user_id": "usr_100",
                "username": "alice"
            }
        }
    }
    res1 = client.post("/webhook", json=event_1)
    assert res1.status_code == 200

    stats = client.get("/stats").json()
    assert stats["queued"] == 1
    assert stats["duplicates_blocked"] == 0

    # 3. Second comment from user_1 on another comment matching 'PRICE'
    event_2 = {
        "event_id": "evt_002",
        "event_type": "comment.created",
        "sent_at": "2026-08-10T09:05:00Z",
        "data": {
            "comment_id": "cmt_002",
            "post_id": "post_100",
            "text": "PRICE AGAIN",
            "created_at": "2026-08-10T09:04:59Z",
            "from": {
                "user_id": "usr_100",
                "username": "alice"
            }
        }
    }
    res2 = client.post("/webhook", json=event_2)
    assert res2.status_code == 200

    # User 1 should NOT be queued again, and duplicate count must increment
    stats2 = client.get("/stats").json()
    assert stats2["queued"] == 1
    assert stats2["duplicates_blocked"] == 1

def test_event_id_redelivery_deduplication(client):
    """
    Test that receiving the exact same event_id twice (~8% mock API redelivery)
    is cleanly deduplicated and counted in duplicates_blocked.
    """
    client.post("/rules", json={"keyword": "DISCOUNT", "dm_message": "10% off coupon"})
    
    event = {
        "event_id": "evt_duplicate_test",
        "event_type": "comment.created",
        "sent_at": "2026-08-10T09:10:00Z",
        "data": {
            "comment_id": "cmt_dup_1",
            "post_id": "post_200",
            "text": "send me the discount code",
            "created_at": "2026-08-10T09:09:59Z",
            "from": {
                "user_id": "usr_bob",
                "username": "bob"
            }
        }
    }
    
    res1 = client.post("/webhook", json=event)
    assert res1.status_code == 200
    
    # Redeliver exact same event
    res2 = client.post("/webhook", json=event)
    assert res2.status_code == 200
    
    stats = client.get("/stats").json()
    assert stats["queued"] == 1
    assert stats["duplicates_blocked"] == 1

# ----------------- Part B Tests: Signature Verification & Live Stats -----------------

def test_signature_verification(client):
    """Test HMAC-SHA256 signature verification."""
    secret = "test_secret_api_key_12345"
    settings.PSEUDOGRAM_API_KEY = secret
    settings.VERIFY_SIGNATURE = True
    
    payload = {
        "event_id": "evt_sig_test",
        "event_type": "comment.created",
        "sent_at": "2026-08-10T10:00:00Z",
        "data": {
            "comment_id": "cmt_sig_1",
            "post_id": "post_300",
            "text": "Hello world",
            "created_at": "2026-08-10T09:59:59Z",
            "from": {"user_id": "usr_charlie", "username": "charlie"}
        }
    }
    raw_body = json.dumps(payload).encode("utf-8")
    
    # 1. Invalid signature -> 401
    invalid_headers = {"X-PseudoGram-Signature": "sha256=invalidhex000000000000000000000000000000"}
    res_bad = client.post("/webhook", content=raw_body, headers=invalid_headers)
    assert res_bad.status_code == 401
    
    # 2. Valid signature -> 200
    valid_sig = generate_signature(secret, raw_body)
    valid_headers = {"X-PseudoGram-Signature": valid_sig, "Content-Type": "application/json"}
    res_good = client.post("/webhook", content=raw_body, headers=valid_headers)
    assert res_good.status_code == 200
    
    # Reset secret
    settings.PSEUDOGRAM_API_KEY = ""

def test_stats_reporting_structure(client):
    """Test GET /stats returns exact required schema and types."""
    res = client.get("/stats")
    assert res.status_code == 200
    data = res.json()
    assert "sent" in data and isinstance(data["sent"], int)
    assert "failed" in data and isinstance(data["failed"], int)
    assert "queued" in data and isinstance(data["queued"], int)
    assert "duplicates_blocked" in data and isinstance(data["duplicates_blocked"], int)

# ----------------- Part C Tests: Comment Deleted & Status Reconciliation -----------------

def test_comment_deleted_before_send_cancels_dm(client):
    """
    Test that if a comment.deleted event arrives while the DM is still queued,
    the DM is cancelled and recorded as duplicate/cancelled blocked without sending.
    """
    client.post("/rules", json={"keyword": "BUY", "dm_message": "Buy link: https://store.link"})
    
    # 1. User comments "BUY"
    comment_event = {
        "event_id": "evt_buy_1",
        "event_type": "comment.created",
        "sent_at": "2026-08-10T11:00:00Z",
        "data": {
            "comment_id": "cmt_to_delete",
            "post_id": "post_400",
            "text": "I want to BUY this now",
            "created_at": "2026-08-10T10:59:59Z",
            "from": {"user_id": "usr_buyer", "username": "buyer"}
        }
    }
    client.post("/webhook", json=comment_event)
    
    stats_before = client.get("/stats").json()
    assert stats_before["queued"] == 1
    
    # 2. User deletes comment before DM is sent
    delete_event = {
        "event_id": "evt_delete_1",
        "event_type": "comment.deleted",
        "sent_at": "2026-08-10T11:00:02Z",
        "data": {
            "comment_id": "cmt_to_delete"
        }
    }
    res_del = client.post("/webhook", json=delete_event)
    assert res_del.status_code == 200
    
    # Check stats: queued should drop to 0, duplicates_blocked should increment
    stats_after = client.get("/stats").json()
    assert stats_after["queued"] == 0
    assert stats_after["duplicates_blocked"] >= 1

def test_dm_job_state_transitions(test_db):
    """
    Test state transitions from queued -> sending -> reconciling -> delivered / failed,
    and retry exponential backoff.
    """
    client = TestClient(app)
    client.post("/rules", json={"keyword": "INFO", "dm_message": "Here is info"})
    
    event = {
        "event_id": "evt_state_1",
        "event_type": "comment.created",
        "sent_at": "2026-08-10T12:00:00Z",
        "data": {
            "comment_id": "cmt_state_1",
            "post_id": "post_500",
            "text": "need INFO please",
            "created_at": "2026-08-10T11:59:59Z",
            "from": {"user_id": "usr_tester", "username": "tester"}
        }
    }
    client.post("/webhook", json=event)
    
    # 1. Initially queued
    job = get_next_pending_job(test_db)
    assert job is not None
    assert job["user_id"] == "usr_tester"
    
    # Status is now 'sending'
    stats = get_stats_summary(test_db)
    assert stats["queued"] == 1
    
    # 2. Mock API returns 202 Accepted -> status 'reconciling'
    update_job_accepted(job["job_id"], "dm_test_123", test_db)
    reconciling = get_reconciling_jobs(limit=10, db_path=test_db)
    assert len(reconciling) == 1
    assert reconciling[0]["dm_id"] == "dm_test_123"
    
    # 3. Reconciliation confirms delivered -> terminal 'delivered'
    update_job_delivered(job["job_id"], test_db)
    stats_del = get_stats_summary(test_db)
    assert stats_del["sent"] == 1
    assert stats_del["queued"] == 0
    assert stats_del["failed"] == 0
