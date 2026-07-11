from app.services import activity_log


def test_log_config_change_records_old_and_new_value(session):
    activity_log.log_config_change(session, "strategy", "wyckoff", "sonicr")
    session.commit()

    items, total = activity_log.list_config_changes(session, page=1, page_size=10)

    assert total == 1
    assert items[0].key == "strategy"
    assert items[0].old_value == "wyckoff"
    assert items[0].new_value == "sonicr"


def test_log_config_change_is_a_no_op_when_value_unchanged(session):
    activity_log.log_config_change(session, "strategy", "wyckoff", "wyckoff")
    session.commit()

    _, total = activity_log.list_config_changes(session, page=1, page_size=10)

    assert total == 0


def test_log_action_start_then_finish_updates_the_same_row(session):
    log_id = activity_log.log_action_start(session, "screener_scan", "manual")
    entries, total = activity_log.list_system_actions(session, page=1, page_size=10)
    assert total == 1
    assert entries[0].status == "running"
    assert entries[0].finished_at is None

    activity_log.log_action_finish(session, log_id, "success", "42 candidate")

    entries, total = activity_log.list_system_actions(session, page=1, page_size=10)
    assert total == 1
    assert entries[0].status == "success"
    assert entries[0].detail == "42 candidate"
    assert entries[0].finished_at is not None


def test_list_system_actions_orders_newest_first(session):
    first = activity_log.log_action_start(session, "vn30_seed", "manual")
    activity_log.log_action_finish(session, first, "success")
    second = activity_log.log_action_start(session, "screener_scan", "manual")
    activity_log.log_action_finish(session, second, "success")

    items, total = activity_log.list_system_actions(session, page=1, page_size=10)

    assert total == 2
    assert items[0].action == "screener_scan"  # started later -> first
    assert items[1].action == "vn30_seed"


def test_mark_stale_running_as_interrupted_fixes_orphaned_rows(session):
    # A "running" row from a process that got killed/restarted before it
    # could call log_action_finish -- would otherwise show as "running"
    # forever, since _scan_state/_scan_lock are in-memory and always reset
    # to "not running" on a fresh process, but the DB row doesn't.
    stale_id = activity_log.log_action_start(session, "screener_scan", "scheduled")
    done_id = activity_log.log_action_start(session, "vn30_seed", "manual")
    activity_log.log_action_finish(session, done_id, "success", "30 mã")

    fixed_count = activity_log.mark_stale_running_as_interrupted(session)

    assert fixed_count == 1
    items, _ = activity_log.list_system_actions(session, page=1, page_size=10)
    stale_entry = next(e for e in items if e.id == stale_id)
    done_entry = next(e for e in items if e.id == done_id)
    assert stale_entry.status == "error"
    assert stale_entry.finished_at is not None
    assert "khởi động lại" in stale_entry.detail
    assert done_entry.status == "success"  # untouched
    assert done_entry.detail == "30 mã"


def test_mark_stale_running_as_interrupted_is_a_no_op_when_nothing_stale(session):
    log_id = activity_log.log_action_start(session, "screener_scan", "manual")
    activity_log.log_action_finish(session, log_id, "success")

    assert activity_log.mark_stale_running_as_interrupted(session) == 0


def test_list_config_changes_paginates(session):
    for i in range(5):
        activity_log.log_config_change(session, "screener_mcap_max", str(i), str(i + 1))
    session.commit()

    page1, total = activity_log.list_config_changes(session, page=1, page_size=2)
    page2, _ = activity_log.list_config_changes(session, page=2, page_size=2)

    assert total == 5
    assert len(page1) == 2
    assert len(page2) == 2
    assert page1[0].id != page2[0].id
