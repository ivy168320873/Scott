from scott_evolution import notifications


def test_outbox_deduplicates_scrubs_secrets_and_delivers(tmp_path):
    db = str(tmp_path / "notify.db")
    first = notifications.enqueue(
        "same-event", "line", {"text": "hello", "token": "do-not-store"}, db_path=db
    )
    duplicate = notifications.enqueue(
        "same-event", "line", {"text": "again"}, db_path=db
    )
    assert first["status"] == "PENDING"
    assert duplicate["deduplicated"] is True
    sent = []
    result = notifications.deliver_due(
        lambda channel, payload: sent.append((channel, payload)) or True,
        db_path=db,
    )
    assert result["delivered"] == 1
    assert sent == [("line", {"text": "hello"})]
    assert notifications.summary(db)["delivered"] == 1


def test_failed_delivery_can_be_retried(tmp_path):
    db = str(tmp_path / "notify.db")
    item = notifications.enqueue("event", "email", {"body": "x"}, db_path=db)

    def fail(_channel, _payload):
        raise RuntimeError("provider unavailable")

    first = notifications.deliver_due(fail, db_path=db)
    assert first["failed"] == 1
    assert notifications.retry(item["id"], db_path=db) == 1
    second = notifications.deliver_due(lambda _c, _p: True, db_path=db)
    assert second["delivered"] == 1
