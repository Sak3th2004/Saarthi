"""Recall must respect the requested local date and observed evidence cutoff."""

from datetime import datetime, timedelta, timezone

import pytest

from saarthi_mcp.memory_query import answer_question
from saarthi_mcp.models import DoseLog, DoseStatus, Event


NOW = datetime(2026, 10, 9, 19, tzinfo=timezone.utc)  # Oct 10, 00:30 in Kolkata


def dose(at, status=DoseStatus.taken):
    return DoseLog(med="Saved medicine", status=status, at=at)


def call(at, detail="The physiotherapist called."):
    return Event(type="call", detail=detail, at=at)


@pytest.mark.parametrize("question", [
    "Did they take the medicine today?", "Did they take the medicine yesterday?",
    "Did the physiotherapist call this morning?", "Did the physiotherapist call this evening?",
])
def test_relative_time_requires_explicit_zone(question):
    answer, evidence = answer_question("Member", question, [dose(NOW)], [call(NOW)], now=NOW)
    assert "IANA time zone" in answer
    assert evidence == []


@pytest.mark.parametrize("zone", ["", "Mars/Unknown", "../../UTC"])
def test_invalid_zone_returns_clarification(zone):
    answer, evidence = answer_question("Member", "Latest dose?", [dose(NOW)], [],
                                       time_zone=zone, now=NOW)
    assert "valid IANA time zone" in answer
    assert evidence == []


@pytest.mark.parametrize("kind", ["dose", "event"])
@pytest.mark.parametrize("offset", [-timedelta(days=8), timedelta(minutes=1)])
def test_today_does_not_reuse_old_or_future_evidence(kind, offset):
    logs = [dose(NOW + offset)] if kind == "dose" else []
    events = [call(NOW + offset)] if kind == "event" else []
    question = "Latest dose today?" if kind == "dose" else "Did the physiotherapist call today?"
    answer, supporting = answer_question("Member", question, logs, events,
                                         time_zone="UTC", now=NOW)
    assert "don't have" in answer
    assert supporting == []


@pytest.mark.parametrize("zone, expected", [
    ("UTC", "2026-10-09T18:59+00:00"),
    ("Asia/Kolkata", "2026-10-10T00:29+05:30"),
])
def test_today_uses_actual_local_date_with_full_timestamp(zone, expected):
    recent = NOW - timedelta(minutes=1)
    old = NOW - timedelta(hours=1)
    answer, _ = answer_question("Member", "Latest dose today?", [dose(old), dose(recent)], [],
                                time_zone=zone, now=NOW)
    assert expected in answer
    assert f"({zone})" in answer


@pytest.mark.parametrize("kind", ["dose", "event"])
def test_yesterday_crosses_kolkata_midnight_without_changing_utc_date(kind):
    yesterday = NOW - timedelta(hours=1)  # 23:30 Oct 9 local
    today = NOW - timedelta(minutes=1)
    logs = [dose(today), dose(yesterday, DoseStatus.missed)] if kind == "dose" else []
    events = [call(today, "The physiotherapist called today."), call(yesterday)] if kind == "event" else []
    question = "Latest dose yesterday?" if kind == "dose" else "Did the physiotherapist call yesterday?"
    answer, supporting = answer_question("Member", question, logs, events,
                                         time_zone="Asia/Kolkata", now=NOW)
    assert "2026-10-09T23:30+05:30" in answer
    if kind == "dose":
        assert "missed Saved medicine" in answer
    else:
        assert supporting == [events[1]]


def test_same_record_is_today_in_utc_but_not_in_kolkata():
    log = dose(NOW - timedelta(hours=1))
    utc, _ = answer_question("Member", "Latest dose today?", [log], [], time_zone="UTC", now=NOW)
    india, evidence = answer_question("Member", "Latest dose today?", [log], [],
                                      time_zone="Asia/Kolkata", now=NOW)
    assert "took Saved medicine" in utc
    assert "don't have" in india
    assert evidence == []


@pytest.mark.parametrize("question", ["Latest dose?", "Did they take Saved medicine?"])
def test_untimed_recall_sorts_input_and_discards_future_logs(question):
    logs = [dose(NOW - timedelta(days=2)), dose(NOW + timedelta(minutes=1)),
            dose(NOW - timedelta(minutes=1), DoseStatus.missed)]
    answer, _ = answer_question("Member", question, logs, [], now=NOW)
    assert "missed Saved medicine" in answer
    assert "2026-10-09T18:59+00:00 (UTC)" in answer


def test_future_event_never_becomes_factual_evidence():
    answer, evidence = answer_question("Member", "Did the physiotherapist call?", [],
                                       [call(NOW + timedelta(seconds=1))], now=NOW)
    assert "don't have" in answer
    assert evidence == []


@pytest.mark.parametrize("expression", [
    "last week", "tomorrow", "last night", "on Friday", "on 2026-10-01",
    "two days ago", "before today", "today and yesterday", "at 10:30",
    "this morning and evening", "today at noon", "on Oct 1", "on the 9th",
    "during 2026", "over the weekend",
])
def test_unsupported_temporal_ranges_do_not_return_unfiltered_facts(expression):
    answer, evidence = answer_question("Member", f"Did the physiotherapist call {expression}?",
                                       [], [call(NOW)], time_zone="UTC", now=NOW)
    assert "can't reliably apply that time range" in answer
    assert evidence == []


def test_morning_event_filter_uses_today_and_explicit_zone():
    current = datetime(2026, 10, 9, 12, tzinfo=timezone.utc)
    morning = call(current.replace(hour=3))  # 08:30 local
    afternoon = call(current.replace(hour=8))
    prior_morning = call(current.replace(hour=3) - timedelta(days=1))
    answer, evidence = answer_question("Member", "Did the physiotherapist call this morning?", [],
                                       [afternoon, prior_morning, morning], time_zone="Asia/Kolkata", now=current)
    assert evidence == [morning]
    assert "2026-10-09T08:30+05:30" in answer


def test_saved_month_name_is_not_misread_as_a_date_expression():
    answer, _ = answer_question("May", "Did May take the medicine?", [dose(NOW)], [], now=NOW)
    assert "May took Saved medicine" in answer
